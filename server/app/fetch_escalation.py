"""Tiered homepage fetch with fallback to a remote crawler service.

The API box tries direct httpx first (fast, cheap). On transient failure --
timeout, TCP reset, 403/429/5xx -- it escalates to the crawler service
running on ots-crawler-1 over the private VPC. The crawler runs real
headless Chromium, which bypasses sites that fingerprint httpx but doesn't
help with IP-AS blocks. Tier 3 (Playwright-via-residential-proxy) is wired
but gated behind a feature flag that stays off until proxy creds land.

Circuit breaker: if the crawler errors N times within a window, skip it
for a cool-off period and fall straight through to the next tier. Prevents
a sick crawler from adding latency to every request during an outage.

The crawler returns JSON; we wrap it in a shim that quacks like an
httpx.Response so the rest of content_check.collect() doesn't care which
tier produced the body.
"""

import asyncio
import os
import time
from typing import Optional

import httpx


def _load_env_file(path: str) -> dict:
    """Read a simple KEY=VALUE env file if present. Missing is fine."""
    env: dict = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        except OSError:
            pass
    return env


_CRAWLER_ENV = _load_env_file("/etc/opentrustseal/crawler.env")
_DECODO_ENV = _load_env_file("/etc/opentrustseal/decodo.env")
_MACBOOK_ENV = _load_env_file("/etc/opentrustseal/macbook.env")

CRAWLER_URL = _CRAWLER_ENV.get("CRAWLER_URL") or os.environ.get("OTS_CRAWLER_URL", "")
CRAWLER_SECRET = _CRAWLER_ENV.get("CRAWLER_SHARED_SECRET") or os.environ.get("OTS_CRAWLER_SECRET", "")
CRAWLER_ENABLED = bool(CRAWLER_URL and CRAWLER_SECRET)

DECODO_HOST = _DECODO_ENV.get("DECODO_HOST", "")
DECODO_PORT = _DECODO_ENV.get("DECODO_PORT", "")
DECODO_USER = _DECODO_ENV.get("DECODO_USER", "")
DECODO_PASS = _DECODO_ENV.get("DECODO_PASS", "")
DECODO_ENABLED = bool(DECODO_HOST and DECODO_PORT and DECODO_USER and DECODO_PASS)

# Tier 4: residential Mac crawlers reachable via Tailscale. Same fetch
# service protocol as tier 2 (crawler box), just different hosts on
# residential IPs. Supports multiple residential endpoints for
# geographic diversity and redundancy. The API box tries each one
# in round-robin order, skipping any whose circuit breaker is open.
#
# Config format in /etc/opentrustseal/macbook.env:
#   MACBOOK_URL=http://100.125.118.64:8901          (single, backward compat)
#   RESIDENTIAL_URLS=http://100.x:8901,http://100.y:8901,http://100.z:8901
#
MACBOOK_URL = _MACBOOK_ENV.get("MACBOOK_URL", "")
MACBOOK_SECRET = _MACBOOK_ENV.get("MACBOOK_SHARED_SECRET") or CRAWLER_SECRET
MACBOOK_ENABLED = bool(MACBOOK_URL and MACBOOK_SECRET)

# Multi-residential fleet: comma-separated list of Tailscale URLs
_residential_urls_raw = _MACBOOK_ENV.get("RESIDENTIAL_URLS", "")
RESIDENTIAL_URLS = [u.strip() for u in _residential_urls_raw.split(",") if u.strip()] if _residential_urls_raw else []
if MACBOOK_URL and MACBOOK_URL not in RESIDENTIAL_URLS:
    RESIDENTIAL_URLS.insert(0, MACBOOK_URL)
RESIDENTIAL_ENABLED = bool(RESIDENTIAL_URLS and MACBOOK_SECRET)

