# OpenTrustToken Python SDK

Trust verification for AI agent commerce. One call to check if a site is safe to transact with.

## Install

```bash
pip install opentrusttoken
```

## Quick Start

```python
from opentrusttoken import check

result = check("merchant.com")

if result.is_blocked:
    print("Refusing:", result.reasoning)
elif result.is_risky:
    print("Proceed with caution:", result.reasoning)
else:
    print("Safe to transact:", result.trust_score)
```

## What You Get Back

```python
result = check("stripe.com")

result.trust_score          # 83
result.recommendation       # "PROCEED"
result.is_safe              # True
result.reasoning            # "Established domain (5+ years), valid SSL..."
result.site_category        # "infrastructure"

# Signal breakdown
result.signals.reputation.score  # 93
result.signals.identity.score    # 55
result.signals.ssl.score         # 100
result.signals.content.score     # 100

# Jurisdiction context
result.jurisdiction.country           # "US"
result.jurisdiction.legal_framework   # "US"
result.jurisdiction.cross_border_risk # "standard"

# Security flags
result.flags                  # []
result.has_critical_flags     # False

# Actionable checklist
for item in result.checklist:
    if item.status == "fail":
        print(f"Fix: {item.item} - {item.fix}")

# Cryptographic proof
result.signature  # "z3FXQ..." (Ed25519)
result.issuer     # "did:web:opentrusttoken.com"
```

## Async

```python
from opentrusttoken import async_check

result = await async_check("merchant.com")
```

## With API Key

```python
from opentrusttoken import OTTClient

client = OTTClient(api_key="ott_live_...")
result = client.check("merchant.com")
```

## Check Multiple Domains

```python
from opentrusttoken import OTTClient

client = OTTClient()
results = client.check_multiple(["site-a.com", "site-b.com", "site-c.com"])
for r in results:
    print(f"{r.domain}: {r.trust_score} ({r.recommendation})")
```

## LangChain Integration

```bash
pip install opentrusttoken[langchain]
```

```python
from opentrusttoken.integrations.langchain import OTTVerifyTool

tools = [OTTVerifyTool()]
agent = create_react_agent(llm, tools)

# Agent can now verify merchants before paying
```

## CrewAI Integration

```bash
pip install opentrusttoken[crewai]
```

```python
from opentrusttoken.integrations.crewai import OTTVerifyTool

agent = Agent(
    role="Purchasing Agent",
    tools=[OTTVerifyTool()]
)
```

## Raw HTTP (No SDK)

```bash
curl https://api.opentrusttoken.com/v1/check/merchant.com
```

Works from any language. The SDK is a convenience wrapper around this endpoint.

## Links

- [API Documentation](https://api.opentrusttoken.com/docs)
- [OpenTrustToken](https://opentrusttoken.com)
