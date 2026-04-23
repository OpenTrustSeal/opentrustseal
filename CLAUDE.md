# OpenTrustSeal Project State

## Session Summary (2026-04-22 through 2026-04-23) -- launch-ready push

One sustained session that cleared the 6 pre-launch blockers, deployed the full Codex fix bundle + v1.4 rescore + tier 6, and added a 5-box Hetzner accelerator fleet to cut seed ETA from 7.8d to ~3.5d.

**All 6 launch blockers done:**

1. **API droplet upgraded** -- `ott-api-1` → `ots-ap-1` (DO label), resized to 2vCPU / 2GB / 60GB ($18/mo). Longer-than-expected resize (~15 min) because disk size bumped from 25 GB to 60 GB. Services all came back clean.
2. **Litestream → Backblaze B2** -- continuous WAL-page replication to `s3://ots-db-backup/production/ott.db`, 72h granular restore, independent failure domain. Verified end-to-end (`litestream restore` match live DB exactly). Runbook at `docs/LITESTREAM-RUNBOOK.md`.
3. **Cloudflare proxy live** -- opentrustseal.com was already CF-registered but proxy was OFF. Turned on orange cloud for apex + www + api. SSL mode Full (strict). Page rule: `/v1/check/*` cache bypass. **Bot Fight Mode OFF** (API needs to accept default Python/Node UAs). Verified default urllib, httpx, requests, curl, node-fetch all get 200.
4. **logrotate deployed** to API + 18 seed boxes (daily, 14 days, compress, copytruncate, 100 MB size cap).
5. **Repo public** at `github.com/OpenTrustSeal/opentrustseal`. Credential audit found zero leaked secrets in tracked files or git history. `SECURITY.md` with 2-day acknowledgment + 7-day first-pass + severity-tiered fix timelines.
6. **Methodology page polished** -- added "Why independent, not a payment rail" section (neutrality narrative), "Confidence and cautionReason" section (agent-decision surface), "Open methodology and open dataset" section (linking repo + v1.4 spec + SECURITY.md).

**Codex fix bundle deployed + v1.4 rescore complete:**

- API box running the fix bundle + v1.4 scoring. All 1,231 production rows rescored.
- Distribution: 1215 high / 12 medium / 4 low confidence. cautionReason: 237 incomplete_evidence / 165 weak_signals / 20 new_domain.
- `CONTENT_UNSCORABLE` flag preserved via one-shot backfill from pre-rescore snapshot (303 rows).
- **Gotcha encountered + fixed:** systemd unit had `OTT_DB_PATH` (legacy) but new code reads `OTS_DB_PATH` → briefly wrote to empty `ots.db` next to populated `ott.db`. Patched the unit env to set both.
- **Gotcha encountered + fixed:** legacy env files live at `/etc/opentrusttoken/*.env`; new code reads `/etc/opentrustseal/*.env`. Symlinked `/etc/opentrustseal/` → `/etc/opentrusttoken/` families.
- **Gotcha encountered + fixed:** cron's `crawl_daily.sh` had been failing silently for 2 days because it referenced `/opt/opentrustseal/`. Resolved by symlinking `/opt/opentrustseal` → `/opt/opentrusttoken` on the API box.
- **Gotcha encountered + fixed:** nginx `proxy_read_timeout 30s` shorter than the full tier 1-6 ladder. Bumped to 120s.

**Tier 6 Bright Data activation complete:**

- `SCRAPER_ENABLED=true`, provider `brightdata`, zone `ots_web_unlocker`, REST API (Bearer UUID token, not legacy proxy-auth — fix committed as `ff65964`).
- 289 currently-stubborn domains preloaded into `tier6_gate` with `strike_count=3` so tier 6 fires on first re-crawl.
- Budget: $100 Allen + $100 Bright Data match = $207 credit. Projected burn ~$12-36/mo.
- 3-strike gate + circuit breaker + feature flag = three safety layers before any paid call.

**Residential fleet configured:** `RESIDENTIAL_URLS=http://100.125.118.64:8901,http://100.123.7.71:8901` (Mac Air + gaming PC). Round-robin with per-endpoint circuit breakers.

**Upptime status page refreshed:** repo moved to `OpenTrustSeal/status`, 8 endpoints monitored (health, full check, stats/dataset, stats fetch counters, DID doc, landing, dashboard, methodology), `bonedoc911` assigned to incident issues for email alerts. CNAME `status.opentrustseal.com` configured.

**Hetzner accelerator fleet added (2026-04-23):**

5 boxes to cut fleet ETA from 7.8 days to ~3.5 days.

| Box | Plan | Location | IP | Slice |
|---|---|---|---|---|
| ots-seed-19 | CX23 | eu-central (Nuremberg) | 46.225.108.52 | v-10 bottom 37.5K |
| ots-seed-20 | CX23 | eu-central | 91.98.117.193 | v-11 bottom 37.5K |
| ots-seed-21 | CX23 | eu-central | 204.168.243.115 | v-14 bottom 37.5K |
| ots-seed-22 | CX23 | eu-central | 204.168.247.42 | v-18 bottom 37.5K |
| ots-seed-23 | CPX11 | us-west (Hillsboro) | 5.78.152.69 | v-9 Sydney range 20001-37500 |

Cost: €4.90/mo × 4 EU + €4.35/mo × 1 US = ~€2.70 prorated for ~3 days.

**Fleet size now 23 boxes:** API (1) + crawler (1) + DO seeds 1-8 + Vultr seeds 9-18 + Hetzner seeds 19-23 + residential tier-4 fleet (Mac + PC).

**What's left before public launch:**

1. Seed crawl completion (~3-3.5 days from 2026-04-23)
2. Post-seed: merge 23 DBs via `merge_db.py`, v1.4 rescore, export CSV+JSON+SHA256
3. Publish dataset to Hugging Face + GitHub Release (draft ready at `dataset/PUBLICATION-DRAFT.md`)
4. Submit CrewAI PR to `crewAIInc/crewAI` with HF URL filled in
5. (Nice-to-have, not blocking) Transparency-log UI browser

---

## Session Summary (2026-04-17 through 2026-04-19)

Three-day push to ship the 100K + 1M seed dataset and harden agent-facing surfaces. Everything below landed in this window.

