"""Example 1: Basic trust check before payment.

The simplest integration. Check a domain, act on the result.
No AI agent needed. Just a direct API call.

Usage: python3 basic_check.py stripe.com
       python3 basic_check.py sketchy-site.xyz
"""

import sys
sys.path.insert(0, "../sdk/python")

from opentrusttoken import check


def pay_merchant(domain: str, amount: float):
    """Simulate a payment with trust verification."""
    print(f"\n--- Attempting to pay ${amount:.2f} to {domain} ---\n")

    result = check(domain)

    print(f"Domain:         {result.domain}")
    print(f"Trust Score:    {result.trust_score}/100")
    print(f"Recommendation: {result.recommendation}")
    print(f"Category:       {result.site_category}")
    print(f"Country:        {result.jurisdiction.country} ({result.jurisdiction.legal_framework})")
    print(f"Cross-border:   {result.jurisdiction.cross_border_risk}")
    print()

    # Signal breakdown
    print("Signals:")
    print(f"  Reputation:  {result.signals.reputation.score}/100")
    print(f"  Identity:    {result.signals.identity.score}/55")
    print(f"  Content:     {result.signals.content.score}/100")
    print(f"  Domain Age:  {result.signals.domain_age.score}/100")
    print(f"  SSL/TLS:     {result.signals.ssl.score}/100")
    print(f"  DNS:         {result.signals.dns.score}/100")
    print()

    if result.flags:
        print(f"Flags: {', '.join(result.flags)}")
        print()

    # Decision logic
    if result.is_blocked:
        print(f"BLOCKED: {result.reasoning}")
        print("Payment refused.")
        return False

    if result.has_critical_flags:
        print("CRITICAL: Security threat detected. Payment refused.")
        return False

    if result.is_risky:
        if amount > 100:
            print(f"CAUTION: {result.reasoning}")
            print(f"Amount ${amount:.2f} exceeds $100 limit for CAUTION sites.")
            print("Payment refused. Reduce amount or get user confirmation.")
            return False
        else:
            print(f"CAUTION: {result.reasoning}")
            print(f"Amount ${amount:.2f} is within CAUTION limit. Proceeding.")
            return True

    if result.is_safe:
        print(f"SAFE: {result.reasoning}")
        print(f"Payment of ${amount:.2f} authorized.")
        return True


if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "stripe.com"
    amount = float(sys.argv[2]) if len(sys.argv) > 2 else 49.99

    success = pay_merchant(domain, amount)
    print(f"\nResult: {'Payment completed' if success else 'Payment declined'}")
