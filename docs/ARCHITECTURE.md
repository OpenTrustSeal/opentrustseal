# OpenTrustToken System Architecture

**Version:** 0.2.0-draft
**Date:** 2026-04-10
**Revised:** 2026-04-10

---

## 0. MVP Architecture (v1: Build This First)

The full architecture below is the scaling roadmap. For v1, build only this:

```
┌──────────────────────────────────────────────┐
│              Single VPS / Railway             │
│                                               │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐ │
│  │  FastAPI     │  │ PostgreSQL│  │  Redis   │ │
│  │  Server      │──│          │  │  (cache) │ │
│  │             │  └──────────┘  └──────────┘ │
│  │  - /v1/check│                              │
│  │  - signing  │  Ed25519 key in encrypted    │
│  │  - scoring  │  file on disk (no HSM yet)   │
│  └─────────────┘                              │
└──────────────────────────────────────────────┘
```

**Components:**
- **FastAPI** -- single process handles API, scoring, and signing
- **PostgreSQL** -- domains, signals, tokens, API keys, audit log
- **Redis** -- cache layer for scored tokens (optional for MVP; can start without)
- **Ed25519 signing** -- in-process using PyNaCl; private key as encrypted file

**What this handles:**
- `GET /v1/check/{domain}` with real signal collection
- `POST /v1/check/request` for on-demand domain checks
- Ed25519-signed responses
- Free tier rate limiting (60 req/min)

**What it does NOT handle (add later):**
- HSM-backed signing (add at Stage 2)
- Kubernetes orchestration (add at Stage 2)
- Message queue (signals collected synchronously in v1)
- KYC verification pipeline (add after demand is proven)
- Webhooks, bulk API, badges

**Estimated cost:** $20--50/mo (single VPS or Railway Pro plan)

**When to graduate to the full architecture:** When you hit 10K domains or
100K API queries/day, whichever comes first.

---

## 1. System Overview (Full Scaling Architecture)

```
                                    ┌─────────────────┐
                                    │   CDN (Edge)    │
                                    │  Cloudflare /   │
                                    │  Fastly         │
                                    └────────┬────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
           ┌────────▼────────┐    ┌─────────▼─────────┐    ┌────────▼────────┐
           │  API Gateway    │    │  Static Token CDN  │    │  Dashboard UI   │
           │  (rate limit,   │    │  (cached ott.json  │    │  (site owner    │
           │   auth, routing)│    │   files)           │    │   portal)       │
           └────────┬────────┘    └───────────────────┘    └────────┬────────┘
                    │                                                │
        ┌───────────┼───────────────┐                               │
        │           │               │                               │
┌───────▼──┐ ┌──────▼──────┐ ┌─────▼──────┐                ┌───────▼───────┐
│Verify    │ │Registration │ │Webhook     │                │Auth Service   │
│Service   │ │Service      │ │Service     │                │(JWT, API keys)│
└───────┬──┘ └──────┬──────┘ └─────┬──────┘                └───────────────┘
        │           │               │
        └───────────┼───────────────┘
                    │
        ┌───────────┼───────────────┐
        │           │               │
┌───────▼──┐ ┌──────▼──────┐ ┌─────▼──────┐
│Scoring   │ │Signing      │ │Signal      │
│Engine    │ │Service      │ │Collectors  │
│          │ │(HSM-backed) │ │(crawlers)  │
└───────┬──┘ └──────┬──────┘ └─────┬──────┘
        │           │               │
        └───────────┼───────────────┘
                    │
    ┌───────────────┼───────────────────┐
    │               │                   │
┌───▼────┐   ┌──────▼──────┐    ┌───────▼──────┐
│Primary │   │ Cache Layer │    │ Message Queue│
│Database│   │ (Redis)     │    │ (NATS/Kafka) │
└────────┘   └─────────────┘    └──────────────┘
```

## 2. Component Breakdown

### 2.1 Edge Layer

**CDN / Reverse Proxy**
- Routes all inbound traffic
- TLS termination
- DDoS protection
- Geographic load balancing

**Scaling notes:**
- Cloudflare or Fastly as primary CDN
- Static token files (ott.json) cached at edge with TTL matching token expiry
- API responses cached for 60s at edge (configurable per endpoint)
- At 10K domains: single CDN plan handles this
- At 100K domains: still single CDN, cache hit ratio climbs (good)
- At 1M+ domains: CDN handles this natively; this is what CDNs are built for

### 2.2 API Gateway

**Purpose:** Rate limiting, authentication, request routing, metrics.

**Technology:** Kong, AWS API Gateway, or custom (Node/Go).

