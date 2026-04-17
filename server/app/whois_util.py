"""Shared wrapper for python-whois that suppresses stderr noise.

python-whois prints socket connection errors to stderr when connecting
to certain ccTLD WHOIS servers (e.g., .ch, .de, .il). These errors are
cosmetic: the library falls back to other servers and still produces
results. But the stderr noise clutters crawl logs and makes it hard to
spot real errors.

Usage:
    from app.whois_util import safe_whois
    w = safe_whois("example.com")
    # w is the same whois.WhoisEntry that whois.whois() returns
"""

import os
import sys
import whois


def safe_whois(domain: str):
    """Call whois.whois(domain) with stderr suppressed."""
    old_stderr = sys.stderr
    try:
        sys.stderr = open(os.devnull, "w")
        return whois.whois(domain)
    finally:
        sys.stderr.close()
        sys.stderr = old_stderr
