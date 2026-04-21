# Tier 6 -- Commercial Scraper API Integration Spec

Design for adding a commercial-scraper tier to the OpenTrustSeal fetch escalation ladder. Rescues content fetches that defeat every owned-infrastructure tier (1 direct, 1.5 protocol probe, 2 crawler-Playwright, 3 crawler-Playwright-through-Decodo, 4 residential Mac Air / gaming PC, 5 Wayback). Gated to keep recurring cost predictable.

## Why this exists

The residual failure set after tiers 1-5 is the hardest tail of the web: merchants whose bot protection defeats residential Mac + stealth Playwright + Wayback snapshots simultaneously. Petco, kohls, and other CF Enterprise configs are examples. The count is small (single-digit count out of 1,231 today, projected ~1-3% of the 100K seed), but the domains matter because they are exactly the category of stores agents get asked to transact with.

Commercial scraper APIs (Bright Data Web Unlocker, ZenRows, ScraperAPI) solve this by maintaining thousands of residential IPs, captcha-solving infrastructure, and fingerprint libraries that no single hobby deployment can match. The right question is not "should we use one" but "how do we use one without letting cost spiral."

## Architectural position

```
Tier 1  (direct httpx)                      -- 70% of fetches
Tier 1.5 (security.txt protocol probe)       -- rescues ~50% of tier-1 failures
Tier 2  (crawler Playwright)                 -- SPAs, TLS challenges
Tier 3  (crawler Playwright via Decodo)      -- residential proxy datacenter escape
Tier 4  (Mac Air / gaming PC residential)    -- CF Enterprise with real Chrome
Tier 5  (Wayback Machine archival)           -- frozen snapshots
Tier 6  (commercial scraper API)             -- the residual tail
Fallback -- mark content_unscorable, score via brand anchor only
```

Tier 6 sits AFTER tier 5 (Wayback) and BEFORE the final fallback. The assumption is: if tiers 1-5 all fail AND the 3-strike gate opens, tier 6 is the last attempt before giving up.

## The 3-strike gate

This is the core cost-control mechanism. Tier 6 is NOT a general "try after everything else failed" fallback. It only fires when a domain has **3+ consecutive tier-1-through-5 failures across separate daily re-crawl cycles**.

Why: if we called tier 6 every time tiers 1-5 failed, a single transient network issue (DO outage, crawler box reboot, Cloudflare brief block) would hit the paid API for every affected domain in the registry. That is the path to a $1000+ surprise bill.

The gate's state machine per domain:

```
strike_count: int    # default 0
last_strike_at: datetime
last_success_at: datetime

On tier-1-through-5 failure in a re-crawl cycle:
  if strike_count == 0 OR last_success_at > last_strike_at:
    strike_count = 1
  else:
    strike_count += 1
  last_strike_at = now

On any tier succeeding for this domain:
  strike_count = 0
  last_success_at = now

Tier 6 is called IFF strike_count >= 3.
```

A new DB table tracks this:

```sql
CREATE TABLE tier6_gate (
  domain TEXT PRIMARY KEY,
  strike_count INTEGER DEFAULT 0,
  last_strike_at TEXT,
  last_success_at TEXT,
  last_tier6_called_at TEXT,
  tier6_call_count INTEGER DEFAULT 0,
  FOREIGN KEY (domain) REFERENCES domains(domain)
);
```

## Cost model

Bright Data Web Unlocker pricing as of 2026-04:

| Volume | Price per 1000 requests | Notes |
|---|---|---|
| Starter | $4 | No commitment, good for MVP |
| Growth | $3 | Requires $500/mo commit |
| Enterprise | $2 | Requires $2000/mo commit, worth it at ~700K/mo |

Projected tier 6 call volume under the 3-strike gate:

| Scenario | Stubborn domains | Strikes per month | Tier 6 calls/month | Monthly cost |
|---|---|---|---|---|
| **100K seed, best case** | 1% = 1000 | 1 (first time only) | 1000 | **$4** |
| **100K seed, steady state** | 1% = 1000 | 3 (daily re-crawl across month) | 3000 | **$12** |
| **100K seed, worst case** | 3% = 3000 | 5 (daily re-crawl hits each stubborn multiple times) | 15000 | **$60** |
| **Full 1M seed, steady state** | 1% = 10000 | 3 | 30000 | **$120** |

Budget: set an external spend cap via Bright Data's dashboard (e.g. $200/mo hard ceiling). Well below risk of runaway cost.

For comparison: scraper-API without the 3-strike gate, called on every tier-5-failure:
- 100K seed, 5% tier-1-5 failure rate, daily re-crawl = 150K calls/month = **$600/mo**
- That is the difference between a $12 line item and a $600 line item. Gate everything.

## Environment + configuration

New env file at `/etc/opentrustseal/scraper.env` (mode 640 root:ott):

```
SCRAPER_PROVIDER=brightdata              # or zenrows, scraperapi
SCRAPER_API_KEY=<paste from Bright Data console>
SCRAPER_ZONE=ots_web_unlocker            # Bright Data-specific
SCRAPER_ENABLED=true                     # feature flag, default false initially
SCRAPER_GATE_STRIKES=3                   # 3-strike gate threshold
SCRAPER_MONTHLY_BUDGET_USD=200           # soft cap; warn in /stats when >80%
SCRAPER_TIMEOUT_S=60                     # generous since commercial APIs wait for captchas
```