**Crawler cluster -- 18 seed droplets, per-host DB, no contention.** The prior burst droplet (165.227.26.56, 8GB/4vCPU, 18 processes on one box) was retired because per-process SQLite writes still contended on the same disk. New layout is one droplet per "seed", each with its own SQLite DB, so corruption is architecturally impossible.

- **ots-seed-1 through ots-seed-8 on DigitalOcean** ($12/2GB droplets, Ubuntu 24.04). DO capped the account at 10 droplets, which forced the Vultr pivot.
- **ots-seed-9 through ots-seed-18 on Vultr** (same specs, spread across Sydney and other global regions for fresh WHOIS rate-limit windows + geographic proximity to country-specific WHOIS servers).
- Seeds 1-6 run **Tranco top-100K** (~16K domains each).
- Seeds 7-18 run **Tranco 100K-1M** (12 droplets × ~75K domains each).
- All boxes checkpoint-resumable, all use the same SSH key (allen@opentrusttoken.com).
- SSH IPs (captured 2026-04-18): seed-1 138.68.30.188, seed-2 146.190.142.163, seed-3 142.93.248.84, seed-4 138.197.129.121, seed-5 142.93.34.243, seed-6 68.183.13.238, seed-7 137.184.1.1, seed-8 64.23.228.248, seed-9 149.28.169.34 (Sydney), seed-10 66.42.118.203, seed-11 144.202.17.121, seed-12 45.76.174.191, seed-13 216.128.176.173, seed-14 45.77.88.41, seed-15 104.238.158.94, seed-16 207.148.102.73, seed-17 144.202.93.207, seed-18 45.77.148.196.
- Prior 123K merged dataset preserved at `/tmp/seed-dbs/merged.db` on the old burst droplet BEFORE retirement; if it was destroyed with the burst droplet, the top-100K is being recrawled clean from seeds 1-6.

**Scoring v1.4 consensus tier:** `spec/SCORING-V1.4.md` shipped. Top-100 Tranco domains with 10+ years age get an elevated identity ceiling (75 vs 55), spreading top-site scores into the 80-90 range. Amazon moves 76 to 82. Awaiting batch rescore after 100K seed merge.

**Dataset export pipeline:** `server/export_dataset.py` produces CSV + JSON + SHA-256 manifest. `server/merge_db.py` merges burst DB into production with --dry-run mode. Dataset README at [dataset/README.md](opentrusttoken/dataset/README.md) now includes `confidence` (high/medium/low) and `cautionReason` (incomplete_evidence/weak_signals/new_domain/infrastructure) fields, so agents can distinguish "CAUTION because evidence is incomplete" from "CAUTION because signals are actually weak."

**Python SDK confidence surface:** `sdk/python/opentrustseal/models.py` exposes `confidence` and `caution_reason`. Both CrewAI integrations ([sdk/python/opentrustseal/integrations/crewai.py](opentrusttoken/sdk/python/opentrustseal/integrations/crewai.py) and the standalone PR-ready [sdk/crewai-pr/opentrustseal_tool.py](opentrusttoken/sdk/crewai-pr/opentrustseal_tool.py)) surface these in tool output with decision guidance keyed to confidence level. Low-confidence CAUTION tells the agent "evidence incomplete, not necessarily bad, low-dollar OK." High-confidence CAUTION with new_domain tells the agent to confirm with user. This is the difference between a tool that blocks false positives and one that just reports a number.

**Merchant outreach email templates:** [docs/MERCHANT-OUTREACH-EMAIL.md](opentrusttoken/docs/MERCHANT-OUTREACH-EMAIL.md). Four variants (A: weak_signals, B: incomplete_evidence, C: new_domain, D: infrastructure) plus a 7-day follow-up. Placeholder-templated, sends from alu@opentrustseal.com. Routing logic at bottom maps `cautionReason` to template.

**Codex critique fix bundle (shipped 2026-04-19, all 5 verified on disk):**

1. **Residential fleet loop bug** -- [server/app/fetch_escalation.py:381](opentrusttoken/server/app/fetch_escalation.py) now `continue`s after exception inside the for-loop instead of `return None` on first failure. Tier 4 actually tries every Mac endpoint now.
2. **confidence + cautionReason cryptographically bound** -- both fields added to the signable payload in [server/app/pipeline.py:389-398](opentrusttoken/server/app/pipeline.py) so the Ed25519 signature covers them. Agents can trust the explanation field the same way they trust the score.
3. **Confidence distinguishes gaps from weakness** -- [server/app/scoring.py:320-381](opentrusttoken/server/app/scoring.py) rewritten to count evidence vs gaps. Reputation/SSL=0 is evidence (blocklist hit, no SSL). Content=0 with `content_scorable=False` is a gap. Domain age score=0 with `domain_age_days<0` is a gap (WHOIS failed) vs domain age score=0 with `domain_age_days>=0` which is evidence (legitimately new). Identity=0 treated as gap.
4. **`_unscorable` persisted to raw_signals** -- [server/app/pipeline.py:275](opentrusttoken/server/app/pipeline.py) writes `"_unscorable": content_unscorable` into the content raw payload so a future rescore can preserve the incomplete_evidence vs weak_signals distinction. [server/rescore.py:197](opentrusttoken/server/rescore.py) reads it on the way back.
5. **Completion-list selector tightened** -- [server/scripts/generate_completion_list.py:76-96](opentrusttoken/server/scripts/generate_completion_list.py) now only requeues true evidence gaps: `content=0 AND CONTENT_UNSCORABLE flag`, `identity=0`, `age=0 with no registeredDate`, or `<3 signals populated`. Legitimately new or weak domains stay as-is.

**Upptime status page:** GitHub repo `bonedoc911/ott-status` monitors API health, landing page, dashboard every 5 minutes.

**Corporate entity:** OpenTrustSeal, Inc. California C-Corp filed 2026-04-17. Private repo at `bonedoc911/opentrustseal`.

**Dropped:** OpenClaw / Claude Code CLI on Mac Air as a backend scraper. Anthropic TOS gray zone, and the protocol probe (tier 1.5) + Wayback (tier 5) tiers made it redundant. Future stubborn-site work should use a commercial scraper API (ScraperAPI, ZenRows, Bright Data) not Claude Code.

