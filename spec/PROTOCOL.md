# OpenTrustSeal Protocol Specification

**Version:** 0.2.0-draft
**Status:** Draft
**Authors:** Allen Lu
**Date:** 2026-04-10
**Revised:** 2026-04-10

---

## 1. Abstract

OpenTrustSeal (OTT) is an open protocol that enables AI agents to verify the
trustworthiness of a website before initiating financial transactions. The
protocol provides cryptographically signed evidence bundles containing
observable trust signals (domain age, SSL, DNS, reputation, content, identity
status) alongside a computed trust score that summarizes the evidence into a
single actionable number.

Agents query the OTT API (or read a static token from a well-known URI) and
receive both the raw evidence and a scored recommendation (PROCEED, CAUTION,
or DENY) in under 200ms.

The protocol separates the "should I pay?" decision from the "how do I pay?"
mechanism. It complements existing agent payment rails (Stripe Machine
Payments Protocol, Coinbase x402, Skyfire KYAPay) rather than competing with
them.

## 2. Problem Statement

AI agents are gaining the ability to browse the web, select products, and
authorize payments autonomously. The payment infrastructure is being built
(rails), but no standardized mechanism exists for agents to assess whether a
given merchant or site is trustworthy before committing funds.

Current gaps:

- No machine-readable trust signal at the site level
- No cryptographic proof that a trust assessment is authentic and current
- No standardized discovery mechanism for agents to find trust data
- No graduated verification system that lets new sites build trust over time
- No neutral, payment-rail-agnostic trust authority

## 3. Design Principles

1. **Evidence-first** -- raw signals are the primary output; the score summarizes them
2. **Rail-agnostic** -- works with any payment protocol, not tied to one
3. **Machine-first** -- structured data optimized for agent consumption
4. **Cryptographically verifiable** -- Ed25519 signatures on every token
5. **Progressive trust** -- new sites start low, earn trust through verification
6. **Open protocol, monetized service** -- spec is public, premium verification is paid
7. **Sub-200ms verification** -- agents need answers at transaction speed
8. **Continuously improvable** -- scoring weights are versioned and refined based on real-world fraud correlation data
9. **Horizontally scalable** -- every component scales independently

## 4. Terminology

| Term | Definition |
|------|------------|
| **Trust Token** | A signed JSON document asserting a site's trust score and verification status |
| **Site Owner** | The entity that controls a domain and opts into OTT verification |
| **Verification Authority (VA)** | The service that performs verification checks and signs trust tokens |
| **Querying Agent** | An AI agent that reads trust tokens before making transaction decisions |
| **Trust Score** | A numerical value (0--100) representing assessed trustworthiness |
| **Verification Tier** | The level of verification a site has undergone (automated, enhanced, KYC-verified) |
| **Token TTL** | Time-to-live; how long a trust token remains valid before requiring refresh |

## 5. Protocol Overview

### 5.1 Flow Summary

```
Site Owner                 Verification Authority           Querying Agent
    |                              |                              |
    |  1. Register domain          |                              |
    |----------------------------->|                              |
    |                              |                              |
    |  2. Prove ownership          |                              |
    |  (DNS TXT or HTTP)           |                              |
    |----------------------------->|                              |
    |                              |                              |
    |  3. VA runs automated checks |                              |
    |                              |                              |
    |  4. VA signs token           |                              |
    |<-----------------------------|                              |
    |                              |                              |
    |  5. Site publishes           |                              |
    |  /.well-known/ots.json       |                              |
    |                              |                              |
    |                              |  6. Agent queries token      |
    |                              |<-----------------------------|
    |                              |                              |
    |                              |  7. VA returns verified      |
    |                              |     trust assessment         |
    |                              |----------------------------->|
    |                              |                              |
    |                              |  8. Agent decides:           |
    |                              |     transact or reject       |
```

### 5.2 Two Discovery Paths

Agents can verify trust through two complementary paths:

**Path A: Direct Discovery**
Agent fetches `https://{domain}/.well-known/ots.json` directly from the site.
The token is self-contained and cryptographically signed. The agent verifies
the signature against the VA's published public key. No API call needed.

