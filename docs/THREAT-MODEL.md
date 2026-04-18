# OpenTrustSeal Threat Model

Version 1.0 | April 2026

## What this document covers

This is the threat model for the OpenTrustSeal trust attestation API. It describes what attacks the system defends against, what attacks it does not defend against, and the residual risks an integrator should understand before relying on OTS attestations in a payment flow.

## System boundaries

OTS is a read-only attestation service. It never handles payments, stores user credentials, or processes financial transactions. Its output is a signed evidence bundle containing a trust score, signal breakdown, and recommendation for a queried domain. The primary consumers are AI agent frameworks that call the API before making a payment on behalf of a user.

**In scope:** the API server, the scoring pipeline, the signing infrastructure, the data collection (crawlers), and the published attestation bundles.

**Out of scope:** the agent frameworks that consume OTS, the payment rails that cite OTS verdicts, and the merchant sites being scored. OTS cannot control how consumers interpret or enforce its recommendations.

## Assets

| Asset | Sensitivity | Impact if compromised |
|---|---|---|
| Ed25519 signing key | Critical | Attacker can forge attestations for any domain. Every existing and future signature is untrustworthy. |
| SQLite database (raw_signals, scored_results) | High | Attacker can manipulate stored scores, causing incorrect PROCEED/DENY verdicts on cached lookups. |
| Scoring algorithm (scoring.py, pipeline.py) | Medium | Attacker can bias scores systematically (e.g., inflate a fraudulent domain or suppress a competitor). |
| Tranco list (tranco.csv) | Medium | Replacement with a manipulated list would shift brand-anchor eligibility for ~100K domains. |
| VPS access (SSH keys) | Critical | Full control of all assets above. |
| Spamhaus DQS key | Low | Attacker could exhaust the daily query quota, degrading reputation signal quality. |
| Crawler shared secret | Medium | Attacker could inject fake crawler responses, poisoning content signals for specific domains. |

## Threat categories

### T1: Signing key compromise

**Attack:** Attacker obtains the Ed25519 private key and forges attestation bundles with arbitrary scores.

**Current mitigations:**
- Key stored at `/opt/opentrustseal/keys/signing.key` with `chmod 600`, owned by the `ott` service user
- VPS SSH access restricted to key-based auth (no password login)
- Key is not in the git repo, not in iCloud, not in any backup service

**Residual risk:** A single key with no rotation schedule means a compromise is permanent until detected. No transparency log means forged attestations are indistinguishable from real ones.

**Planned mitigations:** Key rotation schedule (see KEY-ROTATION.md). Transparency log for all issued attestations (see TRANSPARENCY-LOG.md).

### T2: Score manipulation via database tampering

**Attack:** Attacker with VPS access modifies scored_results or raw_signals to change a domain's trust score.

**Current mitigations:**
- SQLite WAL mode provides some crash recovery
- raw_signals are append-only (new rows, never updated), so historical data survives
- rescore.py can regenerate all scored_results from raw_signals, so a detected tampering is recoverable

**Residual risk:** If raw_signals are tampered, the rescore will propagate the manipulated data. No integrity verification on raw_signals rows.

**Planned mitigation:** Hash chain on raw_signals rows (each row includes the hash of the previous row for the same domain, creating a per-domain tamper-evident chain).

### T3: Scoring algorithm bias

**Attack:** An insider or compromised deployment introduces a scoring change that systematically favors or penalizes specific domains.

**Current mitigations:**
- Scoring model is versioned (`ots-v1.4-weights`). Every attestation includes the model version.
- rescore.py with --dry-run shows the full impact of any scoring change before it's applied
- All scoring code is in a private git repo with commit history

**Residual risk:** A subtle weight adjustment (e.g., changing reputation weight from 30% to 35%) would shift scores without triggering obvious alarms. No automated drift detection.

**Planned mitigation:** Automated calibration report that compares score distributions before and after any model change, flagging shifts above a threshold.

### T4: Crawler poisoning

**Attack:** Attacker intercepts or replaces the content a crawler fetches for a target domain, injecting false signals (e.g., fake privacy policy, fake contact info).

**Current mitigations:**
- Content is fetched via HTTPS (TLS prevents in-transit tampering)
- Multiple signal categories cross-reference (a site with fake content but real WHOIS/SSL/DNS signals will score inconsistently)
- The brand anchor requires unfakeable long-term signals (Tranco rank, domain age) that a content-only attack cannot satisfy