**Scaling notes:**
- Stateless; scale horizontally behind load balancer
- Rate limit state stored in Redis (shared across instances)
- At 10K RPM: 1 instance
- At 100K RPM: 3--5 instances behind ALB
- At 1M+ RPM: auto-scaling group, 10--50 instances
- Key metric: p99 latency must stay under 50ms at gateway level

### 2.3 Verify Service

**Purpose:** Handles `GET /v1/check/{domain}` and `POST /v1/check/bulk` (future).

**Logic:**
1. Check Redis cache for domain's current token
2. If cache hit and not expired: return immediately (target: <10ms)
3. If cache miss: query database, populate cache, return
4. If domain not found: return 404

**Scaling notes:**
- Stateless; pure read path
- Cache-first architecture means database load stays flat even as queries grow
- At 10K RPM: 1--2 instances, Redis handles 99% of reads
- At 100K RPM: 3--5 instances, Redis cluster (3 nodes)
- At 1M+ RPM: 10+ instances, Redis cluster (6 nodes with replicas)
- Bulk endpoint: fan out to parallel cache lookups, aggregate, return
- Circuit breaker on database fallback to prevent cascade failure

### 2.4 Registration Service

**Purpose:** Domain registration, ownership verification, tier management.

**Logic:**
1. Accept registration request
2. Generate verification code
3. Queue domain ownership check (DNS or HTTP)
4. On success: trigger initial signal collection
5. On failure: retry 3x over 24h, then notify owner

**Scaling notes:**
- Low throughput relative to verify (100x fewer requests)
- 1 instance handles thousands of registrations/day
- Verification checks are async (queued via message broker)
- At any scale: 1--2 instances is sufficient

### 2.5 Signal Collectors

**Purpose:** Gather trust signals from external sources.

Six independent collectors, one per signal category:

| Collector | External Dependencies | Check Frequency |
|-----------|----------------------|----------------|
| Domain Age | WHOIS/RDAP servers | Weekly per domain |
| SSL Probe | Direct TLS handshake + CT logs | Daily |
| DNS Check | DNS resolvers | Daily |
| Content Crawler | Headless browser (Playwright) | Weekly |
| Reputation | Safe Browsing API, PhishTank, Spamhaus, VirusTotal | Every 6h |
| Identity | Internal KYC pipeline | On submission |

**Scaling notes:**
- Each collector is an independent worker pool
- Work distributed via message queue (domain + check type)
- Collectors are CPU/IO bound, not memory bound
- At 10K domains: 1 worker per collector type (6 total)
- At 100K domains: 3--5 workers per collector type (18--30 total)
- At 1M+ domains: auto-scaled worker pools per type
- Content Crawler is the most expensive (headless browser); scale separately
- Reputation checks are API-call-based; limited by external API rate limits
- Back-pressure: if queue depth > threshold, slow new registrations

### 2.6 Scoring Engine

**Purpose:** Compute trust scores from collected signals.

**Logic:**
1. Receive signal update event from collector
2. Load all current signals for domain
3. Apply weighted formula (Section 7 of PROTOCOL.md)
4. Apply tier ceiling
5. Apply flag logic (critical flags override score)
6. If score changed: trigger re-signing
7. Publish score-change event (for webhooks)

**Scaling notes:**
- Triggered by signal collector events (not by API requests)
- Pure computation; fast and stateless
- At any scale: 2--3 instances handle burst scoring
- Batch scoring for bulk re-verification: partition by domain hash

### 2.7 Signing Service

**Purpose:** Cryptographic signing of trust tokens with Ed25519.

**This is the most security-sensitive component.**

**Architecture:**
- Private keys stored in HSM (AWS CloudHSM, Azure Dedicated HSM, or Hashicorp Vault)
- Signing service is the ONLY component that can request signatures
- Keys never leave the HSM
- All signing operations logged to append-only audit trail

**Scaling notes:**
- HSM throughput: most HSMs handle 1,000--10,000 signs/sec
- At 10K domains (weekly re-sign): ~17 signs/min -- trivially handled
- At 100K domains: ~170 signs/min -- still trivial
- At 1M domains: ~1,700 signs/min -- single HSM handles this
- At 10M+ domains: HSM cluster or sign in batches during off-peak
- Signing is NOT on the hot path (verify reads cached tokens)
- Sign operations are async, triggered by score changes

### 2.8 Cache Layer (Redis)

**Purpose:** Primary read store for verification queries.

**Data model:**
```
Key: ott:domain:{domain}
Value: serialized trust token JSON
TTL: matches token expirationDate
```

