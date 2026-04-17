# Scoring Model v1.4: Top-Tranco Consensus Tier

## Problem

Under v1.3, the `WELL_KNOWN_SCORE_FLOOR` of 75 mechanically caps top-Tranco brands at the PROCEED threshold. Amazon, Wikipedia, and OpenAI all land at exactly 75 because the automated identity ceiling is 55 and the anchor floor is 75. An investor looking at the dashboard sees amazon.com at the same score as a mid-tier regional brand. This undermines confidence in the scoring model's ability to distinguish genuine trust levels among top sites.

The root cause is the automated identity ceiling of 55. The identity signal carries 25% of the weighted score. With a ceiling of 55, the maximum identity contribution is 55 x 0.25 = 13.75 points. Combined with the other five signals at their maximums, the theoretical ceiling for an automated (non-KYC) domain is ~88. But most top brands score 72-78 on the raw calculation, and the anchor floor clamps them to exactly 75.

## Proposal: Top-Tranco Consensus Tier

Add a new identity tier between "automated" (ceiling 55) and "enhanced" (ceiling 65) called "consensus". The consensus tier recognizes that a domain's sustained presence in the Tranco top-100 across billions of real-user requests is itself a form of identity verification that no automated system can fake.

### Eligibility

All of the following must be true:
- Tranco rank in the top 100
- Domain age >= 10 years (3,650 days)
- SSL certificate valid
- Reputation clean (no malware, phishing, or spam listing)
- Identity score (pre-ceiling) >= 30

### Effect

- Identity ceiling raised from 55 to 75
- No change to the WELL_KNOWN_SCORE_FLOOR (stays at 75)
- No change to the identity FLOOR (stays at 50 for well-known brands)
- `identityTier` field in the response set to `"consensus"` (new value alongside `"automated"`, `"enhanced"`, `"kyc_verified"`, `"enterprise"`)

### Impact Analysis

With an identity ceiling of 75 instead of 55, the identity contribution rises from max 13.75 to max 18.75 points. For amazon.com (Tranco #1, 31 years old, clean rep, valid SSL):

| Signal | Weight | v1.3 score | v1.3 contribution | v1.4 score | v1.4 contribution |
|---|---|---|---|---|---|
| Reputation | 30% | 96 | 28.8 | 96 | 28.8 |
| Identity | 25% | 55 (capped) | 13.75 | 75 (consensus) | 18.75 |
| Content | 17% | 55 | 9.35 | 55 | 9.35 |
| Domain Age | 10% | 100 | 10.0 | 100 | 10.0 |
| SSL | 10% | 100 | 10.0 | 100 | 10.0 |
| DNS | 8% | 60 | 4.8 | 60 | 4.8 |
| **Total** | | | **76.7** | | **81.7** |

Amazon moves from 76 (technically PROCEED but indistinguishable from the floor) to 82 (solidly PROCEED, clearly above the threshold). Google, with its stronger content and DNS scores, would land around 86-88. Wikipedia around 80-82.

### Domains Affected

The Tranco top-100 list includes: google.com, facebook.com, youtube.com, microsoft.com, apple.com, amazon.com, wikipedia.org, twitter.com, instagram.com, linkedin.com, netflix.com, reddit.com, github.com, openai.com, and roughly 85 others. All are established brands with decade-plus histories. The consensus tier would affect only these ~100 domains out of the full registry.

### Why Not Just Raise the Anchor Floor?

Raising `WELL_KNOWN_SCORE_FLOOR` from 75 to 82 would produce the same dashboard optics for top brands but would be opaque about WHY the score is higher. The identity-ceiling approach is honest: the score is higher because the identity signal is stronger, not because a floor was arbitrarily raised. The evidence bundle shows `identityTier: "consensus"` with a real mechanism (Tranco top-100 sustained presence) rather than hiding the adjustment inside a floor value.

### Why Not Lower the Threshold?

Lowering PROCEED from 75 to 70 would also solve the "amazon is barely PROCEED" problem but would let lower-quality domains pass the threshold. The current 75 cutoff is calibrated against the scoring model's signal distribution and changing it has second-order effects across the full registry.

### Implementation

1. Add `CONSENSUS_TRANCO_MAX = 100` and `CONSENSUS_MIN_AGE_DAYS = 3650` constants to scoring.py
2. Add `is_consensus_tier(signals, domain_age_days) -> bool` gate function
3. In `compute_score()`, when consensus tier applies, set identity ceiling to 75 instead of 55
4. Add `"consensus"` to the identity tier enum in the response
5. Update rescore.py to call the new gate and pass it through
6. Bump `SCORING_MODEL` to `"ots-v1.4-weights"`
7. Run rescore.py --dry-run to preview the impact across the full registry
8. Run rescore.py to apply

### Rollback

The consensus tier is purely a ceiling adjustment. Removing it returns identity ceilings to 55 and scores revert to v1.3 values. No data migration, no schema change. `rescore.py` handles the rollback in a single pass.

### Timeline

Ship after the 100K seed completes and the v1.3 rescore has run across the full registry. The v1.4 bump should be the next scoring change, applied as a single batch rescore with a --dry-run preview first.