**Residual risk:** A sophisticated attacker who controls DNS for a target domain could redirect OTS's crawlers to a fake site. The fake site would score well on content but poorly on identity (unless the attacker also controls WHOIS and SSL certs).

### T5: Denial of service

**Attack:** Attacker floods the API with requests to exhaust rate limits or crash the service, preventing legitimate agent queries.

**Current mitigations:**
- Rate limiting: 60 requests/minute per IP
- Nginx connection limits
- Cached results (7-day TTL) mean most queries don't trigger the full pipeline
- Circuit breakers on all crawler tiers prevent cascading failures

**Residual risk:** A distributed attack from many IPs could exhaust the API box's 1GB RAM. No CDN or WAF in front of the API.

**Planned mitigation:** Cloudflare proxy mode (orange cloud) on api.opentrustseal.com for DDoS absorption. Currently DNS-only (gray cloud).

### T6: Tranco list manipulation

**Attack:** Attacker replaces the Tranco CSV with a modified version that adds a fraudulent domain to the top-100 list, qualifying it for the brand anchor.

**Current mitigations:**
- Tranco list is downloaded once at server startup from the official source
- The list is stored at a path only writable by root
- The brand anchor requires four independent conditions (Tranco rank + domain age + clean rep + valid SSL), so Tranco manipulation alone is insufficient

**Residual risk:** If the official Tranco source is compromised, OTS would consume the manipulated data. No integrity verification on the downloaded list.

**Planned mitigation:** Verify Tranco list SHA-256 against a known-good hash published by the Tranco project.

### T7: False negatives (failing to detect a scam)

**Attack:** A sophisticated scam site scores PROCEED by satisfying all automated checks: valid SSL, clean reputation (not yet flagged), privacy policy present, 2+ year domain age (bought an aged domain).

**Current mitigations:**
- The scoring model is designed so that the maximum score without KYC verification is ~88. Scores above 80 require multiple strong signals.
- Aged-domain purchases are detectable via WHOIS registrant-change monitoring (planned, not yet implemented)
- The registration/KYC path rewards verified identity with higher ceilings, creating an incentive for legitimate merchants to distinguish themselves from scammers

**Residual risk:** This is the fundamental limitation of any automated trust system. OTS scores observable evidence from public data. A sufficiently sophisticated scammer can satisfy all observable criteria. The defense is longitudinal monitoring (score changes over time) and the KYC path (which raises the ceiling above what automation can reach).

### T8: False positives (incorrectly flagging a legitimate site)

**Attack:** A legitimate merchant scores CAUTION or DENY due to missing signals (no privacy policy detected, WHOIS redacted under GDPR, content fetch blocked by CF).

**Current mitigations:**
- GDPR-aware WHOIS handling (redaction scored as neutral, not negative)
- Six-tier fetch escalation ladder with Wayback and protocol-probe fallbacks
- Well-known brand anchor floors top-Tranco sites at PROCEED
- Dashboard shows the specific checklist items causing a low score, giving merchants a path to improve
- Registration flow allows merchants to prove identity and earn points

**Residual risk:** New or small merchants without Tranco presence, with basic web infrastructure, will score in the CAUTION range (40-74) by default. This is by design (CAUTION means "proceed with human review," not "block"), but agents that treat CAUTION as DENY could harm legitimate small businesses.

## What OTS does NOT defend against

1. **Compromised merchant after scoring.** A site that scores PROCEED today could be compromised tomorrow. OTS scores are point-in-time snapshots with 7-day TTL, not continuous guarantees.

2. **Business model fraud.** A site that legitimately operates (valid SSL, privacy policy, real business registration) but sells counterfeit goods or runs a bait-and-switch scheme. OTS scores technical trust signals, not business ethics.

3. **Agent misuse of verdicts.** An agent that ignores CAUTION recommendations or treats all scores below 90 as DENY is making policy decisions OTS cannot control.

4. **Legal disputes.** OTS attestations are evidence, not legal guarantees. A signed bundle saying "PROCEED" does not create liability for OTS if the transaction goes wrong.

## Recommendations for integrators

1. Treat OTS scores as one input to your risk model, not the sole decision authority.
2. Cache OTS responses locally to reduce dependency on API availability.
3. Verify Ed25519 signatures on every response using the public key from the DID document at `/.well-known/did.json`.
4. Monitor the `scoringModel` field in responses. A model version change means the scoring algorithm was updated and scores may shift.
5. Implement a fallback policy for when the OTS API is unreachable (e.g., default to CAUTION + human review).
