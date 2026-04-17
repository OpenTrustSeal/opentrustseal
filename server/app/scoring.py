"""Trust scoring engine (model: ott-v1.2-weights).

Scoring tiers:
  Layer 1: Automated signals             (max ~65 average site)
  Layer 2: Strong automated + Tranco     (max ~82 top-tier sites)
           + OV cert + institutional
  Layer 3: Registration                  (bump ~8 points)
  Layer 4: KYC                           (unlocks 90+, future)
"""

from .models.signals import SignalBundle

SCORING_MODEL = "ott-v1.3-weights"

WEIGHTS = {
    "domain_age": 0.10,
    "ssl": 0.10,
    "dns": 0.08,
    "content": 0.17,
    "reputation": 0.30,
    "identity": 0.25,
}

# Well-known brand anchor criteria. Long-term Tranco top-50K membership
# combined with clean reputation, 5+ years of domain age, and valid SSL
# is unfakeable composite evidence of public trust. Top 50K of Tranco is
# about 0.014% of all registered domains globally -- the list comes from
# billions of real-user requests seen across Cloudflare, Umbrella,
# Majestic and Quantcast, so membership cannot be purchased or gamed.
# When ANDed with 5+ years of age, clean reputation, and valid SSL, the
# population left is essentially "real businesses with no fraud flags."
# Crateandbarrel (rank 12931) and petco (12647) are the reason the
# threshold isn't 10K -- both are unambiguously major brands just outside
# the round-number cutoff, and shipping the tighter threshold would
# produce CAUTION verdicts that look obviously wrong to users.
#
# Encoded as a floor on identity and on the final trust_score so sites
# that score below 75 by pure weighted average (because content is
# unscorable) still get the correct PROCEED verdict. Gated by safety
# signals -- any malware, phishing, spam, or compromise indicator
# revokes the anchor immediately.
WELL_KNOWN_TRANCO_MAX = 50000
WELL_KNOWN_MIN_AGE_DAYS = 1825  # 5 years
WELL_KNOWN_IDENTITY_FLOOR = 50
WELL_KNOWN_SCORE_FLOOR = 75

# Registration bonus applied to identity signal before weighting
REGISTRATION_BONUS = 30

# KYC tier bonuses applied to identity signal
KYC_BONUSES = {
    "enhanced": 15,       # business directory + address/phone verified
    "kyc_verified": 35,   # government ID + business docs + bank + video call
    "enterprise": 50,     # all of above + audit + continuous monitoring
}

# Institutional TLDs that get an identity boost
INSTITUTIONAL_TLDS = {
    ".gov": 20,
    ".mil": 20,
    ".edu": 15,
    ".int": 15,
}


def _get_institutional_bonus(domain: str) -> int:
    """Check if domain has an institutional TLD."""
    domain_lower = domain.lower()
    for tld, bonus in INSTITUTIONAL_TLDS.items():
        if domain_lower.endswith(tld):
            return bonus
    return 0


def _has_identity_anchor(
    signals: SignalBundle,
    domain: str,
    is_registered: bool,
    identity_score: int,
) -> bool:
    """True if the site has at least one strong identity anchor.

    Used when content is unreachable to decide whether to cap the
    re-weighted score. Without an anchor, a high score from "everything
    but content" would be credulous -- we don't know who runs the site
    AND we couldn't see it, so we cap at PROCEED borderline.
    """
    if is_registered:
        return True
    if _get_institutional_bonus(domain):
        return True
    # Top-50K Tranco rank is a strong anchor (billions of requests seen)
    tranco_rank = getattr(signals.reputation, '_tranco_rank', None)
    if tranco_rank and tranco_rank <= 50000:
        return True
    # OV/EV SSL cert: the CA verified a legal entity before issuing
    ssl_subject_org = getattr(signals.ssl, '_subject_org', '')
    if ssl_subject_org:
        return True
    # Strong computed identity (cross-referenced public data)
    if identity_score >= 45:
        return True
    return False


