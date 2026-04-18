"""CrewAI tool integration for OpenTrustSeal.

Usage:
    from opentrustseal.integrations.crewai import OpenTrustSealTool

    agent = Agent(
        role="Purchasing Agent",
        tools=[OpenTrustSealTool()]
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
from pydantic import BaseModel, Field
from ..client import OTSClient


class OpenTrustSealInput(BaseModel):
    """Input schema for the OpenTrustSeal trust verification tool."""
    domain: str = Field(..., description="Domain to verify, e.g. 'macys.com'")


class OpenTrustSealTool(BaseTool):
    """Verify merchant trust before making a payment.

    Checks a merchant domain against OpenTrustSeal's trust attestation
    API, which scores sites across six signal categories (reputation,
    identity, content, domain age, SSL, DNS) using publicly observable
    data. Returns a trust score (0-100), a PROCEED/CAUTION/DENY
    recommendation, and an Ed25519-signed evidence bundle.

    Call this tool BEFORE any payment or checkout action to verify the
    merchant is trustworthy.
    """

    name: str = "Verify merchant trust (OpenTrustSeal)"
    description: str = (
        "Check if a merchant website is trustworthy before making a payment. "
        "Pass a domain name (e.g. 'merchant.com') and get back a trust score "
        "(0-100), a PROCEED/CAUTION/DENY recommendation, and evidence from "
        "six signal categories. Call this tool BEFORE any payment action."
    )
    args_schema: type[BaseModel] = OpenTrustSealInput

    api_key: Optional[str] = None
    base_url: str = "https://api.opentrustseal.com"

    def _run(self, domain: str) -> str:
        client = OTSClient(api_key=self.api_key, base_url=self.base_url)
        try:
            result = client.check(domain.strip())
        except Exception as e:
            return f"Error checking {domain}: {str(e)}"

        lines = [
            f"Domain: {result.domain}",
            f"Trust Score: {result.trust_score}/100 ({result.recommendation})",
            f"Brand Tier: {result.brand_tier}",
            f"Reasoning: {result.reasoning}",
        ]

        # Signal breakdown
        lines.append(f"Signals: reputation={result.signals.reputation.score} "
                     f"identity={result.signals.identity.score} "
                     f"content={result.signals.content.score} "
                     f"ssl={result.signals.ssl.score} "
                     f"dns={result.signals.dns.score} "
                     f"age={result.signals.domain_age.score}")

        lines.append(f"Country: {result.jurisdiction.country}")

        if result.flags:
            lines.append(f"Flags: {', '.join(result.flags)}")

        # Top 3 checklist items for context
        failing = [c for c in result.checklist if c.status in ("fail", "available")]
        if failing:
            top = "; ".join(c.item for c in failing[:3])
            lines.append(f"Top issues: {top}")

        # Clear decision guidance for the agent
        if result.has_critical_flags:
            lines.append("ACTION: DO NOT proceed. Critical safety flags detected.")
        elif result.is_safe:
            lines.append("ACTION: Safe to proceed with this merchant.")
        elif result.is_risky:
            lines.append("ACTION: Proceed with caution. Consider confirming with the user first.")
        else:
            lines.append("ACTION: Refuse this transaction.")

        # Include the signature snippet for auditability
        sig = result.signature[:32] if result.signature else ""
        if sig:
            lines.append(f"Signed attestation: {sig}... (verify at did:web:opentrustseal.com)")

        return "\n".join(lines)


# Backward compatibility alias
OTSVerifyTool = OpenTrustSealTool