# Tier 5: Internet Archive Wayback Machine. When tiers 1-4 all fail, ask
# the archive if it has a recent copy of the homepage. This works for
# almost every major brand (archive.org has a long-standing relationship
# with most site operators and is rarely blocked) but fails for the
# strictest CF Enterprise configs that blanket-block automated crawlers
# including archive.org (petco is an example). When Wayback does have a
# snapshot, we get real HTML from the site sometime in the last N days,
# which is fresh enough for body-based signals (privacy policy, terms,
# contact info, schema.org markup). Response headers are NOT preserved
# by Wayback, so security-header signals will read as zero for
# wayback-sourced content -- see known follow-up in task backlog.
#
# Feature-flagged via OTS_ENABLE_WAYBACK_TIER for the initial rollout
# so we can compare wayback-sourced signals against live-fetched signals
# for domains where both work before making this a default tier.
WAYBACK_ENABLED = os.environ.get("OTS_ENABLE_WAYBACK_TIER", "").lower() in ("1", "true", "yes", "on")
WAYBACK_MAX_AGE_DAYS = int(os.environ.get("OTS_WAYBACK_MAX_AGE_DAYS", "60"))

# Tier 6 (protocol probe): direct httpx to a deliberately-404 protocol-
# standard path on the target origin. Most sites serve a static 404 error
# shell from their base template, which contains the footer with the
# privacy/terms/contact links OTT scores on. This works because bot
# protection (captchas, JS challenges, Turnstile) is typically applied to
# the homepage and dynamic category pages, not to static error shells
# served directly by the web server layer.
#
# The preferred probe path is /.well-known/security.txt -- it's protocol-
# standard, adoption is under 5% among top sites, and when a site DOES
# have a real security.txt the response is itself a positive signal
# (published vulnerability disclosure process). Either outcome produces
# useful data.
#
# Despite the tier number, the probe runs BEFORE tiers 2-5 in
# content_check because it's as cheap as tier 1 (one direct httpx call)
# and supersedes the need for Playwright escalation when it works. The
# tier numbering reflects order-of-implementation, not fetch-ladder order.
PROBE_ENABLED = os.environ.get("OTS_ENABLE_PROBE_TIER", "").lower() in ("1", "true", "yes", "on")
PROBE_PATH = os.environ.get("OTS_PROBE_PATH", "/.well-known/security.txt")


class _CircuitBreaker:
    """Simple count-based breaker: after N errors in WINDOW seconds, open
    for COOLDOWN seconds and return None for any calls until cooldown ends."""

    def __init__(self, threshold: int = 3, window: float = 60.0, cooldown: float = 300.0):
        self.threshold = threshold
        self.window = window
        self.cooldown = cooldown
        self._errors: list[float] = []
        self._open_until: float = 0.0

    def is_open(self) -> bool:
        now = time.monotonic()
        if now < self._open_until:
            return True
        # Prune errors outside the window
        cutoff = now - self.window
        self._errors = [t for t in self._errors if t >= cutoff]
        return False

    def record_success(self) -> None:
        self._errors.clear()

    def record_error(self) -> None:
        now = time.monotonic()
        self._errors.append(now)
        cutoff = now - self.window
        self._errors = [t for t in self._errors if t >= cutoff]
        if len(self._errors) >= self.threshold:
            self._open_until = now + self.cooldown
            self._errors.clear()


_breaker_tier2 = _CircuitBreaker()
# Tier 3 gets its own breaker so a Decodo outage can't corrupt tier 2 state
# and so tier 2 failures can't trip tier 3 unnecessarily.
_breaker_tier3 = _CircuitBreaker(threshold=3, window=60.0, cooldown=300.0)
# Tier 4 Mac Air may be offline/asleep/traveling. Short window and short
# cooldown so the breaker opens fast on outage and recovers fast when the
# Mac is back.
_breaker_tier4 = _CircuitBreaker(threshold=2, window=30.0, cooldown=120.0)
# Tier 5 Wayback is stable (archive.org rarely goes down) but the CDX API
# occasionally 504s under load. Medium threshold, short cooldown so one
# flaky minute doesn't disable the tier for long.
_breaker_tier5 = _CircuitBreaker(threshold=4, window=60.0, cooldown=180.0)
# Probe breaker: direct httpx is cheap and usually reliable. Short
# cooldown so a transient DNS or network blip doesn't disable the probe
# for long.
_breaker_probe = _CircuitBreaker(threshold=4, window=60.0, cooldown=120.0)