**Next on resume (2026-04-19 handoff):**
1. Check seed cluster progress -- seeds 1-4 were near done at rollover
2. After seeds 1-6 finish: merge, run v1.4 rescore, export dataset, destroy burst droplet
3. Publish dataset to Hugging Face or GitHub Releases (CC-BY-4.0)
4. Submit CrewAI tool PR with dataset link as proof of coverage
5. Begin merchant outreach using templates above, starting with high-impact CAUTION domains

## What This Is

An independent trust attestation layer for AI agent commerce. Agents call our API before making a payment to check if a merchant site is trustworthy. We return a signed evidence bundle with a trust score, brand tier classification, crawlability status, and an actionable checklist.

The product has three faces:
- **For merchants:** "Check your site's trust score and see what to fix" (the landing page + dashboard)
- **For agents:** "One API call before paying" (the API)
- **For the ecosystem:** signed Ed25519 evidence bundles that can be verified without trusting us, enabling compositional use by payment protocols (AP2, x402, MPP) without putting us on their critical path

The real asset is the longitudinal database of trust profiles for every domain we check. Raw signal data stored separately from scores so we can re-score without re-crawling. Scoring is versioned (currently ots-v1.4-weights, consensus tier for Tranco top-100).

**Rebrand status (2026-04-17/18):** Code fully renamed from opentrusttoken/OTT to opentrustseal/OTS in the git repo. VPS infrastructure still runs under the old paths/names (deferred to post-seed cutover). Both domains (opentrusttoken.com and opentrustseal.com) serve the same API simultaneously.

**100K + 1M seed crawl (in progress):** 18-droplet cluster replaces the prior single burst droplet. Seeds 1-6 (DigitalOcean) cover Tranco top-100K (~16K each). Seeds 7-18 (DO 7-8, Vultr 9-18) cover Tranco 100K-1M (~75K each). Each droplet has its own SQLite DB -- zero contention, zero corruption risk. Checkpoint-resumable. After completion: per-droplet DBs pulled to a merge host, merged via merge_db.py, rescored with v1.4, dataset exported, droplets destroyed.

## What's Built and Deployed

### Production Infrastructure -- Two Droplets

