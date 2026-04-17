# Trust Scoring Algorithm and KYC Monetization

**Version:** 0.1.0-draft
**Date:** 2026-04-10

---

## 1. Scoring Philosophy

The scoring system rewards two things: **longevity** and **transparency**.

A site that has existed for years with clean history and full disclosure earns
trust passively. A new site with no history earns trust actively, by proving
identity. Both paths lead to high scores, but neither path is free. Time costs
time. Identity verification costs money.

This asymmetry is the business model.

## 2. Signal Categories (Deep Dive)

### 2.1 Domain Age (15% weight)

**What we check:**
- WHOIS/RDAP registration date
- Wayback Machine first snapshot date
- Historical DNS records

**Scoring curve:**
```
0--30 days:    0 points
31--90 days:   20 points
91--180 days:  40 points
181--365 days: 60 points
1--2 years:    75 points
2--5 years:    90 points
5+ years:      100 points
```

**Why logarithmic:** The difference between a 1-day-old site and a 90-day-old
site matters enormously. The difference between a 5-year-old and a 10-year-old
site barely matters. The curve reflects real-world fraud patterns: most scam
sites are used within their first 90 days.

**Data source:** WHOIS/RDAP is free but rate-limited. Bulk WHOIS providers
(DomainTools, WhoisXML API) charge $0.01--0.05 per lookup. At 10K domains,
this costs $100--500/month.

### 2.2 SSL/TLS (10% weight)

**What we check:**
- Certificate present and valid
- Certificate not expired or expiring within 14 days
- TLS version 1.2 or higher
- No known vulnerabilities (POODLE, Heartbleed, etc.)
- HSTS header present

**Scoring:**
```
No SSL:              0 points
SSL valid:           60 points
SSL + TLS 1.2+:     80 points
SSL + TLS 1.3:      90 points
SSL + HSTS:         100 points
```

**Critical note from Claude chat:** Do NOT penalize by certificate authority.
Let's Encrypt certificates are equally valid and trusted by all browsers. The
old Grok code penalized Let's Encrypt, which is wrong and would destroy
credibility with any technical audience. The check is: does valid SSL exist?
Not: who issued it?

**Exception:** If the CA itself is on a known-bad list (rare, but happens
when CAs get compromised or delisted), flag it as `SSL_CA_UNTRUSTED`.

### 2.3 DNS Security (10% weight)

**What we check:**
- SPF record present and valid
- DMARC record present and valid
- DNSSEC enabled
- MX records present (for sites claiming to be businesses)
- No open resolver
- CAA record present

**Scoring:**
```
No DNS security records: 20 points (base; DNS exists and resolves)
SPF only:                40 points
SPF + DMARC:             60 points
SPF + DMARC + MX:        75 points
SPF + DMARC + DNSSEC:    90 points
Full suite (all above):  100 points
```

**Why only 10% weight:** DNS security is a strong positive signal but its
absence is not necessarily a negative one. Many legitimate small businesses
have no DMARC or DNSSEC. We reward it but don't heavily penalize its absence.

### 2.4 Content Signals (15% weight)

**What we check (via headless browser crawl):**
- Privacy policy page exists and contains substance
- Terms of service page exists and contains substance
- Contact information (address, phone, email) present on site
- About page or equivalent
- Business name matches domain registration
- No content scraping from other sites detected
- No deceptive design patterns (dark patterns)

**Scoring:**
```
No content signals:          0 points
Contact info only:           30 points
Contact + privacy policy:    50 points
Contact + privacy + terms:   70 points
Full disclosure (all above): 85 points
Full + business consistency: 100 points
```

**Crawl cost:** Headless browser (Playwright) is the most expensive signal
to collect. Each crawl takes 5--30 seconds and requires compute. At scale:
- 10K domains weekly: ~40 compute-hours/week
- 100K domains weekly: ~400 compute-hours/week
- Cost: ~$0.02 per crawl (spot instances)

### 2.5 Reputation (25% weight)

**What we check:**
- Google Safe Browsing API (malware, phishing, unwanted software)
- PhishTank (known phishing URLs)
- Spamhaus (spam-related domains)
- VirusTotal (multi-engine malware scan)
- SURBL (spam URI blacklists)
- Abuse report databases
- DNSBL listings