**Scaling notes:**
- Single Redis instance holds 10M tokens at ~2KB each = ~20GB (fits in memory)
- At 10K domains: single Redis instance (t3.medium)
- At 100K domains: single Redis instance (r6g.large)
- At 1M domains: Redis Cluster with 3 primaries + 3 replicas
- At 10M+ domains: Redis Cluster with 6+ shards
- Read replicas in each region for geographic distribution
- Persistence: RDB snapshots every 15 min + AOF for durability
- Fallback: if Redis is down, verify service reads from database directly

### 2.9 Primary Database

**Purpose:** Persistent store for domains, signals, scores, KYC data, audit logs.

**Technology:** PostgreSQL.

**Schema overview:**
```sql
-- Core tables
domains          (id, domain, owner_id, tier, created_at, verified_at)
trust_tokens     (id, domain_id, score, tier, recommendation, signed_token, issued_at, expires_at)
signals          (id, domain_id, category, raw_data, score, checked_at)
signal_history   (id, domain_id, category, score, recorded_at)

-- KYC tables
kyc_submissions  (id, domain_id, tier, status, submitted_at, reviewed_at, reviewer_id)
kyc_documents    (id, submission_id, doc_type, encrypted_blob, uploaded_at)

-- API tables
api_keys         (id, owner_id, tier, key_hash, rate_limit, created_at)
webhook_subs     (id, domain_id, url, events, secret_hash, created_at)

-- Audit
audit_log        (id, action, actor, target, metadata, created_at)
signing_log      (id, domain_id, key_id, token_hash, signed_at)
```

**Scaling notes:**
- At 10K domains: single PostgreSQL instance (db.r6g.large)
- At 100K domains: single instance with read replicas for dashboard queries
- At 1M domains: primary + 2 read replicas; partition signal_history by month
- At 10M+ domains: horizontal sharding by domain_id hash (Citus or manual)
- KYC documents stored in encrypted S3 bucket, not in database
- signal_history is the fastest-growing table; partition and archive aggressively
- Connection pooling via PgBouncer (transaction mode)
- Verify service should NEVER query the database directly in the hot path (Redis first)

### 2.10 Message Queue

**Purpose:** Async communication between services.

**Technology:** NATS JetStream (lightweight, fast) or Kafka (if enterprise-scale from day one).

**Topics:**
```
ott.domain.registered     -> triggers initial signal collection
ott.domain.verified       -> triggers scoring + signing
ott.signal.collected      -> triggers re-scoring
ott.score.changed         -> triggers re-signing + webhook dispatch
ott.token.signed          -> triggers cache update + CDN purge
ott.webhook.dispatch      -> triggers outbound webhook delivery
ott.kyc.submitted         -> routes to KYC review queue
ott.kyc.completed         -> triggers tier upgrade + re-scoring
```

**Scaling notes:**
- NATS: single server handles 10M+ msgs/sec
- At 10K domains: single NATS server
- At 100K domains: NATS cluster (3 nodes)
- At 1M+ domains: NATS supercluster or migrate to Kafka
- Messages are small (<1KB); throughput is the constraint, not bandwidth
- Dead letter queue for failed webhook deliveries (retry 3x with exponential backoff)

## 3. Data Flow: End-to-End Verification Query

```
Agent sends: GET /v1/check/merchant.com
  |
  v
CDN Edge (cache check, ~5ms)
  |-- cache HIT -> return cached response (total: ~15ms)
  |-- cache MISS:
  v
API Gateway (auth + rate limit, ~5ms)
  |
  v
Verify Service
  |
  v
Redis Cache (lookup ott:domain:merchant.com, ~2ms)
  |-- cache HIT -> serialize + return (~10ms)
  |-- cache MISS:
  v
PostgreSQL (query trust_tokens, ~15ms)
  |-- found -> populate Redis, return (~25ms)
  |-- not found -> return 404
```

**Target latencies:**
- CDN cache hit: 15ms
- Redis cache hit: 30ms
- Database fallback: 50ms
- 99th percentile: under 100ms
- All paths under 200ms SLA

## 4. Data Flow: New Domain Registration

