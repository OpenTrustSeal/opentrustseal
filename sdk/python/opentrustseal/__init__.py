"""OpenTrustSeal SDK - Trust verification for AI agent commerce.

Quick start:
    from opentrustseal import check

    result = check("merchant.com")
    if result.recommendation == "DENY":
        raise Exception(result.reasoning)

Async:
    from opentrustseal import async_check

    result = await async_check("merchant.com")

Full client:
    from opentrustseal import OTSClient

    client = OTSClient(api_key="ots_...")  # optional, free tier needs no key
    result = client.check("merchant.com")
    print(result.trust_score, result.recommendation)
"""

from .client import OTSClient, check, async_check
from .models import CheckResult, Signal, Jurisdiction, ChecklistItem

__version__ = "0.1.0"
__all__ = [
    "OTSClient",
    "check",
    "async_check",
    "CheckResult",
    "Signal",
    "Jurisdiction",
    "ChecklistItem",
]
