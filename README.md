# OpenTrustSeal

Independent trust attestation for AI agent commerce.

AI agents are starting to make autonomous payments. OpenTrustSeal answers the pre-transaction question: **should this agent pay this merchant?** One API call returns a cryptographically signed evidence bundle with a trust score, six-category signal breakdown, and a PROCEED / CAUTION / DENY recommendation.

**Website:** [opentrustseal.com](https://opentrustseal.com)
**API Docs:** [api.opentrustseal.com/docs](https://api.opentrustseal.com/docs)
**Status:** [opentrustseal.github.io/status](https://opentrustseal.github.io/status/)

## Quick start

Check any domain:

```bash
curl https://api.opentrustseal.com/v1/check/stripe.com
```

Python SDK:

```python
from opentrustseal import check

result = check("stripe.com")
print(result.trust_score)     # 83
print(result.recommendation)  # "PROCEED"
print(result.is_safe)         # True
```

## What you get back

Every response includes:

- **Trust score** (0-100) computed from six signal categories
- **Recommendation:** PROCEED (75+), CAUTION (40-74), DENY (0-39)
- **Six signal scores:** domain age, SSL/TLS, DNS security, content analysis, reputation, identity
- **Ed25519 signature** proving the result was issued by OpenTrustSeal
- **Actionable checklist** showing what the site can improve
- **Jurisdiction context:** country, legal framework, cross-border risk
- **Brand tier:** "well_known" for established top-Tranco brands, "scored" for others

## Architecture

```
Client (agent/SDK)
    |
    v
API Server (FastAPI + uvicorn, api.opentrustseal.com)
    |
    +-- Collectors (domain_age, ssl, dns, content, reputation, identity)
    |       |
    |       +-- Fetch Escalation Ladder:
    |           Tier 1: direct httpx
    |           Tier 1.5: protocol probe (/.well-known/security.txt 404 shell)
    |           Tier 2: Playwright headless Chrome (crawler box)
    |           Tier 3: Playwright + Decodo residential proxy
    |           Tier 4: Mac Air residential Chrome via Tailscale
    |           Tier 5: Wayback Machine (Internet Archive)
    |
    +-- Scoring (ots-v1.4-weights, brand anchor, consensus tier)
    |
    +-- Signing (Ed25519, DID document at /.well-known/did.json)
    |
    +-- Transparency Log (per-domain hash chain, /v1/log/{domain})
    |
    +-- Database (SQLite: raw_signals, scored_results, transparency_log)
```

## Project structure

```
server/
  app/
    main.py              API server (FastAPI)
    pipeline.py          Check orchestration
    scoring.py           Scoring model (v1.4, consensus tier)
    signing.py           Ed25519 key management
    transparency.py      Transparency log with hash chain
    database.py          SQLite persistence
    heartbeat.py         Daily crawl pipeline monitoring
    whois_util.py        Safe WHOIS wrapper (suppresses stderr noise)
    fetch_escalation.py  6-tier fetch ladder with circuit breakers
    collectors/          Signal collectors (domain_age, ssl, dns, content, reputation, identity)
    models/              Pydantic models (signals, token, registration)
    routes/              API route handlers (check, token, register)
  scripts/
    crawl_daily.py       Daily re-crawl (stalest N domains)
    crawl_daily.sh       Cron wrapper
  crawl_seed.py          Parallel seeder for registry expansion
  rescore.py             Batch re-score from stored raw signals
  export_dataset.py      CSV + JSON dataset export
  merge_db.py            Merge databases (for burst-droplet seed)

sdk/
  python/                Python SDK (opentrustseal on PyPI)
  typescript/            TypeScript SDK (@opentrustseal/sdk on npm)

crawler/
  fetch_service.py       Playwright fetch service (runs on crawler boxes)

deploy/
  deploy.sh              rsync + restart deployment
  setup-vps.sh           VPS provisioning

spec/
  PROTOCOL.md            Protocol specification v0.2
  SCORING-V1.4.md        Scoring model v1.4 spec (consensus tier)

docs/
  ARCHITECTURE.md        System architecture
  SCORING-AND-KYC.md     Scoring deep dive + KYC model
  THREAT-MODEL.md        Threat model (8 categories)
  KEY-ROTATION.md        Key rotation schedule
  TRANSPARENCY-LOG.md    Transparency log design
```

## Scoring model (v1.4)

| Signal | Weight | Source |
|---|---|---|
| Reputation | 30% | Spamhaus DBL, SURBL, URLhaus, Google Safe Browsing, Tranco |
| Identity | 25% | WHOIS, SSL cert org, Tranco rank, public company, schema.org |
| Content | 17% | Privacy policy, terms, contact info, security headers, robots.txt |
| Domain Age | 10% | WHOIS registration date |
| SSL/TLS | 10% | Certificate validity, TLS version, HSTS |
| DNS | 8% | SPF, DMARC, DNSSEC, CAA |

**Thresholds:** 75+ = PROCEED, 40-74 = CAUTION, 0-39 = DENY

**Brand anchor:** Tranco top-50K + 5 years + clean rep + valid SSL = floor at PROCEED

**Consensus tier (v1.4):** Tranco top-100 + 10 years = identity ceiling raised from 55 to 75

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /v1/check/{domain}` | Check a domain (cached, `?refresh=true` to force) |
| `GET /v1/check/{domain}/dashboard` | Check + history + registration data |
| `GET /v1/token/{domain}/ots.json` | Serve cached token |
| `GET /v1/log/{domain}` | Transparency log entries for a domain |
| `GET /v1/log/{domain}/verify` | Verify hash chain integrity |
| `GET /.well-known/did.json` | DID document (public signing key) |
| `GET /stats` | Registry statistics + fetch tier counters |
| `GET /health` | Health check |

## Development

```bash
cd server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8900
```

## License

Proprietary. All rights reserved by OpenTrustSeal, Inc.