**Path B: API Verification**
Agent queries the VA's API: `GET https://api.opentrustseal.com/v1/check/{domain}`.
The VA returns the latest trust assessment. This path supports bulk queries,
real-time scoring, and enriched metadata not available in the static token.

Path A is for speed and decentralization. Path B is for depth and freshness.
Agents should support both; payment rails can choose which to require.

## 6. Trust Token Schema

### 6.1 Well-Known URI

Sites MUST publish their trust token at:

```
https://{domain}/.well-known/ots.json
```

The file MUST be served with:
- Content-Type: `application/json`
- CORS headers: `Access-Control-Allow-Origin: *`
- Cache-Control: `max-age={ttl_seconds}`

### 6.2 Token Structure

The token leads with observable evidence. The trust score is a computed
summary of the evidence, not an independent judgment. Every input that
feeds the score is visible in the `signals` object, so any consumer can
inspect, challenge, or override the score based on the underlying data.

```json
{
  "@context": [
    "https://www.w3.org/ns/credentials/v2",
    "https://opentrustseal.com/ns/v1"
  ],
  "type": ["VerifiableCredential", "OpenTrustSeal"],
  "issuer": {
    "id": "did:web:opentrustseal.com",
    "name": "OpenTrustSeal Verification Authority"
  },
  "issuanceDate": "2026-04-10T00:00:00Z",
  "expirationDate": "2026-04-17T00:00:00Z",
  "credentialSubject": {
    "id": "https://example.com",
    "domain": "example.com",
    "signals": {
      "domainAge": {
        "registeredDate": "2019-03-15",
        "band": "5+ years",
        "score": 95
      },
      "ssl": {
        "valid": true,
        "issuer": "Let's Encrypt",
        "expiresDate": "2026-07-10",
        "tlsVersion": "1.3",
        "hsts": true,
        "score": 100
      },
      "dns": {
        "hasSPF": true,
        "hasDMARC": true,
        "hasDNSSEC": false,
        "hasCAA": false,
        "score": 80
      },
      "content": {
        "hasPrivacyPolicy": true,
        "hasTermsOfService": true,
        "hasContactInfo": true,
        "score": 100
      },
      "reputation": {
        "malwareDetected": false,
        "phishingDetected": false,
        "spamListings": 0,
        "score": 100
      },
      "identity": {
        "verified": false,
        "verificationTier": "automated",
        "whoisDisclosed": true,
        "businessDirectoryFound": true,
        "contactInfoOnSite": true,
        "score": 35
      }
    },
    "flags": [],
    "trustScore": 82,
    "scoringModel": "ots-v1-weights",
    "recommendation": "PROCEED",
    "agentGuidance": "Strong technical signals, clean reputation. No identity verification on file. Suitable for transactions under $500 without additional checks."
  },
  "proof": {
    "type": "Ed25519Signature2020",
    "created": "2026-04-10T00:00:00Z",
    "verificationMethod": "did:web:opentrustseal.com#signing-key-1",
    "proofPurpose": "assertionMethod",
    "proofValue": "z3FXQjecWNiPg...base58-encoded-signature"
  }
}
```

Note: `signals` appears before `trustScore` deliberately. Evidence first,
then the computed summary. The `scoringModel` field identifies which version
of the scoring weights produced this score, so agents can track algorithm
changes and scoring model improvements over time.

### 6.3 Field Definitions

#### credentialSubject

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string (URI) | Yes | The canonical URL of the verified site |
| `domain` | string | Yes | The bare domain name |
| `signals` | object | Yes | Observable evidence: raw facts and per-signal scores |
| `flags` | array[string] | Yes | Active warnings (empty array if none) |
| `trustScore` | integer (0--100) | Yes | Computed summary of all signals (see Section 7) |
| `scoringModel` | string | Yes | Identifies the scoring weight version (e.g. `ots-v1-weights`) |
| `recommendation` | enum | Yes | One of: `PROCEED`, `CAUTION`, `DENY` |
| `agentGuidance` | string | Yes | Natural language guidance for agent decision-making |

