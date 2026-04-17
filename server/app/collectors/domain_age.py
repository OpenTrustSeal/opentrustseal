"""Domain age signal collector using WHOIS with RDAP fallback.

python-whois has a hard-coded map of TLD -> WHOIS server. Some newer
gTLDs (notably .app and .dev, both operated by Google Registry) have
their WHOIS hostnames either missing or pointing at unreachable
endpoints, so python-whois returns creation_date=None silently. We fall
back to RDAP (RFC 7483), which is the modern, standardized JSON-over-HTTP
replacement for WHOIS that every gTLD registry must operate. The IANA
bootstrap at https://data.iana.org/rdap/dns.json maps every gTLD to its
authoritative RDAP base URL.

Country-code TLDs (.io, .so, .sh, etc) are NOT in the IANA bootstrap and
don't have centrally-mapped RDAP servers, but python-whois handles most
of them directly -- so the fallback only fires when python-whois gives
us nothing AND the TLD has an IANA-registered RDAP endpoint.

Also records WHOIS update metadata for observability. The
ownership-change heuristic used to drop the score on any "recently
updated" aged domain, but that produced false positives on every major
brand's routine admin churn (annual renewals, DNSSEC changes, contact
tweaks). True detection needs historical WHOIS snapshots, which is a
future integration. Until then this function just records raw dates and
always returns possibleOwnershipChange=False.
"""

import asyncio
import httpx
from ..whois_util import safe_whois
from datetime import datetime, timezone
from typing import Optional

from ..models.signals import DomainAgeSignal


_RDAP_BOOTSTRAP: Optional[dict[str, str]] = None
_RDAP_BOOTSTRAP_LOCK = asyncio.Lock()
_RDAP_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"


async def _load_rdap_bootstrap() -> dict[str, str]:
    """Fetch the IANA RDAP bootstrap registry once per process and cache
    in memory. Returns a mapping from lowercase TLD (without leading dot)
    to the RDAP base URL for that registry. Empty dict on failure; a
    populated cache sticks for the life of the process.
    """
    global _RDAP_BOOTSTRAP
    if _RDAP_BOOTSTRAP is not None:
        return _RDAP_BOOTSTRAP
    async with _RDAP_BOOTSTRAP_LOCK:
        if _RDAP_BOOTSTRAP is not None:
            return _RDAP_BOOTSTRAP
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(_RDAP_BOOTSTRAP_URL)
            data = r.json()
        except Exception:
            return {}
        mapping: dict[str, str] = {}
        for service in data.get("services", []):
            if len(service) < 2:
                continue
            tlds, servers = service[0], service[1]
            if not servers:
                continue
            base = servers[0].rstrip("/")
            for tld in tlds:
                mapping[tld.lower()] = base
        _RDAP_BOOTSTRAP = mapping
        return mapping


def _parse_rdap_date(date_str: str) -> Optional[datetime]:
    """Parse an RDAP event date (ISO 8601 with optional Z suffix) into a
    tz-aware datetime. Returns None on any parsing failure so the caller
    falls through gracefully."""
    if not date_str:
        return None
    try:
        if date_str.endswith("Z"):
            dt = datetime.fromisoformat(date_str[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(date_str)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _rdap_lookup(domain: str) -> Optional[dict]:
    """Fetch domain metadata via RDAP. Returns a dict with keys
    creation_date and updated_date (datetimes or None), or None if the
    TLD has no registered RDAP endpoint or the lookup fails.
    """
    tld = domain.rsplit(".", 1)[-1].lower()
    mapping = await _load_rdap_bootstrap()
    base = mapping.get(tld)
    if not base:
        return None
    url = f"{base}/domain/{domain}"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"Accept": "application/rdap+json"})
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    creation_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    for event in data.get("events", []):
        action = (event.get("eventAction") or "").lower()
        parsed = _parse_rdap_date(event.get("eventDate", ""))
        if parsed is None:
            continue
        if action == "registration" and creation_date is None:
            creation_date = parsed
        elif action in ("last changed", "last update", "last updated") and updated_date is None:
            updated_date = parsed
    return {
        "creation_date": creation_date,
        "updated_date": updated_date,
    }


def _days_since(date_val) -> int:
    if date_val is None:
        return 0
    if isinstance(date_val, list):
        date_val = date_val[0]
    if isinstance(date_val, datetime):
        if date_val.tzinfo is None:
            date_val = date_val.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - date_val
        return max(0, delta.days)
    return 0


def _score_from_days(days: int) -> int:
    if days <= 30:
        return 0
    if days <= 90:
        return 20
    if days <= 180:
        return 40
    if days <= 365:
        return 60
    if days <= 730:
        return 75
    if days <= 1825:
        return 90
    return 100


def _band_from_days(days: int) -> str:
    if days <= 30:
        return "< 30 days"
    if days <= 90:
        return "1-3 months"
    if days <= 180:
        return "3-6 months"
    if days <= 365:
        return "6-12 months"
    if days <= 730:
        return "1-2 years"
    if days <= 1825:
        return "2-5 years"
    return "5+ years"


def _normalize_date(raw) -> Optional[datetime]:
    """Coerce python-whois date output (which may be None, datetime, or
    list of datetimes) into a single datetime or None."""
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, datetime):
        return raw
    return None


async def collect(domain: str) -> DomainAgeSignal:
    creation: Optional[datetime] = None
    updated: Optional[datetime] = None

    # Tier 1: python-whois. Works for .com and most ccTLDs. Fails on
    # .app / .dev (Google Registry) where it returns creation_date=None
    # silently.
    try:
        w = safe_whois(domain)
        creation = _normalize_date(w.creation_date)
        updated = _normalize_date(w.updated_date)
    except Exception:
        pass

    # Tier 2: RDAP fallback via IANA bootstrap. Fires when python-whois
    # gave us nothing AND the TLD has a registered RDAP endpoint. gTLDs
    # are covered; ccTLDs are not (but those usually work on tier 1).
    if creation is None:
        rdap = await _rdap_lookup(domain)
        if rdap:
            if rdap.get("creation_date"):
                creation = rdap["creation_date"]
            if updated is None and rdap.get("updated_date"):
                updated = rdap["updated_date"]

    # Final fallback: neither tier produced a creation date. Return a
    # failure signal so the pipeline knows this domain has no usable age
    # data. Scoring will treat it as unknown (score 0) rather than new.
    if creation is None:
        result = DomainAgeSignal(band="unknown", score=0)
        result._registrant_change = {
            "recentlyUpdated": False,
            "updatedDate": None,
            "possibleOwnershipChange": False,
        }
        return result

    days = _days_since(creation)
    registered_date = creation.strftime("%Y-%m-%d")

    # Ownership-change heuristic disabled until historical WHOIS is
    # integrated. See module docstring. We still record updatedDate for
    # observability and for the future heuristic to consume.
    updated_date_str: Optional[str] = None
    if isinstance(updated, datetime):
        updated_date_str = updated.strftime("%Y-%m-%d")

    change_info = {
        "recentlyUpdated": False,
        "updatedDate": updated_date_str,
        "possibleOwnershipChange": False,
    }

    result = DomainAgeSignal(
        registeredDate=registered_date,
        band=_band_from_days(days),
        score=_score_from_days(days),
    )
    result._registrant_change = change_info
    return result
