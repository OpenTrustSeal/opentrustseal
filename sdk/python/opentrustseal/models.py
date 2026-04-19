"""Response models for the OpenTrustSeal SDK."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signal:
    """Individual signal category scores and data."""
    score: int = 0
    raw: dict = field(default_factory=dict)

    def __getattr__(self, name):
        if name in self.raw:
            return self.raw[name]
        raise AttributeError(f"Signal has no attribute '{name}'")


@dataclass
class Signals:
    """All six signal categories."""
    domain_age: Signal = field(default_factory=Signal)
    ssl: Signal = field(default_factory=Signal)
    dns: Signal = field(default_factory=Signal)
    content: Signal = field(default_factory=Signal)
    reputation: Signal = field(default_factory=Signal)
    identity: Signal = field(default_factory=Signal)


@dataclass
class Jurisdiction:
    """Jurisdiction and cross-border risk context."""
    country: str = "UNKNOWN"
    legal_framework: str = "unknown"
    cross_border_risk: str = "unknown"
    dispute_resolution: str = "unknown"
    kyc_available: bool = False
    has_public_registry: bool = False


@dataclass
class ChecklistItem:
    """Actionable checklist item."""
    category: str = ""
    item: str = ""
    status: str = ""  # pass, fail, improve, available
    impact: str = ""  # high, medium, low
    fix: str = ""


@dataclass
class CheckResult:
    """Result of a trust check.

    Primary fields for agent decision-making:
        result.trust_score       -> int (0-100)
        result.recommendation    -> "PROCEED" | "CAUTION" | "DENY"
        result.flags             -> list of active warnings

    Evidence access:
        result.signals.reputation.score  -> int
        result.signals.ssl.score         -> int
        result.signals.identity.score    -> int

    Context:
        result.jurisdiction.country          -> "US", "DE", etc.
        result.jurisdiction.cross_border_risk -> "standard" | "elevated"
        result.site_category                 -> "consumer" | "infrastructure"
    """
    check_id: str = ""
    domain: str = ""
    trust_score: int = 0
    recommendation: str = ""
    confidence: str = "high"  # "high", "medium", "low"
    caution_reason: Optional[str] = None  # "incomplete_evidence", "weak_signals", "new_domain", "infrastructure"
    reasoning: str = ""
    scoring_model: str = ""
    site_category: str = "consumer"
    brand_tier: str = "scored"  # "well_known" or "scored"
    crawlability: str = "ok"   # "ok" or "blocked"
    flags: list = field(default_factory=list)
    signals: Signals = field(default_factory=Signals)
    jurisdiction: Jurisdiction = field(default_factory=Jurisdiction)
    checklist: list = field(default_factory=list)
    checklist_summary: dict = field(default_factory=dict)
    signature: str = ""
    signature_key_id: str = ""
    issuer: str = ""
    checked_at: str = ""
    expires_at: str = ""
    raw_response: dict = field(default_factory=dict)

    @property
    def is_safe(self) -> bool:
        """Quick check: is this site safe to transact with?"""
        return self.recommendation == "PROCEED"

    @property
    def is_risky(self) -> bool:
        """Quick check: should the agent apply caution?"""
        return self.recommendation == "CAUTION"

    @property
    def is_blocked(self) -> bool:
        """Quick check: should the agent refuse?"""
        return self.recommendation == "DENY"

    @property
    def has_critical_flags(self) -> bool:
        """Check for malware, phishing, or other critical flags."""
        critical = {"MALWARE_DETECTED", "PHISHING_DETECTED", "RECENTLY_COMPROMISED"}
        return bool(critical & set(self.flags))


def _parse_response(data: dict) -> CheckResult:
    """Parse API response JSON into a CheckResult."""
    signals_data = data.get("signals", {})
    signals = Signals(
        domain_age=Signal(score=signals_data.get("domainAge", {}).get("score", 0), raw=signals_data.get("domainAge", {})),
        ssl=Signal(score=signals_data.get("ssl", {}).get("score", 0), raw=signals_data.get("ssl", {})),
        dns=Signal(score=signals_data.get("dns", {}).get("score", 0), raw=signals_data.get("dns", {})),
        content=Signal(score=signals_data.get("content", {}).get("score", 0), raw=signals_data.get("content", {})),
        reputation=Signal(score=signals_data.get("reputation", {}).get("score", 0), raw=signals_data.get("reputation", {})),
        identity=Signal(score=signals_data.get("identity", {}).get("score", 0), raw=signals_data.get("identity", {})),
    )

    j = data.get("jurisdiction", {})
    jurisdiction = Jurisdiction(
        country=j.get("country", "UNKNOWN"),
        legal_framework=j.get("legalFramework", "unknown"),
        cross_border_risk=j.get("crossBorderRisk", "unknown"),
        dispute_resolution=j.get("disputeResolution", "unknown"),
        kyc_available=j.get("kycAvailable", False),
        has_public_registry=j.get("hasPublicRegistry", False),
    )

    checklist = [
        ChecklistItem(
            category=c.get("category", ""),
            item=c.get("item", ""),
            status=c.get("status", ""),
            impact=c.get("impact", ""),
            fix=c.get("fix", ""),
        )
        for c in data.get("checklist", [])
    ]

    return CheckResult(
        check_id=data.get("checkId", ""),
        domain=data.get("domain", ""),
        trust_score=data.get("trustScore", 0),
        recommendation=data.get("recommendation", ""),
        confidence=data.get("confidence", "high"),
        caution_reason=data.get("cautionReason"),
        reasoning=data.get("reasoning", ""),
        scoring_model=data.get("scoringModel", ""),
        site_category=data.get("siteCategory", "consumer"),
        brand_tier=data.get("brandTier", "scored"),
        crawlability=data.get("crawlability", "ok"),
        flags=data.get("flags", []),
        signals=signals,
        jurisdiction=jurisdiction,
        checklist=checklist,
        checklist_summary=data.get("checklistSummary", {}),
        signature=data.get("signature", ""),
        signature_key_id=data.get("signatureKeyId", ""),
        issuer=data.get("issuer", ""),
        checked_at=data.get("checkedAt", ""),
        expires_at=data.get("expiresAt", ""),
        raw_response=data,
    )
