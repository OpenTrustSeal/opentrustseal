# Add OpenTrustSealTool -- merchant trust verification for agent payments

Target repository: `crewAIInc/crewAI`, path `lib/crewai-tools/src/crewai_tools/tools/opentrustseal_tool/`.

Heads up: the standalone `crewAIInc/crewAI-tools` repo is now archived and redirects here. This PR targets the maintained monorepo location.

---

## 1. PR description

### What this adds

`OpenTrustSealTool` lets an agent verify a merchant domain before a payment or checkout. One API call returns four things the agent needs to make the right decision. The first is a 0-to-100 trust score. The second is a recommendation of either `PROCEED`, `CAUTION`, or `DENY`. The third is an evidence confidence field with three possible values: `high`, `medium`, or `low`. The fourth is a `cautionReason` that explains WHY a `CAUTION` verdict was issued (one of `incomplete_evidence`, `weak_signals`, `new_domain`, or `infrastructure`). Agents use the confidence together with the reason to do policy correctly. For example, the agent should proceed on low-confidence `CAUTION` for small-dollar transactions, confirm with the user on high-confidence `CAUTION` tagged `new_domain`, and refuse on `DENY`. Every response is Ed25519-signed, and the signed payload covers the score together with the confidence together with the cautionReason, so the explanation can be trusted the same way the number is.

### Why this matters for CrewAI

Shopping, purchasing, and procurement agents routinely transact with merchants an agent has never seen before. Default behavior today falls into one of two failure modes. Either the agent runs risk-blind transactions (it pays any site the LLM suggests), or it refuses anything unfamiliar (it only transacts with a small allowlist). Neither approach scales to real-world commerce. An independent trust attestation tool is the missing primitive here. It is a precheck any agent can call that returns a signed, auditable evidence bundle from a third party with no stake in the transaction. This tool slots directly in front of any payment action a CrewAI agent takes.

### Example usage

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
    description="Before buying from example-merchant.com, verify its trust score.",
    expected_output="A trust verdict with score, recommendation, and reasoning.",
    agent=shopper,
)

Crew(agents=[shopper], tasks=[task]).kickoff()
```

Example of the text an agent sees on tool call:

```
Domain: stripe.com
Trust Score: 88/100 (PROCEED)
Brand Tier: well_known
Reasoning: Long-established, publicly verified identity, clean reputation.
Signals: reputation=95 identity=75 content=90 ssl=100 dns=95 domainAge=100
Country: US
Evidence confidence: high
ACTION: Safe to proceed with this merchant.
Signed: MEUCIQDx... (verify at did:web:opentrustseal.com)
```

### Dataset proof-of-coverage

A companion trust dataset lives at `{HF_URL}` (Hugging Face, CC-BY-4.0). It contains trust scores and raw signal data for every domain in the Tranco top-1M. This matters because a trust tool that fails on long-tail merchants is not really a trust tool, it is an allowlist. The dataset exposes the same confidence and cautionReason fields the API returns, so agent developers can inspect coverage and tune policy against the full distribution before deployment.

### API posture

Free tier is open with no API key required, rate limited to 60 requests per minute and 10,000 checks per month per IP. A paid tier exists for higher rate limits and commercial SLAs. Every response is signed with Ed25519, and the signing key is published at `https://opentrustseal.com/.well-known/did.json` using DID Web. Agents and downstream systems can verify the attestation without trusting OpenTrustSeal to serve them honest JSON. That compositional property is what payment protocols (AP2, x402, MPP) want from an external trust source.

### Backwards compatibility and dependencies

New optional tool. Pure addition. The implementation uses `httpx` for both `_run` (sync) and `_arun` (async), matching the Tavily tool's pattern of declaring `package_dependencies = ["httpx"]` on the tool class itself. We picked `httpx` over `requests` because the async method is the primary use case for agent runtimes, and `httpx` gives us one client library for both sync and async. If you prefer the tool ride under an optional extra, we can add it to `[project.optional-dependencies]` as an `opentrustseal` extra; just let us know and we will update the PR.

---

## 2. Testing recipe

A reviewer can verify this end-to-end in under two minutes.

Install minimal deps:

```bash
pip install crewai crewai-tools httpx pydantic
```