```
Site owner: POST /v1/register {domain: "newshop.com"}
  |
  v
Registration Service
  |-> Generate verification code
  |-> Return code + instructions to owner
  |
  v (owner adds DNS TXT or HTTP file)
  |
Registration Service (polling or webhook from owner)
  |-> Verify domain ownership
  |-> Publish: ott.domain.verified
  |
  v
Signal Collectors (parallel, triggered by queue)
  |-> Domain Age collector: WHOIS lookup
  |-> SSL collector: TLS probe
  |-> DNS collector: SPF/DMARC/DNSSEC check
  |-> Content collector: headless crawl
  |-> Reputation collector: Safe Browsing + PhishTank + Spamhaus
  |-> Each publishes: ott.signal.collected
  |
  v
Scoring Engine (triggered by signal events)
  |-> Aggregate all signals
  |-> Compute weighted score
  |-> Apply tier ceiling (automated = 60 max)
  |-> Publish: ott.score.changed
  |
  v
Signing Service (triggered by score change)
  |-> Build token JSON
  |-> Sign with Ed25519 via HSM
  |-> Store signed token in database
  |-> Publish: ott.token.signed
  |
  v
Cache + CDN update
  |-> Write to Redis
  |-> Purge CDN cache for domain
  |-> Token available at /.well-known/ott.json (via CDN proxy)
```

## 5. Data Flow: KYC Verification Upgrade (Future)

This flow activates when KYC tiers launch (target: after 5,000+ scored
domains and 100+ active API developers).

```
Site owner: POST /v1/kyc/submit {domain, tier: "kyc_verified", documents: [...]}
  |
  v
Auth Service (verify owner identity + existing domain registration)
  |
  v
KYC Service
  |-> Validate document types and formats
  |-> Store encrypted documents in S3
  |-> Create kyc_submission record (status: pending)
  |-> Publish: ott.kyc.submitted
  |
  v
KYC Review Queue (human reviewers)
  |-> Reviewer pulls next submission
  |-> Verifies government ID via identity provider API
  |-> Cross-references business registration
  |-> Initiates micro-deposit verification
  |-> Schedules video call (if KYC tier)
  |-> Marks submission: approved or rejected
  |-> Publish: ott.kyc.completed
  |
  v
Scoring Engine (triggered by KYC completion)
  |-> Update domain tier: automated -> kyc_verified
  |-> Identity signal score: 0 -> 85 (based on KYC depth)
  |-> Recalculate composite score with new tier ceiling (95)
  |-> Publish: ott.score.changed
  |
  v
Signing Service -> Cache -> CDN (same as registration flow)
```

## 6. Scaling Tiers Reference

Quick reference for infrastructure sizing at each growth stage:

### Stage 1: Launch (0--10K domains, <10K API queries/day)

| Component | Infrastructure | Monthly Cost |
|-----------|---------------|-------------|
| API Gateway | 1 instance (t3.medium) | $30 |
| Verify Service | 1 instance (t3.medium) | $30 |
| Registration Service | 1 instance (t3.small) | $15 |
| Signal Collectors | 6 workers (1 per type, t3.small) | $90 |
| Scoring Engine | 1 instance (t3.small) | $15 |
| Signing Service | 1 instance (t3.medium) + Vault | $80 |
| Redis | 1 instance (cache.t3.micro) | $15 |
| PostgreSQL | 1 instance (db.t3.medium) | $50 |
| NATS | 1 instance (t3.small) | $15 |
| CDN | Cloudflare Pro | $20 |
| **Total** | | **~$360/mo** |

### Stage 2: Traction (10K--100K domains, 100K queries/day)

| Component | Infrastructure | Monthly Cost |
|-----------|---------------|-------------|
| API Gateway | 3 instances behind ALB | $150 |
| Verify Service | 3 instances | $150 |
| Registration Service | 1 instance | $30 |
| Signal Collectors | 18 workers (3 per type) | $270 |
| Scoring Engine | 2 instances | $60 |
| Signing Service | 2 instances + CloudHSM | $2,000 |
| Redis | 3-node cluster | $200 |
| PostgreSQL | Primary + 1 read replica | $300 |
| NATS | 3-node cluster | $90 |
| CDN | Cloudflare Business | $200 |
| **Total** | | **~$3,450/mo** |

### Stage 3: Scale (100K--1M domains, 1M+ queries/day)

| Component | Infrastructure | Monthly Cost |
|-----------|---------------|-------------|
| API Gateway | Auto-scaling group (5--20) | $1,000 |
| Verify Service | Auto-scaling group (5--15) | $800 |
| Registration Service | 2 instances | $60 |
| Signal Collectors | 50+ workers (auto-scaled per type) | $1,500 |
| Scoring Engine | 3--5 instances | $300 |
| Signing Service | 3 instances + HSM cluster | $5,000 |
| Redis | 6-node cluster with replicas | $1,200 |
| PostgreSQL | Primary + 2 replicas + Citus sharding | $2,000 |
| NATS/Kafka | Production cluster | $500 |
| CDN | Cloudflare Enterprise | $5,000 |
| KYC team | 2--5 reviewers (headcount, not infra) | variable |
| **Total (infra only)** | | **~$17,360/mo** |