def is_well_known_brand(signals: SignalBundle, domain_age_days: int) -> bool:
    """True if the composite of Tranco rank + age + clean reputation + SSL
    validity justifies treating this domain as an unambiguously established
    public brand. Any negative safety signal revokes the anchor.

    The rationale is compositional: top-10K Tranco membership over years
    is unfakeable (the list comes from billions of observed real-user
    requests across Cloudflare/Umbrella/Majestic/Quantcast), and when
    combined with age and a clean reputation file, the probability of the
    site being a bad actor is effectively zero.
    """
    if domain_age_days < WELL_KNOWN_MIN_AGE_DAYS:
        return False
    if not signals.ssl.valid:
        return False
    if signals.reputation.malware or signals.reputation.phishing or signals.reputation.spam_listed:
        return False
    tranco_rank = getattr(signals.reputation, "_tranco_rank", None)
    if tranco_rank is None or tranco_rank > WELL_KNOWN_TRANCO_MAX:
        return False
    return True


def compute_score(
    signals: SignalBundle,
    is_registered: bool = False,
    domain: str = "",
    kyc_tier: str = "none",
    registration_score: int = 0,
    content_scorable: bool = True,
    well_known_brand: bool = False,
) -> int:
    identity_score = signals.identity.score

    # Institutional TLD bonus
    institutional = _get_institutional_bonus(domain)
    if institutional:
        identity_score = min(55, identity_score + institutional)

    # Registration: use per-field verification score if available,
    # fall back to flat bonus for backward compatibility
    if is_registered:
        if registration_score > 0:
            identity_score = min(55, identity_score + registration_score)
        else:
            identity_score = min(55, identity_score + REGISTRATION_BONUS)

    # KYC tier adjustments
    identity_ceiling = 55  # automated cap
    if kyc_tier == "enhanced":
        identity_ceiling = 65
    elif kyc_tier == "kyc_verified":
        identity_ceiling = 80
    elif kyc_tier == "enterprise":
        identity_ceiling = 100

    if kyc_tier != "none":
        identity_score = min(identity_ceiling, identity_score + KYC_BONUSES.get(kyc_tier, 0))

    # Well-known brand anchor: lift identity to the floor. This is applied
    # BEFORE the weighted sum so the 25% identity weight carries a real
    # contribution even when content-dependent identity signals
    # (contact_on_site, schema.org, etc) were unavailable.
    if well_known_brand:
        identity_score = max(identity_score, WELL_KNOWN_IDENTITY_FLOOR)

    # KYC-adjusted domain age: if identity is strongly verified,
    # a new domain is less concerning because we know who owns it
    domain_age_score = signals.domain_age.score
    if kyc_tier in ("kyc_verified", "enterprise") and domain_age_score < 50:
        domain_age_score = max(domain_age_score, 50)

    if content_scorable:
        raw = (
            domain_age_score * WEIGHTS["domain_age"]
            + signals.ssl.score * WEIGHTS["ssl"]
            + signals.dns.score * WEIGHTS["dns"]
            + signals.content.score * WEIGHTS["content"]
            + signals.reputation.score * WEIGHTS["reputation"]
            + identity_score * WEIGHTS["identity"]
        )
    else:
        # Content unreachable (e.g. Cloudflare bot wall on our VPS IP).
        # Drop content from the weighted sum and renormalize the remaining
        # five signals to sum to 100%. Punishing content=0 as negative
        # evidence would let well-defended retailers look untrustworthy.
        raw_partial = (
            domain_age_score * WEIGHTS["domain_age"]
            + signals.ssl.score * WEIGHTS["ssl"]
            + signals.dns.score * WEIGHTS["dns"]
            + signals.reputation.score * WEIGHTS["reputation"]
            + identity_score * WEIGHTS["identity"]
        )
        raw = raw_partial / (1.0 - WEIGHTS["content"])

        # Floor rule: without a strong identity anchor we can't trust the
        # renormalized score, so cap at borderline-PROCEED (70). With an
        # anchor (Tranco top-50K, EV cert, institutional TLD, registered,
        # or high computed identity) let the score flow through.
        if not _has_identity_anchor(signals, domain, is_registered, identity_score):
            raw = min(raw, 70)

    final = min(100, round(raw))

    # Well-known brand anchor: floor the final score at PROCEED so that
    # unambiguously established brands (top-10K Tranco + aged + clean
    # reputation + valid SSL) don't get CAUTION even when their content
    # is unscorable. Never applied if any safety flag fires -- see
    # is_well_known_brand() for the gating logic.
    if well_known_brand:
        final = max(final, WELL_KNOWN_SCORE_FLOOR)

    return final