Agents MAY ignore `trustScore` and make decisions based on individual signals
directly. The score is a convenience for agents that want a single threshold
check. Agents that need fine-grained control should inspect `signals` and
`flags` individually.

#### Verification Tiers

**v1 (MVP):**

| Tier | Description | Cost |
|------|-------------|------|
| `automated` | Machine-only checks (SSL, DNS, WHOIS, reputation DBs, content crawl) | Free |

In v1, all domains are scored using automated signals only. The identity
signal scores 0 for all unverified sites, which naturally limits scores for
sites with no established history.

**Future tiers (see Appendix D):**

| Tier | Description | Cost | Score Ceiling |
|------|-------------|------|---------------|
| `enhanced` | Automated + business directory cross-ref + address/phone verification | $29/mo | 80 |
| `kyc_verified` | Enhanced + human identity verification + document review | $99/mo | 95 |
| `enterprise` | KYC + on-site audit + continuous monitoring + insurance | $499/mo | 100 |

These tiers will be introduced after the v1 API has proven demand and
established scoring credibility. The tier ceiling system creates a natural
upgrade path: sites that want higher scores to attract agent transactions
have economic motivation to prove their legitimacy through deeper verification.

The `identity.verificationTier` field in the token schema is present from v1
so the format does not need to change when paid tiers launch.

#### Recommendation Thresholds

| Score Range | Recommendation | Agent Behavior |
|-------------|----------------|----------------|
| 75--100 | `PROCEED` | Transaction authorized without additional checks |
| 40--74 | `CAUTION` | Agent should apply transaction limits or request user confirmation |
| 0--39 | `DENY` | Agent should refuse the transaction |

These thresholds are defaults. Payment rails and agent operators can configure
their own thresholds based on risk tolerance.

#### Flags

Flags are active warnings that override the numerical score. If any critical
flag is present, the recommendation MUST be `DENY` regardless of score.

| Flag | Severity | Description |
|------|----------|-------------|
| `MALWARE_DETECTED` | Critical | Active malware found on site |
| `PHISHING_DETECTED` | Critical | Site identified as phishing |
| `RECENTLY_COMPROMISED` | Critical | Site was breached within last 90 days |
| `DOMAIN_SQUATTING` | High | Domain mimics a known brand |
| `NEW_DOMAIN` | Info | Domain registered less than 90 days ago |
| `SSL_EXPIRING_SOON` | Info | SSL certificate expires within 14 days |
| `NO_IDENTITY` | Info | No identity verification on file |
| `SCORE_DECLINING` | Warning | Trust score has dropped 10+ points in 30 days |

## 7. Trust Scoring Algorithm

### 7.0 Scoring Philosophy

The trust score is a **computed summary**, not an authoritative verdict.
Every input is observable in the evidence bundle. The score exists because
agents making real-time payment decisions need a single number to threshold
against, but the evidence is always available for agents that want to make
their own assessment.

The scoring model is **versioned and continuously improvable**. Weights are
identified by model version (e.g. `ots-v1-weights`). As real-world fraud
correlation data accumulates, weights will be refined. Old model versions
remain documented so consumers can understand score changes across versions.

When the model changes:
- The `scoringModel` field in the token updates to the new version
- A changelog is published at `opentrustseal.com/scoring/changelog`
- Existing tokens are re-scored on next refresh cycle
- Score changes of 10+ points trigger the `SCORE_DECLINING` or equivalent flag

### 7.1 Signal Categories and Weights (Model: ots-v1-weights)

The composite trust score is a weighted average of six signal categories:

| Category | Weight | What It Measures |
|----------|--------|-----------------|
| Domain Age | 15% | WHOIS registration date, historical presence |
| SSL/TLS | 10% | Valid certificate, proper configuration |
| DNS Security | 10% | SPF, DMARC, DNSSEC, MX records |
| Content Signals | 15% | Privacy policy, terms, contact info, business details |
| Reputation | 25% | Malware scans, phishing DBs, spam lists, abuse reports |
| Identity | 25% | KYC status, business registration, verified contact |

