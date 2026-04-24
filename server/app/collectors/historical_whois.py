"""Historical WHOIS collector.

Detects recent registrant-change events by querying a historical-WHOIS
provider. This is the single strongest defense against the expired-
domain-purchase attack: a scammer buys a dropped 10-year-old domain,
inherits its Tranco age + clean reputation, and rides our well-known
brand anchor. Current-WHOIS alone can't see this; you need history.

Ships dark. The collector is a no-op when HISTORICAL_WHOIS_ENABLED is
false or when no API key is configured. That lets the code land in prod
before the external account/billing is set up.

Provider support:
- whoisxmlapi (https://whoisxmlapi.com/): UUID API key in URL params.
  Starter tier ~$20/mo for ~1000 queries, more than enough for our daily
  re-crawl. Coverage back to ~2010.
- securitytrails (https://securitytrails.com/): X-API-Key header. Different
  response shape; not implemented here yet.

To enable:
  # /etc/opentrustseal/historical_whois.env (mode 640 root:ott)
  HISTORICAL_WHOIS_ENABLED=true
  HISTORICAL_WHOIS_PROVIDER=whoisxmlapi
  HISTORICAL_WHOIS_API_KEY=<uuid>
  # Threshold: flag a registrant change that happened <N days ago.
  # 90 is a reasonable default; shorter = fewer false positives, longer
  # = better coverage of slow-moving fraud.
  HISTORICAL_WHOIS_RECENT_DAYS=90
"""

import os
from dataclasses import dataclass
from typing import Optional

import httpx


def _load_env_file(path: str) -> dict:
    try:
        result = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip()
        return result
    except Exception:
        return {}


_ENV = _load_env_file("/etc/opentrustseal/historical_whois.env")
HISTORICAL_WHOIS_ENABLED = _ENV.get("HISTORICAL_WHOIS_ENABLED", "false").lower() in ("1", "true", "yes", "on")
HISTORICAL_WHOIS_PROVIDER = _ENV.get("HISTORICAL_WHOIS_PROVIDER", "").lower().strip()
HISTORICAL_WHOIS_API_KEY = _ENV.get("HISTORICAL_WHOIS_API_KEY", "").strip()
HISTORICAL_WHOIS_RECENT_DAYS = int(_ENV.get("HISTORICAL_WHOIS_RECENT_DAYS", "90"))
HISTORICAL_WHOIS_TIMEOUT_S = float(_ENV.get("HISTORICAL_WHOIS_TIMEOUT_S", "30"))


@dataclass
class HistoricalWhoisSignal:
    """Result of a historical WHOIS query.

    Fields:
        enabled: True if the collector actually made a network call.
                 False when feature-flagged off or no API key; callers
                 should treat False as "we don't know" not "no change."
        queried: True if we made a live call to the provider this pass.
                 False if we returned cached or empty result.
        record_count: Number of historical WHOIS records available for
                      this domain. 0 means the provider has no history.
        earliest_seen: ISO date of the oldest WHOIS record the provider has.
        registrant_changed_recently: True if a registrant field value
                                     changed within HISTORICAL_WHOIS_RECENT_DAYS
                                     days. Primary anti-fraud signal.
        recent_change_at: ISO date of the most recent registrant change.
        current_registrant: Most recent registrant email/name (may be
                            redacted on GDPR zones).
        previous_registrant: Second-most-recent registrant email/name.
        error: Short description of any failure; None on success.
    """
    enabled: bool = False
    queried: bool = False
    record_count: int = 0
    earliest_seen: Optional[str] = None
    registrant_changed_recently: bool = False
    recent_change_at: Optional[str] = None
    current_registrant: Optional[str] = None
    previous_registrant: Optional[str] = None
    error: Optional[str] = None