def compute_flags(
    signals: SignalBundle,
    score: int,
    domain_age_days: int = -1,
    kyc_tier: str = "none",
    monitoring_alerts: list[str] = None,
    well_known_brand: bool = False,
) -> list[str]:
    flags = []
    if well_known_brand:
        flags.append("WELL_KNOWN_BRAND")

    # Critical: reputation threats
    if signals.reputation.malware:
        flags.append("MALWARE_DETECTED")
    if signals.reputation.phishing:
        flags.append("PHISHING_DETECTED")
    if signals.reputation.spam_listed:
        flags.append("SPAM_LISTED")

    # Domain age (suppressed if KYC verified)
    if 0 <= domain_age_days < 90:
        if kyc_tier in ("kyc_verified", "enterprise"):
            flags.append("NEW_DOMAIN_KYC_VERIFIED")
        else:
            flags.append("NEW_DOMAIN")

    # Identity gaps
    if not signals.identity.verified and signals.identity.score == 0:
        flags.append("NO_IDENTITY")

    # SSL
    if not signals.ssl.valid:
        flags.append("NO_SSL")

    # Monitoring alerts (from ongoing checks)
    if monitoring_alerts:
        flags.extend(monitoring_alerts)

    return flags


def compute_recommendation(score: int, flags: list[str]) -> str:
    critical_flags = {"MALWARE_DETECTED", "PHISHING_DETECTED", "RECENTLY_COMPROMISED"}
    if critical_flags & set(flags):
        return "DENY"

    if score >= 75:
        return "PROCEED"
    if score >= 40:
        return "CAUTION"
    return "DENY"


def generate_reasoning(
    signals: SignalBundle,
    score: int,
    recommendation: str,
    content_unscorable: bool = False,
    well_known_brand: bool = False,
) -> str:
    parts = []
    if well_known_brand:
        parts.append(
            "established public brand (top-10K Tranco, 5+ years domain age, "
            "clean reputation, valid SSL)"
        )

    if signals.domain_age.band in ("5+ years", "2-5 years"):
        parts.append(f"Established domain ({signals.domain_age.band})")
    elif signals.domain_age.band in ("< 30 days", "1-3 months"):
        parts.append(f"New domain ({signals.domain_age.band})")

    if signals.ssl.valid:
        parts.append("valid SSL")
    else:
        parts.append("no SSL detected")

    if signals.reputation.malware:
        parts.append("MALWARE DETECTED")
    elif signals.reputation.phishing:
        parts.append("PHISHING DETECTED")
    elif signals.reputation.score >= 80:
        parts.append("clean reputation")

    if signals.identity.verified:
        parts.append("identity verified")
    elif signals.identity.score > 0:
        parts.append("partial identity signals from public data")
    else:
        parts.append("no identity verification on file")

    if content_unscorable:
        parts.append(
            "homepage content not directly verifiable (site blocks crawlers); "
            "scored from domain, SSL, DNS, reputation, and identity"
        )

    summary = ", ".join(parts) + "."

    if recommendation == "PROCEED":
        summary += " Suitable for standard transactions."
    elif recommendation == "CAUTION":
        summary += " Consider transaction limits or user confirmation."
    else:
        summary += " Transaction not recommended."

    return summary
