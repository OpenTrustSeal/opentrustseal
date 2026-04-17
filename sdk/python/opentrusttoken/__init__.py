"""OpenTrustToken SDK - Trust verification for AI agent commerce.

Quick start:
    from opentrusttoken import check

    result = check("merchant.com")
    if result.recommendation == "DENY":
        raise Exception(result.reasoning)

Async:
    from opentrusttoken import async_check

    result = await async_check("merchant.com")

Full client:
    from opentrusttoken import OTTClient

    client = OTTClient(api_key="ott_...")  # optional, free tier needs no key
    result = client.check("merchant.com")
    print(result.trust_score, result.recommendation)
"""

from .client import OTTClient, check, async_check
from .models import CheckResult, Signal, Jurisdiction, ChecklistItem

__version__ = "0.1.0"
__all__ = [
    "OTTClient",
    "check",
    "async_check",
    "CheckResult",
    "Signal",
    "Jurisdiction",
    "ChecklistItem",
]