async def collect(domain: str) -> HistoricalWhoisSignal:
    """Look up historical WHOIS for a domain via the configured provider.

    Returns a HistoricalWhoisSignal. When the feature is disabled or no
    key is configured, returns a sentinel with enabled=False and
    everything else empty. Callers treat that as "no data" not
    "no change."
    """
    if not HISTORICAL_WHOIS_ENABLED or not HISTORICAL_WHOIS_API_KEY:
        return HistoricalWhoisSignal(enabled=False)

    if HISTORICAL_WHOIS_PROVIDER == "whoisxmlapi":
        return await _collect_whoisxmlapi(domain)

    return HistoricalWhoisSignal(
        enabled=False,
        error=f"unknown provider: {HISTORICAL_WHOIS_PROVIDER}",
    )


async def _collect_whoisxmlapi(domain: str) -> HistoricalWhoisSignal:
    """WhoisXMLAPI Historical WHOIS adapter.

    Endpoint: https://whois-history.whoisxmlapi.com/api/v1
    Auth: apiKey query param.
    Response shape (trimmed):
        {
          "recordsCount": 4,
          "records": [
            {"auditUpdatedDate": "2024-01-15 ...", "registrarName": "...",
             "registrantContact": {"email": "...", "name": "...", ...}, ...},
            ...
          ]
        }
    """
    from datetime import datetime, timezone, timedelta

    url = "https://whois-history.whoisxmlapi.com/api/v1"
    params = {
        "apiKey": HISTORICAL_WHOIS_API_KEY,
        "domainName": domain,
        "mode": "purchase",  # returns full records; "preview" returns count only
        "outputFormat": "JSON",
    }

    try:
        async with httpx.AsyncClient(timeout=HISTORICAL_WHOIS_TIMEOUT_S) as client:
            r = await client.get(url, params=params)
        if r.status_code != 200:
            return HistoricalWhoisSignal(
                enabled=True, queried=True,
                error=f"http {r.status_code}",
            )
        data = r.json()
    except Exception as e:
        return HistoricalWhoisSignal(
            enabled=True, queried=True,
            error=f"{type(e).__name__}: {str(e)[:100]}",
        )

    records = data.get("records") or []
    count = int(data.get("recordsCount", len(records)))

    if count == 0:
        return HistoricalWhoisSignal(enabled=True, queried=True, record_count=0)

    # Sort newest-first by the audit update date
    def _parse_date(s: str) -> datetime:
        try:
            return datetime.fromisoformat((s or "").replace("Z", "+00:00").split(" ")[0])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    records_sorted = sorted(
        records,
        key=lambda r: _parse_date(r.get("auditUpdatedDate", "")),
        reverse=True,
    )

    earliest = records_sorted[-1].get("auditUpdatedDate") if records_sorted else None
    current = records_sorted[0] if records_sorted else {}
    previous = records_sorted[1] if len(records_sorted) >= 2 else {}

    def _registrant_fingerprint(rec: dict) -> str:
        c = rec.get("registrantContact") or {}
        return "|".join(str(c.get(k, "")).lower().strip() for k in ("email", "name", "organization"))

    current_fp = _registrant_fingerprint(current)
    previous_fp = _registrant_fingerprint(previous)

    changed_recently = False
    recent_change_at = None
    if current_fp and previous_fp and current_fp != previous_fp:
        change_date = current.get("auditUpdatedDate")
        if change_date:
            recent_change_at = change_date
            try:
                d = _parse_date(change_date).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - d < timedelta(days=HISTORICAL_WHOIS_RECENT_DAYS):
                    changed_recently = True
            except Exception:
                pass

    return HistoricalWhoisSignal(
        enabled=True,
        queried=True,
        record_count=count,
        earliest_seen=earliest,
        registrant_changed_recently=changed_recently,
        recent_change_at=recent_change_at,
        current_registrant=(current.get("registrantContact") or {}).get("email"),
        previous_registrant=(previous.get("registrantContact") or {}).get("email"),
    )


def stats() -> dict:
    """Surface current configuration in /stats for operational visibility."""
    return {
        "historical_whois_enabled": HISTORICAL_WHOIS_ENABLED,
        "historical_whois_provider": HISTORICAL_WHOIS_PROVIDER or None,
        "historical_whois_recent_days": HISTORICAL_WHOIS_RECENT_DAYS,
    }