### Stage 4: Dominance (1M+ domains, 10M+ queries/day)

At this scale, you are a trust infrastructure company. Architecture shifts to:
- Multi-region deployment (US-East, US-West, EU, APAC)
- Regional Redis clusters with cross-region replication
- Database sharding by domain hash across regions
- Dedicated HSM clusters per region
- Kafka for event streaming (NATS outgrown)
- Dedicated SRE team
- SOC 2 Type II compliance
- Monthly infra: $50K--$200K depending on region count

## 7. Deployment Architecture

### 7.1 Containerization

All services are containerized (Docker) and orchestrated with Kubernetes.

```
Namespace: ott-production
  Deployments:
    api-gateway        (replicas: 1-20, HPA on CPU/request count)
    verify-service     (replicas: 1-15, HPA on CPU/request count)
    registration-svc   (replicas: 1-2)
    scoring-engine     (replicas: 1-5)
    signing-service    (replicas: 1-3, node affinity to HSM instances)
    webhook-dispatcher (replicas: 1-3)
    dashboard-ui       (replicas: 1-3)

  StatefulSets:
    redis-cluster      (replicas: 3-6)
    postgresql         (replicas: 1-3)
    nats-cluster       (replicas: 3)

  CronJobs:
    signal-collector-domain-age   (schedule: weekly per domain batch)
    signal-collector-ssl          (schedule: daily per domain batch)
    signal-collector-dns          (schedule: daily per domain batch)
    signal-collector-content      (schedule: weekly per domain batch)
    signal-collector-reputation   (schedule: every 6h per domain batch)
    token-expiry-checker          (schedule: hourly)
    score-decay-processor         (schedule: daily)
```

### 7.2 CI/CD Pipeline

```
Push to main
  -> Build containers (GitHub Actions)
  -> Run unit + integration tests
  -> Security scan (Trivy)
  -> Deploy to staging (auto)
  -> Integration tests against staging
  -> Manual approval gate
  -> Blue/green deploy to production
  -> Smoke tests
  -> Traffic shift (canary: 5% -> 25% -> 100%)
```

### 7.3 Observability

| Layer | Tool | What It Watches |
|-------|------|----------------|
| Metrics | Prometheus + Grafana | Request rates, latencies, error rates, cache hit ratio |
| Logging | Loki or CloudWatch | Structured JSON logs from all services |
| Tracing | OpenTelemetry + Jaeger | Request flow across services |
| Alerts | PagerDuty | SLA breaches, signing failures, HSM health |
| Uptime | External (Pingdom/UptimeRobot) | API availability from multiple regions |

**Key SLOs:**
- Verify API: 99.9% availability, p99 < 200ms
- Signing service: 99.99% availability (tokens must always be signable)
- Score freshness: signals re-collected within stated frequency +/- 10%

## 8. Security Architecture

### 8.1 Network

```
Internet -> CDN -> WAF -> API Gateway (public subnet)
                              |
                    ┌─────────┼──────────┐
                    |    Private Subnet   |
                    |                     |
                    |  Services           |
                    |  Redis              |
                    |  PostgreSQL         |
                    |  NATS               |
                    |                     |
                    |  ┌───────────────┐  |
                    |  │ HSM Subnet    │  |
                    |  │ (isolated)    │  |
                    |  │ Signing Svc   │  |
                    |  └───────────────┘  |
                    └─────────────────────┘
```

- Signing service runs in an isolated subnet with no internet access
- HSM accessible only from signing service security group
- Database accessible only from service security groups
- All inter-service communication over mTLS
- KYC document storage: separate S3 bucket with its own IAM role

### 8.2 Secrets Management

- API keys: hashed with Argon2 before storage
- HSM credentials: managed by AWS/Azure IAM, never in application config
- Database credentials: rotated via Vault, injected as K8s secrets
- Webhook secrets: HMAC-SHA256 for payload signing
- No secrets in environment variables, config files, or container images

## 9. Disaster Recovery

| Scenario | Recovery Strategy | RTO | RPO |
|----------|------------------|-----|-----|
| Redis failure | Auto-failover to replica; rebuild from DB | 30s | 0 (replica is sync) |
| Database failure | Auto-failover to standby; point-in-time recovery | 5 min | 1 min (WAL streaming) |
| HSM failure | Failover to standby HSM; key backup in escrow | 15 min | 0 (keys are durable) |
| Region failure | DNS failover to secondary region | 5 min | 5 min |
| Signing key compromise | Revoke key, rotate, re-sign all active tokens | 1 hour | 0 |
| Full data loss | Restore from S3 backups + rebuild cache | 2 hours | 1 hour |