**Scoring:**
```
Any malware/phishing detection:   0 points (+ critical flag)
Listed on any spam/abuse list:    20 points (+ warning flag)
No data (too new):                50 points (+ NEW_DOMAIN flag)
Clean on all lists:               100 points
```

**Why 25% weight:** Reputation is the strongest existing signal. If Google
Safe Browsing flags a site, agents should not send money there regardless of
other signals. This is the one category where a zero score triggers an
automatic `DENY` recommendation through the flag system.

**Cost:** Google Safe Browsing is free (Update API with local DB). PhishTank
is free. VirusTotal: free tier is 4 lookups/min, paid starts at $0.004/lookup.
Spamhaus: free for non-commercial use under 300K queries/day.

### 2.6 Identity (25% weight)

**What we check (varies by tier):**
- Automated: nothing (score = 0)
- Enhanced: business name + address + phone verification
- KYC-Verified: government ID + business documents + bank verification + video call
- Enterprise: all of above + on-site audit + continuous monitoring

**Scoring by tier:**
```
No verification:     0 points
Enhanced verified:   60 points
KYC verified:        85 points
Enterprise verified: 100 points
```

**Why 25% weight and why this is the monetization engine:**

Identity is weighted equally with Reputation because it answers a
fundamentally different question. Reputation asks "has this site done anything
bad?" Identity asks "do we know who runs this site?"

A brand new site has zero reputation data and zero identity data. That's 50%
of the total score returning zero. Combined with the tier ceiling (automated =
60), a new site maxes out around 35 points, which triggers `DENY`.

The only way to break out of this gravity well quickly is to verify identity.
This is not a gate to extract money. It's genuine risk mitigation. An
anonymous site asking for money is fundamentally riskier than one where the
operator's identity is on file. If the operator commits fraud, there's a
person to pursue. That matters to agents, payment rails, and insurance
underwriters.

## 3. Score Calculation Examples

### 3.1 Established Legitimate Business

```
Domain: oldshop.com (registered 2018)
Tier: automated (free)

Domain Age:    95/100  x 0.15 = 14.25
SSL:          100/100  x 0.10 = 10.00
DNS:           80/100  x 0.10 =  8.00
Content:      100/100  x 0.15 = 15.00
Reputation:   100/100  x 0.25 = 25.00
Identity:       0/100  x 0.25 =  0.00
                              --------
Raw score:                      72.25
Tier ceiling:                   60
Final score:                    60
Recommendation:                 CAUTION

Note: Even a 7-year-old legitimate shop gets capped at 60 on the free tier.
Upgrading to Enhanced ($29/mo) lifts the ceiling to 80, revealing the real
score of 72. KYC ($99/mo) lifts it further to 95.
```

### 3.2 Brand New Startup (No KYC)

```
Domain: newstartup.io (registered 2 weeks ago)
Tier: automated (free)

Domain Age:     0/100  x 0.15 =  0.00
SSL:          100/100  x 0.10 = 10.00
DNS:           40/100  x 0.10 =  4.00
Content:       70/100  x 0.15 = 10.50
Reputation:    50/100  x 0.25 = 12.50  (capped; insufficient data)
Identity:       0/100  x 0.25 =  0.00
                              --------
Raw score:                      37.00
Tier ceiling:                   60
Final score:                    37
Flags:                          NEW_DOMAIN, NO_IDENTITY
Recommendation:                 DENY
```

### 3.3 Brand New Startup (KYC Verified)

```
Domain: newstartup.io (registered 2 weeks ago)
Tier: kyc_verified ($99/mo)

Domain Age:     0/100  x 0.15 =  0.00
SSL:          100/100  x 0.10 = 10.00
DNS:           40/100  x 0.10 =  4.00
Content:       70/100  x 0.15 = 10.50
Reputation:    50/100  x 0.25 = 12.50
Identity:      85/100  x 0.25 = 21.25
                              --------
Raw score:                      58.25
Tier ceiling:                   95
Final score:                    58
Flags:                          NEW_DOMAIN
Recommendation:                 CAUTION

Note: KYC didn't magically make this site trusted. It lifted the score from
DENY to CAUTION. The agent will still apply transaction limits or ask for
human confirmation. But it won't refuse outright. That's the value proposition
for new sites: you go from "blocked" to "allowed with guardrails."
```