# Counters for /stats endpoint visibility
COUNTERS = {
    "tier2_ok": 0,
    "tier2_error": 0,
    "tier2_skipped_breaker": 0,
    "tier2_disabled": 0,
    "tier3_ok": 0,
    "tier3_error": 0,
    "tier3_skipped_breaker": 0,
    "tier3_disabled": 0,
    "tier4_ok": 0,
    "tier4_error": 0,
    "tier4_skipped_breaker": 0,
    "tier4_disabled": 0,
    "tier5_ok": 0,
    "tier5_error": 0,
    "tier5_skipped_breaker": 0,
    "tier5_disabled": 0,
    "tier5_no_recent_snapshot": 0,
    "probe_ok": 0,
    "probe_error": 0,
    "probe_skipped_breaker": 0,
    "probe_disabled": 0,
    "probe_no_body": 0,
}


class CrawlerResponse:
    """Shim that quacks like httpx.Response for content_check.collect().

    Only the attributes collect() actually reads are implemented: status_code,
    text, headers, history (as an empty list -- redirect count is in the JSON
    but content_check derives it from len(resp.history), so we populate that
    with placeholder objects). url is set to the final_url from the crawler.
    """

    def __init__(self, payload: dict):
        self.status_code: int = payload.get("status", 0) or 0
        self.text: str = payload.get("body", "") or ""
        raw_headers = payload.get("headers", {}) or {}
        self.headers = httpx.Headers({str(k): str(v) for k, v in raw_headers.items()})
        self.url = payload.get("final_url", "")
        rc = int(payload.get("redirect_count", 0) or 0)
        # Fake history: content_check only calls len() on it, never indexes
        self.history = [None] * rc


async def fetch_via_crawler(url: str, timeout_s: float = 30.0) -> Optional[CrawlerResponse]:
    """Tier 2 fetch through the crawler service. Returns None on any failure
    (caller should fall through to tier 3 or bail). Updates counters and
    circuit breaker along the way."""
    if not CRAWLER_ENABLED:
        COUNTERS["tier2_disabled"] += 1
        return None
    if _breaker_tier2.is_open():
        COUNTERS["tier2_skipped_breaker"] += 1
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{CRAWLER_URL}/fetch",
                json={"url": url, "timeout_ms": int(timeout_s * 1000) - 2000},
                headers={"X-Crawler-Secret": CRAWLER_SECRET},
            )
        if r.status_code != 200:
            _breaker_tier2.record_error()
            COUNTERS["tier2_error"] += 1
            return None
        payload = r.json()
        if payload.get("error"):
            # Crawler reached the site but navigation errored (timeout,
            # connection refused). Don't count as breaker-worthy error --
            # the crawler itself is healthy; the target is what failed.
            # Still return None so the caller falls through.
            return None
        if (payload.get("status") or 0) >= 400:
            # Got a real 4xx/5xx from the target. Return the shim so
            # callers see the error status and can decide what to do.
            _breaker_tier2.record_success()
            COUNTERS["tier2_ok"] += 1
            return CrawlerResponse(payload)
        _breaker_tier2.record_success()
        COUNTERS["tier2_ok"] += 1
        return CrawlerResponse(payload)
    except Exception:
        _breaker_tier2.record_error()
        COUNTERS["tier2_error"] += 1
        return None


async def fetch_via_crawler_proxied(url: str, timeout_s: float = 45.0) -> Optional[CrawlerResponse]:
    """Tier 3 fetch: crawler box runs Playwright through Decodo residential
    proxy. This is the heavy artillery -- fresh rotating residential IP per
    request, stealth-patched Chrome, real TLS/HTTP2 fingerprint of the
    proxy-tunneled residential exit node. Target use case: sites that
    block both direct httpx AND direct Playwright on datacenter AS.

    Still doesn't beat Cloudflare Enterprise Bot Management, which reads
    browser fingerprints deeper than stealth plugins can patch. Those sites
    fall through to crawlability=blocked and Phase 1 re-weighting handles
    the scoring without content.
    """
    if not CRAWLER_ENABLED:
        COUNTERS["tier3_disabled"] += 1
        return None
    if not DECODO_ENABLED:
        COUNTERS["tier3_disabled"] += 1
        return None
    if _breaker_tier3.is_open():
        COUNTERS["tier3_skipped_breaker"] += 1
        return None
    try:
        payload = {
            "url": url,
            "timeout_ms": int(timeout_s * 1000) - 3000,
            "wait_until": "domcontentloaded",
            "proxy": {
                "server": f"http://{DECODO_HOST}:{DECODO_PORT}",
                "username": DECODO_USER,
                "password": DECODO_PASS,
            },
        }
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{CRAWLER_URL}/fetch",
                json=payload,
                headers={"X-Crawler-Secret": CRAWLER_SECRET},
            )
        if r.status_code != 200:
            _breaker_tier3.record_error()
            COUNTERS["tier3_error"] += 1
            return None
        body = r.json()
        if body.get("error"):
            # Crawler service is healthy but the target-or-proxy couldn't
            # reach. Don't record as breaker error; caller falls through.
            return None
        _breaker_tier3.record_success()
        COUNTERS["tier3_ok"] += 1
        return CrawlerResponse(body)
    except Exception:
        _breaker_tier3.record_error()
        COUNTERS["tier3_error"] += 1
        return None


