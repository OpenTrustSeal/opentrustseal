# Security Policy

OpenTrustSeal takes security seriously. The service produces signed attestations that AI agents use to decide whether to transact with a merchant, so a vulnerability here can directly impact real-world money movement. This document tells you how to report an issue and what you can expect from us.

## Supported versions

We support the latest production deployment at `api.opentrustseal.com`. Older scoring-model versions (e.g. `ots-v1.3-weights`) remain valid for any already-signed bundles, but new fixes land only on the current model.

| Component | Version in production |
|---|---|
| API server | HEAD of `main` on this repository |
| Scoring model | `ots-v1.4-weights` |
| Signing algorithm | Ed25519 over a canonical JSON payload |
| DID method | `did:web:opentrustseal.com` |

## Reporting a vulnerability

Email **alu@opentrustseal.com** with:

- A clear description of the issue.
- Steps to reproduce, ideally with a minimal test case.
- The commit SHA or API endpoint you tested against.
- Whether you believe disclosure is time-sensitive.

**Please do not open a public GitHub issue for security problems.** Open issues are fine for functional bugs, feature requests, and questions about the methodology.

We aim to:

- Acknowledge receipt within **2 business days**.
- Provide a first-pass assessment (confirmed / not reproducible / need more info) within **7 days**.
- Ship a fix on a timeline proportional to severity (critical inside 72 hours, high inside 14 days, moderate inside 60 days, low at next release window).
- Credit reporters publicly in the fix announcement if they want credit; otherwise keep the report confidential.

## What we consider in scope

- Authentication bypass against the API.
- Signature forgery or verification bypass on attestation payloads.
- Inputs that cause the scoring pipeline to produce structurally invalid or unsigned responses.
- Injection against the trust dataset (WHOIS, DNS, content, reputation signal ingestion paths).
- Anything that breaks the transparency log's per-domain hash chain.
- Sensitive-data exposure: secrets in responses, registration data being returned where it should be private, PII leaking into public endpoints.
- Denial-of-service that a single attacker can trigger with modest resources.

## What we consider out of scope

- Missing HTTP security headers on static HTML pages where no user input is processed.
- Public disclosure of information that is already public (domain names, WHOIS data, Tranco rankings, SSL certificate details).
- Rate-limit bypass that only lets you use more of your own free allowance without impact on others.
- "Best practice" reports without a concrete exploit (e.g. "you don't use TLS 1.3 on X" when the setting is working as designed).
- Issues in third-party services we integrate with (Tranco, Spamhaus, Google Safe Browsing, Cloudflare, Let's Encrypt, Backblaze, Bright Data). Report those to the upstream vendor.
- Self-XSS or social-engineering scenarios that require the victim to paste code into their own console.

## Safe-harbor posture

If you make a good-faith effort to follow this policy -- report privately, avoid destructive testing against our production infrastructure, respect our users' data -- we will not pursue legal action against you for your research. We can not make that promise on behalf of third parties whose systems might happen to be involved.

## Data we care about

The signing key and the transparency log are the two assets whose integrity matters most:

- **Signing keys** live at `/opt/opentrustseal/keys/` on the API box. They never leave the box; only the public portion is published at `https://opentrustseal.com/.well-known/did.json`. A compromise of the signing key would let an attacker mint arbitrary attestations under our DID -- report this with maximum urgency.
- **Transparency log** is an append-only hash chain over every attestation. Any finding that lets you insert, delete, or reorder entries without the chain detecting it is a critical issue.

## Verifying an attestation

Agents and auditors can verify any response we sign:

1. Fetch our DID document: `curl https://opentrustseal.com/.well-known/did.json`
2. Extract the Ed25519 public key from `verificationMethod`.
3. Re-canonicalize the response's signable fields (`domain`, `trustScore`, `scoringModel`, `recommendation`, `confidence`, `cautionReason`, plus the full `signals` block).
4. Verify the Ed25519 signature in the response against the canonical bytes.

A mismatch means someone tampered with the bytes in transit or we made a mistake when signing. Either is worth reporting.

## Disclosure timeline

We publish a short post-mortem for any confirmed critical or high-severity finding once the fix has shipped and we're confident no active exploitation is ongoing. Moderate and low findings are summarized at the next monthly release note.

## Contact

- Security: alu@opentrustseal.com
- General: alu@opentrustseal.com
- Signed by: did:web:opentrustseal.com
