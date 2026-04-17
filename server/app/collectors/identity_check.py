"""Identity signal collector (v1: automated public data only).

Builds an identity score from multiple public signals:
- WHOIS registrant disclosure (GDPR-aware)
- SSL certificate organization (OV/EV certs)
- Publicly traded company (SEC EDGAR match)
- Contact information on the site
- Schema.org Organization structured data
- Tranco rank as identity proof
- ccTLD trust signals
"""

import re
from ..whois_util import safe_whois
from ..models.signals import IdentitySignal
from .public_company import is_public_company


_SCHEMA_ORG_PATTERN = re.compile(
    r'"@type"\s*:\s*"Organization"', re.IGNORECASE
)

# ccTLDs that require local presence or verified registration
# These registries already perform some identity verification
VERIFIED_CCTLDS = {
    ".de": 5,   # DENIC requires valid contact
    ".uk": 5,   # Nominet requires UK address
    ".fr": 5,   # AFNIC requires EU presence
    ".jp": 7,   # JPRS requires Japanese entity
    ".cn": 7,   # CNNIC requires Chinese entity
    ".au": 7,   # .au requires ABN
    ".nl": 5,   # SIDN requires valid contact
    ".se": 5,   # .SE requires valid contact
    ".ch": 5,   # SWITCH requires valid contact
    ".be": 5,   # DNS Belgium requires valid contact
    ".it": 5,   # NIC.it requires fiscal code
    ".br": 7,   # requires CPF/CNPJ
    ".kr": 7,   # requires Korean entity
    ".in": 5,   # requires Indian entity
}

# GDPR-affected TLDs where WHOIS redaction is legally required
# These domains should NOT be penalized for hidden WHOIS
GDPR_TLDS = {
    ".eu", ".de", ".fr", ".nl", ".it", ".es", ".pt", ".be", ".at",
    ".se", ".dk", ".fi", ".ie", ".pl", ".cz", ".ro", ".bg", ".hr",
    ".sk", ".si", ".lt", ".lv", ".ee", ".hu", ".mt", ".cy", ".lu",
    ".uk", ".ch", ".no", ".is", ".li",
}

# Major GDPR-compliant registrars (even for .com domains)
GDPR_REGISTRARS = [
    "gdpr", "redacted for privacy", "data protected",
    "not disclosed", "statutory masking",
]


def _whois_analysis(domain: str) -> dict:
    """Analyze WHOIS data with GDPR awareness."""
    try:
        w = safe_whois(domain)
        org = w.org or w.registrant_name or ""
        registrar = str(w.registrar or "").lower()

        if not org:
            # Check if domain is under a GDPR TLD
            for tld in GDPR_TLDS:
                if domain.lower().endswith(tld):
                    return {
                        "disclosed": False,
                        "gdpr_redacted": True,
                        "org": "",
                    }
            return {"disclosed": False, "gdpr_redacted": False, "org": ""}

        org_lower = org.lower()

        # Check if redaction is GDPR-related
        is_gdpr = any(term in org_lower for term in GDPR_REGISTRARS)
        if not is_gdpr:
            for tld in GDPR_TLDS:
                if domain.lower().endswith(tld):
                    is_gdpr = True
                    break

        privacy_terms = [
            "privacy", "proxy", "redacted", "whoisguard",
            "domains by proxy", "contact privacy", "withheld",
            "not disclosed", "data protected", "gdpr",
            "statutory masking", "identity protection",
        ]
        is_hidden = any(term in org_lower for term in privacy_terms)

        return {
            "disclosed": not is_hidden,
            "gdpr_redacted": is_gdpr and is_hidden,
            "org": org if not is_hidden else "",
        }
    except Exception:
        return {"disclosed": False, "gdpr_redacted": False, "org": ""}


def _get_cctld_bonus(domain: str) -> int:
    """Check if domain has a ccTLD that requires verified registration."""
    domain_lower = domain.lower()
    for tld, bonus in VERIFIED_CCTLDS.items():
        if domain_lower.endswith(tld):
            return bonus
    return 0


async def collect(
    domain: str,
    contact_on_site: bool = False,
    ssl_subject_org: str = "",
    page_body: str = "",
    has_ott_file: bool = False,
    tranco_rank: int | None = None,
) -> IdentitySignal:
    whois_info = _whois_analysis(domain)
    whois_disclosed = whois_info["disclosed"]
    gdpr_redacted = whois_info["gdpr_redacted"]

    # Check for OV/EV cert
    has_cert_org = bool(ssl_subject_org and len(ssl_subject_org) > 1)

    # Check for publicly traded company
    is_public = False
    if has_cert_org:
        is_public = await is_public_company(ssl_subject_org)

    # Check for schema.org Organization markup
    has_schema_org = bool(_SCHEMA_ORG_PATTERN.search(page_body)) if page_body else False

    # Build score from signals
    score = 0

    # Tranco rank as identity signal. Being in the top N means Cloudflare,
    # Umbrella, Majestic, and Quantcast have all seen real traffic to this
    # domain across billions of requests -- you can't fake it. Top 5K was
    # the old ceiling, which left top retailers like petco (rank ~8K) with
    # zero Tranco credit. Expanded to cover the full top-500K population
    # with a smoothly decreasing curve.
    if tranco_rank is not None:
        if tranco_rank <= 100:
            score += 25
        elif tranco_rank <= 1000:
            score += 20
        elif tranco_rank <= 5000:
            score += 15
        elif tranco_rank <= 10000:
            score += 12
        elif tranco_rank <= 50000:
            score += 8
        elif tranco_rank <= 100000:
            score += 5
        elif tranco_rank <= 500000:
            score += 3

    if has_ott_file:
        score += 20

    # WHOIS: reward disclosure, but don't penalize GDPR redaction
    if whois_disclosed:
        score += 15
    elif gdpr_redacted:
        # GDPR redaction is legally required, treat as neutral (+5 instead of +15)
        # They still get some credit because the domain is registered under
        # a GDPR jurisdiction, which itself implies a real entity
        score += 5

    # ccTLD bonus (registry already verified identity)
    cctld_bonus = _get_cctld_bonus(domain)
    if cctld_bonus:
        score += cctld_bonus

    if has_cert_org:
        score += 25

    if is_public:
        score += 10

    if contact_on_site:
        score += 10

    if has_schema_org:
        score += 5

    # Cap at 55 for automated (KYC tiers go higher)
    score = min(55, score)

    result = IdentitySignal(
        verified=False,
        verificationTier="automated",
        whoisDisclosed=whois_disclosed,
        businessDirectory=has_cert_org or has_schema_org or is_public,
        contactOnSite=contact_on_site,
        score=score,
    )
    result._is_public_company = is_public
    result._gdpr_redacted = gdpr_redacted
    result._cctld_bonus = cctld_bonus
    return result
