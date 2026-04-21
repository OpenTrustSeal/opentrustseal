# Merchant Outreach -- CAUTION-Scored Sites

Email templates for merchants whose domain scored CAUTION (40 to 74) in the OpenTrustSeal dataset. Goal is to get the merchant to claim their dashboard, fix surfaced issues, and re-check. Fixes move them to PROCEED and open the site to AI-agent transactions.

Sender: alu@opentrustseal.com
Reply-to: alu@opentrustseal.com
From name: Allen Lu, OpenTrustSeal

## Placeholders

- `{merchant_name}` -- business name (from WHOIS or site scrape); fall back to domain if unknown
- `{first_name}` -- contact first name if we have it; otherwise omit greeting line
- `{domain}` -- the domain that was scored
- `{trust_score}` -- integer 40 to 74
- `{confidence}` -- "high", "medium", or "low"
- `{caution_reason}` -- "incomplete_evidence", "weak_signals", "new_domain", or "infrastructure"
- `{top_issues}` -- bullet list of the three highest-impact checklist items, one per line
- `{dashboard_url}` -- https://opentrustseal.com/dashboard.html?domain={domain}
- `{check_url}` -- https://api.opentrustseal.com/v1/check/{domain}

## Split-test protocol

Each template below ships with two subject lines labeled **Subject A** and **Subject B**. A is the neutral informative variant. B is a sharper benefit or loss framed variant. Both use the same body.

How to run the test:

1. Pick a batch of 100+ recipients for the template. Do not send fewer than 50 per arm. Split-test signal is too noisy below that.
2. Randomly assign 50% to A and 50% to B. Use a stable hash of the recipient email so the same address always lands in the same arm if you retry later.
3. Wait 48 hours before reading results. Most opens happen in the first 24 but replies trail.
4. Winner is the variant with the higher reply rate not the open rate. Opens are vanity; replies are revenue.
5. Once one variant beats the other by more than 20% relative (e.g. 12% vs 10% replies) on a sample of 100+, switch 100% of the next batch to the winner. Retest every 500 sends since merchant segments drift.

## Personalization hooks that move reply rates

Insert these into subject or body where a merchant-specific value is known. Both increase perceived relevance which moves reply rates.

- Use `{merchant_name}` or `{first_name}` in the subject line when known. Subjects with names outperform generic subjects by 10-15% on reply rate.
- Reference the single largest checklist gap by name in the subject (e.g. "DMARC" or "privacy policy") not the generic "3 fixes" phrasing. This moves replies up another 5-10%.
- Mention the `{confidence}` rating in the body for low-confidence sites. Low-confidence CAUTION often surprises merchants because the site works fine for humans; naming the gap reduces the "is this spam" reflex.

## Template A -- Cold outreach, weak_signals (sub-75 but real evidence)

**Subject A (neutral informative):** Your site scored {trust_score}/100 in the AI agent trust index
**Subject B (loss framed):** AI shopping agents flag {domain} as CAUTION (here is the fix)

Hi {first_name},

We run OpenTrustSeal, an independent trust attestation API that AI shopping agents call before they pay a merchant. Agents use our score to decide whether to proceed, flag for human review, or refuse a transaction.

{domain} currently scores {trust_score}/100 (CAUTION). The top three fixes:

{top_issues}

Each of these is a one-time change on your end. Fix them and the score moves to PROCEED (75+), which is what agents look for to auto-complete transactions without asking the user.

Your full dashboard, with the signal breakdown and a live checklist, is at:
{dashboard_url}

No signup, no API key. The score is free and always will be.

Happy to walk you through it if helpful.

Allen Lu
OpenTrustSeal
alu@opentrustseal.com

## Template B -- incomplete_evidence (blocked crawler, score artificially low)

**Subject A (direct diagnostic):** {domain} is blocking our trust scanner
**Subject B (benefit framed):** Your bot protection is blocking AI agent transactions on {domain}

Hi {first_name},

We run OpenTrustSeal, the trust API that AI shopping agents call before transacting. We score every domain in the Tranco top-100K on six signal categories (reputation, identity, content, domain age, SSL, DNS).

{domain} scored {trust_score}/100 (CAUTION), but not because your site has issues. Our crawler could not read your homepage content: bot protection blocked the fetch. We have the other five signals, but without content we cannot verify your privacy policy, terms, or contact info, so the score is capped.

Two paths to raise it:

1. Allowlist our crawler IPs on your bot protection. The full list is at {dashboard_url}. This lets us score content the same way we do for every other merchant.

2. Claim your dashboard and register. Registration gives us direct evidence of your business identity (EIN, address, phone) which raises the identity score independently.

Either path moves you to PROCEED. Agents pay PROCEED merchants without asking the user; they flag CAUTION ones for human approval, which kills conversion.

Allen Lu
OpenTrustSeal
alu@opentrustseal.com

