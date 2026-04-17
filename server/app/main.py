"""OpenTrustToken API Server.

Run with: uvicorn app.main:app --reload
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from .routes.check import router as check_router
from .routes.token import router as token_router
from .routes.register import router as register_router
from .signing import ensure_keys, get_public_key_multibase
from .database import init_db, get_stats
from .fetch_escalation import stats as fetch_stats
from .heartbeat import read_heartbeat

API_VERSION = "0.2.0"

app = FastAPI(
    title="OpenTrustToken API",
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="OpenTrustToken API",
        version=API_VERSION,
        description="""
## Trust verification for AI agent commerce

OpenTrustToken provides pre-transaction trust checks for AI agents. One API call returns a cryptographically signed evidence bundle with a trust score and actionable checklist.

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
- **Ed25519 signature** proving the result was issued by OpenTrustToken

### Authentication

Free tier requires no authentication. Rate limit: 60 requests/minute, 10,000/month.

### Scoring model

Current model: `ott-v1.2-weights`. Weights: reputation 30%, identity 25%, content 17%, domain age 10%, SSL 10%, DNS 8%.

The trust score is a computed summary of observable evidence. Every input is visible in the signals object. Agents can inspect individual signals or rely on the composite score.

### Links

- [OpenTrustToken website](https://opentrusttoken.com)
- [DID Document](/.well-known/did.json) (public signing key)
        """,
        routes=app.routes,
    )
    schema["info"]["x-logo"] = {
        "url": "https://opentrusttoken.com/ottlogo.png",
        "altText": "OpenTrustToken",
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


@app.get("/", tags=["Meta"], summary="API information")
async def root():
    """Returns basic API info and links."""
    return {
        "name": "OpenTrustToken",
        "version": API_VERSION,
        "description": "Trust verification for AI agent commerce",
        "endpoints": {
            "check": "/v1/check/{domain}",
            "request_check": "/v1/check/request",
            "token": "/v1/token/{domain}/ott.json",
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
    OpenTrustToken and have not been tampered with.
    """
    pub_key = get_public_key_multibase()
    return {
        "@context": "https://www.w3.org/ns/did/v1",
        "id": "did:web:opentrusttoken.com",
        "verificationMethod": [
            {
                "id": "did:web:opentrusttoken.com#signing-key-1",
                "type": "Ed25519VerificationKey2020",
                "controller": "did:web:opentrusttoken.com",
                "publicKeyMultibase": pub_key,
            }
        ],
        "assertionMethod": ["did:web:opentrusttoken.com#signing-key-1"],
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
