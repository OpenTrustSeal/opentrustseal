# OpenTrustSealTool

Verify a merchant domain before an agent makes a payment. Calls the OpenTrustSeal trust attestation API. Returns a signed evidence bundle the agent can use to decide whether to transact.

## What the bundle contains

- A numeric trust score from 0 to 100
- A verdict: PROCEED / CAUTION / DENY
- A confidence rating: high / medium / low
- On any CAUTION verdict the response also includes a cautionReason field. Possible values are incomplete_evidence / weak_signals / new_domain / infrastructure

Every response is Ed25519-signed. The signed payload covers all four fields so the explanation can be trusted the same way the number is.

## Why use this

AI shopping, purchasing, and procurement agents routinely need to transact with merchants they have never seen before. Two failure modes without a trust check are common. One is risk-blind transactions where the agent pays any site the LLM suggests. The other is a narrow allowlist that refuses anything unfamiliar. Neither scales. OpenTrustSeal provides an independent third-party attestation an agent can call before the payment action.

## Installation

```bash
pip install crewai crewai-tools httpx
```

## Basic usage

```python
from crewai import Agent, Task, Crew
from crewai_tools import OpenTrustSealTool

trust_check = OpenTrustSealTool()

shopper = Agent(
    role="Shopping assistant",
    goal="Buy the requested item from a trustworthy merchant",
    backstory="You verify merchant trust before any purchase.",
    tools=[trust_check],
    verbose=True,
)

task = Task(
    description="Before buying from example-merchant.com verify its trust score.",
    expected_output="A trust verdict with score recommendation and reasoning.",
    agent=shopper,
)

Crew(agents=[shopper], tasks=[task]).kickoff()
```

## Sample output

What the agent sees on a clean PROCEED:

```
Domain: stripe.com
Trust Score: 88/100 (PROCEED)
Brand Tier: well_known
Reasoning: Long-established publicly verified identity. Clean reputation.
Signals: reputation=95 identity=75 content=90 ssl=100 dns=95 domainAge=100
Country: US
Evidence confidence: high
ACTION: Safe to proceed with this merchant.
Signed: MEUCIQDx... (verify at did:web:opentrustseal.com)
```

Output on a low-confidence CAUTION:

```
Evidence confidence: low
CAUTION reason: incomplete_evidence
ACTION: Evidence incomplete. Not necessarily bad. Low-dollar OK. Confirm larger amounts.
```

## Guidance lines the tool emits

| Verdict or flag | Guidance line the agent sees |
|---|---|
| PROCEED | Safe to proceed with this merchant. |
| Malware or phishing flagged | DO NOT proceed. Critical safety flags detected. |
| CAUTION with low confidence | Evidence incomplete. Not necessarily bad. Low-dollar OK. Confirm larger amounts. |
| CAUTION with cautionReason new_domain | New domain. Confirm with user before transacting. |
| CAUTION other cases | Proceed with caution. Confirm with user first. |
| DENY | Refuse this transaction. |

## Signal categories

The score is a weighted composite across six categories. All sources are publicly observable.

| Signal | Weight | Sources |
|---|---|---|
| Reputation | 30% | Tranco top-1M ranking / Spamhaus DBL / SURBL / URLhaus / Google Safe Browsing |
| Identity | 25% | WHOIS / SSL cert organization / public company registry / schema.org markup |
| Content | 17% | Privacy policy / terms of service / contact info / security headers |
| Domain age | 10% | WHOIS registration date |
| SSL/TLS | 10% | Certificate validity / TLS version / HSTS |
| DNS | 8% | SPF / DMARC / DNSSEC / CAA |

## API posture

Free tier is open with no API key required. Rate limits are 60 requests per minute per IP and 10,000 checks per month per IP. A paid tier exists for higher rate limits and commercial SLAs.

Every response is Ed25519-signed. The signing key is published at https://opentrustseal.com/.well-known/did.json using DID Web. The signed payload covers domain / trustScore / recommendation / confidence / cautionReason. Agents and downstream systems can verify the attestation without trusting OpenTrustSeal to serve honest JSON.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENTRUSTSEAL_API_KEY` | No | unset | Optional key for higher rate limits on the paid tier |
| `OPENTRUSTSEAL_BASE_URL` | No | https://api.opentrustseal.com | Override for self-hosted deployments |

## Async support

The tool implements both `_run` and `_arun`. Use async in async agent runtimes.

```python
result = await trust_check._arun("merchant.com")
```

## Coverage

The companion trust dataset covers the Tranco top-1M. Check your merchant's coverage and confidence rating at https://opentrustseal.com or via the API.

## Source and docs

- Tool source and issues: https://github.com/crewAIInc/crewAI
- API documentation: https://api.opentrustseal.com/docs
- Methodology: https://opentrustseal.com/docs/methodology
