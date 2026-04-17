"""Example 3: Compare multiple merchants.

An agent comparing prices across merchants uses OTT to filter
out untrustworthy options before presenting choices to the user.

Usage: python3 multi_merchant.py
"""

import sys
sys.path.insert(0, "../sdk/python")

from opentrusttoken import OTTClient


def find_best_merchant():
    """Simulate an agent comparing merchants with trust filtering."""

    # Agent found these merchants selling the same product
    merchants = [
        {"domain": "amazon.com", "price": 29.99, "product": "Wireless Mouse"},
        {"domain": "ebay.com", "price": 24.99, "product": "Wireless Mouse"},
        {"domain": "scosi.com", "price": 27.50, "product": "Wireless Mouse"},
        {"domain": "totally-fake-xyz123.com", "price": 9.99, "product": "Wireless Mouse"},
        {"domain": "bestbuy.com", "price": 31.99, "product": "Wireless Mouse"},
    ]

    print("Agent: Finding the best deal for a Wireless Mouse...\n")
    print(f"{'Domain':<30} {'Price':>8} {'Score':>6} {'Rec':>10} {'Status':<15}")
    print("-" * 75)

    client = OTTClient()
    trusted = []
    rejected = []

    for m in merchants:
        result = client.check(m["domain"])
        m["score"] = result.trust_score
        m["rec"] = result.recommendation
        m["country"] = result.jurisdiction.country

        if result.is_blocked or result.has_critical_flags:
            status = "REJECTED"
            rejected.append(m)
        elif result.is_risky:
            status = "CAUTION"
            trusted.append(m)
        else:
            status = "TRUSTED"
            trusted.append(m)

        print(f"{m['domain']:<30} ${m['price']:>7.2f} {m['score']:>5}/100 {m['rec']:>10} {status:<15}")

    print()

    if rejected:
        print(f"Filtered out {len(rejected)} untrustworthy merchant(s):")
        for m in rejected:
            print(f"  {m['domain']} (score {m['score']}, ${m['price']:.2f})")
        print()

    if trusted:
        # Sort by price among trusted merchants
        trusted.sort(key=lambda x: x["price"])
        best = trusted[0]
        print(f"Best trusted deal: {best['domain']} at ${best['price']:.2f}")
        print(f"  Trust score: {best['score']}/100 ({best['rec']})")
        print(f"  Country: {best['country']}")
        print()
        print(f"Agent: I recommend purchasing from {best['domain']} for ${best['price']:.2f}.")
        print(f"       Trust verified (score {best['score']}).")

        if len(trusted) > 1:
            print(f"\n  Other trusted options:")
            for m in trusted[1:]:
                print(f"    {m['domain']}: ${m['price']:.2f} (score {m['score']})")
    else:
        print("No trusted merchants found. Cannot complete purchase.")


if __name__ == "__main__":
    find_best_merchant()