### 3.4 Scam Site

```
Domain: fr33-iphonez.xyz (registered 3 days ago)
Tier: automated (free)

Domain Age:     0/100  x 0.15 =  0.00
SSL:           60/100  x 0.10 =  6.00
DNS:           20/100  x 0.10 =  2.00
Content:        0/100  x 0.15 =  0.00
Reputation:     0/100  x 0.25 =  0.00  (flagged by Safe Browsing)
Identity:       0/100  x 0.25 =  0.00
                              --------
Raw score:                       8.00
Tier ceiling:                   60
Final score:                     8
Flags:                          MALWARE_DETECTED, NEW_DOMAIN, NO_IDENTITY
Recommendation:                 DENY (flag override: MALWARE_DETECTED)
```

## 4. KYC Monetization Model

### 4.1 Revenue Streams

**Stream 1: Verification Tiers (recurring)**

| Tier | Price | Gross margin | Target conversion |
|------|-------|-------------|-------------------|
| Automated | Free | n/a | 100% of registrations |
| Enhanced | $29/mo | ~90% | 20% of free tier |
| KYC-Verified | $99/mo | ~80% | 5% of free tier |
| Enterprise | $499/mo | ~70% | 1% of free tier |

**Stream 2: API Access (recurring)**

| Tier | Price | Target customers |
|------|-------|-----------------|
| Free | $0 | Indie developers, testing |
| Pro | $49/mo | Small agent companies |
| Enterprise | Custom ($500--5,000/mo) | Payment rails, large agent platforms |

**Stream 3: Transaction Insurance (per-transaction)**

For enterprise-tier sites, OTT offers transaction insurance:
- $0.10 per verified transaction (floor)
- Coverage up to $50,000 per incident
- Underwritten by insurance partner (OTT takes 20--30% of premium)
- This only works at scale (100K+ insured transactions/month)

### 4.2 Revenue Projections

**Year 1 (0--10K domains):**
```
Free:       8,000 domains x $0    = $0
Enhanced:   1,500 domains x $29   = $43,500/mo
KYC:          400 domains x $99   = $39,600/mo
Enterprise:    50 domains x $499  = $24,950/mo
API Pro:      200 accounts x $49  = $9,800/mo
API Ent:       10 accounts x $1K  = $10,000/mo
                                  ---------------
Monthly:                            $127,850
Annual:                             ~$1.5M ARR
```

**Year 2 (10K--100K domains):**
```
Free:      70,000 domains x $0    = $0
Enhanced:  15,000 domains x $29   = $435,000/mo
KYC:        4,000 domains x $99   = $396,000/mo
Enterprise:   500 domains x $499  = $249,500/mo
API Pro:    1,000 accounts x $49  = $49,000/mo
API Ent:       50 accounts x $2K  = $100,000/mo
Insurance:     ~$50K/mo (early)
                                  ---------------
Monthly:                            ~$1.28M
Annual:                             ~$15M ARR
```

### 4.3 Why Sites Will Pay

The pitch to site owners is dead simple:

"AI agents are going to start buying things on behalf of humans. Right now,
those agents have no way to know if your site is legitimate. If you're not
verified, agents will refuse to transact with you. Your competitors will
get verified. You'll lose revenue from the fastest-growing payment channel
in history."

This is the Let's Encrypt moment inverted. Let's Encrypt made SSL free so
everyone would adopt it. OTT makes basic verification free, but charges for
the verification level that agents actually trust enough to send money.

The ceiling system makes this visceral. A site owner logs into the dashboard,
sees their score is 58, and sees: "Your tier ceiling is 60. Your real
signals support a score of 72. Upgrade to Enhanced to unlock your full
score." That's a conversion moment.

### 4.4 KYC Operational Costs

The KYC pipeline is the most operationally complex part of the business.

