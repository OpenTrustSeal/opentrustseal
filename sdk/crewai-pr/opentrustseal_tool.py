"""OpenTrustSeal trust verification tool for CrewAI.

Verifies merchant trustworthiness before AI agent payments. Returns a
trust score (0-100), a PROCEED/CAUTION/DENY recommendation, and signal
evidence from six categories. Call this tool before any payment or
checkout action.

Free API. No API key required. No external dependencies beyond httpx.

Learn more: https://opentrustseal.com/docs/methodology.html
API docs: https://api.opentrustseal.com/docs
Dataset: 100,000+ domains scored from the Tranco top-100K

Example:
    from crewai import Agent
    from opentrustseal_tool import OpenTrustSealTool

    shopper = Agent(
        role="Shopping assistant",
        goal="Buy the requested item from a trustworthy merchant",
        tools=[OpenTrustSealTool()],
    )
"""

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

import httpx


class _OTSInput(BaseModel):
    domain: str = Field(..., description="Domain to verify, e.g. 'macys.com'")


class OpenTrustSealTool(BaseTool):
    """Verify merchant trust before making a payment.

    Checks a domain against the OpenTrustSeal trust attestation API,
    which scores sites across six signal categories using publicly
    observable data: reputation (Spamhaus, Google Safe Browsing, Tranco),
    identity (WHOIS, SSL cert org, public company), content (privacy
    policy, terms, contact info), domain age, SSL/TLS, and DNS security.

    Returns a trust score (0-100) and a recommendation:
    - PROCEED (75+): safe for automated transactions
    - CAUTION (40-74): flag for human review
    - DENY (0-39): refuse the transaction

    Every response is cryptographically signed with Ed25519 so the
    attestation can be independently verified.
    """

    name: str = "Verify merchant trust (OpenTrustSeal)"
    description: str = (
        "Check if a merchant website is trustworthy before making a payment. "
        "Pass a domain name (e.g. 'merchant.com') and get a trust score "
        "(0-100) with a PROCEED/CAUTION/DENY recommendation based on six "
        "signal categories. Call this BEFORE any payment or checkout action."
    )
    args_schema: type[BaseModel] = _OTSInput

    def _run(self, domain: str) -> str:
        domain = domain.strip().lower()
        domain = domain.replace("https://", "").replace("http://", "").split("/")[0]

        try:
            with httpx.Client(timeout=90) as client:
                r = client.get(
                    f"https://api.opentrustseal.com/v1/check/{domain}",
                    headers={"User-Agent": "crewai-opentrustseal/1.0"},
                )
                r.raise_for_status()
                d = r.json()
        except Exception as e:
            return f"Error checking {domain}: {str(e)[:100]}"

        score = d.get("trustScore", 0)
        rec = d.get("recommendation", "UNKNOWN")
        reasoning = d.get("reasoning", "")
        brand_tier = d.get("brandTier", "scored")
        signals = d.get("signals", {})
        flags = d.get("flags", [])
        checklist = d.get("checklist", [])
        jurisdiction = d.get("jurisdiction", {})
        signature = d.get("signature", "")

        lines = [
            f"Domain: {domain}",
            f"Trust Score: {score}/100 ({rec})",
            f"Brand Tier: {brand_tier}",
            f"Reasoning: {reasoning}",
        ]

        # Signal breakdown
        sig_parts = []
        for key in ["reputation", "identity", "content", "ssl", "dns", "domainAge"]:
            s = signals.get(key, {})
            sig_parts.append(f"{key}={s.get('score', '?')}")
        lines.append(f"Signals: {' '.join(sig_parts)}")

        country = jurisdiction.get("country", "UNKNOWN")
        lines.append(f"Country: {country}")

        if flags:
            lines.append(f"Flags: {', '.join(flags)}")

        # Top issues
        failing = [c for c in checklist if c.get("status") in ("fail", "available")]
        if failing:
            top = "; ".join(c.get("item", "") for c in failing[:3])
            lines.append(f"Top issues: {top}")

        # Decision guidance
        malware = d.get("signals", {}).get("reputation", {}).get("malware", False)
        phishing = d.get("signals", {}).get("reputation", {}).get("phishing", False)

        if malware or phishing:
            lines.append("ACTION: DO NOT proceed. Critical safety flags detected.")
        elif rec == "PROCEED":
            lines.append("ACTION: Safe to proceed with this merchant.")
        elif rec == "CAUTION":
            lines.append("ACTION: Proceed with caution. Confirm with user first.")
        else:
            lines.append("ACTION: Refuse this transaction.")

        if signature:
            lines.append(f"Signed: {signature[:32]}... (verify at did:web:opentrustseal.com)")

        return "\n".join(lines)