# Round-robin index for residential fleet. Each call to fetch_via_macbook
# tries the next residential URL in the list, skipping any with open
# circuit breakers. This distributes load and IP-reputation pressure
# across all residential endpoints.
_residential_index = 0
_residential_breakers: dict[str, _CircuitBreaker] = {}

def _get_residential_breaker(url: str) -> _CircuitBreaker:
    if url not in _residential_breakers:
        _residential_breakers[url] = _CircuitBreaker(threshold=2, window=30.0, cooldown=120.0)
    return _residential_breakers[url]


async def fetch_via_macbook(url: str, timeout_s: float = 40.0) -> Optional[CrawlerResponse]:
    """Tier 4 fetch: residential Mac crawlers reachable over Tailscale.

    Tries each residential endpoint in round-robin order, skipping any
    whose circuit breaker is open. Supports a fleet of Macs at different
    residences (different ISPs, different IP reputations) for geographic
    diversity against CF Enterprise Bot Management.

    Falls back to the single MACBOOK_URL if no RESIDENTIAL_URLS are
    configured (backward compatible).
    """
    global _residential_index

    urls = RESIDENTIAL_URLS if RESIDENTIAL_ENABLED else ([MACBOOK_URL] if MACBOOK_ENABLED else [])
    if not urls:
        COUNTERS["tier4_disabled"] += 1
        return None

    # Try each residential endpoint starting from the round-robin index
    for attempt in range(len(urls)):
        idx = (_residential_index + attempt) % len(urls)
        endpoint = urls[idx]
        breaker = _get_residential_breaker(endpoint)

        if breaker.is_open():
            continue

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                r = await client.post(
                    f"{endpoint}/fetch",
                    json={
                        "url": url,
                        "timeout_ms": int(timeout_s * 1000) - 5000,
                        "wait_until": "domcontentloaded",
                    },
                    headers={"X-Crawler-Secret": MACBOOK_SECRET},
                )
            _residential_index = (idx + 1) % len(urls)  # advance round-robin

            if r.status_code != 200:
                breaker.record_error()
                COUNTERS["tier4_error"] += 1
                continue
            body = r.json()
            if body.get("error"):
                continue
            breaker.record_success()
            COUNTERS["tier4_ok"] += 1
            return CrawlerResponse(body)
        except Exception:
            breaker.record_error()
            COUNTERS["tier4_error"] += 1
            continue  # try next residential endpoint

    # All endpoints tried, all failed or had open breakers
    return None


