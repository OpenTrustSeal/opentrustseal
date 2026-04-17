"""Domain ownership verification and cross-referencing.

Handles:
1. Verification code generation
2. DNS TXT record checking
3. HTTP file checking
4. Cross-referencing registration data against public signals
"""

import secrets
import dns.resolver
import httpx
import re
from .whois_util import safe_whois
from datetime import datetime, timezone

from .database import get_registration, update_registration_verification, log_audit
from .models.registration import REGISTRATION_SCORE_MAP


def generate_verification_code() -> str:
    """Generate a random verification code."""
    return f"ott-verify-{secrets.token_hex(16)}"


async def check_dns_verification(domain: str, expected_code: str) -> bool:
    """Check if the DNS TXT record contains our verification code."""
    try:
        answers = dns.resolver.resolve(f"_ott-verify.{domain}", "TXT")
        for rdata in answers:
            txt = rdata.to_text().strip('"')
            if expected_code in txt:
                return True
    except Exception:
        pass
    return False


async def check_http_verification(domain: str, expected_code: str) -> bool:
    """Check if the HTTP verification file exists with the correct code."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, verify=False) as client:
            resp = await client.get(f"https://{domain}/.well-known/ott-verify.txt")
            if resp.status_code == 200 and expected_code in resp.text:
                return True
    except Exception:
        pass
    return False


async def verify_domain(domain: str) -> dict:
    """Attempt to verify domain ownership using the stored verification code."""
    reg = get_registration(domain)
    if reg is None:
        return {"verified": False, "error": "Domain not registered"}

    if reg["domain_verified"]:
        return {"verified": True, "message": "Already verified"}

    code = reg["verification_code"]
    method = reg["verification_method"]

    verified = False
    if method == "dns":
        verified = await check_dns_verification(domain, code)
    elif method == "http":
        verified = await check_http_verification(domain, code)
    else:
        # Try both
        verified = await check_dns_verification(domain, code) or \
                   await check_http_verification(domain, code)

    if verified:
        now = datetime.now(timezone.utc).isoformat()
        update_registration_verification(domain, {
            "domain_verified": 1,
            "domain_verified_at": now,
            "status": "active",
            "updated_at": now,
        })
        log_audit("registration.domain_verified", domain)

        # Run cross-referencing now that domain is verified
        await run_cross_references(domain)

        # Invalidate cached score so next check picks up registration
        from .database import _get_conn
        with _get_conn() as conn:
            conn.execute("DELETE FROM scored_results WHERE domain = ?", (domain,))

        return {"verified": True, "message": "Domain verified successfully"}

    return {
        "verified": False,
        "message": f"Verification code not found via {method}",
        "instructions": _get_instructions(domain, code, method),
    }


async def run_cross_references(domain: str) -> dict:
    """Cross-reference registration data against public signals."""
    reg = get_registration(domain)
    if reg is None:
        return {}

    results = {}
    biz_name = reg["business_name"].lower().strip()

    # 1. Email domain match
    email = reg["contact_email"] or ""
    if email and "@" in email:
        email_domain = email.split("@")[1].lower()
        results["email_domain_match"] = int(
            email_domain == domain or domain.endswith(f".{email_domain}")
        )

    # 2. Business name vs WHOIS org
    try:
        w = safe_whois(domain)
        whois_org = (w.org or "").lower().strip()
        if whois_org and biz_name:
            # Fuzzy match: check if key words appear
            biz_words = set(re.sub(r'[,.\-]', ' ', biz_name).split())
            biz_words -= {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the"}
            whois_words = set(re.sub(r'[,.\-]', ' ', whois_org).split())
            if biz_words and biz_words & whois_words:
                overlap = len(biz_words & whois_words) / len(biz_words)
                results["business_name_whois_match"] = int(overlap >= 0.5)
            else:
                results["business_name_whois_match"] = 0
        else:
            results["business_name_whois_match"] = 0
    except Exception:
        results["business_name_whois_match"] = 0

    # 3. Business name vs SSL cert org
    try:
        import ssl
        import socket
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                cert_org = ""
                for rdn in cert.get("subject", ()):
                    for attr_type, attr_value in rdn:
                        if attr_type == "organizationName":
                            cert_org = attr_value.lower().strip()
                if cert_org and biz_name:
                    biz_words = set(re.sub(r'[,.\-]', ' ', biz_name).split())
                    biz_words -= {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the"}
                    cert_words = set(re.sub(r'[,.\-]', ' ', cert_org).split())
                    if biz_words and biz_words & cert_words:
                        results["business_name_cert_match"] = 1
                    else:
                        results["business_name_cert_match"] = 0
                else:
                    results["business_name_cert_match"] = 0
    except Exception:
        results["business_name_cert_match"] = 0

    # 4. Phone match (check if phone on site matches registration)
    if reg.get("phone"):
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, verify=False) as client:
                resp = await client.get(f"https://{domain}/")
                if resp.status_code < 400:
                    # Normalize phone: strip non-digits for comparison
                    reg_phone = re.sub(r'\D', '', reg["phone"])
                    if len(reg_phone) >= 7 and reg_phone in re.sub(r'\D', '', resp.text):
                        results["phone_verified"] = 1
                    else:
                        results["phone_verified"] = 0
                else:
                    results["phone_verified"] = 0
        except Exception:
            results["phone_verified"] = 0

    # 5. Social media bidirectional link check
    social_verified = False
    if reg.get("social_twitter"):
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, verify=False) as client:
                resp = await client.get(f"https://{domain}/")
                if resp.status_code < 400:
                    twitter_handle = reg["social_twitter"].rstrip("/").split("/")[-1].lower()
                    if twitter_handle in resp.text.lower():
                        social_verified = True
        except Exception:
            pass

    if reg.get("social_linkedin") and not social_verified:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, verify=False) as client:
                resp = await client.get(f"https://{domain}/")
                if resp.status_code < 400:
                    if "linkedin.com" in resp.text.lower():
                        social_verified = True
        except Exception:
            pass

    results["social_verified"] = int(social_verified)

    # 6. EIN verification (placeholder, needs IRS API or partner)
    if reg.get("ein_tax_id"):
        # For now: mark as submitted but not auto-verified
        # Real verification would hit IRS EIN lookup or a partner API
        results["ein_verified"] = 0  # requires manual or partner verification

    # 7. Address verification (placeholder)
    if reg.get("address"):
        results["address_verified"] = 0  # requires USPS/Google Places API

    # 8. Business registry match (placeholder)
    results["business_registry_match"] = 0  # requires state registry or Companies House API

    # Compute verification score
    score = 0
    if reg["domain_verified"]:
        score += REGISTRATION_SCORE_MAP["domain_verified"]
    if results.get("email_domain_match"):
        score += REGISTRATION_SCORE_MAP["email_domain_match"]
    if results.get("business_name_whois_match") or results.get("business_name_cert_match"):
        score += REGISTRATION_SCORE_MAP["business_name_matches"]
    if results.get("ein_verified"):
        score += REGISTRATION_SCORE_MAP["ein_verified"]
    if results.get("phone_verified"):
        score += REGISTRATION_SCORE_MAP["phone_verified"]
    if results.get("address_verified"):
        score += REGISTRATION_SCORE_MAP["address_verified"]
    if results.get("social_verified"):
        score += REGISTRATION_SCORE_MAP["social_verified"]
    if results.get("business_registry_match"):
        score += REGISTRATION_SCORE_MAP["business_registry"]

    results["verification_score"] = score
    results["updated_at"] = datetime.now(timezone.utc).isoformat()

    update_registration_verification(domain, results)
    log_audit("registration.cross_referenced", domain, f"score={score}")

    return results


def _get_instructions(domain: str, code: str, method: str) -> dict:
    if method == "dns":
        return {
            "method": "dns",
            "steps": [
                f"Add a TXT record to your DNS",
                f"Name: _ott-verify.{domain}",
                f"Type: TXT",
                f"Value: {code}",
                "Wait a few minutes for DNS propagation, then try again",
            ],
        }
    else:
        return {
            "method": "http",
            "steps": [
                f"Create a file at: https://{domain}/.well-known/ott-verify.txt",
                f"Contents: {code}",
                "Make sure it is accessible over HTTPS, then try again",
            ],
        }