### 7.2 Scoring Formula

```
trustScore = min(tierCeiling, sum(signal_score * weight))
```

Each signal category produces a score from 0--100. The weighted sum is then
capped at the site's verification tier ceiling.

### 7.3 New Site Handling

A site registered less than 90 days ago receives the `NEW_DOMAIN` flag and
the following adjustments:

- Domain Age score: 0 (no history to evaluate)
- Reputation score: capped at 50 (insufficient data for full confidence)
- Maximum composite score: limited to tier ceiling (60 for automated)
- `agentGuidance` includes explicit warning about site newness

**This is where KYC verification becomes critical.** A new site that completes
KYC verification ($99/mo tier) can reach a score of up to 95 despite having
no domain history. The identity verification compensates for the lack of
historical signals. This creates a clear upgrade path:

```
New site (automated, no KYC):  max score ~35
New site (enhanced):           max score ~55
New site (KYC-verified):       max score ~80
New site (enterprise):         max score ~95
```

### 7.4 Score Decay and Refresh

Trust tokens have a TTL (default: 7 days). When a token expires:

- Automated checks re-run automatically
- If the site's signals have degraded, the score drops
- If no re-verification occurs within 30 days, the token is revoked
- Score decay rate: -2 points per week after token expiration

Sites on paid tiers get continuous monitoring (checks every 24h for enterprise,
every 72h for KYC-verified, weekly for enhanced).

## 8. Domain Ownership Verification

Before a VA issues a trust token, the site owner must prove domain control
using one of two methods:

### 8.1 DNS TXT Record

Add a TXT record to the domain's DNS:

```
_ott-verify.example.com  TXT  "ott-verify=abc123-verification-code"
```

The VA checks for this record. Once verified, the record can be removed.

### 8.2 HTTP File Verification

Place a verification file at:

```
https://example.com/.well-known/ott-verify.txt
```

Contents:
```
ott-verify=abc123-verification-code
```

The VA fetches this file over HTTPS. Once verified, the file can be removed.

## 9. Cryptographic Signing

### 9.1 Key Management

The VA maintains Ed25519 signing keys. The public key is published at:

```
https://opentrustseal.com/.well-known/did.json
```

In DID Document format:
```json
{
  "@context": "https://www.w3.org/ns/did/v1",
  "id": "did:web:opentrustseal.com",
  "verificationMethod": [{
    "id": "did:web:opentrustseal.com#signing-key-1",
    "type": "Ed25519VerificationKey2020",
    "controller": "did:web:opentrustseal.com",
    "publicKeyMultibase": "z6Mkf5rGMoatrSj1f...base58-encoded-public-key"
  }],
  "assertionMethod": ["did:web:opentrustseal.com#signing-key-1"]
}
```

### 9.2 Signature Generation

1. Canonicalize the `credentialSubject` using JSON Canonicalization Scheme (JCS, RFC 8785)
2. Compute SHA-256 hash of the canonical form
3. Sign the hash with Ed25519 private key
4. Encode the signature as base58btc (multibase prefix `z`)
5. Attach as `proof` object per W3C VC Data Model 2.0

### 9.3 Signature Verification (Agent Side)

1. Fetch the VA's public key from `did:web:opentrustseal.com`
2. Extract `credentialSubject` from the token
3. Canonicalize with JCS
4. Hash with SHA-256
5. Verify Ed25519 signature against the public key
6. Check `expirationDate` has not passed
7. If valid: trust the token. If invalid: fall back to API verification (Path B)

### 9.4 Key Rotation

Keys are rotated annually. The DID document supports multiple keys. Old keys
remain listed (with `revoked` date) for 90 days to allow token expiration.
Key rotation events are logged to an append-only transparency log at:

```
https://opentrustseal.com/.well-known/ott-keylog.json
```

## 10. API Specification

### 10.1 Check Endpoint (v1 Core)

```
GET /v1/check/{domain}
Host: api.opentrustseal.com
Authorization: Bearer {api_key}
Accept: application/json
```

**Response (200):**

