"""Check API routes (/v1/check)."""

import re
from fastapi import APIRouter, HTTPException, Query, Request, Depends

from ..models.token import CheckResponse, CheckRequestBody, ErrorResponse
from ..pipeline import run_check
from ..database import get_cached_check, store_check, log_audit, get_score_history, get_registration_public
from ..ratelimit import check_rate_limit


def _rate_limit(request: Request):
    check_rate_limit(request)

router = APIRouter(prefix="/v1/check", tags=["Trust Checks"])

_DOMAIN_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,}$"
)


def _validate_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if domain.startswith("http://"):
        domain = domain[7:]
    if domain.startswith("https://"):
        domain = domain[8:]
    domain = domain.split("/")[0]
    domain = domain.split(":")[0]

    if not _DOMAIN_RE.match(domain):
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_DOMAIN", "message": f"'{domain}' is not a valid domain name"},
        )
    return domain


@router.get(
    "/{domain}",
    response_model=CheckResponse,
    response_model_by_alias=True,
    responses={
        404: {"model": ErrorResponse},
        400: {"model": ErrorResponse},
    },
)
async def check_domain(
    domain: str,
    refresh: bool = Query(False, description="Force a fresh check, ignoring cache"),
    _rl=Depends(_rate_limit),
):
    """Check a domain's trust signals and return a signed evidence bundle.

    Returns six signal categories (domain age, SSL, DNS, content, reputation,
    identity), a computed trust score (0-100), a PROCEED/CAUTION/DENY
    recommendation, and an actionable checklist of improvements.

    Results are cached for 7 days. Pass `?refresh=true` to force a fresh check.

    The response is signed with Ed25519. Verify the signature against the
    public key at `/.well-known/did.json`.
    """
    domain = _validate_domain(domain)

    if not refresh:
        cached = get_cached_check(domain)
        if cached is not None:
            log_audit("check.cache_hit", domain)
            return cached

    result = await run_check(domain)
    result_dict = result.model_dump(by_alias=True)
    store_check(domain, result_dict)
    log_audit("check.fresh", domain, f"score={result.trust_score}")
    return result_dict


@router.post(
    "/request",
    response_model=CheckResponse,
    response_model_by_alias=True,
    status_code=200,
)
async def request_check(body: CheckRequestBody, _rl=Depends(_rate_limit)):
    """Request a fresh trust check for a domain.

    Always runs all signal collectors regardless of cache state.
    Use this when you need guaranteed-fresh results, such as after
    a site owner has made improvements to their configuration.
    """
    domain = _validate_domain(body.domain)
    result = await run_check(domain)
    result_dict = result.model_dump(by_alias=True)
    store_check(domain, result_dict)
    log_audit("check.requested", domain, f"score={result.trust_score}")
    return result_dict


@router.get(
    "/{domain}/dashboard",
    summary="Dashboard data for a domain",
    tags=["Trust Checks"],
)
async def dashboard_data(domain: str, _rl=Depends(_rate_limit)):
    """Get dashboard data: current check, score history, and registration status.

    Used by the site owner dashboard to display a comprehensive view.
    """
    domain = _validate_domain(domain)

    # Current check (from cache or fresh)
    cached = get_cached_check(domain)
    if cached is None:
        result = await run_check(domain)
        cached = result.model_dump(by_alias=True)
        store_check(domain, cached)
        log_audit("dashboard.fresh", domain, f"score={result.trust_score}")
    else:
        log_audit("dashboard.cache_hit", domain)

    # Score history
    history = get_score_history(domain)

    # Registration status
    registration = get_registration_public(domain)

    return {
        "current": cached,
        "history": history,
        "registration": registration,
    }
