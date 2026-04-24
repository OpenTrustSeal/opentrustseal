"""Check pipeline: orchestrates signal collection, scoring, signing, and checklist."""

import asyncio
from datetime import datetime, timedelta, timezone

from .collectors import domain_age, ssl_check, dns_check, content_check
from .collectors import reputation_check, identity_check
from .collectors import historical_whois
from .models.signals import (
    SignalBundle, DomainAgeSignal, SSLSignal, DNSSignal,
    ContentSignal, ReputationSignal,
)
from .models.token import CheckResponse, ChecklistSummary
from . import scoring, signing
from .checklist import generate_checklist, checklist_summary
from .transparency import log_attestation
from .database import (
    store_raw_signals, is_registered, store_score_snapshot,
    get_latest_raw_content, log_audit,
)
from .collectors.jurisdiction import detect_jurisdiction


def _content_signal_from_raw(raw: dict) -> ContentSignal:
    """Rebuild a ContentSignal (including private fields) from a stored raw row.

    Used when the current content fetch hit a transient failure and we want
    to reuse the last-known-good signals instead of writing zeros to history.
    """
    sig = ContentSignal(
        privacyPolicy=bool(raw.get("privacyPolicy")),
        termsOfService=bool(raw.get("termsOfService")),
        contactInfo=bool(raw.get("contactInfo")),
        score=0,  # recomputed below from category
    )
    sig._hsts = False  # stored separately via SSL / we can't recover it here
    sig._has_security_txt = bool(raw.get("hasSecurityTxt"))
    sig._has_robots = bool(raw.get("hasRobots"))
    sig._has_sitemap = bool(raw.get("hasSitemap"))
    sig._security_header_count = int(raw.get("securityHeaderCount") or 0)
    sig._payment_processors = list(raw.get("paymentProcessors") or [])
    sig._tech_stack = list(raw.get("techStack") or [])
    sig._has_cookie_consent = bool(raw.get("hasCookieConsent"))
    sig._redirect_count = int(raw.get("redirectCount") or 0)
    sig._response_time_ms = int(raw.get("responseTimeMs") or 0)
    sig._social_links = list(raw.get("socialLinks") or [])
    sig._structured_data = dict(raw.get("structuredData") or {})
    sig._site_category = raw.get("siteCategory") or "consumer"
    sig._has_api_docs = bool(raw.get("hasApiDocs"))
    sig._has_api_paths = bool(raw.get("hasApiPaths"))
    sig._has_status_page = bool(raw.get("hasStatusPage"))
    sig._fetch_failed = False
    sig._reused_from_history = True

    # Recompute score from the reused signals using the same logic as
    # content_check.collect(). We can't call the collector's internal path
    # because it's interleaved with network calls, so mirror the formula.
    if sig._site_category in ("infrastructure", "api_service"):
        score = 0
        if sig._security_header_count >= 4:
            score += 40
        elif sig._security_header_count >= 3:
            score += 30
        elif sig._security_header_count >= 1:
            score += 20
        if sig._has_api_docs or sig._has_api_paths:
            score += 20
        if sig._has_status_page:
            score += 10
        if sig._has_robots:
            score += 5
        if sig._has_security_txt:
            score += 10
        if sig._has_sitemap:
            score += 5
        if sig.privacy_policy:
            score += 5
        if sig.terms_of_service:
            score += 5
    else:
        found = sum([sig.privacy_policy, sig.terms_of_service, sig.contact_info])
        score = {0: 0, 1: 30, 2: 50, 3: 70}[found]
        if sig._has_robots:
            score += 5
        if sig._has_security_txt:
            score += 10
        if sig._security_header_count >= 3:
            score += 15
        elif sig._security_header_count >= 1:
            score += 5
    sig.score = min(100, score)
    return sig


# In-flight dedupe: if the same worker is already running a check for a
# domain, concurrent callers wait for that task instead of starting their
# own. This prevents the N-writes-within-1-second pattern seen on sephora
# when the dashboard endpoint got hit multiple times in quick succession.
# Cross-worker races aren't covered (would need a Redis or SQLite lock);
# in practice this dedupe is enough since uvicorn only runs 2 workers.
_IN_FLIGHT: dict[str, asyncio.Task] = {}