async def fetch_via_wayback(url: str, timeout_s: float = 25.0) -> Optional[CrawlerResponse]:
    """Tier 5 fetch: Internet Archive Wayback Machine.

    Asks the CDX index for the most recent 200-status snapshot of the
    homepage within WAYBACK_MAX_AGE_DAYS, then retrieves the raw body via
    the `id_` (identity) mode, which returns the archived content without
    Wayback's own toolbar injection. Returns a CrawlerResponse shim that
    quacks like the other tiers so content_check doesn't need to know
    the source of the bytes.

    Known limitations:
    - Response headers are NOT archived by Wayback, so the returned shim
      has an empty headers dict. Security-header signals will read as
      zero for wayback-sourced content; treat those as "unknown" not
      "absent" at the scoring layer. Tracked as a follow-up task.
    - Aggressive CF Enterprise configs (petco) block archive.org too,
      so CDX will either return empty or only show 403 rows. The
      statuscode:200 filter below rejects those and returns no snapshot.
    - Content may be up to WAYBACK_MAX_AGE_DAYS old, which is fine for
      body-based signals (privacy policy, schema.org) on established
      brands but would be too stale for a new/changing site. Tier 5
      should only fire after tiers 1-4 have tried the live path.

    The `x-ots-source` synthetic header on the returned shim tags the
    snapshot timestamp so downstream consumers (raw_signals, dashboards)
    can see where the data came from.
    """
    if not WAYBACK_ENABLED:
        COUNTERS["tier5_disabled"] += 1
        return None
    if _breaker_tier5.is_open():
        COUNTERS["tier5_skipped_breaker"] += 1
        return None

    # CDX API: most recent 200-status snapshot within the freshness window.
    # `from=YYYYMMDD` bounds the search window; `limit=-1` returns the most
    # recent matching row (negative = latest-first ordering).
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=WAYBACK_MAX_AGE_DAYS)
    from_ts = cutoff.strftime("%Y%m%d")
    cdx_url = (
        "http://web.archive.org/cdx/search/cdx"
        f"?url={url.replace('https://', '').replace('http://', '').rstrip('/')}/"
        f"&output=json&limit=-1&filter=statuscode:200&from={from_ts}"
    )

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            cdx_resp = await client.get(cdx_url)
        if cdx_resp.status_code != 200:
            _breaker_tier5.record_error()
            COUNTERS["tier5_error"] += 1
            return None
        rows = cdx_resp.json()
        # CDX returns [header_row, data_row, ...]. If only the header is
        # present, there's no snapshot in the window.
        if not rows or len(rows) < 2:
            COUNTERS["tier5_no_recent_snapshot"] += 1
            return None
        row = rows[-1]  # limit=-1 gives us the most recent first, but be defensive
        header = rows[0]
        try:
            ts_idx = header.index("timestamp")
            orig_idx = header.index("original")
        except ValueError:
            _breaker_tier5.record_error()
            COUNTERS["tier5_error"] += 1
            return None
        timestamp = row[ts_idx]
        original_url = row[orig_idx]

        snapshot_url = f"https://web.archive.org/web/{timestamp}id_/{original_url}"

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            snap_resp = await client.get(snapshot_url)
        if snap_resp.status_code != 200 or not snap_resp.text:
            _breaker_tier5.record_error()
            COUNTERS["tier5_error"] += 1
            return None

        # Build the shim payload. Wayback strips the original response
        # headers so we synthesize only a source tag. Consumers that care
        # about security headers must treat the missing values as unknown.
        payload = {
            "status": 200,
            "body": snap_resp.text,
            "headers": {
                "x-ots-source": f"wayback-{timestamp}",
                "x-ots-snapshot-age-days": str((datetime.now(timezone.utc) - datetime.strptime(timestamp[:8], "%Y%m%d").replace(tzinfo=timezone.utc)).days),
                "content-type": snap_resp.headers.get("content-type", "text/html"),
            },
            "final_url": original_url,
            "redirect_count": 0,
        }
        _breaker_tier5.record_success()
        COUNTERS["tier5_ok"] += 1
        return CrawlerResponse(payload)
    except Exception:
        _breaker_tier5.record_error()
        COUNTERS["tier5_error"] += 1
        return None