The response returns the evidence bundle first, then the computed score and
recommendation. This is the lightweight API format; the full W3C VC format
(Section 6.2) is available via `Accept: application/vc+ld+json`.

```json
{
  "domain": "example.com",
  "checkedAt": "2026-04-10T12:00:00Z",
  "expiresAt": "2026-04-17T12:00:00Z",
  "signals": {
    "domainAge": {
      "registeredDate": "2019-03-15",
      "band": "5+ years",
      "score": 95
    },
    "ssl": {
      "valid": true,
      "issuer": "Let's Encrypt",
      "tlsVersion": "1.3",
      "score": 100
    },
    "dns": {
      "spf": true,
      "dmarc": true,
      "dnssec": false,
      "score": 80
    },
    "content": {
      "privacyPolicy": true,
      "termsOfService": true,
      "contactInfo": true,
      "score": 100
    },
    "reputation": {
      "malware": false,
      "phishing": false,
      "spamListed": false,
      "score": 100
    },
    "identity": {
      "verified": false,
      "verificationTier": "automated",
      "whoisDisclosed": true,
      "businessDirectory": true,
      "contactOnSite": true,
      "score": 35
    }
  },
  "flags": [],
  "trustScore": 82,
  "scoringModel": "ots-v1-weights",
  "recommendation": "PROCEED",
  "reasoning": "Strong technical signals, clean reputation. No identity verification on file. Suitable for transactions under $500.",
  "signature": "z3FXQjecWNiPg...base58-encoded-ed25519-signature",
  "issuer": "did:web:opentrustseal.com"
}
```

**Response (404):**
```json
{
  "error": "DOMAIN_NOT_FOUND",
  "message": "No trust data exists for this domain. It may not have been checked yet.",
  "suggestion": "Request a check at POST /v1/check/request"
}
```

**Response (410):**
```json
{
  "error": "CHECK_EXPIRED",
  "message": "Trust data for this domain has expired and not been refreshed",
  "lastKnownScore": 72,
  "expiredAt": "2026-04-03T00:00:00Z"
}
```

### 10.2 Request Check Endpoint

For domains not yet in the registry, agents or site owners can request
an initial check:

```
POST /v1/check/request
Host: api.opentrustseal.com
Authorization: Bearer {api_key}
Content-Type: application/json

{
  "domain": "newsite.com"
}
```

**Response (202):**
```json
{
  "domain": "newsite.com",
  "status": "queued",
  "estimatedCompletionSeconds": 120,
  "pollUrl": "/v1/check/newsite.com"
}
```

Automated checks run within 2 minutes. The agent can poll the check
endpoint or proceed with caution.

### 10.3 Bulk Check (Future)

```
POST /v1/check/bulk
```

Bulk checking will be available in a future API version after v1 adoption
is established.

### 10.4 Rate Limits (v1)

| Tier | Requests/min | Monthly queries |
|------|-------------|----------------|
| Free | 60 | 10,000 |
| Pro ($49/mo) | 600 | 500,000 |
| Enterprise (custom) | 6,000 | Unlimited |

### 10.5 Agent Discovery via llms.txt

Sites can reference their trust status in `llms.txt`:

```
# Trust Verification
This site is checked by OpenTrustSeal.
Trust Score: 82/100
Check: https://api.opentrustseal.com/v1/check/example.com
```

This gives agents a secondary discovery path through the llms.txt
convention.

## 11. Identity Verification (v1: Automated Only)

In v1, the identity signal is derived entirely from automated checks:
- WHOIS registrant data (redacted vs. disclosed)
- Business directory cross-references (Google Business, BBB, state registries)
- Presence of verifiable contact information on the site itself

All sites start with `identity.verified: false` and
`identity.verificationTier: "automated"`. The identity signal score reflects
what can be determined from public data alone.

### 11.1 Automated Identity Scoring (v1)

```
No public identity data:                 0 points
WHOIS registrant disclosed:             15 points
+ Contact info on site:                 25 points
+ Google Business or BBB listing:       35 points
+ State/national business registry:     45 points
+ All of the above consistent:          55 points
```

