"""Token serving routes for embed.js and cron-based refresh."""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from ..database import get_cached_check, store_check, log_audit
from ..pipeline import run_check
from ..ratelimit import check_rate_limit
from .check import _validate_domain


def _rate_limit(request: Request):
    check_rate_limit(request)


router = APIRouter(prefix="/v1/token", tags=["Token Serving"])


@router.get("/{domain}/ott.json")
async def serve_token(domain: str, _rl=Depends(_rate_limit)):
    """Serve the latest signed trust token for a domain.

    This endpoint is designed for two use cases:
    1. embed.js fetches it to inject token data into the page
    2. Site owners use it in a cron job to refresh their .well-known/ott.json

    Returns cached results if available. If no check exists for the domain,
    runs a fresh check automatically.

    Served with CORS * and cache headers matching the token TTL.
    """
    domain = _validate_domain(domain)

    cached = get_cached_check(domain)
    if cached is not None:
        log_audit("token.served", domain)
        return JSONResponse(
            content=cached,
            headers={
                "Cache-Control": "public, max-age=86400",
                "Access-Control-Allow-Origin": "*",
            },
        )

    # No cached result; run a fresh check
    result = await run_check(domain)
    result_dict = result.model_dump(by_alias=True)
    store_check(domain, result_dict)
    log_audit("token.generated", domain, f"score={result.trust_score}")

    return JSONResponse(
        content=result_dict,
        headers={
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )
