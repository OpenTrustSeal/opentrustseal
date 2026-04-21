# OpenTrustSeal Dataset -- Publication Artifacts

Ready-to-post drafts for the public launch of the OpenTrustSeal trust dataset. Fill placeholders (`{DOMAIN_COUNT}`, `{DATE}`, `{VERSION}`, `{HF_URL}`, `{GH_RELEASE_URL}`, `{CHECKSUM_SHA256}`) at ship time.

---

## Section 1: Hugging Face Dataset Card

```yaml
---
license: cc-by-4.0
pretty_name: OpenTrustSeal Trust Dataset
size_categories:
- 100K<n<1M
task_categories:
- tabular-classification
- feature-extraction
tags:
- agents
- trust
- e-commerce
- fraud-detection
- web-trust
- verification
- merchant-identity
- langchain
- crewai
- agentic-commerce
- domain-reputation
- pre-transaction
---
```

# OpenTrustSeal Trust Dataset

Version `{VERSION}` -- released `{DATE}` -- `{DOMAIN_COUNT}` domains.

Trust scores and signal data for `{DOMAIN_COUNT}` web domains, produced by the [OpenTrustSeal](https://opentrustseal.com) independent trust attestation API. Every row is a pre-transaction trust assessment scored across six signal categories using publicly observable data.

## What this is

An independent trust attestation layer for agentic commerce. AI agents transacting on behalf of users need to answer one question before paying a merchant: is this site trustworthy? This dataset is the batch-accessible form of that answer for the Tranco top-1M web. The live API returns the same signals with a signed Ed25519 evidence bundle; the dataset is for developers who want to cache, filter, or train against the scored distribution without making per-domain API calls.

## Who this is for

- **AI agent developers** building purchase agents, shopping agents, research agents, or any agent that dispatches to third-party merchants. Use the dataset to pre-filter allow-lists, cache trust scores locally, or enrich retrieval with merchant identity signals.
- **Researchers** studying web trust signals, domain reputation, merchant identity verification, or fraud detection at scale.
- **Payment protocol implementers** (AP2, x402, MPP) wanting a reference trust layer that is independent of the protocol.

## Headline fields

The full field list lives in the canonical [dataset README](https://huggingface.co/datasets/opentrustseal/trust-dataset/blob/main/README.md). Agent-operational fields called out here:

| Field | Why it matters for agents |
|---|---|
| `trustScore` (0-100) | Composite, weighted across six signals. |
| `recommendation` | PROCEED (75+), CAUTION (40-74), DENY (0-39). Direct routing decision. |
| `confidence` (high / medium / low) | Separates "we have strong evidence" from "our evidence is thin." An agent can treat a low-confidence CAUTION differently from a high-confidence CAUTION. |
| `cautionReason` (`incomplete_evidence` / `weak_signals` / `new_domain` / `infrastructure`) | Populated when recommendation is CAUTION. Tells the agent *why*. A CAUTION with `incomplete_evidence` on a low-dollar transaction is fine to proceed with. A CAUTION with `weak_signals` on a high-dollar transaction should confirm with the user. |
| `brandTier` (`well_known` / `scored`) | Whether the brand anchor applied. |
| `crawlability` (`ok` / `blocked`) | Whether content signals could be fetched. |
| `trancoBucket` | Approximate Tranco rank (`top-100`, `top-1K`, ... `top-1M`). |
| `signalCompleteness` | `full` / `partial` / `minimal` -- how many of the six signals populated. |

The `confidence` and `cautionReason` fields are the reason this dataset is agent-operational, not just a list of scores. They let an agent make a routing decision that reflects the underlying evidence, not just a threshold crossing. In the live API these fields are cryptographically bound into the signed evidence bundle, so they carry the same trust guarantee as the score itself.

## Fields

See the full field list and types in the canonical [dataset README](https://opentrustseal.com/docs/dataset). The schema includes per-signal scores (`reputationScore`, `identityScore`, `contentScore`, `domainAgeScore`, `sslScore`, `dnsScore`), raw signal values (`ssl_valid`, `ssl_issuer`, `ssl_tlsVersion`, `dns_spf`, `dns_dmarc`, `dns_dnssec`, `content_privacyPolicy`, `content_termsOfService`, `content_contactInfo`, `reputation_malware`, `reputation_phishing`, `reputation_spamListed`), domain age (`domainAge_registeredDate`), and assessment metadata (`checkedAt`, `scoringModel`, `crawlMode`, `contentScorable`, `flags`).

## Scoring methodology

Scores are produced by the OpenTrustSeal v1.4 scoring model (`ots-v1.4-weights`). Six signal categories are weighted into a composite: Reputation 30%, Identity 25%, Content 17%, Domain Age 10%, SSL/TLS 10%, DNS 8%. A brand anchor applies a score floor of 75 for Tranco top-50K domains with 5+ years of age, clean reputation, and valid SSL. A consensus tier (new in v1.4) raises the identity ceiling from 55 to 75 for Tranco top-100 domains with 10+ years of age, clean reputation, valid SSL, and a pre-ceiling identity score at or above 30. Full spec: [SCORING-V1.4.md](https://github.com/opentrustseal/opentrustseal/blob/main/spec/SCORING-V1.4.md). Full methodology: [opentrustseal.com/docs/methodology](https://opentrustseal.com/docs/methodology).

## How to use it

```python
from datasets import load_dataset

ds = load_dataset("opentrustseal/trust-dataset", split="train")

# Allow-list: PROCEED with high confidence
allow = ds.filter(lambda r: r["recommendation"] == "PROCEED" and r["confidence"] == "high")

# Filter to a specific tier
top_1k = ds.filter(lambda r: r["trancoBucket"] in {"top-100", "top-1K"})

# Route by cautionReason
def route(row):
    if row["recommendation"] == "PROCEED":
        return "proceed"
    if row["recommendation"] == "DENY":
        return "block"
    # CAUTION -- look at the reason
    if row["cautionReason"] == "incomplete_evidence" and row["confidence"] == "low":
        return "proceed_low_dollar"
    if row["cautionReason"] == "new_domain":
        return "confirm_with_user"
    return "block"
```

For per-domain lookups with a signed evidence bundle, use the live API:

```bash
curl https://api.opentrustseal.com/v1/check/stripe.com
```

Python SDK (`pip install opentrustseal`) exposes `confidence` and `caution_reason` and ships with LangChain and CrewAI integrations. Free tier: 60 req/min, 10K checks/month, no API key required.

## Limitations

- **Point-in-time snapshot.** Scores reflect the state at `checkedAt`. Sites change; scores age. Re-check against the live API for transactions that matter.
- **Content signal gaps.** Some sites block automated content fetching (Cloudflare Enterprise, DataDome). These rows carry `crawlability: "blocked"` and `contentScorable: "no"`, and are scored without content signals. The `confidence` field reflects this.
- **No business-model assessment.** The dataset scores technical trust signals, not business ethics or product quality. A site can score PROCEED and still sell low-quality products.
- **Automated identity ceiling.** Without KYC verification the maximum identity score is capped at 55 (or 75 for consensus-tier domains). This mechanically limits the maximum trust score to approximately 88 for non-consensus-tier domains.
- **Tranco-biased coverage.** Domains outside the Tranco top-1M are not assessed in this release.

## License

[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Use, share, and adapt for any purpose including commercial use, with appropriate credit.

## Citation

```
OpenTrustSeal Trust Dataset ({DATE}). OpenTrustSeal, Inc.
Version {VERSION}.
https://opentrustseal.com
```

## Contact

OpenTrustSeal, Inc.
alu@opentrustseal.com
https://opentrustseal.com

---

## Section 2: GitHub Release Description

**OpenTrustSeal Trust Dataset `{VERSION}` -- `{DOMAIN_COUNT}` domains scored for agentic commerce.**

An independent trust attestation layer for AI agents. Before an agent pays a merchant, it needs to answer one question: is this site trustworthy? This release is the batch-accessible form of that answer for `{DOMAIN_COUNT}` domains across the Tranco top-1M web, scored with the OpenTrustSeal v1.4 model.

### What's in the release

- `opentrustseal-{VERSION}.csv` -- one row per domain, UTF-8, header row, full field schema.
- `opentrustseal-{VERSION}.json` -- `{meta, domains[]}` structure with dataset metadata and per-domain objects.
- `opentrustseal-{VERSION}.sha256` -- SHA-256 manifest for integrity verification. Release hash: `{CHECKSUM_SHA256}`.

### Agent-operational fields

Beyond `trustScore` and `recommendation`, every row carries `confidence` (high / medium / low) and `cautionReason` (`incomplete_evidence` / `weak_signals` / `new_domain` / `infrastructure`). These are what let an agent distinguish a CAUTION caused by thin evidence from a CAUTION caused by actually weak signals, and route accordingly. In the live API both fields are cryptographically bound into the Ed25519 evidence bundle.

### Links

- Full methodology: [opentrustseal.com/docs/methodology](https://opentrustseal.com/docs/methodology)
- v1.4 scoring spec: [SCORING-V1.4.md](https://github.com/opentrustseal/opentrustseal/blob/main/spec/SCORING-V1.4.md)
- Hugging Face mirror: {HF_URL}
- Live API: [api.opentrustseal.com](https://api.opentrustseal.com/docs) (free tier: 60 req/min, 10K checks/month, no API key)
- Python SDK: `pip install opentrustseal` -- LangChain and CrewAI integrations included
- Dataset README: [dataset/README.md](https://github.com/opentrustseal/opentrustseal/blob/main/dataset/README.md)

### License

[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Use, share, adapt, including commercial use, with credit.

### Merchants

If your domain appears with a CAUTION or DENY recommendation, check your report and fix list at [opentrustseal.com/dashboard?domain=yourdomain.com](https://opentrustseal.com/dashboard?domain=yourdomain.com). Most CAUTION cases are `incomplete_evidence` (a content fetch was blocked, WHOIS was rate-limited) and resolve with a single rescore after a configuration fix.

### Verify the release

```bash
sha256sum -c opentrustseal-{VERSION}.sha256
```

### Citation

```
OpenTrustSeal Trust Dataset ({DATE}). OpenTrustSeal, Inc.
Version {VERSION}.
{GH_RELEASE_URL}
```

Contact: alu@opentrustseal.com
