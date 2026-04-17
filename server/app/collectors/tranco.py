"""Tranco top-sites list for reputation boosting.

The Tranco list is a research-grade ranking of the top 1M domains by traffic,
combining data from Alexa, Cisco Umbrella, Majestic, and Quantcast. It is
more stable and resistant to manipulation than any single source.

On first use, downloads the latest list and caches it to disk.
Refreshed weekly via the update() function.
"""

import os
import csv
import io
import time
from pathlib import Path

import httpx

DATA_DIR = Path(os.environ.get("OTT_DATA_DIR", "./data"))
TRANCO_FILE = DATA_DIR / "tranco.csv"
TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
MAX_AGE_SECONDS = 7 * 86400  # refresh weekly

_rank_cache: dict[str, int] = {}
_loaded = False


def _load():
    global _rank_cache, _loaded
    if not TRANCO_FILE.exists():
        return
    _rank_cache = {}
    with open(TRANCO_FILE, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                try:
                    _rank_cache[row[1].strip().strip('\r').lower()] = int(row[0].strip())
                except (ValueError, IndexError):
                    continue
    _loaded = True


def _needs_refresh() -> bool:
    if not TRANCO_FILE.exists():
        return True
    age = time.time() - TRANCO_FILE.stat().st_mtime
    return age > MAX_AGE_SECONDS


async def ensure_loaded():
    """Download the Tranco list if missing or stale, then load into memory."""
    global _loaded
    if _loaded and not _needs_refresh():
        return

    if _needs_refresh():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            import zipfile
            import io
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(TRANCO_URL)
                if resp.status_code == 200:
                    z = zipfile.ZipFile(io.BytesIO(resp.content))
                    data = z.read(z.namelist()[0]).decode("utf-8")
                    TRANCO_FILE.write_text(data)
        except Exception:
            if not TRANCO_FILE.exists():
                return

    _load()


def get_rank(domain: str) -> int | None:
    """Return the Tranco rank for a domain, or None if not in the top 1M."""
    if not _loaded:
        _load()
    domain = domain.strip().lower()
    # Check exact match first
    rank = _rank_cache.get(domain)
    if rank is not None:
        return rank
    # Check without www
    if domain.startswith("www."):
        return _rank_cache.get(domain[4:])
    # Check with www
    return _rank_cache.get("www." + domain)


def rank_to_score(rank: int | None) -> int:
    """Convert a Tranco rank to a reputation score using a log curve.

    Higher-ranked sites get proportionally more credit. The curve:
      score = 100 - 4 * log10(rank)

    Results:
      Rank 1:        100
      Rank 10:        97
      Rank 100:       94
      Rank 1,000:     91
      Rank 10,000:    88
      Rank 100,000:   85
      Rank 1,000,000: 82

    Floor is 82 (any site in Tranco top 1M has strong traffic).
    Sites not in Tranco return -1 (use other reputation signals).
    """
    if rank is None:
        return -1
    if rank <= 0:
        return 100

    import math
    score = 100 - 3 * math.log10(rank)
    return max(82, min(100, round(score)))
