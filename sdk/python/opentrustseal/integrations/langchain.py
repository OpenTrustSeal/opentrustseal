"""LangChain / LangGraph tool integration for OpenTrustSeal.

Usage:
    from opentrustseal.integrations.langchain import OTTVerifyTool

    tools = [OTTVerifyTool()]
    agent = create_react_agent(llm, tools)

    # The agent can now call: OTTVerifyTool.run("merchant.com")
    # Returns: "merchant.com: Score 81/100 (PROCEED). Established domain, valid SSL..."
"""

try:
    from langchain_core.tools import BaseTool
except ImportError:
    raise ImportError(
        "langchain-core is required for LangChain integration. "
        "Install with: pip install opentrustseal[langchain]"
    )

from typing import Optional
from ..client import OTTClient


class OTTVerifyTool(BaseTool):
    """Tool that checks a domain's trust score before an agent transacts.

    Returns a human-readable summary that an LLM agent can use to
    decide whether to proceed with a payment or transaction.
    """

    name: str = "verify_merchant_trust"
    description: str = (
        "Check if a website is trustworthy before sending a payment or making a purchase. "
        "Input should be a domain name like 'merchant.com'. "
        "Returns a trust score (0-100), recommendation (PROCEED/CAUTION/DENY), "
        "and details about the site's security, reputation, and identity signals."
    )

    api_key: Optional[str] = None
    base_url: str = "https://api.opentrustseal.com"

    def _run(self, domain: str) -> str:
        client = OTTClient(api_key=self.api_key, base_url=self.base_url)
        try:
            result = client.check(domain.strip())
        except Exception as e:
            return f"Error checking {domain}: {str(e)}"

        lines = [
            f"{result.domain}: Score {result.trust_score}/100 ({result.recommendation})",
            f"Reasoning: {result.reasoning}",
            f"Category: {result.site_category}",
            f"Country: {result.jurisdiction.country} ({result.jurisdiction.legal_framework})",
            f"Cross-border risk: {result.jurisdiction.cross_border_risk}",
        ]

        if result.flags:
            lines.append(f"Flags: {', '.join(result.flags)}")

        if result.has_critical_flags:
            lines.append("WARNING: Critical security flags detected. DO NOT transact.")

        lines.append(f"Signal scores: reputation={result.signals.reputation.score}, "
                     f"identity={result.signals.identity.score}, "
                     f"content={result.signals.content.score}, "
                     f"ssl={result.signals.ssl.score}, "
                     f"dns={result.signals.dns.score}, "
                     f"age={result.signals.domain_age.score}")

        return "\n".join(lines)

    async def _arun(self, domain: str) -> str:
        client = OTTClient(api_key=self.api_key, base_url=self.base_url)
        try:
            result = await client.async_check(domain.strip())
        except Exception as e:
            return f"Error checking {domain}: {str(e)}"

        return self._format_result(result)

    def _format_result(self, result) -> str:
        lines = [
            f"{result.domain}: Score {result.trust_score}/100 ({result.recommendation})",
            f"Reasoning: {result.reasoning}",
        ]
        if result.has_critical_flags:
            lines.append("WARNING: Critical security flags detected. DO NOT transact.")
        return "\n".join(lines)
