import os
from typing import Any

from crewai.tools import BaseTool, EnvVar
from dotenv import load_dotenv
from pydantic import BaseModel, Field


load_dotenv()

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class OpenTrustSealInput(BaseModel):
    """Input schema for OpenTrustSealTool."""

    domain: str = Field(..., description="Domain to verify, e.g. 'merchant.com'")


class OpenTrustSealTool(BaseTool):
    """Verify a merchant domain before making a payment.

    Calls the OpenTrustSeal trust attestation API, which scores sites across
    six signal categories using publicly observable data: reputation
    (Spamhaus, Google Safe Browsing, Tranco), identity (WHOIS, SSL cert
    org, public company status), content (privacy policy, terms, contact
    info), domain age, SSL/TLS posture, and DNS security (SPF, DMARC,
    DNSSEC).

    Returns a trust score (0 to 100), a recommendation (PROCEED, CAUTION,
    or DENY), a confidence rating (high, medium, low), and when the
    recommendation is CAUTION, a cautionReason explaining why
    (incomplete_evidence, weak_signals, new_domain, or infrastructure).
    Every response is Ed25519-signed and the signed payload covers all
    four fields, so an agent can trust the explanation the same way it
    trusts the score.

    The free tier requires no API key (60 requests per minute per IP,
    10,000 checks per month). Set OPENTRUSTSEAL_API_KEY for higher
    rate limits on the paid tier.
    """

    name: str = "OpenTrustSeal Check"
    description: str = (
        "Check if a merchant website is trustworthy before making a payment. "
        "Pass a domain name (e.g. 'merchant.com') and get a trust score "
        "(0-100), a PROCEED/CAUTION/DENY recommendation, confidence rating, "
        "and the reason for any CAUTION verdict. Call this BEFORE any "
        "payment or checkout action."
    )
    args_schema: type[BaseModel] = OpenTrustSealInput

    api_key: str | None = Field(
        default_factory=lambda: os.getenv("OPENTRUSTSEAL_API_KEY"),
        description="Optional API key for higher rate limits. Free tier works without one.",
    )
    base_url: str = Field(
        default_factory=lambda: os.getenv(
            "OPENTRUSTSEAL_BASE_URL", "https://api.opentrustseal.com"
        ),
        description="OpenTrustSeal API base URL. Override for self-hosted deployments.",
    )
    timeout_s: float = Field(
        default=90.0,
        description="HTTP timeout in seconds. Some merchant checks require multiple fetch tiers.",
    )
    package_dependencies: list[str] = Field(default_factory=lambda: ["httpx"])
    env_vars: list[EnvVar] = Field(
        default_factory=lambda: [
            EnvVar(
                name="OPENTRUSTSEAL_API_KEY",
                description="Optional API key for higher rate limits. Free tier works without one.",
                required=False,
            ),
            EnvVar(
                name="OPENTRUSTSEAL_BASE_URL",
                description="Override the API base URL. Defaults to https://api.opentrustseal.com.",
                required=False,
            ),
        ]
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "The 'httpx' package is required to use OpenTrustSealTool. "
                "Install it with: pip install httpx"
            )

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "crewai-opentrustseal/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _normalize_domain(self, domain: str) -> str:
        d = domain.strip().lower()
        d = d.replace("https://", "").replace("http://", "").split("/")[0]
        return d

    def _action_message(self, data: dict[str, Any]) -> str:
        """Confidence-aware decision guidance.

        Mirrors the action_message property in the opentrustseal-python SDK.
        Kept inline so this tool has zero SDK dependency. If the SDK's
        canonical messages change, update this function to match.
        """
        signals = data.get("signals", {})
        rep = signals.get("reputation", {})
        if rep.get("malware") or rep.get("phishing"):
            return "DO NOT proceed. Critical safety flags detected."
        rec = data.get("recommendation", "")
        if rec == "DENY":
            return "Refuse this transaction."
        if rec == "PROCEED":
            return "Safe to proceed with this merchant."
        if rec == "CAUTION":
            if data.get("confidence") == "low":
                return "Evidence incomplete. Not necessarily bad. Low-dollar OK. Confirm larger amounts."
            if data.get("cautionReason") == "new_domain":
                return "New domain. Confirm with user before transacting."
            return "Proceed with caution. Confirm with user first."
        return "Unexpected state. Fall back to human review."

    def _format_response(self, domain: str, data: dict[str, Any]) -> str:
        score = data.get("trustScore", 0)
        rec = data.get("recommendation", "UNKNOWN")
        reasoning = data.get("reasoning", "")
        brand_tier = data.get("brandTier", "scored")
        signals = data.get("signals", {})
        flags = data.get("flags", [])
        checklist = data.get("checklist", [])
        jurisdiction = data.get("jurisdiction", {})
        signature = data.get("signature", "")
        confidence = data.get("confidence", "unknown")
        caution_reason = data.get("cautionReason", "")

        lines = [
            f"Domain: {domain}",
            f"Trust Score: {score}/100 ({rec})",
            f"Brand Tier: {brand_tier}",
            f"Reasoning: {reasoning}",
        ]

        sig_parts = []
        for key in ["reputation", "identity", "content", "ssl", "dns", "domainAge"]:
            s = signals.get(key, {})
            sig_parts.append(f"{key}={s.get('score', '?')}")
        lines.append(f"Signals: {' '.join(sig_parts)}")

        country = jurisdiction.get("country", "UNKNOWN")
        lines.append(f"Country: {country}")

        if flags:
            lines.append(f"Flags: {', '.join(flags)}")

        failing = [c for c in checklist if c.get("status") in ("fail", "available")]
        if failing:
            top = "; ".join(c.get("item", "") for c in failing[:3])
            lines.append(f"Top issues: {top}")

        lines.append(f"Evidence confidence: {confidence}")
        if caution_reason:
            lines.append(f"CAUTION reason: {caution_reason}")

        # Action messages mirror the opentrustseal-python SDK's action_message
        # property verbatim. Keep in sync if the SDK's strings change.
        lines.append(f"ACTION: {self._action_message(data)}")

        if signature:
            lines.append(
                f"Signed: {signature[:32]}... (verify at did:web:opentrustseal.com)"
            )

        return "\n".join(lines)

    def _run(self, domain: str) -> str:
        d = self._normalize_domain(domain)
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                r = client.get(f"{self.base_url}/v1/check/{d}", headers=self._headers())
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            return f"Error checking {d}: {str(e)[:200]}"
        return self._format_response(d, data)

    async def _arun(self, domain: str) -> str:
        d = self._normalize_domain(domain)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.get(
                    f"{self.base_url}/v1/check/{d}", headers=self._headers()
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            return f"Error checking {d}: {str(e)[:200]}"
        return self._format_response(d, data)
