"""CrewAI tool integration for OpenTrustSeal.

Usage:
    from opentrustseal.integrations.crewai import OTTVerifyTool

    agent = Agent(
        role="Purchasing Agent",
        tools=[OTTVerifyTool()]
    )
"""

try:
    from crewai.tools import BaseTool
except ImportError:
    raise ImportError(
        "crewai is required for CrewAI integration. "
        "Install with: pip install opentrustseal[crewai]"
    )

from typing import Optional
from ..client import OTTClient


class OTTVerifyTool(BaseTool):
    """Verifies merchant trustworthiness before a transaction."""

    name: str = "Verify Merchant Trust"
    description: str = (
        "Check if a website is safe for transactions. "
        "Pass a domain name (e.g. 'merchant.com') and get back a trust score, "
        "recommendation (PROCEED/CAUTION/DENY), and security details."
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
            f"Domain: {result.domain}",
            f"Trust Score: {result.trust_score}/100",
            f"Recommendation: {result.recommendation}",
            f"Reasoning: {result.reasoning}",
            f"Country: {result.jurisdiction.country}",
            f"Cross-border Risk: {result.jurisdiction.cross_border_risk}",
        ]

        if result.flags:
            lines.append(f"Flags: {', '.join(result.flags)}")

        if result.has_critical_flags:
            lines.append("CRITICAL: DO NOT proceed with this transaction.")
        elif result.is_safe:
            lines.append("Safe to proceed with standard transaction limits.")
        elif result.is_risky:
            lines.append("Proceed with caution. Consider lower transaction limits or user confirmation.")
        else:
            lines.append("Transaction should be refused.")

        return "\n".join(lines)
