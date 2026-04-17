"""Publicly traded company detection via SEC EDGAR.

Uses the free SEC EDGAR full-text search API to check if a company name
(from an OV/EV SSL certificate) matches a publicly traded entity.

Anti-spoofing: This signal only fires when the SSL certificate's
organization name (verified by a Certificate Authority) matches a
company in the SEC database. A scam site cannot get an OV/EV cert
with a false organization name because CAs verify against business
registration records.
"""

import httpx


# Cache results to avoid repeat API calls
_cache: dict[str, bool] = {}


async def is_public_company(org_name: str) -> bool:
    """Check if an organization name matches a SEC-registered entity.

    Returns True only for strong matches. Returns False on any error
    or ambiguity (fail-safe).
    """
    if not org_name or len(org_name) < 3:
        return False

    # Normalize
    org_clean = org_name.strip().lower()

    # Skip generic names that would false-positive
    skip = ["inc", "llc", "ltd", "corp", "corporation", "company"]
    if org_clean in skip:
        return False

    if org_clean in _cache:
        return _cache[org_clean]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # SEC EDGAR company search (free, no key needed)
            resp = await client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={"q": org_name, "dateRange": "custom", "startdt": "2020-01-01"},
                headers={"User-Agent": "OpenTrustSeal/0.1 (info@opentrustseal.com)"},
            )

            if resp.status_code != 200:
                # Try the simpler company tickers endpoint
                resp2 = await client.get(
                    f"https://www.sec.gov/cgi-bin/browse-edgar?company={org_name}&CIK=&type=10-K&dateb=&owner=include&count=5&search_text=&action=getcompany",
                    headers={"User-Agent": "OpenTrustSeal/0.1 (info@opentrustseal.com)"},
                )
                # If we get results page with matches, company exists
                is_match = resp2.status_code == 200 and "Results" in resp2.text and org_name.split()[0].lower() in resp2.text.lower()
                _cache[org_clean] = is_match
                return is_match

            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                _cache[org_clean] = False
                return False

            # Check if any hit's company name closely matches
            for hit in hits[:5]:
                source = hit.get("_source", {})
                company = source.get("company_name", "").lower()
                # Fuzzy match: check if our org name words appear in SEC company name
                org_words = set(org_clean.replace(",", "").split())
                org_words -= {"inc", "inc.", "llc", "ltd", "corp", "corporation", "co", "co."}
                if not org_words:
                    continue
                match_count = sum(1 for w in org_words if w in company)
                if match_count >= len(org_words) * 0.6:
                    _cache[org_clean] = True
                    return True

            _cache[org_clean] = False
            return False

    except Exception:
        _cache[org_clean] = False
        return False
