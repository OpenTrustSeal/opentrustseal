"""Trust checklist generator.

Produces an actionable list of items site owners can fix to improve
their trust score. Each item includes status, impact, and how to fix it.
"""

from .models.signals import SignalBundle


def generate_checklist(signals: SignalBundle, is_registered: bool = False) -> list[dict]:
    """Generate a checklist from signal data."""
    items = []

    # === SSL / TLS ===
    items.append({
        "category": "Security",
        "item": "Valid SSL certificate",
        "status": "pass" if signals.ssl.valid else "fail",
        "impact": "high",
        "fix": "Install a free SSL certificate from Let's Encrypt. Most hosting providers offer one-click SSL setup.",
    })

    if signals.ssl.valid:
        items.append({
            "category": "Security",
            "item": "TLS 1.3",
            "status": "pass" if signals.ssl.tls_version == "TLSv1.3" else "improve",
            "impact": "low",
            "fix": "Update your server's TLS configuration to support TLS 1.3. Check with your hosting provider.",
        })

        items.append({
            "category": "Security",
            "item": "HSTS header",
            "status": "pass" if signals.ssl.hsts else "fail",
            "impact": "medium",
            "fix": "Add the Strict-Transport-Security header to your server config. Apache: 'Header always set Strict-Transport-Security \"max-age=31536000\"'. Nginx: 'add_header Strict-Transport-Security \"max-age=31536000\" always;'",
        })

    # === DNS ===
    items.append({
        "category": "Email Security",
        "item": "SPF record",
        "status": "pass" if signals.dns.spf else "fail",
        "impact": "medium",
        "fix": "Add a TXT record to your DNS: 'v=spf1 include:_spf.google.com ~all' (adjust for your email provider).",
    })

    items.append({
        "category": "Email Security",
        "item": "DMARC record",
        "status": "pass" if signals.dns.dmarc else "fail",
        "impact": "medium",
        "fix": "Add a TXT record at _dmarc.yourdomain.com: 'v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com'",
    })

    items.append({
        "category": "DNS Security",
        "item": "DNSSEC",
        "status": "pass" if signals.dns.dnssec else "improve",
        "impact": "low",
        "fix": "Enable DNSSEC through your domain registrar. This prevents DNS spoofing attacks.",
    })

    items.append({
        "category": "DNS Security",
        "item": "CAA record",
        "status": "pass" if signals.dns.caa else "improve",
        "impact": "low",
        "fix": "Add a CAA DNS record specifying which certificate authorities can issue certs for your domain.",
    })

    # === Content ===
    items.append({
        "category": "Legal",
        "item": "Privacy policy",
        "status": "pass" if signals.content.privacy_policy else "fail",
        "impact": "high",
        "fix": "Add a privacy policy page at /privacy or /privacy-policy. Link to it from your footer. Required by GDPR and CCPA.",
    })

    items.append({
        "category": "Legal",
        "item": "Terms of service",
        "status": "pass" if signals.content.terms_of_service else "fail",
        "impact": "high",
        "fix": "Add a terms of service page at /terms. Link to it from your footer. Essential for any site accepting payments.",
    })

    items.append({
        "category": "Trust",
        "item": "Contact information",
        "status": "pass" if signals.content.contact_info else "fail",
        "impact": "high",
        "fix": "Add visible contact information (email, phone, or physical address) to your site. A /contact page linked from the footer is standard.",
    })

    # === Identity ===
    items.append({
        "category": "Identity",
        "item": "Registered with OpenTrustSeal",
        "status": "pass" if is_registered else "available",
        "impact": "high",
        "fix": "Register your domain at opentrustseal.com/register.html to prove ownership and provide business details. Each verified field earns points (up to +30). Free registration.",
    })

    items.append({
        "category": "Identity",
        "item": "WHOIS information disclosed",
        "status": "pass" if signals.identity.whois_disclosed else "improve",
        "impact": "medium",
        "fix": "Consider disabling WHOIS privacy protection to show your business identity publicly. This is optional but increases trust.",
    })

    # === Maturity (from content collector internal data) ===
    # These are detected by the content collector but stored as score components
    has_security_txt = getattr(signals.content, '_has_security_txt', False)
    has_robots = getattr(signals.content, '_has_robots', False)
    sec_headers = getattr(signals.content, '_security_header_count', 0)

    items.append({
        "category": "Security",
        "item": "security.txt file",
        "status": "pass" if has_security_txt else "improve",
        "impact": "medium",
        "fix": "Add a security.txt file at /.well-known/security.txt per RFC 9116. Include contact info for security researchers. Generator: securitytxt.org",
    })

    items.append({
        "category": "Technical",
        "item": "robots.txt file",
        "status": "pass" if has_robots else "improve",
        "impact": "low",
        "fix": "Add a robots.txt file at /robots.txt. Even a simple 'User-agent: *\\nAllow: /' shows operational maturity.",
    })

    items.append({
        "category": "Security",
        "item": "Security headers (CSP, X-Frame-Options)",
        "status": "pass" if sec_headers >= 3 else ("improve" if sec_headers >= 1 else "fail"),
        "impact": "medium",
        "fix": "Add HTTP security headers: Content-Security-Policy, X-Frame-Options, X-Content-Type-Options, Referrer-Policy. These protect your visitors and signal security maturity.",
    })

    # Sort: fails first, then improve, then pass
    order = {"fail": 0, "available": 1, "improve": 2, "pass": 3}
    items.sort(key=lambda x: (order.get(x["status"], 4), x["category"]))

    return items


def checklist_summary(items: list[dict]) -> dict:
    """Summarize checklist pass/fail counts."""
    passing = sum(1 for i in items if i["status"] == "pass")
    failing = sum(1 for i in items if i["status"] == "fail")
    improvable = sum(1 for i in items if i["status"] in ("improve", "available"))
    return {
        "total": len(items),
        "passing": passing,
        "failing": failing,
        "improvable": improvable,
    }