A site with disclosed WHOIS, verifiable contact info, and a matching
business directory listing can score up to 55 in identity without any paid
verification. This gives v1 scores meaningful variance in the identity
signal rather than defaulting every site to 0.

The `identity.verified` field remains `false` for all automated checks.
It becomes `true` only when a human verification tier (Appendix D) is
completed in a future version.

Human verification (KYC) tiers are defined in Appendix D and will launch
after the v1 API has proven demand (target: 5,000+ scored domains, 100+
active API developers).

## 12. Integration Points

### 12.1 Primary Integration: Python SDK (v1)

The v1 SDK targets Python because the dominant agent frameworks (LangGraph,
CrewAI, AutoGen) are Python-based.

```python
from opentrustseal import check

# Quick check with recommendation
result = check("merchant.com")
if result.recommendation == "DENY":
    raise UntrustedMerchant(result.reasoning)

# Access the full evidence bundle
print(result.signals.domain_age.band)      # "5+ years"
print(result.signals.reputation.malware)    # False
print(result.signals.identity.verified)     # False

# Access the computed score
print(result.trust_score)                   # 82
print(result.scoring_model)                 # "ots-v1-weights"
```

#### LangChain / LangGraph Tool

```python
from opentrustseal.langchain import OTTVerifyTool

# Add to any LangChain agent as a tool
tools = [OTTVerifyTool()]
agent = create_react_agent(llm, tools)
```

#### CrewAI Tool

```python
from opentrustseal.crewai import OTTVerifyTool

# Add to any CrewAI agent
agent = Agent(
    role="Purchasing Agent",
    tools=[OTTVerifyTool()]
)
```

### 12.2 For Payment Rails (Future)

Payment protocols can integrate OTT as a pre-transaction check:

```
Agent wants to pay merchant.com
  -> Agent calls OTT: GET /v1/check/merchant.com
  -> OTT returns: evidence + score=82 + recommendation=PROCEED
  -> Agent proceeds with x402/Stripe MPP/Skyfire payment
```

Middleware integrations for specific payment rails (Stripe MPP pre-checkout
hook, x402 header assertion, Skyfire wallet policy) will be developed after
v1 API adoption validates demand.

### 12.3 JavaScript SDK (Future)

A Node.js SDK (`@opentrustseal/sdk`) will follow the Python SDK once agent
framework adoption is established. The API is identical across languages.

## 13. Security Considerations

### 13.1 Threat Model

| Threat | Mitigation |
|--------|-----------|
| Forged trust token | Ed25519 signatures; agents verify against VA's published public key |
| Replay of expired token | `expirationDate` field; agents MUST check expiry |
| Man-in-the-middle on .well-known | HTTPS required; SRI on badge script |
| Compromised VA signing key | Key rotation protocol; transparency log; multi-key support |
| Score manipulation by site owner | Scores computed server-side; site owners cannot modify signed tokens |
| DDoS on verification API | Rate limiting; CDN caching; static token fallback (Path A) |
| Fake VA impersonation | DID-based identity; agents pin to known VA DIDs |

### 13.2 Privacy

- OTT does not track which agents query which domains (at the free tier)
- API queries are logged for rate limiting only; logs are purged after 30 days
- When KYC tiers launch: KYC data stored encrypted at rest (AES-256), processed by verified humans only
- PII from verification is never included in the public trust token
- GDPR and CCPA compliant: data deletion on request

### 13.3 Abuse Prevention

- Sites cannot self-issue trust tokens
- Score appeals go through human review
- Repeated failed verification attempts trigger rate limiting and review
- Whistleblower endpoint for reporting fraudulent sites: `POST /v1/report`

## 14. Governance

### 14.1 Protocol Governance

The OTT protocol specification is open and versioned. Changes go through:

1. RFC published to opentrustseal.com/rfcs/
2. 30-day public comment period
3. Review by advisory board
4. Ratification and version bump

### 14.2 Multi-VA Future

The protocol supports multiple Verification Authorities. The `issuer` field
in the trust token identifies which VA signed it. Agents can maintain a list
of trusted VAs, similar to how browsers maintain trusted certificate authorities.

