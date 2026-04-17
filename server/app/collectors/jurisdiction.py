"""Jurisdiction detection for domains.

Determines the likely country and legal framework from:
1. ccTLD (strongest signal)
2. WHOIS registrant country
3. SSL cert country (from OV/EV certs)
4. Server IP geolocation (future)

Also assesses cross-border risk factors for agent decision-making.
"""

# ccTLD to country mapping (ISO 3166-1 alpha-2)
CCTLD_MAP = {
    ".us": "US", ".uk": "GB", ".co.uk": "GB", ".de": "DE", ".fr": "FR",
    ".jp": "JP", ".cn": "CN", ".kr": "KR", ".au": "AU", ".ca": "CA",
    ".br": "BR", ".in": "IN", ".ru": "RU", ".it": "IT", ".es": "ES",
    ".nl": "NL", ".se": "SE", ".no": "NO", ".dk": "DK", ".fi": "FI",
    ".ch": "CH", ".at": "AT", ".be": "BE", ".pt": "PT", ".pl": "PL",
    ".cz": "CZ", ".ro": "RO", ".hu": "HU", ".ie": "IE", ".nz": "NZ",
    ".za": "ZA", ".mx": "MX", ".ar": "AR", ".cl": "CL", ".co": "CO",
    ".il": "IL", ".sg": "SG", ".hk": "HK", ".tw": "TW", ".th": "TH",
    ".my": "MY", ".id": "ID", ".ph": "PH", ".vn": "VN", ".tr": "TR",
    ".ae": "AE", ".sa": "SA", ".eu": "EU",
}

# Legal framework categories
US_FRAMEWORK = {"US", "CA"}
EU_FRAMEWORK = {
    "GB", "DE", "FR", "IT", "ES", "NL", "SE", "NO", "DK", "FI",
    "CH", "AT", "BE", "PT", "PL", "CZ", "RO", "HU", "IE", "EU",
    "BG", "HR", "SK", "SI", "LT", "LV", "EE", "MT", "CY", "LU",
    "IS", "LI",
}
APAC_FRAMEWORK = {
    "JP", "KR", "AU", "NZ", "SG", "HK", "TW",
}

# Countries with strong dispute resolution for cross-border commerce
STRONG_DISPUTE = US_FRAMEWORK | EU_FRAMEWORK | APAC_FRAMEWORK | {"IL"}

# Countries with accessible public business registries
HAS_PUBLIC_REGISTRY = {
    "US", "GB", "DE", "FR", "NL", "AU", "CA", "SE", "NO", "DK",
    "FI", "IE", "NZ", "SG", "HK", "JP", "KR", "IN", "BR", "IT",
    "ES", "BE", "CH", "AT",
}


def detect_jurisdiction(
    domain: str,
    whois_country: str = "",
    ssl_country: str = "",
) -> dict:
    """Detect jurisdiction and assess cross-border risk."""

    country = None

    # 1. ccTLD is strongest signal
    domain_lower = domain.lower()
    for tld, cc in sorted(CCTLD_MAP.items(), key=lambda x: -len(x[0])):
        if domain_lower.endswith(tld):
            country = cc
            break

    # 2. WHOIS country as fallback
    if not country and whois_country:
        country = whois_country.upper().strip()
        if len(country) > 2:
            country = None

    # 3. SSL cert country as last resort
    if not country and ssl_country:
        country = ssl_country.upper().strip()
        if len(country) > 2:
            country = None

    # 4. Generic TLDs (.com, .org, .net) default to unknown
    if not country:
        country = "UNKNOWN"

    # Determine legal framework
    if country in US_FRAMEWORK:
        legal_framework = "US"
    elif country in EU_FRAMEWORK:
        legal_framework = "EU/EEA"
    elif country in APAC_FRAMEWORK:
        legal_framework = "APAC"
    elif country == "UNKNOWN":
        legal_framework = "unknown"
    else:
        legal_framework = "other"

    # Cross-border risk assessment
    if country == "UNKNOWN":
        cross_border_risk = "unknown"
        dispute_resolution = "unknown"
    elif country in STRONG_DISPUTE:
        cross_border_risk = "standard"
        dispute_resolution = "established"
    else:
        cross_border_risk = "elevated"
        dispute_resolution = "limited"

    # KYC availability
    kyc_available = country in HAS_PUBLIC_REGISTRY or country == "UNKNOWN"

    return {
        "country": country,
        "legalFramework": legal_framework,
        "crossBorderRisk": cross_border_risk,
        "disputeResolution": dispute_resolution,
        "kycAvailable": kyc_available,
        "hasPublicRegistry": country in HAS_PUBLIC_REGISTRY,
    }
