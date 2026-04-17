"""Registration API routes (/v1/register)."""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Depends

from ..models.registration import RegistrationRequest
from ..database import (
    get_registration, save_registration, get_registration_public, log_audit,
)
from ..verification import generate_verification_code, verify_domain, _get_instructions
from ..ratelimit import check_rate_limit

router = APIRouter(prefix="/v1/register", tags=["Registration"])


def _rate_limit(request: Request):
    check_rate_limit(request)


@router.post(
    "",
    status_code=201,
    summary="Register a domain",
)
async def register_domain(body: RegistrationRequest, request: Request, _rl=Depends(_rate_limit)):
    """Register a domain for trust verification.

    Submit business information to begin the registration process.
    You'll receive a verification code and instructions to prove
    domain ownership. Once verified, your data is cross-referenced
    against public records and your trust score is updated.

    Required: domain, business_name, country, business_type,
    website_category, contact_email.

    Optional (more data = more score): contact_name, phone, address,
    ein_tax_id, year_established, social_twitter, social_linkedin.
    """
    domain = body.domain.strip().lower()

    # Check if already registered
    existing = get_registration(domain)
    if existing and existing["status"] == "active":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ALREADY_REGISTERED",
                "message": f"{domain} is already registered and verified",
            },
        )

    code = generate_verification_code()
    now = datetime.now(timezone.utc).isoformat()

    reg_data = {
        "domain": domain,
        "business_name": body.business_name,
        "country": body.country,
        "state_province": body.state_province,
        "business_type": body.business_type,
        "website_category": body.website_category,
        "year_established": body.year_established,
        "contact_name": body.contact_name,
        "contact_email": body.contact_email,
        "phone": body.phone,
        "address": body.address,
        "ein_tax_id": body.ein_tax_id,
        "social_twitter": body.social_twitter,
        "social_linkedin": body.social_linkedin,
        "verification_code": code,
        "verification_method": body.verification_method,
        "registered_at": now,
    }

    save_registration(reg_data)
    log_audit("registration.submitted", domain, f"method={body.verification_method}")

    instructions = _get_instructions(domain, code, body.verification_method)

    return {
        "domain": domain,
        "status": "pending_verification",
        "verificationMethod": body.verification_method,
        "verificationCode": code,
        "instructions": instructions,
        "message": "Follow the instructions to verify domain ownership, then call POST /v1/register/verify",
    }


@router.post(
    "/verify",
    summary="Verify domain ownership",
)
async def verify_registration(body: dict, _rl=Depends(_rate_limit)):
    """Verify domain ownership after adding the DNS record or HTTP file.

    Once verified, registration data is automatically cross-referenced
    against public records (WHOIS, SSL cert, site content) and the
    verification score is computed.
    """
    domain = body.get("domain", "").strip().lower()
    if not domain:
        raise HTTPException(status_code=400, detail={"error": "MISSING_DOMAIN"})

    result = await verify_domain(domain)

    if result.get("verified"):
        public = get_registration_public(domain)
        return {
            "domain": domain,
            "status": "active",
            "verified": True,
            "verificationScore": public.get("verificationScore", 0) if public else 0,
            "profile": public,
            "message": "Domain verified. Your trust score has been updated.",
        }

    return {
        "domain": domain,
        "status": "pending_verification",
        "verified": False,
        "message": result.get("message", "Verification failed"),
        "instructions": result.get("instructions"),
    }


@router.get(
    "/{domain}",
    summary="Get registration status",
)
async def get_status(domain: str, _rl=Depends(_rate_limit)):
    """Get the registration status and public verification profile for a domain.

    Returns only public fields. Private data (email, phone, address, EIN)
    is never exposed through the API.
    """
    domain = domain.strip().lower()
    reg = get_registration(domain)

    if reg is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_REGISTERED",
                "message": f"{domain} is not registered with OpenTrustToken",
                "suggestion": "Register at POST /v1/register",
            },
        )

    public = get_registration_public(domain)

    # If pending, include instructions
    if reg["status"] == "pending":
        instructions = _get_instructions(
            domain, reg["verification_code"], reg["verification_method"]
        )
        public["instructions"] = instructions

    return public
