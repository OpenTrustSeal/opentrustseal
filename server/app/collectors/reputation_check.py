"""Reputation signal collector.

Combines multiple sources:
- Tranco top-sites ranking (strongest automated trust signal)
- Google Safe Browsing (malware/phishing detection)
- DNS-based blocklists: Spamhaus DBL, SURBL, URLhaus
"""

import os
import dns.resolver
import httpx
from ..models.signals import ReputationSignal
from . import tranco


def _load_env_file(path: str) -> dict:
    """Read a simple KEY=VALUE env file if present."""
    env: dict = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        except OSError:
            pass
    return env


_SPAMHAUS_ENV = _load_env_file("/etc/opentrustseal/spamhaus.env")
SPAMHAUS_DQS_KEY = _SPAMHAUS_ENV.get("SPAMHAUS_DQS_KEY") or os.environ.get("SPAMHAUS_DQS_KEY", "")


def _check_dnsbl(domain: str, blocklist: str) -> bool:
    """Check if a domain is listed in a DNS-based blocklist.

    These work by querying {domain}.{blocklist} as a DNS A record.
    If a record exists in a valid range, the domain is listed.

    Important: Spamhaus/SURBL return special IPs for control purposes:
    - 127.255.255.254 = test/rate-limit response (NOT a real listing)
    - 127.255.255.255 = not authorized (NOT a real listing)
    - 127.0.1.x, 127.0.0.x = actual listings

    We only count 127.0.0.x and 127.0.1.x as real listings.
    """
    try:
        answers = dns.resolver.resolve(f"{domain}.{blocklist}", "A")
        for rdata in answers:
            ip = rdata.to_text()
            # Only count 127.0.0.x and 127.0.1.x as real listings
            if ip.startswith("127.0.0.") or ip.startswith("127.0.1."):
                return True
        return False
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return False
    except Exception:
        return False


def _check_blocklists(domain: str) -> dict:
    """Check domain against multiple DNS blocklists.

    Spamhaus DBL requires a DQS key to query via public DNS resolvers.
    Without the key, Spamhaus returns 127.255.255.254 (not authorized)
    for every query, which the code treats as 'not listed' -- producing
    zero real Spamhaus data. With the key, queries route directly to
    Spamhaus's authoritative servers and return real listing data.

    Format without key: {domain}.dbl.spamhaus.org (BROKEN on public resolvers)
    Format with key:    {domain}.{key}.dbl.spamhaus.org (works everywhere)
    """
    # Use the DQS-keyed endpoint when a key is configured, otherwise
    # fall back to the unkeyed endpoint (which will likely return
    # 127.255.255.254 "not authorized" on public resolvers).
    #
    # DQS queries go to dq.spamhaus.net (not dbl.spamhaus.org). The
    # key sits between the queried domain and the blocklist zone:
    # {domain}.{key}.dbl.dq.spamhaus.net
    if SPAMHAUS_DQS_KEY:
        spamhaus_bl = f"{SPAMHAUS_DQS_KEY}.dbl.dq.spamhaus.net"
    else:
        spamhaus_bl = "dbl.spamhaus.org"

    results = {
        "spamhaus": _check_dnsbl(domain, spamhaus_bl),
        "surbl": _check_dnsbl(domain, "multi.surbl.org"),
        "urlhaus": _check_dnsbl(domain, "urlhaus.abuse.ch"),
    }
    results["listed"] = any(results.values())
    return results


async def _check_safe_browsing(domain: str) -> dict:
    """Query Google Safe Browsing API v4."""
    api_key = os.environ.get("GOOGLE_SAFE_BROWSING_KEY")
    if not api_key:
        return {"checked": False}

    url = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={api_key}"
    payload = {
        "client": {"clientId": "opentrustseal", "clientVersion": "0.2.0"},
        "threatInfo": {
            "threatTypes": [
                "MALWARE",
                "SOCIAL_ENGINEERING",
                "UNWANTED_SOFTWARE",
                "POTENTIALLY_HARMFUL_APPLICATION",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": f"https://{domain}/"}],
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            matches = data.get("matches", [])
            has_malware = any(
                m.get("threatType") in ("MALWARE", "POTENTIALLY_HARMFUL_APPLICATION")
                for m in matches
            )
            has_phishing = any(
                m.get("threatType") == "SOCIAL_ENGINEERING" for m in matches
            )
            return {"checked": True, "malware": has_malware, "phishing": has_phishing}
    except Exception:
        return {"checked": False}


async def collect(domain: str) -> ReputationSignal:
    # Ensure Tranco list is loaded
    await tranco.ensure_loaded()

    # Run Safe Browsing and blocklist checks
    sb_result = await _check_safe_browsing(domain)
    bl_result = _check_blocklists(domain)

    # Critical: any malware/phishing detection = score 0
    malware = sb_result.get("malware", False)
    phishing = sb_result.get("phishing", False)
    spam_listed = bl_result.get("listed", False)

    if malware or phishing:
        result = ReputationSignal(
            malware=True if malware else False,
            phishing=True if phishing else False,
            spamListed=spam_listed,
            score=0,
        )
        result._tranco_rank = tranco.get_rank(domain)
        result._blocklist_detail = bl_result
        return result

    if spam_listed:
        result = ReputationSignal(
            malware=False,
            phishing=False,
            spamListed=True,
            score=20,
        )
        result._tranco_rank = tranco.get_rank(domain)
        result._blocklist_detail = bl_result
        return result

    # Use Tranco ranking as primary reputation signal
    rank = tranco.get_rank(domain)
    tranco_score = tranco.rank_to_score(rank)

    if tranco_score >= 0:
        score = tranco_score
    elif sb_result.get("checked"):
        # Clean on Safe Browsing, not in Tranco
        score = 80
    else:
        # No data sources available
        score = 70

    result = ReputationSignal(
        malware=False,
        phishing=False,
        spamListed=False,
        score=score,
    )
    result._tranco_rank = rank
    result._blocklist_detail = bl_result
    return result