## Template C -- new_domain (under 1 year old)

**Subject A (educational):** New domain guidance for {domain}
**Subject B (action framed):** Two ways to raise the trust floor on {domain} today

Hi {first_name},

OpenTrustSeal scores {domain} at {trust_score}/100 (CAUTION) primarily because the domain is under one year old. Domain age is an unfakeable trust signal, so newer domains start lower by default.

Two things shorten the path to PROCEED:

1. Registration with KYC. Verified business identity (EIN, business address, phone) raises the identity ceiling and gives new domains a trust floor of 50 right away. Details at {dashboard_url}.

2. Ship the content signals now. Privacy policy, terms of service, contact info, and HTTPS with a valid cert are table stakes. The dashboard lists what is present and what is missing.

Domain age will climb on its own over the next 12 months. Registration plus clean content signals get you most of the way there immediately.

Allen Lu
OpenTrustSeal
alu@opentrustseal.com

## Template D -- infrastructure (API / CDN / non-consumer)

**Subject A (diagnostic):** {domain} is flagged as infrastructure not consumer merchant
**Subject B (technical hook):** Your API should not be scored like a consumer storefront

Hi {first_name},

OpenTrustSeal scores {domain} at {trust_score}/100. The site appears to be an API or infrastructure service, not a consumer storefront. Our default scoring model weighs content signals like privacy policy and contact info, which matter less for infrastructure.

If that is correct, claim your dashboard and set your site category to `api_service` or `infrastructure`:

{dashboard_url}

The infrastructure scoring model weighs security headers, documented endpoints, and certificate posture instead. Your score will reflect what actually matters for your category.

Allen Lu
OpenTrustSeal
alu@opentrustseal.com

## Follow-up template (no reply after 7 days)

**Subject A (thread continuation):** Re: Your site scored {trust_score}/100 in the AI agent trust index
**Subject B (fresh thread):** Still at {trust_score}/100 on {domain} -- want me to walk through it?

Hi {first_name},

Following up on this. If the timing is wrong, no problem. If you want to see what is in the dashboard first, it is at {dashboard_url} with no signup.

The short version: AI shopping agents are using trust scores to decide what to buy. Merchants stuck at CAUTION lose those transactions to merchants at PROCEED. The fixes are small; the conversion delta is not.

Allen Lu
OpenTrustSeal
alu@opentrustseal.com

## Sending notes

- Send in batches of 50, not more, to keep list-unsubscribe warm
- Always BCC alu@opentrustseal.com so replies thread into one inbox
- If the domain scored CAUTION with confidence=low and caution_reason=incomplete_evidence, use Template B
- If caution_reason=new_domain, use Template C
- If caution_reason=infrastructure, use Template D
- Everything else uses Template A
- Do not send to contact@, info@, abuse@, privacy@, or security@ addresses for first outreach since those are operational inboxes and responses will be slow. Prefer a named person from the site's about or team page.
- FERPA / PII note: these emails go to merchants, not consumers. No student data, no consumer PII. The dashboard URL is public and does not require auth.

## Tracking log format

For each send, log one row in a spreadsheet or DB with these fields so subject-line tests can be compared later.

| Field | Example | Notes |
|---|---|---|
| send_id | 2026-04-21-batch-07 | Batch identifier, date + sequence |
| domain | example.com | Recipient's scored domain |
| template | A / B / C / D / F (follow-up) | Template letter |
| subject_variant | A or B | Which subject arm was used |
| caution_reason | incomplete_evidence | Why the recipient is CAUTION (drives template choice) |
| score_at_send | 58 | Trust score on the day of send |
| sent_at | 2026-04-21T14:20Z | Timestamp |
| opened | true / false | From tracking pixel if enabled |
| replied | true / false | Did they reply at all |
| claimed_dashboard | true / false | Did they register within 14 days |
| score_after | 78 | Trust score 30 days after send (for conversion analysis) |

The key conversion metric is not reply rate but `score_after - score_at_send` averaged across a batch. That is the actual product outcome. Everything else is a leading indicator.

## Deliverability pre-flight

Before the first batch goes out from alu@opentrustseal.com:

- Verify SPF record includes the sending IP or service. Currently `v=spf1 +mx +ip4:96.31.72.73 include:spf.gzo.com ~all`. If sending from a new service like Postmark or SendGrid, add their include.
- Verify DMARC is `p=quarantine` or stricter (already enforced at `p=quarantine` as of 2026-04-13).
- Verify DKIM signing is active on the sending path. Test with mail-tester.com before the first real send.
- Warm up sending volume gradually: 20 per day for the first 3 days, then 50, then 100. Avoid day-one blasts that trigger provider spam heuristics.
- List-Unsubscribe header is required by Gmail and Yahoo for bulk sends. Must be one-click per RFC 8058.