async def fetch_via_protocol_probe(url: str, timeout_s: float = 15.0) -> Optional[CrawlerResponse]:
    """Tier 6 (positioned early in the ladder): protocol-path probe.

    Direct httpx request to a deliberately-404 protocol-standard path
    on the target origin. Most sites serve a static 404 error shell
    containing the footer template (privacy/terms/contact/copyright),
    which is exactly the content signal OTT scores on. Bot protection
    is almost never applied to static 404 responses because blocking
    them would break the site's own error handling for real users.

    If the site actually has the probe path (e.g., a real security.txt),
    we get a 200 response with real content -- also a positive signal
    (published vulnerability disclosure process). Either outcome yields
    parseable content for the scoring pipeline.

    Response headers from the probe are the REAL origin headers (CSP,
    HSTS, X-Frame-Options, etc.) because the request hits the same web
    server the homepage would, just at a different path. This is an
    advantage over tier 5 (Wayback) which strips response headers.

    The returned shim tags the source as `probe-security-txt-<status>`
    in the x-ots-source header, and preserves the original HTTP status
    in x-ots-probe-status, so downstream consumers can distinguish a
    real-200 from a 404-shell path.
    """
    if not PROBE_ENABLED:
        COUNTERS["probe_disabled"] += 1
        return None
    if _breaker_probe.is_open():
        COUNTERS["probe_skipped_breaker"] += 1
        return None

    base = url.rstrip("/")
    probe_url = f"{base}{PROBE_PATH}"

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            r = await client.get(probe_url)

        # 403/429/5xx mean the probe path is also walled. 404 is the
        # expected success path (we want the shell). Status 200 on a
        # small body is typically a real security.txt, which does not
        # give us footer signals, so we reject it here -- the value of
        # a real security.txt is accounted for elsewhere via
        # hasSecurityTxt. Anything else (429, 5xx) is a real failure.
        if r.status_code not in (200, 404):
            _breaker_probe.record_error()
            COUNTERS["probe_error"] += 1
            return None

        body = r.text or ""

        # Reject responses that clearly contain a bot-challenge
        # interstitial rather than the site's real error shell. These
        # pages are large (sometimes 190KB+) and contain keywords that
        # downstream parsers would false-positive on ("privacy",
        # "contact" appear inside CF challenge boilerplate). Signature
        # strings come from CF (cf-chl-bypass / challenge-platform /
        # "Just a moment"), DataDome (datadome / captcha-delivery),
        # and PerimeterX (px-captcha / _pxCaptcha). If any are present
        # the response is not a real footer shell.
        CHALLENGE_MARKERS = (
            "cf-chl-bypass", "challenge-platform", "__cf_chl_",
            "just a moment", "attention required",
            "captcha-delivery.com", "datadome",
            "_pxcaptcha", "px-captcha", "perimeterx",
        )
        low = body.lower()
        if any(m in low for m in CHALLENGE_MARKERS):
            _breaker_probe.record_error()
            COUNTERS["probe_error"] += 1
            return None

        # Reject 200-status small bodies (real security.txt). These
        # are valuable as a signal but not as content-footer source.
        if r.status_code == 200 and len(body) < 2000:
            COUNTERS["probe_no_body"] += 1
            return None

        # For 404 shells, require a body that plausibly contains an
        # HTML footer template. 3KB minimum plus a <html tag. Minimal
        # error handlers (nginx default 404, Apache default 404) are
        # typically under 1KB, and anything under 3KB rarely has the
        # full site chrome with footer links.
        if r.status_code == 404:
            if len(body) < 3000 or "<html" not in low:
                COUNTERS["probe_no_body"] += 1
                return None

        original_status = r.status_code
        source_tag = f"probe-security-txt-{original_status}"

        payload = {
            # Force status 200 so content_check treats the shim as a
            # successful fetch. The real HTTP status is preserved in
            # x-ots-probe-status for transparency.
            "status": 200,
            "body": body,
            "headers": {
                **{str(k): str(v) for k, v in r.headers.items()},
                "x-ots-source": source_tag,
                "x-ots-probe-path": PROBE_PATH,
                "x-ots-probe-status": str(original_status),
            },
            "final_url": str(r.url),
            "redirect_count": 0,
        }
        _breaker_probe.record_success()
        COUNTERS["probe_ok"] += 1
        return CrawlerResponse(payload)
    except Exception:
        _breaker_probe.record_error()
        COUNTERS["probe_error"] += 1
        return None


def stats() -> dict:
    """Snapshot of fetch tier counters, safe to expose in /stats."""
    return {
        **COUNTERS,
        "tier2_enabled": CRAWLER_ENABLED,
        "tier2_breaker_open": _breaker_tier2.is_open(),
        "tier3_enabled": CRAWLER_ENABLED and DECODO_ENABLED,
        "tier3_breaker_open": _breaker_tier3.is_open(),
        "tier4_enabled": MACBOOK_ENABLED,
        "tier4_breaker_open": _breaker_tier4.is_open(),
        "tier5_enabled": WAYBACK_ENABLED,
        "tier5_breaker_open": _breaker_tier5.is_open(),
        "tier5_max_age_days": WAYBACK_MAX_AGE_DAYS,
        "probe_enabled": PROBE_ENABLED,
        "probe_breaker_open": _breaker_probe.is_open(),
        "probe_path": PROBE_PATH,
        "crawler_url": CRAWLER_URL if CRAWLER_ENABLED else None,
        "decodo_host": DECODO_HOST if DECODO_ENABLED else None,
        "macbook_url": MACBOOK_URL if MACBOOK_ENABLED else None,
    }