Feature flag `SCRAPER_ENABLED=false` by default so the tier ships dark. Flip to `true` on the API box to activate after verifying the 3-strike gate works on a test domain.

## Code integration sketch

New function in `server/app/fetch_escalation.py`, mirroring the existing tier function patterns:

```python
# Tier 6: commercial scraper API (Bright Data / ZenRows / ScraperAPI).
# Gated behind a 3-strike accumulator so we only pay for domains that
# have genuinely defeated every other tier across multiple retries.

_SCRAPER_ENV = _load_env_file("/etc/opentrustseal/scraper.env")
SCRAPER_PROVIDER = _SCRAPER_ENV.get("SCRAPER_PROVIDER", "").lower()
SCRAPER_API_KEY = _SCRAPER_ENV.get("SCRAPER_API_KEY", "")
SCRAPER_ZONE = _SCRAPER_ENV.get("SCRAPER_ZONE", "")
SCRAPER_ENABLED = _SCRAPER_ENV.get("SCRAPER_ENABLED", "false").lower() == "true"
SCRAPER_GATE_STRIKES = int(_SCRAPER_ENV.get("SCRAPER_GATE_STRIKES", "3"))
SCRAPER_TIMEOUT_S = float(_SCRAPER_ENV.get("SCRAPER_TIMEOUT_S", "60"))

_breaker_tier6 = _CircuitBreaker(threshold=5, window=300.0, cooldown=600.0)

COUNTERS.update({
    "tier6_ok": 0,
    "tier6_error": 0,
    "tier6_skipped_breaker": 0,
    "tier6_skipped_gate": 0,
    "tier6_disabled": 0,
})


async def fetch_via_commercial_scraper(
    url: str,
    domain: str,
    timeout_s: float = SCRAPER_TIMEOUT_S,
) -> Optional[CrawlerResponse]:
    """Tier 6: last-resort commercial scraper API.

    Gated behind a 3-strike accumulator in the tier6_gate DB table.
    Only called if a domain has failed tiers 1-5 on >=3 separate
    re-crawl cycles since its last success.
    """
    if not SCRAPER_ENABLED or not SCRAPER_API_KEY:
        COUNTERS["tier6_disabled"] += 1
        return None

    if _breaker_tier6.is_open():
        COUNTERS["tier6_skipped_breaker"] += 1
        return None

    # 3-strike gate check
    strikes = tier6_gate.get_strike_count(domain)
    if strikes < SCRAPER_GATE_STRIKES:
        COUNTERS["tier6_skipped_gate"] += 1
        return None

    # Dispatch to the configured provider
    if SCRAPER_PROVIDER == "brightdata":
        return await _fetch_via_brightdata(url, timeout_s)
    elif SCRAPER_PROVIDER == "zenrows":
        return await _fetch_via_zenrows(url, timeout_s)
    elif SCRAPER_PROVIDER == "scraperapi":
        return await _fetch_via_scraperapi(url, timeout_s)
    else:
        COUNTERS["tier6_disabled"] += 1
        return None
```

Per-provider adapter functions handle the API-specific request shape:

```python
async def _fetch_via_brightdata(url: str, timeout_s: float) -> Optional[CrawlerResponse]:
    """Bright Data Web Unlocker: HTTPS proxy, format 'zone:password@brd.superproxy.io:22225'."""
    proxy_url = f"http://brd-customer-{SCRAPER_ZONE}-zone-ots_web_unlocker:{SCRAPER_API_KEY}@brd.superproxy.io:22225"
    try:
        async with httpx.AsyncClient(
            proxies={"https://": proxy_url, "http://": proxy_url},
            timeout=timeout_s,
            verify=False,  # Bright Data injects their own cert
        ) as client:
            r = await client.get(url, headers={
                "User-Agent": _REALISTIC_CHROME_UA,
                "Accept": "text/html,application/xhtml+xml",
            })
            if r.status_code >= 200 and r.status_code < 400:
                _breaker_tier6.record_success()
                COUNTERS["tier6_ok"] += 1
                return CrawlerResponse({
                    "status": r.status_code,
                    "body": r.text[:500_000],
                    "headers": dict(r.headers),
                    "final_url": str(r.url),
                    "redirect_count": 0,
                    "elapsed_ms": 0,
                    "fetched_via": "scraper-brightdata",
                })
    except Exception as e:
        pass
    _breaker_tier6.record_error()
    COUNTERS["tier6_error"] += 1
    return None
```

Update to `content_check.py` to call tier 6 after tier 5 failure:

```python
# After tier 5 Wayback attempt fails:
if result is None and SCRAPER_ENABLED:
    result = await fetch_via_commercial_scraper(url, domain)
    if result is not None:
        raw["content"]["_source"] = "scraper-tier6"
```

## DB helper for the gate

New module `server/app/tier6_gate.py`:

```python
"""3-strike gate for tier 6 commercial scraper calls.

Every domain accumulates strikes on tier-1-through-5 failure, resets on
any tier success. Tier 6 is called iff strike_count >= SCRAPER_GATE_STRIKES.
"""

from datetime import datetime, timezone
from .database import _get_conn


def record_strike(domain: str) -> int:
    """Called when tiers 1-5 all failed for this domain. Returns new strike count."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT strike_count, last_success_at FROM tier6_gate WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO tier6_gate (domain, strike_count, last_strike_at) VALUES (?, 1, ?)",
                (domain, now),
            )
            return 1
        new_count = (row["strike_count"] or 0) + 1
        conn.execute(
            "UPDATE tier6_gate SET strike_count = ?, last_strike_at = ? WHERE domain = ?",
            (new_count, now, domain),
        )
        return new_count


def record_success(domain: str) -> None:
    """Called when any tier (including tier 6) succeeded. Resets strikes."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO tier6_gate (domain, strike_count, last_success_at)
               VALUES (?, 0, ?)
               ON CONFLICT(domain) DO UPDATE SET strike_count = 0, last_success_at = ?""",
            (domain, now, now),
        )


def get_strike_count(domain: str) -> int:
    """Called before dispatching tier 6. Returns current strike count."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT strike_count FROM tier6_gate WHERE domain = ?",
            (domain,),
        ).fetchone()
        return row["strike_count"] if row else 0


def record_tier6_call(domain: str) -> None:
    """Called after each tier 6 invocation. For billing audit trail."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """UPDATE tier6_gate
               SET last_tier6_called_at = ?,
                   tier6_call_count = tier6_call_count + 1
               WHERE domain = ?""",
            (now, domain),
        )
```

## Rollout plan

1. **Phase 0 (ship dark).** Deploy all the code with `SCRAPER_ENABLED=false`. Verify existing tiers 1-5 still work end-to-end with no regression. The tier6_gate table exists but no strikes recorded yet.

2. **Phase 1 (shadow mode, 1 week).** `SCRAPER_ENABLED=true` but record_strike is called AND tier 6 is actually invoked -- EXCEPT the resulting content is NOT used for scoring (shadow mode). Logs and counter `tier6_ok` increment so we can measure how often it actually rescues content, without spending money on bad data or breaking scoring. Confirm cost trajectory matches the projection.

3. **Phase 2 (live, 1 month).** Tier 6 content feeds scoring. Monitor monthly spend vs `SCRAPER_MONTHLY_BUDGET_USD` cap; set up an alert at 70% of budget.

4. **Phase 3 (optimize).** Decide whether to stay on Starter pricing ($4/1000) or commit to Growth ($3/1000 with $500/mo commit). Break-even at ~125K calls/month.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Tier 6 called on every tier-5 miss | Gate bypassed / bug in record_strike | Check `SCRAPER_GATE_STRIKES` env; check gate table populated |
| Monthly cost spiking | Daily re-crawl batch too large, many strikes at once | Lower daily batch size; raise SCRAPER_GATE_STRIKES to 5 |
| Tier 6 rescue rate <10% | Commercial provider also failing on these sites | Try second provider in parallel; file a ticket with current provider |
| Bright Data bills charge for 4xx responses | Depends on provider policy | Read terms; may need to not-use-if status>=400 |
| CPU spike in content_check when tier 6 hits | Parser blocking on 500KB body | Already capped at 500_000 bytes; no action needed |
| Signed payload divergence | tier 6 content scored differently from tier 1-5 | Ensure `_source: scraper-tier6` persisted in raw_signals so rescore.py handles it |

## Provider selection rationale

**Default: Bright Data Web Unlocker.**

Why over ZenRows or ScraperAPI:
- Bright Data is the category leader. Largest residential IP pool, best captcha-solver integrations, most mature abuse-compliance stance. The extra ~$1/1000 over cheaper competitors is insurance on the long tail we are explicitly targeting.
- HTTP proxy model is the cleanest integration -- drops into `httpx.AsyncClient(proxies=...)` with no code path divergence from how Decodo (tier 3) already works.
- Bright Data has public SDK examples in both Python and Node, matching our stack.
- Transparent pricing and opt-in commit tiers; no surprise charges for captcha solving.

**Fallback: ZenRows.** Cheaper ($2.49/1000 on their Business plan), browser-based so handles more JS-heavy cases, but smaller residential pool.

**Avoid: ScraperAPI.** Cheaper still but consensus reports say the success rate on CF Enterprise is noticeably lower than Bright Data, which defeats the purpose of being the last-tier rescue.

## Open questions for future work

- Should tier 6 content carry a visible flag in the signed response (e.g. `COMMERCIAL_SCRAPER_USED`) so agents know the content path? Leaning yes for transparency.
- Should commercial provider be a dimension of the scoring confidence rating (e.g. tier 6 content = `confidence: medium` even on otherwise full signals)? Probably not -- the data quality is comparable to tier 4 residential.
- Can we pre-warm the gate with the known-stubborn set from the 100K seed to skip the 3-week strike ramp-up? Yes, via a one-time bootstrap SQL insert after the seed merge.
