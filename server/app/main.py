"""OpenTrustSeal API Server.

Run with: uvicorn app.main:app --reload
"""

from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

from .routes.check import router as check_router
from .routes.token import router as token_router
from .routes.register import router as register_router
from .signing import ensure_keys, get_public_key_multibase
from .database import init_db, get_stats, get_dataset_stats, get_coverage, store_feedback, get_feedback_summary
from .fetch_escalation import stats as fetch_stats
from .heartbeat import read_heartbeat
from .transparency import init_transparency_log, get_log_for_domain, get_latest_entries, verify_chain
from .collectors import tranco

API_VERSION = "0.2.0"

app = FastAPI(
    title="OpenTrustSeal API",
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="OpenTrustSeal API",
        version=API_VERSION,
        description="""
## Trust verification for AI agent commerce

OpenTrustSeal provides pre-transaction trust checks for AI agents. One API call returns a cryptographically signed evidence bundle with a trust score and actionable checklist.

### Quick start

Check any domain:
```
GET /v1/check/stripe.com
```

### What you get back

- **Six signal categories** with individual scores: domain age, SSL, DNS, content, reputation, identity
- **Trust score** (0-100) computed from all signals
- **Recommendation**: PROCEED (75+), CAUTION (40-74), DENY (0-39)
- **Actionable checklist** showing what the site can improve
- **Ed25519 signature** proving the result was issued by OpenTrustSeal

### Authentication

Free tier requires no authentication. Rate limit: 60 requests/minute, 10,000/month.

### Scoring model

Current model: `ots-v1.2-weights`. Weights: reputation 30%, identity 25%, content 17%, domain age 10%, SSL 10%, DNS 8%.

The trust score is a computed summary of observable evidence. Every input is visible in the signals object. Agents can inspect individual signals or rely on the composite score.

### Links

- [OpenTrustSeal website](https://opentrustseal.com)
- [DID Document](/.well-known/did.json) (public signing key)
        """,
        routes=app.routes,
    )
    schema["info"]["x-logo"] = {
        "url": "https://opentrustseal.com/otslogo.png",
        "altText": "OpenTrustSeal",
    }
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(check_router)
app.include_router(token_router)
app.include_router(register_router)


@app.on_event("startup")
async def startup():
    ensure_keys()
    init_db()
    init_transparency_log()


@app.get("/", tags=["Meta"], summary="API information")
async def root():
    """Returns basic API info and links."""
    return {
        "name": "OpenTrustSeal",
        "version": API_VERSION,
        "description": "Trust verification for AI agent commerce",
        "endpoints": {
            "check": "/v1/check/{domain}",
            "request_check": "/v1/check/request",
            "token": "/v1/token/{domain}/ots.json",
            "did_document": "/.well-known/did.json",
            "docs": "/docs",
            "stats": "/stats",
            "health": "/health",
        },
    }


@app.get("/.well-known/did.json", tags=["Meta"], summary="DID Document")
async def did_document():
    """Returns the DID document containing the public Ed25519 signing key.

    Agents use this key to verify that trust tokens were signed by
    OpenTrustSeal and have not been tampered with.
    """
    pub_key = get_public_key_multibase()
    return {
        "@context": "https://www.w3.org/ns/did/v1",
        "id": "did:web:opentrustseal.com",
        "verificationMethod": [
            {
                "id": "did:web:opentrustseal.com#signing-key-1",
                "type": "Ed25519VerificationKey2020",
                "controller": "did:web:opentrustseal.com",
                "publicKeyMultibase": pub_key,
            }
        ],
        "assertionMethod": ["did:web:opentrustseal.com#signing-key-1"],
    }


@app.get("/health", tags=["Meta"], summary="Health check")
async def health():
    """Returns ok if the service is running."""
    return {"status": "ok"}


@app.get("/stats", tags=["Meta"], summary="Registry statistics")
async def stats():
    """Returns aggregate statistics about the trust registry plus
    fetch-tier counters so we can see what percent of homepage fetches
    are resolving at each tier (direct httpx, crawler, proxy), and a
    daily-crawl heartbeat so external monitors can alert on pipeline
    freshness."""
    return {
        **get_stats(),
        "fetch": fetch_stats(),
        "daily_crawl": read_heartbeat(),
    }


def _tranco_bucket(rank: int | None) -> str:
    """Map a Tranco rank into a coarse bucket suitable for public display.

    Buckets match the ones shipped in the dataset CSV's `trancoBucket`
    column so merchants can cross-reference.
    """
    if rank is None:
        return "unlisted"
    if rank <= 100:
        return "top-100"
    if rank <= 1_000:
        return "top-1K"
    if rank <= 10_000:
        return "top-10K"
    if rank <= 100_000:
        return "top-100K"
    if rank <= 500_000:
        return "top-500K"
    return "top-1M"


_VALID_FEEDBACK_SOURCES = {"agent", "merchant"}
_VALID_AGENT_OUTCOMES = {"transaction_success", "transaction_failed", "chargeback", "fraud", "refused", "not_a_merchant", "other"}
_VALID_MERCHANT_OUTCOMES = {"score_incorrect", "data_stale", "registration_mismatch", "content_outdated", "other"}