async def run_check(domain: str) -> CheckResponse:
    """Run all signal collectors, score, sign, and return a CheckResponse."""

    existing = _IN_FLIGHT.get(domain)
    if existing is not None and not existing.done():
        # Another coroutine on this worker is already running the same check.
        # Wait for its result instead of launching a duplicate pipeline.
        return await existing

    task = asyncio.create_task(_run_check_inner(domain))
    _IN_FLIGHT[domain] = task
    try:
        return await task
    finally:
        # Clean up only if this task is still the registered one (guard
        # against a newer task having replaced us while we were awaiting).
        if _IN_FLIGHT.get(domain) is task:
            _IN_FLIGHT.pop(domain, None)


async def _run_check_inner(domain: str) -> CheckResponse:
    # Phase 1: run independent collectors in parallel
    age_result, dns_result, content_result, reputation_result, hist_whois_result = await asyncio.gather(
        domain_age.collect(domain),
        dns_check.collect(domain),
        content_check.collect(domain),
        reputation_check.collect(domain),
        historical_whois.collect(domain),
        return_exceptions=True,
    )

    if isinstance(age_result, Exception):
        age_result = DomainAgeSignal()
    if isinstance(dns_result, Exception):
        dns_result = DNSSignal()
    if isinstance(content_result, Exception):
        content_result = ContentSignal()
        content_result._fetch_failed = True
    if isinstance(reputation_result, Exception):
        reputation_result = ReputationSignal(score=80)
    if isinstance(hist_whois_result, Exception):
        hist_whois_result = historical_whois.HistoricalWhoisSignal(
            enabled=False, error=f"{type(hist_whois_result).__name__}"
        )

    # Recover from transient content fetch failures by reusing the last
    # known-good signals. Without this, one timeout / 403 / bot block wipes
    # out content.score and poisons score_history + scored_results until the
    # next successful crawl. See CLAUDE.md "score fluctuation" bug.
    content_unscorable = False
    if getattr(content_result, "_fetch_failed", False):
        last_good = get_latest_raw_content(domain)
        # A row is usable recovery data only if a prior fetch actually
        # parsed something off the homepage. A row of all zeros (no legal
        # links, no security headers) came from a prior fetch_failed and
        # substituting it would keep the site stuck at content=0 forever.
        has_real_content = bool(
            last_good and (
                last_good.get("privacyPolicy")
                or last_good.get("termsOfService")
                or last_good.get("contactInfo")
                or (last_good.get("securityHeaderCount") or 0) > 0
            )
        )
        if has_real_content:
            content_result = _content_signal_from_raw(last_good)
            log_audit(
                "content.reused_from_history",
                domain,
                "transient fetch failure, substituted last-good content signals",
            )
        else:
            # No prior good data to recover from. Mark content as unscorable
            # so compute_score drops its 17% weight from the aggregate and
            # renormalizes the other five signals. Without this, a blocked
            # site like chewy.com would score content=0 and look like it has
            # no privacy/terms/contact -- which is wrong. Unscorable !=
            # "site has no content", it means "we couldn't look."
            content_unscorable = True
            log_audit(
                "content.unscorable",
                domain,
                "fetch failed and no recoverable history; excluding content from score",
            )

    # Phase 2: SSL check uses HSTS from content check
    hsts = getattr(content_result, "_hsts", False)
    ssl_result = await ssl_check.collect(domain, hsts=hsts)
    if isinstance(ssl_result, Exception):
        ssl_result = SSLSignal()
        ssl_result._subject_org = ""

    # Phase 3: Identity check uses data from content + SSL
    contact_on_site = getattr(content_result, "contact_info", False)
    ssl_subject_org = getattr(ssl_result, "_subject_org", "")

    # Fetch page body for schema.org check + check for .well-known/ots.json
    page_body = ""
    has_ott_file = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, verify=False) as client:
            resp = await client.get(f"https://{domain}/")
            if resp.status_code < 400:
                page_body = resp.text[:100_000]

            ott_resp = await client.get(f"https://{domain}/.well-known/ots.json")
            if ott_resp.status_code == 200:
                try:
                    ott_data = ott_resp.json()
                    if "trustScore" in ott_data or "signals" in ott_data:
                        has_ott_file = True
                except Exception:
                    pass
    except Exception:
        pass

    # Get Tranco rank for identity signal
    tranco_rank = getattr(reputation_result, '_tranco_rank', None)

    identity_result = await identity_check.collect(
        domain,
        contact_on_site=contact_on_site,
        ssl_subject_org=ssl_subject_org,
        page_body=page_body,
        has_ott_file=has_ott_file,
        tranco_rank=tranco_rank,
    )

    signals = SignalBundle(
        domainAge=age_result,
        ssl=ssl_result,
        dns=dns_result,
        content=content_result,
        reputation=reputation_result,
        identity=identity_result,
    )

    # Preserve internal metadata on signals for checklist generation
    signals.content._has_security_txt = getattr(content_result, '_has_security_txt', False)
    signals.content._has_robots = getattr(content_result, '_has_robots', False)
    signals.content._security_header_count = getattr(content_result, '_security_header_count', 0)

    # Parent-company linkage. If the domain matches a known infrastructure
    # provider (AWS CloudFront, GCP, Vercel, Shopify, etc.), we override the
    # site category and carry the parent identity into raw_signals so the
    # scoring path treats this as infrastructure rather than a consumer
    # merchant missing a privacy policy.
    from .collectors import parent_company as _pc
    parent_match = _pc.lookup(domain)
    if parent_match and _pc.is_infrastructure_category(parent_match.category):
        # Only override when content didn't already detect a consumer storefront.
        # Some domains like shopify.com are themselves consumer-facing even
        # though they match their own pattern; defer to content's judgment when
        # content already reports a consumer category with real content signals.
        if not (
            getattr(content_result, "_site_category", "consumer") == "consumer"
            and (getattr(content_result, "privacy_policy", False)
                 or getattr(content_result, "terms_of_service", False))
        ):
            content_result._site_category = "infrastructure"

    # Merge historical WHOIS findings into age_result._registrant_change so
    # compute_score's existing "recent ownership change" path picks them up.
    existing_change = getattr(age_result, '_registrant_change', {}) or {}
    if hist_whois_result.enabled:
        existing_change.setdefault("source", "historical_whois_api")
        existing_change.setdefault("recordCount", hist_whois_result.record_count)
        existing_change.setdefault("earliestSeen", hist_whois_result.earliest_seen)
        existing_change["recentChange"] = hist_whois_result.registrant_changed_recently
        existing_change["recentChangeAt"] = hist_whois_result.recent_change_at
        if hist_whois_result.error:
            existing_change["error"] = hist_whois_result.error
        age_result._registrant_change = existing_change

    # Store raw signal data (facts only, no scores) for re-scoring later
    raw_data = {
        "domainAge": {
            "registeredDate": age_result.registered_date,
            "band": age_result.band,
            "registrantChange": getattr(age_result, '_registrant_change', {}),
        },
        "ssl": {
            "valid": ssl_result.valid,
            "issuer": ssl_result.issuer,
            "tlsVersion": ssl_result.tls_version,
            "hsts": ssl_result.hsts,
            "subjectOrg": getattr(ssl_result, '_subject_org', ''),
        },
        "dns": {
            "spf": dns_result.spf,
            "dmarc": dns_result.dmarc,
            "dnssec": dns_result.dnssec,
            "caa": dns_result.caa,
        },
        "content": {
            "privacyPolicy": content_result.privacy_policy,
            "termsOfService": content_result.terms_of_service,
            "contactInfo": content_result.contact_info,
            "hasSecurityTxt": getattr(content_result, '_has_security_txt', False),
            "hasRobots": getattr(content_result, '_has_robots', False),
            "hasSitemap": getattr(content_result, '_has_sitemap', False),
            "securityHeaderCount": getattr(content_result, '_security_header_count', 0),
            "paymentProcessors": getattr(content_result, '_payment_processors', []),
            "techStack": getattr(content_result, '_tech_stack', []),
            "hasCookieConsent": getattr(content_result, '_has_cookie_consent', False),
            "redirectCount": getattr(content_result, '_redirect_count', 0),
            "responseTimeMs": getattr(content_result, '_response_time_ms', 0),
            "socialLinks": getattr(content_result, '_social_links', []),
            "_unscorable": content_unscorable,
            "structuredData": getattr(content_result, '_structured_data', {}),
            "siteCategory": getattr(content_result, '_site_category', 'consumer'),
            "hasApiDocs": getattr(content_result, '_has_api_docs', False),
            "hasApiPaths": getattr(content_result, '_has_api_paths', False),
            "hasStatusPage": getattr(content_result, '_has_status_page', False),
        },
        "reputation": {
            "malware": reputation_result.malware,
            "phishing": reputation_result.phishing,
            "spamListed": reputation_result.spam_listed,
            "trancoRank": getattr(reputation_result, '_tranco_rank', None),
            "blocklists": getattr(reputation_result, '_blocklist_detail', {}),
        },
        "identity": {
            "whoisDisclosed": identity_result.whois_disclosed,
            "businessDirectory": identity_result.business_directory,
            "contactOnSite": identity_result.contact_on_site,
            "hasOttFile": has_ott_file,
            "sslSubjectOrg": ssl_subject_org,
            "hasSchemaOrg": bool(page_body and '"@type"' in page_body and '"Organization"' in page_body),
            "isPublicCompany": getattr(identity_result, '_is_public_company', False),
            "gdprRedacted": getattr(identity_result, '_gdpr_redacted', False),
            "cctldBonus": getattr(identity_result, '_cctld_bonus', 0),
            "parentCompany": ({
                "parent": parent_match.parent,
                "parentName": parent_match.parent_name,
                "category": parent_match.category,
                "matchedSuffix": parent_match.matched_suffix,
            } if parent_match else None),
            "whoisCountry": "",  # populated after jurisdiction detection
        },
    }
    store_raw_signals(domain, raw_data)

    registered = is_registered(domain)

    # Get registration verification score if registered
    reg_verification_score = 0
    if registered:
        from .database import get_registration
        reg = get_registration(domain)
        if reg:
            reg_verification_score = reg.get("verification_score", 0)

    # Compute domain age in days first so the well-known brand detector
    # has everything it needs before compute_score runs.
    domain_age_days = -1
    if age_result.registered_date:
        try:
            reg = datetime.strptime(age_result.registered_date, "%Y-%m-%d")
            domain_age_days = (datetime.now() - reg).days
        except ValueError:
            pass

    # Well-known brand anchor: compositional evidence of trust for aged
    # top-Tranco domains with clean reputation. Applied as an identity
    # floor and final-score floor inside compute_score.
    well_known = scoring.is_well_known_brand(signals, domain_age_days)

    # v1.4 consensus tier: top-100 Tranco + 10-year domain age raises
    # the identity ceiling from 55 to 75, spreading top brands above
    # the 75 anchor floor into the 80-90 range.
    consensus = scoring.is_consensus_tier(signals, domain_age_days)

    # Compute score and recommendation
    trust_score = scoring.compute_score(
        signals, is_registered=registered, domain=domain,
        registration_score=reg_verification_score,
        content_scorable=not content_unscorable,
        well_known_brand=well_known,
        consensus_tier=consensus,
    )

    # Monitoring alerts from signal analysis
    monitoring_alerts = []
    registrant_change = getattr(age_result, '_registrant_change', {})
    if registrant_change.get("possibleOwnershipChange"):
        monitoring_alerts.append("POSSIBLE_OWNERSHIP_CHANGE")

    flags = scoring.compute_flags(
        signals, trust_score, domain_age_days,
        monitoring_alerts=monitoring_alerts,
        well_known_brand=well_known,
    )
    if content_unscorable:
        flags.append("CONTENT_UNSCORABLE")
        # Distinguish "fetch failed and we care" from "fetch failed but the
        # brand anchor covers this domain so the missing content signal is
        # not a scoring concern." Dashboard and SDK consumers can render
        # these cases differently without having to combine brand_tier and
        # CONTENT_UNSCORABLE themselves.
        if well_known:
            flags.append("ANCHOR_ONLY")
    recommendation = scoring.compute_recommendation(trust_score, flags)

    confidence = scoring.compute_confidence(
        signals, content_scorable=not content_unscorable,
        domain_age_days=domain_age_days,
    )
    caution_reason = scoring.compute_caution_reason(
        signals, trust_score, domain_age_days,
        content_scorable=not content_unscorable,
        confidence=confidence,
        site_category=getattr(content_result, '_site_category', 'consumer'),
    )

    reasoning = scoring.generate_reasoning(
        signals, trust_score, recommendation,
        content_unscorable=content_unscorable,
        well_known_brand=well_known,
    )

    # Generate checklist
    cl = generate_checklist(signals, is_registered=registered)
    cl_summary = checklist_summary(cl)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)

    signable = {
        "domain": domain,
        "signals": signals.model_dump(by_alias=True),
        "flags": flags,
        "trustScore": trust_score,
        "scoringModel": scoring.SCORING_MODEL,
        "recommendation": recommendation,
        "confidence": confidence,
        "cautionReason": caution_reason,
    }

    signature = signing.sign_payload(signable)

    # Store score snapshot for history tracking
    store_score_snapshot(
        domain, trust_score, recommendation,
        {
            "reputation": signals.reputation.score,
            "identity": signals.identity.score,
            "content": signals.content.score,
            "domainAge": signals.domain_age.score,
            "ssl": signals.ssl.score,
            "dns": signals.dns.score,
        },
    )

    site_category = getattr(content_result, '_site_category', 'consumer')

    # Jurisdiction detection
    # Extract country from WHOIS if available
    whois_country = ""
    try:
        from .whois_util import safe_whois as _safe_whois
        w = _safe_whois(domain)
        wc = w.country
        if wc:
            whois_country = wc[0] if isinstance(wc, list) else wc
    except Exception:
        pass

    # Extract country from SSL cert if available
    ssl_country = ""
    ssl_org = getattr(ssl_result, '_subject_org', '')
    # OV/EV certs include country in the subject, but we only have org name
    # For now, rely on ccTLD and WHOIS

    jurisdiction = detect_jurisdiction(domain, whois_country=whois_country)

    checked_at_str = now.isoformat(timespec="seconds") + "Z"

    response = CheckResponse(
        domain=domain,
        checkedAt=checked_at_str,
        expiresAt=expires.isoformat(timespec="seconds") + "Z",
        signals=signals,
        flags=flags,
        trustScore=trust_score,
        scoringModel=scoring.SCORING_MODEL,
        siteCategory=site_category,
        jurisdiction=jurisdiction,
        recommendation=recommendation,
        confidence=confidence,
        cautionReason=caution_reason,
        reasoning=reasoning,
        crawlability="blocked" if content_unscorable else "ok",
        brandTier="well_known" if well_known else "scored",
        checklist=cl,
        checklistSummary=ChecklistSummary(**cl_summary),
        signature=signature,
    )

    # Write to the transparency log. Non-blocking: if the log write fails,
    # the attestation is still returned to the caller. The log is an
    # auditability layer, not a gate.
    try:
        log_attestation(
            check_id=response.check_id,
            domain=domain,
            trust_score=trust_score,
            recommendation=recommendation,
            scoring_model=scoring.SCORING_MODEL,
            checked_at=checked_at_str,
            signature_key_id=response.signature_key_id,
            signature=signature,
        )
    except Exception:
        pass  # log failure must not block the attestation response

    return response
