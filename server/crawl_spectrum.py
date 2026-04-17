#!/usr/bin/env python3
"""Crawl a spectrum of domains for score distribution analysis.

Crawls:
1. Re-check all existing domains (picks up new signals: jurisdiction,
   category detection, international content, GDPR-aware WHOIS)
2. Mid-tier Tranco domains (ranks 500-5000, sampled)
3. Low-tier Tranco domains (ranks 5000-50000, sampled)
4. Tail Tranco domains (ranks 50000-500000, sampled)
5. Known commerce sites across regions

Usage: python3 crawl_spectrum.py
"""

import asyncio
import sys
import time
import json
import os
import random
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))

from app.pipeline import run_check
from app.database import init_db, store_check, get_stats
from app.signing import ensure_keys


def load_tranco(n_samples_per_tier: int = 50) -> list[str]:
    """Sample domains across the full Tranco spectrum."""
    from pathlib import Path
    import csv

    data_dir = Path(os.environ.get("OTS_DATA_DIR", "./data"))
    tranco_file = data_dir / "tranco.csv"

    if not tranco_file.exists():
        print("Tranco list not found")
        return []

    all_domains = []
    with open(tranco_file, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                try:
                    rank = int(row[0].strip())
                    domain = row[1].strip().lower()
                    all_domains.append((rank, domain))
                except (ValueError, IndexError):
                    continue

    print(f"Loaded {len(all_domains)} Tranco domains")

    # Sample across tiers
    tiers = [
        ("Mid-tier (500-2000)", 500, 2000, n_samples_per_tier),
        ("Upper-mid (2000-5000)", 2000, 5000, n_samples_per_tier),
        ("Lower-mid (5000-20000)", 5000, 20000, n_samples_per_tier),
        ("Low-tier (20000-50000)", 20000, 50000, n_samples_per_tier),
        ("Tail (50000-200000)", 50000, 200000, 30),
        ("Deep tail (200000-500000)", 200000, 500000, 20),
    ]

    sampled = []
    for name, low, high, count in tiers:
        tier_domains = [d for r, d in all_domains if low <= r < high]
        sample = random.sample(tier_domains, min(count, len(tier_domains)))
        print(f"  {name}: {len(sample)} domains sampled")
        sampled.extend(sample)

    return sampled


# International commerce sites to ensure global coverage
INTERNATIONAL_COMMERCE = [
    # Europe
    "otto.de", "cdiscount.fr", "bol.com", "allegro.pl",
    "ozon.ru", "wildberries.ru", "hm.com", "ikea.com",
    # Asia
    "flipkart.com", "myntra.com", "lazada.com", "tokopedia.com",
    "shopee.com", "coupang.com", "zozo.jp",
    # Latin America
    "magazineluiza.com.br", "falabella.com", "liverpool.com.mx",
    # Middle East / Africa
    "noon.com", "jumia.com", "souq.com",
    # Global SaaS / API / Infrastructure
    "twilio.com", "sendgrid.com", "datadog.com", "pagerduty.com",
    "sentry.io", "postman.com", "auth0.com", "okta.com",
    "grafana.com", "elastic.co", "mongodb.com", "redis.io",
    # Crypto / Agent commerce
    "opensea.io", "rarible.com", "magic.link", "alchemy.com",
    "infura.io", "moralis.io", "thirdweb.com",
]


async def crawl_domain(domain: str, idx: int, total: int) -> dict:
    start = time.time()
    try:
        result = await run_check(domain)
        result_dict = result.model_dump(by_alias=True)
        store_check(domain, result_dict)
        elapsed = time.time() - start
        score = result_dict["trustScore"]
        rec = result_dict["recommendation"]
        cat = result_dict.get("siteCategory", "?")
        j = result_dict.get("jurisdiction", {})
        country = j.get("country", "?")
        print(f"  [{idx:4d}/{total}] {domain:40s} {score:3d}  {rec:8s}  {cat:15s} {country:5s} ({elapsed:.1f}s)")
        return {"domain": domain, "score": score, "recommendation": rec,
                "category": cat, "country": country, "ok": True}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [{idx:4d}/{total}] {domain:40s} ERROR: {str(e)[:40]}  ({elapsed:.1f}s)")
        return {"domain": domain, "score": 0, "recommendation": "ERROR",
                "category": "?", "country": "?", "ok": False}


async def main():
    init_db()
    ensure_keys()

    # 1. Get existing domains to re-crawl
    db_path = os.environ.get("OTS_DB_PATH", "./data/ots.db")
    conn = sqlite3.connect(db_path)
    existing = [row[0] for row in conn.execute("SELECT domain FROM domains").fetchall()]
    conn.close()
    print(f"Existing domains to re-check: {len(existing)}")

    # 2. Get Tranco spectrum
    spectrum = load_tranco(n_samples_per_tier=50)

    # 3. International commerce
    intl = INTERNATIONAL_COMMERCE

    # Combine and deduplicate
    all_domains = []
    seen = set()
    for d in existing + spectrum + intl:
        d = d.lower().strip()
        if d and d not in seen:
            seen.add(d)
            all_domains.append(d)

    print(f"\nTotal unique domains to crawl: {len(all_domains)}")
    print(f"Estimated time: {len(all_domains) * 5 // 60}-{len(all_domains) * 15 // 60} minutes")
    print()

    results = []
    for i, domain in enumerate(all_domains, 1):
        result = await crawl_domain(domain, i, len(all_domains))
        results.append(result)
        await asyncio.sleep(0.5)

    # Summary
    print()
    print("=" * 70)
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
        print(f"Score range: {min(scores)}-{max(scores)} | Average: {sum(scores)/len(scores):.1f} | Median: {sorted(scores)[len(scores)//2]}")

    # Distribution
    print()
    print("Score distribution:")
    brackets = [(0, 19), (20, 39), (40, 59), (60, 74), (75, 89), (90, 100)]
    for low, high in brackets:
        count = sum(1 for r in ok if low <= r["score"] <= high)
        bar = "#" * (count // 2)
        pct = count / max(len(ok), 1) * 100
        print(f"  {low:3d}-{high:3d}: {bar} ({count}, {pct:.0f}%)")

    # By category
    print()
    print("By category:")
    cats = {}
    for r in ok:
        c = r.get("category", "?")
        cats.setdefault(c, []).append(r["score"])
    for cat, scores in sorted(cats.items()):
        avg = sum(scores) / len(scores)
        print(f"  {cat:20s}: {len(scores):4d} domains, avg score {avg:.1f}")

    # By country
    print()
    print("By country (top 15):")
    countries = {}
    for r in ok:
        c = r.get("country", "?")
        countries.setdefault(c, []).append(r["score"])
    for country, scores in sorted(countries.items(), key=lambda x: -len(x[1]))[:15]:
        avg = sum(scores) / len(scores)
        print(f"  {country:10s}: {len(scores):4d} domains, avg score {avg:.1f}")

    # Save results
    with open(os.environ.get("OTS_DATA_DIR", "./data") + "/spectrum-results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to data/spectrum-results.json")

    stats = get_stats()
    print(f"Registry total: {stats['totalDomains']} domains, {stats['rawSignalRecords']} signal records")


if __name__ == "__main__":
    asyncio.run(main())