**API box (ott-api-1):**
- DigitalOcean, SFO2, 206.189.65.177, Ubuntu 24.04, 1GB RAM
- SSH: root@206.189.65.177 using ~/.ssh/id_ed25519
- API: https://api.opentrustseal.com (FastAPI + uvicorn behind nginx, 2 workers)
- Landing page: https://opentrustseal.com (static HTML served by nginx)
- Internal docs: https://opentrustseal.com/marketing/ (knowledge base for team)
- SSL: Let's Encrypt, auto-renewing via certbot
- Service: systemd unit `opentrustseal.service`, auto-restarts
- Database: SQLite at /opt/opentrustseal/data/ots.db (1,112 domains, 2,888 raw signal records)
- Signing keys: Ed25519 at /opt/opentrustseal/keys/
- Tranco list: /opt/opentrustseal/data/tranco.csv (top 1M domains)
- Runtime dir: /opt/opentrustseal/logs (recreated by deploy.sh so systemd mount namespacing doesn't fail)

**Crawler box (ots-crawler-1):**
- DigitalOcean, SFO2, 167.99.172.189, Ubuntu 24.04, 1GB RAM + 2GB swap
- SSH: root@167.99.172.189 using ~/.ssh/id_ed25519
- Service: systemd unit `ots-crawler.service` running as `ott` user
- Binds: 10.120.0.3:8901 (private VPC only, not public)
- UFW: allows SSH from anywhere + 8901/tcp from 10.120.0.2 (API box private IP) only
- Runs Playwright + headless Chromium + playwright-stealth v2
- Pool of 2 warm contexts for non-proxied fetches
- Ephemeral contexts per request for proxied (tier 3) fetches
- memory caps: MemoryHigh=640M, MemoryMax=768M (hardens against Chromium runaway)

**MacBook Air crawler node (pennys-macbook-air):**
- Physical hardware at Allen's home behind UniFi Cloud Gateway Fiber on Spectrum residential (Charter AS20001, Northridge CA)
- SSH: pennyai@10.10.100.84 using ~/.ssh/id_ed25519
- macOS 26.2 on Apple Silicon, Python 3.9 venv at `~/ots-crawler/`
- Tailnet address: **100.125.118.64** (on Allen's tailnet alongside the API box at 100.123.37.126)
- Service: launchd agent `~/Library/LaunchAgents/com.opentrustseal.crawler.plist`, `KeepAlive` on crash, stdout/stderr to `~/ots-crawler/crawler.*.log`
- Wrapper script `~/ots-crawler/start.sh` sources `crawler.env` and execs `uvicorn fetch_service:app --host 100.125.118.64 --port 8901`
- Env at `~/ots-crawler/crawler.env` (chmod 600): shares CRAWLER_SHARED_SECRET with the DO crawler box so API box auth is identical
- **Browser: Chrome for Testing 138** (not the bundled headless-shell) via `CRAWLER_BROWSER_EXECUTABLE` hardcoded in start.sh because `crawler.env` can't parse paths with spaces cleanly
- playwright-stealth v2 applied to every context
- Reachable from API box only via Tailscale (no port forward on home UniFi; home network policy-route keeps crawler traffic off the office site-to-site VPN tunnel)

**VPC + Tailnet networks:**
- API box ↔ Crawler box (DO VPC private): 10.120.0.2 ↔ 10.120.0.3 via eth1, ~2.6ms RTT
- API box ↔ MacBook Air (Tailscale): 100.123.37.126 ↔ 100.125.118.64, ~30-125ms RTT depending on DERP vs direct route

### Fetch Escalation Ladder (content_check + fetch_escalation)

Six-tier fallback for fetching homepage content. Each tier escalates on transient failure only; real 4xx responses are treated as authoritative at every tier:

| tier | path | circuit breaker | use case |
|---|---|---|---|
| **1** | API box direct httpx with realistic Chrome/Safari headers + brotli | (no breaker) | ~70% of fetches succeed here |
| **1.5 probe** (shipped 2026-04-15) | Direct httpx to `/.well-known/security.txt` on the target origin. Most sites serve a static 404 error shell containing the footer template, which has the legal links OTT scores on. Bot protection typically does not apply to error pages. If the site actually has a real security.txt, that is itself a positive signal. | 4 errors / 60s → 2 min cooldown | Sites whose homepage is captcha-walled but whose error pages are not. Runs AFTER tier 1 transient failure and BEFORE tiers 2-5 because it is as cheap as tier 1. Tagged `probe-security-txt-<status>` in the raw_signals source. |
| **2** | Crawler box Playwright (real headless Chromium, stealth patched) | 3 errors / 60s → 5 min cooldown | SPAs, JS challenges, TLS fingerprint checks |
| **3** | Crawler box Playwright through Decodo rotating residential proxy | 3 errors / 60s → 5 min cooldown | sites blocking DO AS (walmart, target, bestbuy) |
| **4** | MacBook Air (pennys-air) via Tailscale, **Chrome for Testing 138 via `executable_path`** on Spectrum residential | 2 errors / 30s → 2 min cooldown | **Cloudflare Enterprise Bot Management (chewy, crate, macys, petsmart now crawl=ok)** |
| **5** (shipped 2026-04-15) | Internet Archive Wayback Machine CDX + `id_` mode snapshot retrieval | 4 errors / 60s → 3 min cooldown | Sites that block live automated fetching but archive.org has a recent 200-status snapshot. Wayback does NOT preserve response headers, so tier 5 content has security_header_count=0 as a known gap. Tagged `wayback-<timestamp>` in raw_signals source. |

**Phase 4 breakthrough (2026-04-14):** Playwright's `channel="chrome"` hangs on macOS 26.2 + Chrome 147 due to a pipe-transport + CDP handshake regression. Workaround: `chromium.launch(headless=True, executable_path="...Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")` uses the bundled Chrome for Testing 138 binary (path under `~/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/`). Chrome for Testing 138 is a full real-Chrome build with matching TLS fingerprint, HTTP/2, and V8, and Playwright 1.58 was built to drive exactly this version — no CDP version skew. With Spectrum residential egress + stealth patches + playwright-stealth v2 contexts, **chewy, crateandbarrel, macys, and petsmart all now return `crawl=ok` with real content**. Petco and kohls still 403 and fall through to Phase 1 anchor scoring.

**Phase 5 breakthrough (2026-04-15):** Tier 5 Wayback Machine shipped. Tier 5 rescues kohls (Wayback has current snapshots) and most other CF-protected retailers whose archive crawl is not blocked. Tagged with snapshot timestamp for independent auditability.

**Phase 6 breakthrough (2026-04-15):** Tier 1.5 protocol probe shipped. The discovery: requesting `/.well-known/security.txt` on a captcha-walled site returns a clean static 404 shell containing the full site footer (privacy/terms/contact links). Bot protection applies only to the homepage and dynamic category pages, not to error handling. Verified on costco (152KB shell), nordstrom (302KB shell), and petco via interactive Chrome on residential. The probe works architecturally in the escalation path for any domain whose tier 1 homepage fetch fails with a transient error. Strictest CF Enterprise configs (petco, reddit, macys) also block the probe path from datacenter IPs and require either residential-routed probe or the sparse-content escalation follow-up below.

**Target: zero permanent ANCHOR_ONLY domains.** The brand anchor is secondary evidence layered on top of content signals, not a substitute for them. Every domain whose content fetch fails should be rescued by one of tiers 1.5 through 5 OR flagged as a bug in the ladder. Current count: 0 anchor-only rows in scored_results as of 2026-04-15. Follow-up work to keep it at zero is tracked as Task #23 (residential probe) and Task #24 (escalate on weak content).

Counters exposed at `/stats.fetch` per tier. Shared-secret header between API and crawler. Graceful degradation: if any tier fails, falls through to next tier or finally returns `crawlability: blocked` + `CONTENT_UNSCORABLE` flag, and Phase 1 re-weighting carries the non-content signals.

### Server Code Structure (server/app/)

```
main.py                 -- FastAPI app, DID document, health, /stats with fetch tier counters + daily_crawl heartbeat
heartbeat.py            -- Reads the last-successful daily-crawl timestamp so /stats.daily_crawl exposes pipeline freshness for external monitors
pipeline.py             -- Orchestrates collectors -> scoring -> signing -> checklist + in-flight dedupe
scoring.py              -- ots-v1.3-weights, PROCEED/CAUTION/DENY, well-known brand anchor
signing.py              -- Ed25519 key management, sign/verify
database.py             -- SQLite with raw_signals, scored_results, score_history, registrations
checklist.py            -- Generates actionable fix items from signal data
ratelimit.py            -- 60 req/min per IP
verification.py         -- Domain ownership verification + cross-referencing (WHOIS, SSL, etc)
fetch_escalation.py     -- Tiered fetch ladder (tier 2 crawler, tier 3 crawler+Decodo) + circuit breakers
collectors/
  domain_age.py         -- WHOIS registration date, age bands (ownership-change penalty disabled until historical WHOIS)
  ssl_check.py          -- TLS version, issuer, HSTS, OV/EV cert org, www-fallback
  dns_check.py          -- SPF, DMARC, DNSSEC, CAA
  content_check.py      -- httpx + tier 2/3 escalation, www-fallback, brotli-aware, Cloudflare detection
  reputation_check.py   -- Tranco log curve + Google Safe Browsing + Spamhaus + SURBL + URLhaus
  identity_check.py     -- WHOIS disclosure, SSL cert org, Tranco identity (expanded buckets), institutional TLD, schema.org
  tranco.py             -- Tranco top-1M list loader with logarithmic scoring curve
  public_company.py     -- SEC EDGAR lookup for publicly traded company detection
  jurisdiction.py       -- Country / legal framework detection from ccTLD + WHOIS
models/
  signals.py            -- Pydantic models for all signal types
  token.py              -- CheckResponse with crawlability, brandTier, checklist
  registration.py       -- Registration request/response + verification_score map
routes/
  check.py              -- GET /v1/check/{domain}, POST /v1/check/request, GET dashboard
  token.py              -- GET /v1/token/{domain}/ots.json
  register.py           -- Registration flow
```

### Crawler Code Structure (crawler/)

```
fetch_service.py        -- FastAPI app with /fetch endpoint, pool of warm Chromium contexts,
                           shared secret auth, proxy parameter for tier 3, stealth applied to all contexts
ots-crawler.service     -- systemd unit with memory caps and process hardening
```

### API Endpoints
- GET /v1/check/{domain} -- check a domain (cached, ?refresh=true to force)
- POST /v1/check/request -- request a fresh check
- GET /v1/check/{domain}/dashboard -- current check + history + registration (used by dashboard UI)
- GET /v1/token/{domain}/ots.json -- serve token for a domain
- GET /v1/register -- registration flow endpoints
- GET /.well-known/did.json -- DID document with public signing key
- GET /stats -- registry statistics + fetch tier counters + daily_crawl heartbeat (pipeline freshness)
- GET /health -- health check
- GET /docs -- Swagger UI

### Scoring Model (ots-v1.3-weights)

**Weights (when content is scorable):**
reputation=30%, identity=25%, content=17%, domain_age=10%, ssl=10%, dns=8%

**When content is unscorable** (fetch failed + no usable history), content's 17% is dropped from the aggregate and the remaining five signals are renormalized to sum to 100%. Response carries `crawlability: "blocked"` and `CONTENT_UNSCORABLE` flag.

**Well-known brand anchor (new in v1.3):**
If all of the following are true:
- Tranco rank <= 50,000
- Domain age >= 5 years (1825 days)
- SSL valid
- Reputation clean (no malware/phishing/spam_listed)

Then:
- Identity score floor raised to 50 (before weighted sum)
- Final trust_score floor raised to 75 (after weighted sum)
- `brandTier` set to `"well_known"` in response
- `WELL_KNOWN_BRAND` flag added

This anchor addresses investor pushback that top retailers (crateandbarrel, petco, kohls, macys) were scoring below PROCEED. The rationale is compositional: long-term Tranco top-50K membership is unfakeable (the list comes from billions of real-user requests), and when combined with domain age + clean reputation + valid SSL, the probability of a scam site satisfying all four conditions is effectively zero. Any negative safety signal (malware, phishing, spam) revokes the anchor immediately.

**Tranco identity buckets (expanded in v1.3):**
top 100 +25, top 1K +20, top 5K +15, top 10K +12, top 50K +8, top 100K +5, top 500K +3

**Institutional TLD bonuses:** .gov/.mil +20, .edu/.int +15 to identity

**Registration bonus:** earned per verified field (max +30), not a flat bump
Fields: domain proof +3, biz name match +5, email match +3, EIN/VAT +5,
phone +3, address +3, social +3, registry match +5
Public data (biz name, country, type) shown in API. Private data (EIN, email,
phone, address) used for verification only, never exposed.

**Identity ceilings:** automated=55, enhanced=65, kyc_verified=80, enterprise=100

**KYC-adjusted domain age:** KYC-verified new domains get age floor of 50

**www-fallback:** ssl_check and content_check both attempt `www.{domain}` if apex refuses TCP. Rescues legacy sites like petsmart.com that run no webserver on the apex.

**Thresholds:** 75+ = PROCEED, 40-74 = CAUTION, 0-39 = DENY

**Max possible score without KYC:** ~88 (top Tranco site with valid OV cert, all content signals, registration)

### Response Fields (CheckResponse)

- `domain`, `checkedAt`, `expiresAt`
- `signals`: domainAge, ssl, dns, content, reputation, identity (each with score + evidence)
- `flags`: [CONTENT_UNSCORABLE, WELL_KNOWN_BRAND, NO_SSL, NEW_DOMAIN, MALWARE_DETECTED, PHISHING_DETECTED, SPAM_LISTED, ...]
- `trustScore`: 0-100
- `scoringModel`: "ots-v1.3-weights"
- `siteCategory`: consumer / api_service / infrastructure
- `jurisdiction`: country, legal framework, cross-border risk, dispute resolution
- `recommendation`: PROCEED / CAUTION / DENY
- `reasoning`: human-readable summary
- `crawlability`: "ok" or "blocked"
- `brandTier`: "well_known" or "scored"
- `checklist`: actionable fix items
- `checklistSummary`: total / passing / failing / improvable counts
- `signature`: Ed25519 sig over the canonical signable payload
- `issuer`: "did:web:opentrustseal.com"

### Deployment
From local Mac:
```bash
cd opentrustseal/deploy
bash deploy.sh root@206.189.65.177
```
Or manually:
```bash
rsync -avz --exclude='venv/' --exclude='data/' --exclude='keys/' --exclude='logs/' --exclude='__pycache__/' server/ root@206.189.65.177:/opt/opentrustseal/
ssh root@206.189.65.177 "mkdir -p /opt/opentrustseal/logs && chown -R ott:ott /opt/opentrustseal && systemctl restart opentrustseal"
```

Crawler box deploys are manual right now (rsync crawler/fetch_service.py to /opt/ots-crawler/, chown ott:ott, systemctl restart ots-crawler).

### Environment Files (not in repo)
- `/etc/opentrustseal/crawler.env` on API box + DO crawler box: `CRAWLER_URL=http://10.120.0.3:8901`, `CRAWLER_SHARED_SECRET=<token>`, etc
- `/etc/opentrustseal/decodo.env` on API box: `DECODO_HOST=gate.decodo.com`, `DECODO_PORT=7000`, `DECODO_USER=...`, `DECODO_PASS=...`
- `/etc/opentrustseal/macbook.env` on API box: `MACBOOK_URL=http://100.125.118.64:8901` (shares CRAWLER_SHARED_SECRET, defaults to it if no MACBOOK_SHARED_SECRET override)
- `~/ots-crawler/crawler.env` on Mac Air (user-owned, chmod 600): `CRAWLER_SHARED_SECRET=<matches VPS>`, `CRAWLER_POOL_SIZE=2`, `CRAWLER_DEFAULT_TIMEOUT_MS=25000`, `CRAWLER_BROWSER_CHANNEL=` (empty; Chrome for Testing is set via executable_path in start.sh instead)
- All VPS env files are `chmod 640` owned `root:ott` so the `ott` service user can read but not write

### Credentials
Local only at `~/.config/opentrustseal/credentials` (never in iCloud).
Contains: cPanel creds for scosi.com and dathorn, Google Safe Browsing key, Decodo proxy credentials.
cPanel upload script: deploy/cpanel-upload.py (supports `scosi` and `dathorn` profiles, calls UAPI at cpanel83.gzo.com:2083).

## Key Decisions Made

1. **Evidence-first, score second.** API returns raw signals before the computed score. Score is a convenience summary, not an authoritative verdict.
2. **Scoring model is versioned** (ots-v1.3-weights). Can be changed and re-scored from stored raw data.
3. **Raw signals stored separately from scored results.** Algorithm changes don't require re-crawling.
4. **No site participation required.** We score any domain from public data. Sites don't need to register, host files, or add scripts.
5. **Registration is a data collection event.** Not just domain proof. Collects business name, EIN/VAT, address, phone, social. Each verified field earns points independently. Public/private data separation.
6. **KYC tiers raise identity ceiling.** Auto=55, enhanced=65, kyc=80, enterprise=100. KYC-verified new domains get domain age floor of 50.
7. **The database is the real asset.** Longitudinal trust profiles compound in value. The API is the interface; the data is the moat. Every check produces a raw_signals row plus a score_history snapshot.
8. **One global score with jurisdiction context.** No regional scoring systems. One number, but the API returns country, legal framework, cross-border risk, and dispute resolution context. Agents make their own policy decisions.
9. **Category-aware scoring.** Infrastructure/API sites scored differently from consumer merchants. Security headers and API docs matter more than privacy policies for infra.
10. **GDPR-aware, internationally fair.** Don't penalize EU WHOIS redaction. Detect content in 12+ languages. ccTLD bonuses for registries that verify identity.
11. **Anti-gaming.** Domain age, Tranco rank, and OV certs are unfakeable. Registration monitoring catches bought aged domains (future, when historical WHOIS is integrated). KYC requires ongoing monitoring.
12. **Independent trust attestation, not payment gateway.** We never handle payments. We issue signed evidence bundles that payment protocols (AP2, MPP, x402) can cite without putting us on their critical path. Neutrality is a feature -- payment rails have structural conflicts of interest that prevent them from being their own trust authority.
13. **Compositional brand anchor (v1.3).** Long-term Tranco top-50K + aged + clean reputation + valid SSL functions as a trust floor, analogous to how credit bureaus weight account longevity. Revoked instantly on any safety signal.
14. **Fetch escalation ladder.** Direct httpx -> headless Chromium -> Chromium through residential proxy -> real Chrome on home ISP (planned). Each tier has independent circuit breakers so a failure in one doesn't poison the others.

## What's Next

1. **Registry scaling to 100K (in progress 2026-04-17)** -- Seed crawl running on burst droplet (165.227.26.56, 4GB, $24/mo hourly-billed). `crawl_seed.py --fast --workers 12 --resume` processing Tranco top-100K. As of 2026-04-17 16:00 UTC: ~4,600 domains checkpointed, ~2,578 in burst DB, 40% success rate (expected, Tranco includes infrastructure/CDN), ETA ~6 days. After completion: run `merge_db.py` to merge burst DB into production, run `rescore.py` for v1.3, destroy burst droplet. Daily re-crawl batch size should increase from 200 to 1,500 once registry exceeds 50K.
2. **Registration flow** -- LIVE. Structured data collection, per-field scoring, domain verification (DNS/HTTP), cross-referencing (WHOIS, SSL cert, phone, social). Registration page at /register.html. Cache invalidated on successful verify so new score picks up registration bonus immediately.
3. **Python SDK** -- BUILT at sdk/python/. opentrustseal package with sync/async client, LangChain tool, CrewAI tool. Not published to PyPI yet.
4. **TypeScript SDK** -- BUILT at sdk/typescript/. @opentrustseal/sdk with full types, native fetch. Not published to npm yet.
5. **Site owner dashboard** -- LIVE at /dashboard.html. Score hero, signal breakdown bars, jurisdiction profile, grouped checklist, registration status with per-field verification, score history chart with date labels and PROCEED threshold line. Dashboard API endpoint at /v1/check/{domain}/dashboard returns current check + history + registration data.
6. **Phase 1 re-weighting (shipped 2026-04-13)** -- Content-unscorable sites drop content weight and renormalize remaining signals. Cap at 70 when no identity anchor, otherwise let the score flow.
7. **Phase 2 crawler (shipped 2026-04-13)** -- Second droplet running Playwright behind FastAPI fetch service on private VPC. Used as tier 2 in the escalation ladder.
8. **Phase 3 Decodo residential proxy (shipped 2026-04-13)** -- Rotating residential IPs via gate.decodo.com:7000. Tier 3 wires Playwright through the proxy. Bypasses DO AS filters and Akamai/CF-standard blocks. Does NOT beat Cloudflare Enterprise Bot Management.
9. **Phase 4 MacBook-at-home crawler (shipped 2026-04-14)** -- Chrome for Testing 138 via Playwright executable_path on Allen's Spectrum residential IP via Tailscale. Breakthrough: tier 4 via `executable_path` instead of `channel="chrome"` bypasses the macOS 26.2 + Chrome 147 pipe-transport regression. **Chewy, crateandbarrel, macys all moved from `crawl=blocked` to `crawl=ok`**. Petco and kohls still blocked by stricter CF Enterprise configs; they remain anchored at PROCEED via Phase 1 brand anchor.
10. **Well-known brand anchor (shipped 2026-04-13)** -- scoring v1.3. Compositional floor for aged top-Tranco brands with clean reputation. Every major retailer now PROCEED by default without hand-curated whitelists.
11. **OpenClaw / Claude Code CLI on Mac Air (DROPPED 2026-04-15)** -- retired. Anthropic TOS gray zone for unattended Claude Code as a backend data extraction service, plus the protocol-probe breakthrough made it unnecessary. Replaced by Tier 1.5 (protocol probe) and Tier 5 (Wayback) as the systematic answers, and by Task #23/24/25 as the remaining gap-fills. Interactive Claude in Chrome remains valid for ad-hoc debugging of individual weird-scoring sites (well inside TOS).
12. **LangChain/CrewAI integration** -- tool wrappers for agent frameworks (partially built in SDKs, not published)
13. **KYC infrastructure** -- funded by investor, launches after registration is live
14. **Infrastructure domain scoring** -- current category detection is basic (domain name patterns + 404 fallback). Needs deeper work: parent company linkage (cloudfront.net -> Amazon), x402/MPP endpoint detection, API-specific checklist items.
15. **Calibration dataset** -- every signed bundle has a check_id. Plan: collect outcome feedback from registered merchants and API consumers (via /v1/feedback endpoint, not built yet) to retroactively backtest scores against real fraud/chargeback outcomes. Over 6-12 months this becomes the real moat.
16. **DMARC aggregate report parser** -- daily reports arriving at alu@opentrustseal.com from 2026-04-13 forward (DMARC p=quarantine enforcement enabled). Simple parser + dashboard next time we touch email infra.
17. **Task #20 content body cap (SHIPPED 2026-04-17)** -- Raised from 300KB to 2MB in content_check.py. Costco content score jumped 10 to 55. Crateandbarrel and other large-homepage retailers now fully parsed.
18. **Task #24 weak-content escalation (SHIPPED 2026-04-17)** -- content_check.py now detects SPA shells (<10KB body with no footer keywords) and escalates to probe/Playwright tiers instead of returning the sparse response as "success." Handles the costco/nordstrom/lowes pattern.
19. **Reputation collectors fixed (2026-04-16/17)** -- Spamhaus DQS key (`pfg5oc35...`) wired via `/etc/opentrustseal/spamhaus.env`, queries routed to `dbl.dq.spamhaus.net`. All 4 sub-sources now live: Spamhaus DBL, SURBL, URLhaus, Google Safe Browsing. python-whois stderr noise suppressed via `app/whois_util.py` shared wrapper across all 4 call sites.
20. **v1.3 batch rescore (SHIPPED 2026-04-15)** -- All 1,117 domains rescored from raw_signals using v1.3 anchor logic. Amazon 67 to 75, Google 73 to 82. `rescore.py` now has `--dry-run` flag. rescore.py also updated with v1.3 identity buckets, `_tranco_rank` attribute stashing, and `well_known_brand` kwarg passthrough.
21. **Ghost cron fixed (SHIPPED 2026-04-15)** -- `crawl_daily.sh` restored in `server/scripts/`, runs at 03:00 UTC via ott crontab. Heartbeat JSON exposed at `/stats.daily_crawl` with ok/stale/dead status. Old ghost entry cleaned up by `install-cron.sh`.
22. **Dataset export script (BUILT 2026-04-17)** -- `server/export_dataset.py` exports CSV + JSON + SHA-256 manifest. Tested on 1,228 domains. Ready to run on the full 100K post-merge.
23. **DB merge script (BUILT 2026-04-17)** -- `server/merge_db.py` merges a source SQLite (burst droplet) into production DB. Handles domains, raw_signals, scored_results, score_history tables. Has --dry-run mode.
24. **v1.4 scoring spec (WRITTEN 2026-04-17)** -- `spec/SCORING-V1.4.md`. Top-100 Tranco consensus identity tier (ceiling 75 instead of 55). Amazon would move from 76 to 82. Spec includes impact analysis, implementation plan, and rollback strategy. Awaiting implementation after the 100K seed + v1.3 rescore.
25. **Upptime status page (DEPLOYED 2026-04-17)** -- GitHub repo at `bonedoc911/ott-status`. Monitors API health, landing page, dashboard every 5 minutes. First uptime check green. Status page at `bonedoc911.github.io/ott-status` once GitHub Pages bootstrap completes.
26. **Open dataset strategy (PLANNED)** -- After 100K seed completes, publish the trust dataset as a downloadable CC-BY-4.0 CSV/JSON on Hugging Face or GitHub Releases. The dataset is the growth engine, the API is the monetization layer, the signed evidence bundle is the premium differentiator.
27. **CrewAI tool PR (PLANNED, post-dataset)** -- Contributed tool to crewAI-tools repo. Submit after the dataset proves coverage (100K+ domains). The PR description links to the published dataset as proof of coverage.
28. **Deeper stealth for petco/kohls tier -- CLOSED as won't-fix (2026-04-15).** Task #21. Brand anchor carries scores, ANCHOR_ONLY flag surfaces state, systematic tiers (probe + Wayback) cover most cases. CF-blanket-blocked sites need residential probe (Task #23/25).

## Known Pending Items

- **Phase 5 OpenClaw -- DROPPED (2026-04-15)** -- the "run a second Claude Code CLI instance on the Mac Air and send it prompts over ssh to drive cu-bridge Chrome" plan has been retired. Two reasons: (1) Anthropic's TOS frowns on unattended Claude Code CLI as a backend data-extraction service and we don't want production data pipelines resting on policy gray areas; (2) the effort-to-value math is bad since the entire remaining failure set is 2-3 domains (petco, kohls, occasional stragglers) that already score PROCEED via the v1.3 brand anchor. Any future completeness work should reach for a commercial scraper API (ScraperAPI, ZenRows, Bright Data Web Unlocker -- ~$30-50/mo, no TOS exposure, works on every stubborn site not just the two we have now) rather than OpenClaw. Claude Code on the Mac Air is still useful for interactive human-in-the-loop debugging of weird-scoring sites; that use is fully inside the TOS envelope.
- **Task #22 RETIRED as an operational task (2026-04-15).** Manual quarterly captures were the investor-facing backstop when the plan was "live crawl or brand anchor, nothing in between." After tier 1.5 (protocol probe) and tier 5 (Wayback) shipped, and after the hard critique that treating incomplete information as acceptable was epistemic cowardice, the target is zero permanent anchor-only domains via systematic tiers, not manual captures. Manual capture via Claude in Chrome remains a valid ad-hoc debugging tool for individual weird-scoring domains but is no longer a scheduled operational process. The `_source: "manual_chrome_*"` tag in raw_signals is preserved for auditability when manual captures do happen.

- **Task #23 (NEW): Residential probe variant.** Tier 1.5 probe from the API box works for most sites but fails on petco, kohls, macys, reddit because CF Enterprise blanket-blocks the VPS IP even on `/.well-known/security.txt`. Fix: add a residential-routed probe that proxies the same request through either the Mac Air (tier 4 crawler, free) or Decodo residential proxy (tier 3 infrastructure, paid). Expected 1-2 hours of work: a new function `fetch_via_protocol_probe_residential` that calls the Mac crawler's /fetch endpoint with the probe URL instead of the homepage URL. Runs AFTER the direct probe fails and BEFORE tier 2 Playwright escalation. When shipped, petco/kohls/macys/reddit should all produce real content signals from the 404 shell via residential egress.

- **Task #24 (NEW): Escalate on weak tier 1 content.** Current escalation triggers only on transient tier 1 errors (timeouts, TCP resets, 4xx/5xx). Sites like costco, nordstrom, and most modern SPA storefronts return 200 with a sparse client-rendered shell (body under 5KB, no footer yet, content rendered by JS post-load). Tier 1 sees the 200 and returns it as "success", so tier 2 Playwright never runs, the probe never runs, and the site scores low on content despite being trivially fetchable via the Playwright tier. Fix: after tier 1 returns 200, compute a quick content-signal estimate on the body. If the estimate is below a threshold (e.g. no footer-shaped links, body < 5KB), treat it as weak and escalate through tiers 1.5 → 2 → 3 → 4 → 5 the same way a transient error would. The correct escalation triggers on "missing value" as much as "transport error". Expected 2-3 hours of work in content_check._fetch_homepage.

- **Task #25 (NEW): Residential variant of tier 1.5 probe integrated into the /fetch service on the Mac Air.** Simpler alternative to Task #23 if we decide the Mac Air should own probing: teach `crawler/fetch_service.py` to accept a `probe_mode=true` parameter that short-circuits the Playwright render and just returns the raw response from a direct httpx fetch (with the Mac's residential egress). That keeps the probe cheap (no Playwright overhead) while using the residential IP. One afternoon of Mac crawler work plus a matching tier in fetch_escalation. Decide between Task #23 (proxy existing API-box probe through Mac) and Task #25 (serve the probe from the Mac directly) based on which feels cleaner operationally. My pick: Task #25 because the Mac already owns "residential fetches as a service", and making the Mac the single residential egress point is tidier than proxying.
- **Dathorn domain SPF/DMARC lockdown** -- only scosi.com and opentrustseal.com done. Allen has other domains at dathorn that need the same treatment. Blocked on: list of domains + per-domain cPanel creds OR reseller WHM API token.
- **Nordstrom-style content detection weakness** -- hamburger-menu sites lazy-load their privacy/terms links, regex doesn't catch them. Playwright wait-for-selector or scroll-then-parse would help.
- **Task #26 (NEW): JS-rendered footer escalation.** Lowes.com and similar React-heavy sites return 200 with a large body (500KB+) but zero "privacy" keyword in the server-rendered HTML -- the footer is injected by client-side JS. The current weak-content check (Task #24) doesn't trigger because the body is >10KB. Fix: if the parser finds zero legal links AND the body is >50KB (clearly a real page, not a stub), trigger a second-pass escalation to tier 2 Playwright. Expected 2-3 hours. Lower priority than Tasks #23/25 because the daily re-crawler with Playwright enabled handles this naturally.
- **Task #27 (NEW): GitHub private repo for OTT source code.** Created 2026-04-17 at `bonedoc911/opentrustseal` (private). Load server code, crawler code, SDKs, specs, docs. Will open-source when ready. Enables version control, PR workflow, and CI/CD pipeline.

**Already done (not pending):**
- `crawl_daily.sh` ghost cron on VPS -- fixed 2026-04-15, heartbeat at `/stats.daily_crawl`
- Newer-TLD WHOIS fallback -- RDAP via IANA bootstrap shipped 2026-04-13
- Task #20 content body cap -- SHIPPED 2026-04-17 (300KB to 2MB)
- Task #21 deeper stealth -- CLOSED as won't-fix 2026-04-15
- Task #22 manual quarterly captures -- RETIRED 2026-04-15
- Task #24 weak-content escalation -- SHIPPED 2026-04-17
- Reputation collectors (Spamhaus DQS + whois noise) -- FIXED 2026-04-16/17
- v1.3 batch rescore (1,117 domains) -- SHIPPED 2026-04-15
- rescore.py --dry-run flag -- SHIPPED 2026-04-15
- Dataset export script -- BUILT 2026-04-17
- DB merge script -- BUILT 2026-04-17
- Upptime status page -- DEPLOYED 2026-04-17

## Network / Mail Infrastructure

- opentrustseal.com -- A record -> 206.189.65.177 (VPS)
- www.opentrustseal.com -- A record -> 206.189.65.177
- api.opentrustseal.com -- A record -> 206.189.65.177
- mail.opentrustseal.com -- A record -> 96.31.72.33 (dathorn cpanel83.gzo.com)
- MX opentrustseal.com -- 0 mail.opentrustseal.com. (points to dathorn mail server)
- SPF: `v=spf1 +mx +ip4:96.31.72.73 include:spf.gzo.com ~all` (tightened 2026-04-13; dropped `+a`)
- DMARC: `v=DMARC1; p=quarantine; rua=mailto:alu@opentrustseal.com; sp=quarantine; pct=100; fo=1` (enforcement enabled 2026-04-13)
- DKIM: default._domainkey (dathorn auto key, in zone)
- DNS managed at dathorn cPanel (nameservers ns1.gzo.com / ns2.gzo.com, UAPI via https://cpanel83.gzo.com:2083)

## Scoring Model Docs
- spec/PROTOCOL.md -- v0.2.0 protocol specification
- docs/ARCHITECTURE.md -- v0.2.0 system architecture with MVP section
- docs/SCORING-AND-KYC.md -- scoring deep dive and KYC monetization model (partially outdated, revenue projections assume KYC in v1)

## Corporate Entity

**OpenTrustSeal, Inc.** -- California C-Corp, filed 2026-04-17. 18575 Gale Ave Ste 278, City of Industry, CA 91748.
- Domain: opentrustseal.com (Cloudflare DNS, email routing to alu@scosi.com)
- API: api.opentrustseal.com (SSL via certbot, same VPS as opentrustseal.com)
- GitHub: github.com/OpenTrustSeal (org, private repo + public status page)
- Email: alu@opentrustseal.com
- Rebrand in progress: code rename from opentrustseal/OTT to opentrustseal/OTS scheduled after 100K seed completes. Both domains serve the same API simultaneously.

## Investor Positioning (updated 2026-04-13)

The winning framing from the recent outside review: **"independent trust attestation layer for agentic commerce"** rather than "trust score API." The score is a commodity-in-waiting; the accumulated verification graph, longitudinal history, signed evidence bundles, and compositional brand anchor are the moats. The product sells to two sides at once: agent developers get a risk-control API before payment, merchants get a way to increase agent transaction conversion via registration + KYC tiers. The scoring is good enough for the pitch; the dataset and distribution are what's missing next.

Priority GTM: one reference integration with a widely-used agent SDK (LangChain, CrewAI, AutoGPT, OpenAI Swarm, Anthropic tool-use examples) that calls OTT before payments. That is the cold-start solution. The SDKs are 40% there; the other 60% is BD work with framework maintainers.