In v1, OpenTrustSeal operates as the sole VA. The multi-VA framework is
specified now so the architecture does not need to change when competitors
or regional VAs emerge.

## 15. Versioning

The protocol uses semantic versioning. The version is embedded in:
- The `@context` URL: `https://opentrustseal.com/ns/v1`
- The API path: `/v1/check/`
- The token itself (implicit via context)

Breaking changes increment the major version. New VAs MUST support at least
the current and previous major version for 12 months after a major release.

---

## Appendix A: Complete Token Example

See Section 6.2 for the full token structure.

## Appendix B: Signal Source Reference

| Signal | Data Source | Update Frequency |
|--------|-----------|-----------------|
| Domain Age | WHOIS/RDAP | On registration, then weekly |
| SSL | Certificate Transparency logs + direct probe | Daily |
| DNS | Direct DNS queries | Daily |
| Content | Headless browser crawl | Weekly |
| Reputation | Google Safe Browsing, PhishTank, Spamhaus, VirusTotal | Every 6 hours |
| Identity | WHOIS + business directories (v1); KYC pipeline (future) | Weekly (v1) |

## Appendix C: Comparison with Existing Standards (April 2026)

| Feature | OTT | Visa Trusted Agent | Mastercard Verifiable Intent | Skyfire KYAPay | Google Safe Browsing |
|---------|-----|-------------------|------------------------------|----------------|---------------------|
| Pre-transaction check | Yes | Yes | No (post-tx) | Yes | No |
| Evidence bundle | Yes | No | Yes | Partial | No |
| Computed trust score | Yes | No | No | No | Binary only |
| Cryptographic proof | Ed25519 | Proprietary | Yes | Proprietary | No |
| Agent-optimized API | Yes | Agent-focused | Agent-focused | Agent-focused | No |
| Payment-rail-agnostic | Yes | Visa only | Mastercard only | Skyfire only | n/a |
| Open protocol | Yes | Open framework | Open standard | Proprietary | Proprietary |
| Neutral authority | Yes | No (Visa) | No (MC) | No (Skyfire) | No (Google) |
| Identity verification | Automated (v1) | Agent credentials | Consumer identity | Agent identity | None |

## Appendix D: Future Extensions -- KYC and Human Verification

This appendix defines verification tiers that will launch after v1 API
adoption validates demand. These tiers are the primary monetization mechanism.

### D.1 Why This Matters

Automated checks verify technical signals but cannot verify the humans behind
a website. For new sites with no history, human verification closes the trust
gap. The sites most motivated to pay for verification are exactly the ones
that need it most: new merchants, startups, and international sellers.

### D.2 Tier Definitions

**Enhanced ($29/mo)**
- Business name cross-referenced against state/national registries
- Physical address verified against commercial databases
- Phone number verified (automated call/SMS)
- Website content analyzed for consistency with claimed business
- Social media presence cross-referenced
- Timeline: 24--48 hours
- Score ceiling: 80

**KYC-Verified ($99/mo)**
Everything in Enhanced, plus:
- Government-issued ID of business owner
- Business registration documents (EIN, articles of incorporation)
- Bank account verification (micro-deposit confirmation)
- Video call with verification agent (15 min)
- Beneficial ownership disclosure
- Timeline: 3--5 business days
- Score ceiling: 95

**Enterprise ($499/mo)**
Everything in KYC-Verified, plus:
- Annual on-site or virtual audit
- Continuous transaction monitoring
- Dedicated compliance officer
- Transaction insurance (up to $50,000/incident)
- Priority dispute resolution
- Timeline: 5--10 business days initial, then continuous
- Score ceiling: 100

### D.3 Re-Verification

- Enhanced: annual re-verification (automated)
- KYC-Verified: annual re-verification (document re-upload + brief call)
- Enterprise: continuous monitoring, formal re-audit annually

Failure to re-verify results in tier downgrade and score recalculation.

### D.4 Revenue Projections

See docs/SCORING-AND-KYC.md for detailed revenue modeling at 10K, 100K,
and 1M domain scales.
