"""Example 4: Async batch checking.

Check many domains concurrently for applications that need
to verify a list of merchants at once.

Usage: python3 async_batch.py
"""

import asyncio
import sys
import time
sys.path.insert(0, "../sdk/python")

from opentrusttoken import OTTClient


async def batch_check():
    """Check 10 domains concurrently."""
    domains = [
        "stripe.com", "google.com", "amazon.com", "github.com",
        "cloudflare.com", "scosi.com", "apple.com", "microsoft.com",
        "ebay.com", "shopify.com",
    ]

    client = OTTClient()

    print(f"Checking {len(domains)} domains concurrently...\n")
    start = time.time()

    results = await client.async_check_multiple(domains)

    elapsed = time.time() - start
    print(f"{'Domain':<25} {'Score':>6} {'Rec':>10} {'Category':<18} {'Country':<8}")
    print("-" * 72)

    for r in sorted(results, key=lambda x: -x.trust_score):
        print(f"{r.domain:<25} {r.trust_score:>5}/100 {r.recommendation:>10} {r.site_category:<18} {r.jurisdiction.country:<8}")

    print(f"\nCompleted in {elapsed:.1f}s ({elapsed/len(domains):.1f}s per domain)")

    proceed = [r for r in results if r.is_safe]
    caution = [r for r in results if r.is_risky]
    deny = [r for r in results if r.is_blocked]

    print(f"\nSummary: {len(proceed)} PROCEED, {len(caution)} CAUTION, {len(deny)} DENY")


if __name__ == "__main__":
    asyncio.run(batch_check())