class FeedbackRequest(BaseModel):
    """Input schema for POST /v1/feedback.

    Either `agent` or `merchant` source. Outcome vocabulary differs by
    source so we can slice the calibration dataset cleanly.
    """
    domain: str = Field(..., min_length=1, max_length=253)
    source: str = Field(..., description="'agent' or 'merchant'")
    outcome: str = Field(..., description="source-specific enum; see docs")
    check_id: str | None = Field(None, description="The checkId the feedback refers to, if known")
    detail: str | None = Field(None, max_length=2000, description="Free-text context, max 2KB")
    submitter_type: str | None = Field(None, max_length=100, description="'langchain-agent', 'crewai-tool', 'site-owner', etc")
    submitter_contact: str | None = Field(None, max_length=200, description="Optional email for follow-up on merchant feedback")


@app.post("/v1/feedback", tags=["Feedback"], summary="Report an outcome against a prior check")
async def submit_feedback(feedback: FeedbackRequest, request: Request):
    """Submit outcome feedback tied to a prior trust check.

    Agent feedback lets us measure post-hoc whether our PROCEED/CAUTION/DENY
    calls tracked actual transaction outcomes (chargebacks, fraud, etc).
    Merchant feedback lets us catch stale data and scoring bugs.

    Validation is intentionally thin. Anything more opinionated belongs
    upstream in the dataset cleanup pass, not in the submission path.
    """
    if feedback.source not in _VALID_FEEDBACK_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {sorted(_VALID_FEEDBACK_SOURCES)}")

    valid_outcomes = _VALID_AGENT_OUTCOMES if feedback.source == "agent" else _VALID_MERCHANT_OUTCOMES
    if feedback.outcome not in valid_outcomes:
        raise HTTPException(
            status_code=400,
            detail=f"outcome must be one of {sorted(valid_outcomes)} for source={feedback.source}",
        )

    ip = request.client.host if request.client else None

    feedback_id = store_feedback(
        domain=feedback.domain.strip().lower(),
        source=feedback.source,
        outcome=feedback.outcome,
        check_id=feedback.check_id,
        detail=feedback.detail,
        submitter_type=feedback.submitter_type,
        submitter_contact=feedback.submitter_contact,
        ip_address=ip,
    )
    return {"id": feedback_id, "status": "received"}


@app.get("/v1/feedback/{domain}", tags=["Feedback"], summary="Feedback summary for a domain")
async def feedback_for_domain(domain: str):
    """Aggregated + recent feedback for a domain.

    Used by the merchant dashboard so site owners can see what agents
    are reporting about them, and by internal ops to spot score-vs-outcome
    divergences.
    """
    return get_feedback_summary(domain.strip().lower())


@app.get("/v1/coverage/{domain}", tags=["Meta"], summary="Is this domain in the dataset?")
async def coverage(domain: str):
    """Merchant-facing self-serve coverage check.

    Returns whether a domain is in the scored dataset, the headline score
    + recommendation if so, the Tranco rank bucket (so merchants can tell
    why they were or were not included in the seed pass), and the
    scoring-model version. Does NOT return the full signed bundle --
    that is what /v1/check/{domain} is for.

    Useful for outreach email links, dashboard pre-checks, and the
    "am I in your dataset?" question merchants ask before registering.
    """
    coverage_data = get_coverage(domain)
    rank = tranco.get_rank(domain)
    coverage_data["trancoRank"] = rank
    coverage_data["trancoBucket"] = _tranco_bucket(rank)
    return coverage_data


@app.get("/v1/stats/dataset", tags=["Meta"], summary="Dataset breakdown by confidence and cautionReason")
async def stats_dataset():
    """Dataset-shaped statistics for the trust registry.

    Breaks totals down by confidence (high/medium/low) and cautionReason
    (incomplete_evidence / weak_signals / new_domain / infrastructure).
    Used by the dataset publication card, merchant outreach targeting,
    and external monitors that want to see how much of the registry is
    agent-usable (PROCEED with non-low confidence) vs
    incomplete-evidence vs actually-weak.
    """
    return get_dataset_stats()


@app.get("/v1/log/{domain}", tags=["Transparency"], summary="Attestation log for a domain")
async def log_domain(domain: str, limit: int = 100):
    """Returns all transparency log entries for a domain, newest first.

    Each entry includes a per-domain hash chain: the entry_hash of each
    entry is referenced as previous_entry_hash by the next entry for the
    same domain. An auditor can verify the chain is unbroken by computing
    each entry's hash and confirming it matches the next entry's
    previous_entry_hash.
    """
    entries = get_log_for_domain(domain, limit=limit)
    return {"domain": domain, "entries": entries, "count": len(entries)}


@app.get("/v1/log/{domain}/verify", tags=["Transparency"], summary="Verify hash chain for a domain")
async def log_verify(domain: str):
    """Verify the per-domain hash chain is intact.

    Returns whether all entries for this domain form an unbroken chain.
    If any entry was retroactively modified or deleted, the chain breaks
    and the specific broken link is identified.
    """
    return verify_chain(domain)


@app.get("/v1/log/latest/entries", tags=["Transparency"], summary="Latest attestation log entries")
async def log_latest(limit: int = 50):
    """Returns the N most recent log entries across all domains."""
    entries = get_latest_entries(limit=limit)
    return {"entries": entries, "count": len(entries)}
