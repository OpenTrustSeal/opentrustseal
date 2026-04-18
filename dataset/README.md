# OpenTrustSeal Trust Dataset

Trust scores and signal data for 100,000+ web domains, produced by the [OpenTrustSeal](https://opentrustseal.com) independent trust attestation API.

## What this dataset contains

Every row represents a trust assessment of a web domain, scored across six signal categories using publicly observable data. The dataset is designed for AI agent developers who need pre-transaction trust verification, and for researchers studying web trust signals at scale.

### Fields

| Field | Type | Description |
|---|---|---|
| `domain` | string | The domain assessed (e.g., "stripe.com") |
| `trustScore` | int | Composite trust score, 0-100 |
| `recommendation` | string | PROCEED (75+), CAUTION (40-74), DENY (0-39) |
| `scoringModel` | string | Model version used (e.g., "ots-v1.4-weights") |
| `brandTier` | string | "well_known" (brand anchor applied) or "scored" |
| `crawlability` | string | "ok" (content fetched) or "blocked" (bot protection prevented fetch) |
| `checkedAt` | ISO 8601 | When the assessment was performed |
| `reputationScore` | int | Reputation signal score (0-100) |
| `identityScore` | int | Identity verification score (0-100) |
| `contentScore` | int | Content analysis score (0-100) |
| `domainAgeScore` | int | Domain age score (0-100) |
| `sslScore` | int | SSL/TLS score (0-100) |
| `dnsScore` | int | DNS security score (0-100) |
| `domainAge_registeredDate` | date | WHOIS registration date |
| `ssl_valid` | bool | SSL certificate is valid |
| `ssl_issuer` | string | Certificate issuer (e.g., "DigiCert Inc") |
| `ssl_tlsVersion` | string | TLS version (e.g., "TLSv1.3") |
| `dns_spf` | bool | SPF record present |
| `dns_dmarc` | bool | DMARC record present |
| `dns_dnssec` | bool | DNSSEC enabled |
| `content_privacyPolicy` | bool | Privacy policy detected |
| `content_termsOfService` | bool | Terms of service detected |
| `content_contactInfo` | bool | Contact information detected |
| `reputation_malware` | bool | Flagged for malware |
| `reputation_phishing` | bool | Flagged for phishing |
| `reputation_spamListed` | bool | Listed on spam blocklists |
| `flags` | string | Pipe-separated flags (e.g., "WELL_KNOWN_BRAND") |

### Formats

- **CSV:** One row per domain, header row, UTF-8 encoded
- **JSON:** Object with `meta` (dataset metadata) and `domains` (array of domain objects)
- **SHA-256:** Manifest file with checksums for integrity verification

## Scoring methodology

The trust score is a weighted composite of six signal categories:

| Signal | Weight | Sources |
|---|---|---|
| Reputation | 30% | Tranco top-1M ranking, Spamhaus DBL, SURBL, URLhaus, Google Safe Browsing |
| Identity | 25% | WHOIS disclosure, SSL certificate organization, Tranco rank, public company status, schema.org markup |
| Content | 17% | Privacy policy, terms of service, contact information, security headers, robots.txt |
| Domain Age | 10% | WHOIS registration date |
| SSL/TLS | 10% | Certificate validity, TLS version, HSTS |
| DNS | 8% | SPF, DMARC, DNSSEC, CAA |

**Brand anchor:** Domains in the Tranco top-50K with 5+ years of age, clean reputation, and valid SSL receive a score floor of 75 (PROCEED). This compositional anchor uses signals that cannot be faked.

**Consensus tier (v1.4):** Tranco top-100 domains with 10+ years of age receive an elevated identity ceiling, spreading scores into the 80-90 range.

Full methodology: [opentrustseal.com/docs/methodology](https://opentrustseal.com/docs/methodology)

## Data collection

Domains are sourced from the [Tranco](https://tranco-list.eu/) top-100K list, which aggregates ranking data from Cloudflare, Umbrella, Majestic, and Quantcast. Each domain is assessed by the OpenTrustSeal pipeline, which collects signals from public data sources using a six-tier fetch escalation ladder (direct HTTP, headless Chrome, residential proxy, Internet Archive, protocol probes).

Signal data is collected without site participation. No crawling credentials, API keys, or site-specific integrations are required. All signals are derived from publicly observable data.

## Limitations

- **Point-in-time snapshot.** Scores reflect the state at `checkedAt`. Sites change; scores age.
- **Content signal gaps.** Some sites block automated content fetching (Cloudflare Enterprise, DataDome). These are marked `crawlability: "blocked"` and scored without content signals.
- **No business-model assessment.** The dataset scores technical trust signals, not business ethics or product quality. A site can score PROCEED and still sell low-quality products.
- **Automated identity ceiling.** Without KYC verification, the maximum identity score is capped at 55 (or 75 for consensus-tier domains). This mechanically limits the maximum trust score to approximately 88.

## Updates

This dataset is regenerated periodically as the registry grows and domains are re-checked. Check the `meta.version` field (date-stamped) for the export date.

## License

[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

You may use, share, and adapt this dataset for any purpose, including commercial use, as long as you give appropriate credit.

**Citation:**
```
OpenTrustSeal Trust Dataset (2026). OpenTrustSeal, Inc.
https://opentrustseal.com
```

## API access

For real-time trust checks with signed evidence bundles, use the API:

```bash
curl https://api.opentrustseal.com/v1/check/stripe.com
```

Python SDK:
```bash
pip install opentrustseal
```
```python
from opentrustseal import check
result = check("stripe.com")
print(result.trust_score, result.recommendation)
```

Free tier: 60 requests/minute, 10,000 checks/month. No API key required.

**API Docs:** [api.opentrustseal.com/docs](https://api.opentrustseal.com/docs)

## Contact

OpenTrustSeal, Inc.
alu@opentrustseal.com
https://opentrustseal.com