**Enhanced tier (mostly automated):**
- Identity provider API call: $0.50--2.00 per verification
- Business registry lookup: $0.10--0.50 per lookup
- Phone verification (Twilio): $0.05 per verification
- Total cost per Enhanced verification: ~$3.00
- At $29/mo: payback in first month

**KYC-Verified tier (human + automated):**
- All Enhanced costs: ~$3.00
- Government ID verification (Jumio, Onfido): $2.00--5.00
- Micro-deposit verification: $0.50
- Human reviewer time (15 min video call + 30 min review): ~$25 (at $50/hr)
- Total cost per KYC verification: ~$35.00
- At $99/mo: payback in first month

**Enterprise tier (ongoing):**
- All KYC costs: ~$35.00
- Annual audit (virtual): ~$500 per site
- Continuous monitoring tooling: ~$5/mo per site
- Compliance officer time: amortized across portfolio
- At $499/mo: strong margin even with audit costs

### 4.5 KYC Partner Stack

Rather than building KYC from scratch, integrate with established providers:

| Function | Provider Options | Cost |
|----------|-----------------|------|
| ID Verification | Jumio, Onfido, Persona | $2--5/verification |
| Business Registry | OpenCorporates, Dun & Bradstreet | $0.10--1.00/lookup |
| Bank Verification | Plaid, Stripe Identity | $1.50--3.00/verification |
| Phone Verification | Twilio Verify | $0.05/verification |
| Address Verification | SmartyStreets, Google Address | $0.01--0.05/lookup |
| Video Call | Zoom API, Daily.co | $0.03/min |

Total platform cost per full KYC: under $40.
Revenue per KYC customer: $99/mo = $1,188/year.
LTV at 2-year avg retention: ~$2,376.
CAC target: under $200.

## 5. Competitive Positioning

### 5.1 Who Competes (And Why They're Different)

| Competitor | What They Do | Why We're Different |
|-----------|-------------|-------------------|
| Google Safe Browsing | Binary safe/unsafe flag | No trust score, no identity, no agent API |
| Norton Safe Web | Browser-based reputation | Human-focused, no agent integration |
| Scamadviser | Trust scores for humans | No cryptographic proof, no payment rail integration |
| SSL Certificate Authorities | Domain/org validation | Only validates certificate, not site trustworthiness |
| Stripe Radar | Fraud detection on transactions | Post-transaction, not pre-transaction; Stripe-only |
| Coinbase x402 | Agent payment protocol | Payment rail, not trust layer |

### 5.2 Why a Big Player Won't Just Build This

They might. But the value of a trust authority comes from neutrality. Stripe
building a trust layer that only works with Stripe payments is not credible as
a neutral standard. Neither is Coinbase or Google. The same reason SSL
certificates come from independent CAs, not from browser vendors.

If Stripe wants to integrate trust verification into their agent payments, they
want to call someone else's API, not maintain their own trust database. That
someone else is OTT.

The defense is speed and adoption. Get to 10K verified sites before anyone
else takes this seriously. Then you're the dataset, and datasets compound.

## 6. Score Transparency and Appeals

### 6.1 Dashboard Visibility

Site owners see:
- Their current composite score and per-signal breakdown
- Their tier ceiling and how much score is being capped
- Historical score chart (30/60/90 day trends)
- Which signals are pulling their score down
- Specific actions they can take to improve each signal
- Upgrade path with projected score after upgrade

### 6.2 Appeal Process

If a site owner believes their score is wrong:

1. Submit appeal through dashboard with evidence
2. Human reviewer examines within 48 hours
3. If legitimate: signals re-collected and score recalculated
4. If rejected: owner gets detailed explanation
5. Maximum 2 appeals per 90-day period (prevents gaming)

### 6.3 Dispute by Third Party

If an agent or payment rail disputes a score (e.g., "this site scored 80 but
defrauded our user"):

1. Incident reported via `POST /v1/report`
2. Immediate flag added: `DISPUTE_PENDING`
3. Score frozen pending investigation
4. If fraud confirmed: score zeroed, critical flag applied, insurance claim triggered
5. Post-mortem: which signals missed the fraud? Update algorithm.