Save the tool file locally as `opentrustseal_tool.py` (from the PR diff), then run this script:

```python
from opentrustseal_tool import OpenTrustSealTool

tool = OpenTrustSealTool()
print(tool._run("stripe.com"))
print("---")
print(tool._run("example.com"))
```

Expected output pattern for `stripe.com`:

```
Domain: stripe.com
Trust Score: XX/100 (PROCEED)
...
ACTION: Safe to proceed with this merchant.
Signed: <base64>... (verify at did:web:opentrustseal.com)
```

Line one reads `Domain: stripe.com`. Line two reads `Trust Score: XX/100 (PROCEED)` where the exact score varies by rescore date but the structure is stable. Somewhere further down, the text `ACTION: Safe to proceed with this merchant.` appears. The final line starts with `Signed:`.

The second invocation targets a low-confidence `CAUTION` domain. If you have one handy from a prior run, use it. Otherwise, pick any merchant whose site blocks automated crawlers. Cloudflare Enterprise protected retailers are common examples; in the dataset these surface as `confidence=low` with `cautionReason=incomplete_evidence`. Expected guidance line:

```
ACTION: Evidence incomplete, not necessarily bad. Low-dollar OK, confirm larger amounts.
```

To inspect the Ed25519 signature manually, fetch the DID document:

```bash
curl https://opentrustseal.com/.well-known/did.json
```

Pull the `verificationMethod` public key (Ed25519), then verify the raw response signature against the canonical payload using any Ed25519 library. The signed payload covers these fields: `domain`, `trustScore`, `recommendation`, `confidence`, and `cautionReason`.

---

## 3. Submission checklist

Status of each pre-submit item as of the current draft:

- [x] **File layout.** 3-file directory at `sdk/crewai-pr/opentrustseal_tool/` containing `__init__.py` (empty, matching brave_search_tool's convention), `opentrustseal_tool.py`, and `README.md`. Ready to drop into `lib/crewai-tools/src/crewai_tools/tools/opentrustseal_tool/`.
- [x] **Tool class conventions.** `name="OpenTrustSeal Check"` follows the Title Case pattern used by `TavilySearchTool` and `BraveSearchTool`. `args_schema` is a `BaseModel` subclass. `description` is a short action-oriented string.
- [x] **package_dependencies declared** on the tool class as `["httpx"]`, matching the Tavily pattern.
- [x] **env_vars declared** as a list of `EnvVar` entries for `OPENTRUSTSEAL_API_KEY` and `OPENTRUSTSEAL_BASE_URL`, both non-required (free tier works without either).
- [x] **_arun implemented** using `httpx.AsyncClient`. Both sync and async paths share the same response formatter so output is identical across runtimes.
- [x] **Tests.** Mocked test suite at `sdk/crewai-pr/tests/test_opentrustseal_tool.py` with coverage for PROCEED, low-confidence CAUTION guidance, malware-flag override, network errors, domain normalization, and the package_dependencies / env_vars declarations. Does not hit the live API. Drop into `lib/crewai-tools/tests/tools/test_opentrustseal_tool.py`.
- [ ] **License compatibility.** The maintained `crewAIInc/crewAI` repo is MIT. Existing tools in the repo do not carry per-file MIT headers (verified against `brave_search_tool` and `tavily_search_tool`); they inherit from the repo LICENSE. Our tool follows the same convention. Confirm this is acceptable before opening the PR.
- [ ] **Register in top-level `__init__.py`.** Add these two lines to `lib/crewai-tools/src/crewai_tools/__init__.py` alongside the existing tool registrations:
  ```python
  from crewai_tools.tools.opentrustseal_tool.opentrustseal_tool import OpenTrustSealTool
  # ... and in __all__:
  "OpenTrustSealTool",
  ```
- [ ] **Ruff.** The repo uses `ruff` with a custom rule set at root. Run `ruff check` and `ruff format` locally on the 3 files + test before pushing.
- [ ] **CLA.** Check the `crewAIInc/crewAI` repo for a CLA bot comment on open PRs. If a CLA is required, sign it before requesting review.
- [ ] **Dataset URL.** Replace `{HF_URL}` in the PR body once the Hugging Face dataset is published.
