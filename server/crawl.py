#!/usr/bin/env python3
"""Batch crawler for seeding the OpenTrustSeal registry.

Usage:
    python3 crawl.py                    # Crawl default domain list
    python3 crawl.py domains.txt        # Crawl from file (one domain per line)
    python3 crawl.py --top 500          # Crawl top N from Tranco list

Runs checks sequentially with a delay to avoid hammering targets.
Results are stored in the database with raw signals for re-scoring.

Run on the VPS for best performance (avoids rate limiting your home IP).
"""

import asyncio
import sys
import time
import json
import os

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.dirname(__file__))

from app.pipeline import run_check
from app.database import init_db, store_check, store_raw_signals, get_stats
from app.signing import ensure_keys
from app.collectors.tranco import ensure_loaded, _rank_cache, _loaded, _load, TRANCO_FILE


# Diverse domain list spanning categories for calibration
DEFAULT_DOMAINS = [
    # Top tech (should score very high)
    "google.com", "apple.com", "microsoft.com", "amazon.com", "github.com",
    "stripe.com", "shopify.com", "cloudflare.com", "openai.com", "netflix.com",

    # Major e-commerce (should score high)
    "ebay.com", "etsy.com", "walmart.com", "target.com", "bestbuy.com",
    "homedepot.com", "costco.com", "nike.com", "adidas.com", "nordstrom.com",

    # Financial services (should score very high)
    "chase.com", "bankofamerica.com", "paypal.com", "square.com", "wise.com",
    "coinbase.com", "robinhood.com", "fidelity.com", "schwab.com", "ally.com",

    # Healthcare (should score medium-high)
    "webmd.com", "mayoclinic.org", "clevelandclinic.org", "zocdoc.com",
    "healthline.com", "medlineplus.gov", "nih.gov", "cdc.gov",

    # Small business / niche (varied scores)
    "scosi.com", "ossm.com", "basecamp.com", "notion.so", "linear.app",
    "fly.io", "railway.app", "render.com", "vercel.com", "supabase.com",

    # Media / publishing
    "nytimes.com", "washingtonpost.com", "bbc.com", "reuters.com", "cnn.com",
    "techcrunch.com", "arstechnica.com", "theverge.com", "wired.com",

    # Education
    "mit.edu", "stanford.edu", "harvard.edu", "coursera.org", "khanacademy.org",

    # Crypto / AI agent related
    "coinbase.com", "binance.com", "uniswap.org", "aave.com",
    "anthropic.com", "huggingface.co", "langchain.com",

    # International e-commerce
    "mercadolibre.com", "rakuten.co.jp", "zalando.de", "asos.com",
    "alibaba.com", "jd.com",

    # Known sketchy TLD patterns (should score low)
    "example.xyz", "test123.click", "free-stuff-now.win",

    # Government
    "usa.gov", "whitehouse.gov", "data.gov", "irs.gov",

    # Non-profit
    "wikipedia.org", "mozilla.org", "eff.org", "archive.org",
    "letsencrypt.org", "apache.org",
]


def get_tranco_top(n: int) -> list[str]:
    """Get top N domains from Tranco list."""
    # Force reload with correct path
    import app.collectors.tranco as tranco_mod
    from pathlib import Path
    data_dir = Path(os.environ.get("OTS_DATA_DIR", "./data"))
    tranco_file = data_dir / "tranco.csv"

    if not tranco_file.exists():
        print(f"Tranco list not found at {tranco_file}. Run the server first to download it.")
        return []

    # Load directly
    import csv
    rank_cache = {}
    with open(tranco_file, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                try:
                    rank_cache[row[1].strip().lower()] = int(row[0].strip())
                except (ValueError, IndexError):
                    continue

    print(f"Loaded {len(rank_cache)} domains from Tranco list")
    sorted_domains = sorted(rank_cache.items(), key=lambda x: x[1])
    return [d[0] for d in sorted_domains[:n]]


async def crawl_domain(domain: str, idx: int, total: int) -> dict:
    """Crawl a single domain and return the result."""
    start = time.time()
    try:
        result = await run_check(domain)
        result_dict = result.model_dump(by_alias=True)
        store_check(domain, result_dict)
        elapsed = time.time() - start
        score = result_dict["trustScore"]
        rec = result_dict["recommendation"]
        print(f"  [{idx}/{total}] {domain:40s} {score:3d}  {rec:8s}  ({elapsed:.1f}s)")
        return {"domain": domain, "score": score, "recommendation": rec, "ok": True}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [{idx}/{total}] {domain:40s} ERROR: {str(e)[:50]}  ({elapsed:.1f}s)")
        return {"domain": domain, "score": 0, "recommendation": "ERROR", "ok": False}


async def main():
    init_db()
    ensure_keys()

    # Determine domain list
    domains = []
    if len(sys.argv) > 1 and sys.argv[1] == "--top":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
        await ensure_loaded()
        domains = get_tranco_top(n)
        print(f"Crawling top {n} Tranco domains")
    elif len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        with open(sys.argv[1]) as f:
            domains = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        print(f"Crawling {len(domains)} domains from {sys.argv[1]}")
    else:
        domains = DEFAULT_DOMAINS
        print(f"Crawling {len(domains)} default domains")

    # Deduplicate
    seen = set()
    unique = []
    for d in domains:
        d = d.lower().strip()
        if d not in seen:
            seen.add(d)
            unique.append(d)
    domains = unique

    print(f"Total unique domains: {len(domains)}")
    print(f"Estimated time: {len(domains) * 8}--{len(domains) * 15} seconds")
    print()

    results = []
    for i, domain in enumerate(domains, 1):
        result = await crawl_domain(domain, i, len(domains))
        results.append(result)
        # Small delay to be polite
        await asyncio.sleep(1)

    # Summary
    print()
    print("=" * 60)
    ok = [r for r in results if r["ok"]]
    errors = [r for r in results if not r["ok"]]
    proceed = [r for r in ok if r["recommendation"] == "PROCEED"]
    caution = [r for r in ok if r["recommendation"] == "CAUTION"]
    deny = [r for r in ok if r["recommendation"] == "DENY"]

    print(f"Crawled: {len(results)} | OK: {len(ok)} | Errors: {len(errors)}")
    print(f"PROCEED: {len(proceed)} ({len(proceed)/max(len(ok),1)*100:.0f}%)")
    print(f"CAUTION: {len(caution)} ({len(caution)/max(len(ok),1)*100:.0f}%)")
    print(f"DENY:    {len(deny)} ({len(deny)/max(len(ok),1)*100:.0f}%)")

    if ok:
        scores = [r["score"] for r in ok]
        print(f"Score range: {min(scores)}--{max(scores)} | Average: {sum(scores)/len(scores):.1f}")

    # Score distribution
    print()
    print("Score distribution:")
    brackets = [(0, 19), (20, 39), (40, 59), (60, 74), (75, 89), (90, 100)]
    for low, high in brackets:
        count = sum(1 for r in ok if low <= r["score"] <= high)
        bar = "#" * count
        print(f"  {low:3d}--{high:3d}: {bar} ({count})")

    print()
    stats = get_stats()
    print(f"Registry: {stats['totalDomains']} domains, {stats['rawSignalRecords']} signal records")
    print(f"Average score: {stats['averageScore']}")

    # Save results to file
    with open("data/crawl-results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to data/crawl-results.json")


if __name__ == "__main__":
    asyncio.run(main())
