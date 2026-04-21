"""Content signal collector via HTTP probe.

Checks:
- Privacy policy, terms of service, contact info (links + common paths)
- security.txt (RFC 9116)
- robots.txt
- HTTP security headers (CSP, X-Frame-Options, etc.)
- HSTS header (passed to SSL collector)
"""

import asyncio
import httpx
import re
from ..models.signals import ContentSignal
from ..fetch_escalation import (
    fetch_via_crawler,
    fetch_via_crawler_proxied,
    fetch_via_macbook,
    fetch_via_wayback,
    fetch_via_commercial_scraper,
    fetch_via_protocol_probe,
    CrawlerResponse,
)


# Realistic browser headers. httpx's default "python-httpx/X.Y.Z" gets 403'd
# by Cloudflare/Akamai/Imperva on major retailers. A real Chrome UA plus the
# standard Accept/Sec-Fetch headers bypasses most passive bot walls.
_CHROME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

_SAFARI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# HTTP statuses we treat as transient (bot blocks, rate limits, server errors).
# We retry once and mark _fetch_failed if still failing, so the pipeline can
# reuse the last-known-good content signals instead of writing zeros.
_TRANSIENT_STATUSES = {403, 408, 429, 500, 502, 503, 504}


# Multilingual privacy policy detection
_PRIVACY_LINK = re.compile(
    r'href=["\'][^"\']*(?:privacy|datenschutz|privacidad|confidentialite|'
    r'privacidade|riservatezza|privacybeleid|integritetspolicy|'
    r'prywatnosc|ochrana-osobnich|adatvedelm|gizlilik)[^"\']*["\']',
    re.IGNORECASE,
)
_PRIVACY_TEXT = re.compile(
    r"(privacy\s*policy|data\s*protection|datenschutz|"
    r"politique\s*de\s*confidentialit|pol.tica\s*de\s*privacidad|"
    r"pol.tica\s*de\s*privacidade|informativa\s*sulla\s*privacy|"
    r"privacybeleid|integritetspolicy|polityka\s*prywatno|"
    r"gizlilik\s*politikas)",
    re.IGNORECASE,
)

# Multilingual terms detection
_TERMS_LINK = re.compile(
    r'href=["\'][^"\']*(?:terms|tos|conditions|agb|nutzungsbedingungen|'
    r'condiciones|conditions-generales|termos|condizioni|voorwaarden|'
    r'villkor|regulamin|podminky|felhasznalasi|kullanim-kosullari)[^"\']*["\']',
    re.IGNORECASE,
)
_TERMS_TEXT = re.compile(
    r"(terms\s*(of\s*service|and\s*conditions|of\s*use)|conditions\s*of\s*sale|"
    r"allgemeine\s*gesch.ftsbedingungen|conditions\s*g.n.rales|"
    r"condiciones\s*generales|termos\s*de\s*uso|condizioni\s*generali|"
    r"algemene\s*voorwaarden|allm.nna\s*villkor|"
    r"kullan.m\s*ko.ullar)",
    re.IGNORECASE,
)

# Multilingual contact detection
_CONTACT_PATTERNS = re.compile(
    r"(contact[\s-]?us|get[\s-]?in[\s-]?touch|kontakt|contacto|contatti|"
    r"nous\s*contacter|fale\s*conosco|neem\s*contact|"
    r'mailto:[a-zA-Z0-9._%+-]+@|'
    r"tel:\+?\d|"
    r'href=["\'][^"\']*(?:contact|kontakt|contacto|contatti)[^"\']*["\'])',
    re.IGNORECASE,
)

# Common paths (expanded for international sites)
_COMMON_PATHS = {
    "privacy": [
        "/privacy", "/privacy-policy", "/legal/privacy",
        "/datenschutz", "/privacidad", "/confidentialite",
        "/privacidade", "/riservatezza", "/privacybeleid",
    ],
    "terms": [
        "/terms", "/terms-of-service", "/tos", "/legal/terms",
        "/agb", "/conditions-generales", "/condiciones",
        "/termos", "/condizioni", "/voorwaarden",
    ],
    "contact": [
        "/contact", "/contact-us",
        "/kontakt", "/contacto", "/contatti",
    ],
}


async def _path_exists(client: httpx.AsyncClient, domain: str, path: str) -> bool:
    try:
        resp = await client.head(f"https://{domain}{path}", follow_redirects=True)
        return resp.status_code < 400
    except Exception:
        return False


def _empty_signal(domain: str, *, fetch_failed: bool, response_time_ms: int = 0) -> ContentSignal:
    """Build a zero-information ContentSignal with infra detection from the domain name.

    Used when we can't learn anything from a homepage fetch -- either the site
    genuinely has no content (fetch_failed=False, legitimate 4xx) or we hit a
    transient failure (fetch_failed=True, so the pipeline can substitute the
    last-known-good signals instead of overwriting the registry with zeros).
    """
    infra_kw = re.compile(
        r'(cdn|edge|static|cache|proxy|api|cloud|compute|storage|'
        r'gateway|node|cluster|akamai|fastly|cloudfront|'
        r'googleapis|gstatic|fbcdn|twimg|'
        r'\.net$|\.io$)',
        re.IGNORECASE,
    )
    is_infra = bool(infra_kw.search(domain))
    result = ContentSignal(score=30 if is_infra else 0)
    result._hsts = False
    result._site_category = "infrastructure" if is_infra else "consumer"
    result._has_security_txt = False
    result._has_robots = False
    result._has_sitemap = False
    result._security_header_count = 0
    result._payment_processors = []
    result._tech_stack = []
    result._has_cookie_consent = False
    result._redirect_count = 0
    result._response_time_ms = response_time_ms
    result._social_links = []
    result._structured_data = {}
    result._has_api_docs = False
    result._has_api_paths = False
    result._has_status_page = False
    result._fetch_failed = fetch_failed
    return result


async def _tcp_reachable(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    """Non-blocking check: can we open a TCP connection to host:port?

    Used to detect the petsmart.com-style pattern where the apex domain
    has an A record but no webserver, and the real site is on www.
    Fast-path (connection refused / DNS fail) returns in <100ms; slow-path
    (timeout) capped at `timeout` seconds.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _resolve_effective_host(domain: str) -> str:
    """Return the hostname to actually probe: apex if reachable, else www.

    Only attempts the www fallback if (a) the input isn't already a www
    variant, (b) the apex fails TCP, and (c) the www variant succeeds TCP.
    In any other case returns `domain` unchanged so we don't pay the TCP
    check cost on happy-path fetches more than once per check.
    """
    if domain.startswith("www."):
        return domain
    if await _tcp_reachable(domain):
        return domain
    www = f"www.{domain}"
    if await _tcp_reachable(www):
        return www
    return domain  # both dead; let the tier ladder produce the error


async def _fetch_homepage(domain: str) -> tuple[httpx.Response | None, int, bool]:
    """Fetch the homepage with realistic headers, one retry on transient failure.

    Returns (response, response_time_ms, transient_failure).
    - response is None if every attempt failed or returned a transient status
    - transient_failure is True if the failure was exception/5xx/403/429/408;
      False if we got a real non-2xx response (404, 410, etc.) we should score

    Resolves apex-vs-www up front so legacy sites (petsmart.com style) that
    only serve on the www variant still get scored correctly.
    """
    import time as _time

    def _note_tier_success():
        """Reset the tier 6 strike counter on any tier-1-through-5 success.
        Cheap non-critical DB write; swallow errors so scoring isn't
        blocked by a transient gate-table issue."""
        try:
            from .. import tier6_gate
            tier6_gate.record_success(domain)
        except Exception:
            pass

    effective_host = await _resolve_effective_host(domain)

    attempts = [_CHROME_HEADERS, _SAFARI_HEADERS]
    last_response: httpx.Response | None = None
    transient = False
    response_time_ms = 0

    for i, headers in enumerate(attempts):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=8.0),
                follow_redirects=True,
                verify=False,
                headers=headers,
                http2=False,
            ) as client:
                _t0 = _time.monotonic()
                resp = await client.get(f"https://{effective_host}/")
                response_time_ms = round((_time.monotonic() - _t0) * 1000)

                if resp.status_code < 400:
                    # Weak-content detection: SPA storefronts (costco,
                    # nordstrom, many React/Next.js sites) return 200
                    # with a tiny client-rendered shell (<10KB) that JS
                    # would normally populate. Without a browser renderer,
                    # this response has no footer signals. Detect the
                    # pattern and escalate to the probe (which fetches
                    # the 404 shell containing the real footer) or to
                    # tier 2 Playwright (which renders the JS). Keep the
                    # weak response as last_response so it's used as a
                    # fallback if all tiers fail.
                    resp_text = resp.text or ""
                    body_len = len(resp_text)
                    if body_len < 10_000:
                        preview = resp_text[:20_000].lower()
                        has_footer = any(kw in preview for kw in (
                            "privacy", "terms of", "contact us",
                            "</footer>", "footer-nav", "footer-link",
                        ))
                        if not has_footer:
                            transient = True
                            last_response = resp
                            break  # exit retry loop, fall to escalation
                    _note_tier_success()
                    return resp, response_time_ms, False

                # Server responded but with an error status
                last_response = resp
                if resp.status_code in _TRANSIENT_STATUSES:
                    transient = True
                    if i < len(attempts) - 1:
                        await asyncio.sleep(0.8)
                        continue
                    # Tier 2 escalation below
                    break

                # 4xx non-transient (404, 410, 401, 400): real signal, don't retry
                return None, response_time_ms, False

        except Exception:
            transient = True
            if i < len(attempts) - 1:
                await asyncio.sleep(0.8)
                continue
            # Tier 2 escalation below
            break

    # Tier 1.5 (protocol probe): when the homepage fails, try a
    # deliberately-404 protocol-standard path on the same origin before
    # escalating to the expensive Playwright tiers. Sites running
    # DataDome / Turnstile / CF Enterprise Bot Management typically
    # apply those challenges only to the homepage and dynamic category
    # pages; static 404 shells served by the web server layer pass
    # through cleanly and contain the site's footer template -- which
    # is where the privacy/terms/contact links OTT scores on live. If
    # the site actually has a security.txt file, we get a real 200
    # response with a different positive signal. Either outcome
    # supersedes the need to escalate through tiers 2-5.
    if transient:
        probe_resp = await fetch_via_protocol_probe(f"https://{effective_host}/")
        if probe_resp is not None and probe_resp.status_code < 400:
            _note_tier_success()
            return probe_resp, response_time_ms, False

    # Tier 2: escalate to the crawler service (real headless Chromium on
    # a dedicated box). Catches sites that fingerprint httpx but not real
    # browsers -- SPAs, JS challenges, TLS fingerprint checks. Does NOT
    # help with IP-AS blocks (same VPC, same DO AS) -- those need tier 3.
    if transient:
        crawler_resp = await fetch_via_crawler(f"https://{effective_host}/")
        if crawler_resp is not None and crawler_resp.status_code < 400:
            _note_tier_success()
            return crawler_resp, response_time_ms, False

        # Tier 3: Playwright-via-Decodo-residential-proxy. Fresh rotating
        # residential IP per request bypasses DO AS filters and common
        # Akamai/Cloudflare-standard blocks. Does NOT beat Cloudflare
        # Enterprise Bot Management -- those still return 403 and fall
        # through to crawlability=blocked (Phase 1 re-weighting handles
        # scoring from non-content signals).
        proxied_resp = await fetch_via_crawler_proxied(f"https://{effective_host}/")
        if proxied_resp is not None and proxied_resp.status_code < 400:
            _note_tier_success()
            return proxied_resp, response_time_ms, False

        # Tier 4: residential Mac Air over Tailscale. Physical hardware
        # on a real Spectrum residential connection. Free (no proxy GB
        # metering) and provides redundancy if Decodo has issues, though
        # subject to the Mac being online and not traveling with the
        # operator. Short breaker cooldown so sleep/wake cycles recover
        # quickly. Does NOT beat Cloudflare Enterprise either (same
        # Chromium fingerprint issue as tier 2/3); real-Chrome stealth
        # alternatives tracked in Task 19.
        macbook_resp = await fetch_via_macbook(f"https://{effective_host}/")
        if macbook_resp is not None and macbook_resp.status_code < 400:
            _note_tier_success()
            return macbook_resp, response_time_ms, False

        # Tier 5: Internet Archive Wayback Machine. When every live-fetch
        # tier has failed, ask the archive for the most recent stored copy
        # of the homepage. This fills the gap for sites that block
        # datacenter and residential IPs but not archive.org (most major
        # brands). Returns None if no recent snapshot exists, if archive
        # also got 403'd, or if the feature flag is off. Body-based signals
        # (privacy/terms/contact/schema.org) parse the same way as a live
        # fetch; security-header signals will read as zero because Wayback
        # doesn't preserve response headers -- acceptable trade for having
        # any content at all on otherwise-blocked sites.
        wayback_resp = await fetch_via_wayback(f"https://{effective_host}/")
        if wayback_resp is not None and wayback_resp.status_code < 400:
            _note_tier_success()
            return wayback_resp, response_time_ms, False

        # Tier 6: commercial scraper API (Bright Data Web Unlocker by
        # default). Gated behind a 3-strike accumulator so we only pay
        # for domains that have genuinely defeated every other tier
        # across multiple re-crawl cycles. Ships dark unless
        # SCRAPER_ENABLED is true in scraper.env. See the integration
        # spec for the cost model and the 3-strike rationale.
        scraper_resp = await fetch_via_commercial_scraper(
            f"https://{effective_host}/", domain
        )
        if scraper_resp is not None and scraper_resp.status_code < 400:
            return scraper_resp, response_time_ms, False

        # Every tier failed. Record a strike against the tier 6 gate so
        # this domain progresses toward commercial-scraper eligibility on
        # the next re-crawl. Cheap enough to unconditionally call; the
        # gate module is a single INSERT/UPDATE.
        try:
            from .. import tier6_gate
            tier6_gate.record_strike(domain)
        except Exception:
            # Strike accounting is non-critical. Don't let a DB hiccup
            # fail an otherwise-successful content_check.
            pass

        # Fall through as transient so the pipeline reuses last-good
        # history or marks unscorable.
        return None, response_time_ms, True

    return last_response if last_response and last_response.status_code < 400 else None, response_time_ms, transient


def _count_security_headers(headers) -> int:
    """Count meaningful security headers present."""
    checks = [
        "content-security-policy",
        "x-frame-options",
        "x-content-type-options",
        "permissions-policy",
        "referrer-policy",
        "strict-transport-security",
    ]
    return sum(1 for h in checks if headers.get(h))


async def collect(domain: str) -> ContentSignal:
    hsts_detected = False

    resp, response_time_ms, transient = await _fetch_homepage(domain)

    if resp is None:
        # Either transient failure (retryable networking / bot block / 5xx)
        # or a real 4xx. The pipeline distinguishes via _fetch_failed:
        # transient=True means "reuse last-known-good signals", False means
        # "this is the site's actual state".
        return _empty_signal(domain, fetch_failed=transient, response_time_ms=response_time_ms)

    # Determine the hostname that actually answered. For sites where apex
    # is dead and we fell back to www, probe_host is "www.domain" so the
    # path-existence checks (/privacy, /terms, /robots.txt) hit the real
    # server. Parsing from resp.url handles both direct fetches and the
    # CrawlerResponse shim which also populates .url from Playwright's
    # final_url after redirects.
    from urllib.parse import urlparse
    probe_host = urlparse(str(getattr(resp, "url", ""))).hostname or domain

    # HSTS header (captured even on success path below)
    if resp.headers.get("strict-transport-security"):
        hsts_detected = True

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
            verify=False,
            headers=_CHROME_HEADERS,
        ) as client:
            # Cap at 2MB. The original 300KB cap truncated large retailer
            # homepages (crateandbarrel.com at 989KB) before the footer
            # where privacy/terms/contact links live. 2MB covers every
            # reasonable homepage while bounding memory per request.
            body = resp.text[:2_000_000]
            headers = resp.headers

            # Core content signals
            has_privacy = bool(
                _PRIVACY_LINK.search(body) or _PRIVACY_TEXT.search(body)
            )
            has_terms = bool(
                _TERMS_LINK.search(body) or _TERMS_TEXT.search(body)
            )
            has_contact = bool(_CONTACT_PATTERNS.search(body))

            # Probe common paths if not found on homepage
            if not has_privacy:
                for path in _COMMON_PATHS["privacy"]:
                    if await _path_exists(client, probe_host, path):
                        has_privacy = True
                        break
            if not has_terms:
                for path in _COMMON_PATHS["terms"]:
                    if await _path_exists(client, probe_host, path):
                        has_terms = True
                        break
            if not has_contact:
                for path in _COMMON_PATHS["contact"]:
                    if await _path_exists(client, probe_host, path):
                        has_contact = True
                        break

            # Maturity signals
            has_security_txt = await _path_exists(
                client, probe_host, "/.well-known/security.txt"
            )
            has_robots = await _path_exists(client, probe_host, "/robots.txt")

            # Security headers count
            sec_header_count = _count_security_headers(headers)

            # Payment processor detection
            payment_processors = []
            if 'stripe.com' in body or 'Stripe(' in body or 'stripe.js' in body.lower():
                payment_processors.append('stripe')
            if 'paypal.com/sdk' in body.lower() or 'paypal.com/v1' in body.lower():
                payment_processors.append('paypal')
            if 'square' in body.lower() and ('squareup.com' in body or 'square.js' in body.lower()):
                payment_processors.append('square')
            if 'shopify' in body.lower() and ('cdn.shopify.com' in body or 'checkout.shopify' in body.lower()):
                payment_processors.append('shopify')

            # Technology/CMS detection
            tech_stack = []
            generator = headers.get('x-powered-by', '')
            if generator:
                tech_stack.append(generator)
            if 'wp-content' in body or 'wordpress' in body.lower():
                tech_stack.append('wordpress')
            if 'cdn.shopify.com' in body:
                tech_stack.append('shopify')
            if 'squarespace' in body.lower():
                tech_stack.append('squarespace')
            if 'wix.com' in body:
                tech_stack.append('wix')

            # Cookie consent detection
            has_cookie_consent = bool(re.search(
                r'(cookie[- ]?(consent|banner|notice|policy)|gdpr|CookieConsent|cookiebot|onetrust)',
                body, re.IGNORECASE
            ))

            # Sitemap detection
            has_sitemap = await _path_exists(client, probe_host, "/sitemap.xml")

            # Redirect chain info
            redirect_count = len(resp.history)

            # Social media links
            social_links = []
            social_patterns = {
                'twitter': re.compile(r'href=["\'][^"\']*(?:twitter\.com|x\.com)/[A-Za-z0-9_]+', re.IGNORECASE),
                'linkedin': re.compile(r'href=["\'][^"\']*linkedin\.com/(?:company|in)/[A-Za-z0-9_-]+', re.IGNORECASE),
                'facebook': re.compile(r'href=["\'][^"\']*facebook\.com/[A-Za-z0-9._-]+', re.IGNORECASE),
                'instagram': re.compile(r'href=["\'][^"\']*instagram\.com/[A-Za-z0-9._]+', re.IGNORECASE),
                'youtube': re.compile(r'href=["\'][^"\']*youtube\.com/(?:@|channel/|c/)[A-Za-z0-9_-]+', re.IGNORECASE),
            }
            for platform, pattern in social_patterns.items():
                if pattern.search(body):
                    social_links.append(platform)

            # JSON-LD structured data
            structured_data = {}
            jsonld_pattern = re.compile(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                re.DOTALL | re.IGNORECASE,
            )
            for match in jsonld_pattern.finditer(body):
                try:
                    import json as _json
                    ld = _json.loads(match.group(1))
                    if isinstance(ld, dict):
                        ld_type = ld.get("@type", "")
                        if ld_type == "Organization" or ld_type == "Corporation":
                            structured_data["orgName"] = ld.get("name", "")
                            structured_data["orgType"] = ld_type
                            if ld.get("address"):
                                structured_data["hasAddress"] = True
                            if ld.get("telephone") or ld.get("contactPoint"):
                                structured_data["hasPhone"] = True
                        elif ld_type == "WebSite":
                            structured_data["hasWebSiteSchema"] = True
                    elif isinstance(ld, list):
                        for item in ld:
                            if isinstance(item, dict) and item.get("@type") in ("Organization", "Corporation"):
                                structured_data["orgName"] = item.get("name", "")
                                structured_data["orgType"] = item.get("@type", "")
                except Exception:
                    pass

            # Site category detection
            content_type = resp.headers.get('content-type', '')
            body_lower = body.lower()
            body_len = len(body)

            # Infrastructure/API signals
            is_api_docs = bool(re.search(
                r'(api[- ]?docs|api[- ]?reference|/docs|swagger|openapi|redoc|graphql)',
                body_lower
            ))
            has_api_paths = await _path_exists(client, probe_host, "/api") or \
                            await _path_exists(client, probe_host, "/v1") or \
                            await _path_exists(client, probe_host, "/docs")
            is_minimal_html = body_len < 5000 and '<html' in body_lower
            is_json_response = 'application/json' in content_type
            is_redirect_only = resp.status_code in (301, 302, 307, 308)
            has_status_page = await _path_exists(client, probe_host, "/status")

            # CDN/infrastructure domain patterns
            infra_patterns = re.compile(
                r'(cdn|edge|static|cache|proxy|relay|api|cloud|compute|storage|'
                r'gateway|node|cluster|service|platform|registry)',
                re.IGNORECASE
            )
            domain_looks_infra = bool(infra_patterns.search(domain))

            # Determine category
            infra_signals = sum([
                is_api_docs,
                has_api_paths,
                is_minimal_html or is_json_response,
                domain_looks_infra,
                has_status_page,
                not has_privacy and not has_terms and not has_contact,
            ])

            if infra_signals >= 3:
                site_category = "infrastructure"
            elif is_api_docs or (has_api_paths and is_minimal_html):
                site_category = "api_service"
            else:
                site_category = "consumer"

            # Category-aware scoring
            if site_category in ("infrastructure", "api_service"):
                # Infrastructure scoring: security posture matters more,
                # legal pages matter less
                score = 0

                # Security headers are the primary content signal for infra
                if sec_header_count >= 4:
                    score += 40
                elif sec_header_count >= 3:
                    score += 30
                elif sec_header_count >= 1:
                    score += 20

                # API docs or status page shows operational maturity
                if is_api_docs or has_api_paths:
                    score += 20
                if has_status_page:
                    score += 10

                # Standard signals still count but less critical
                if has_robots:
                    score += 5
                if has_security_txt:
                    score += 10
                if has_sitemap:
                    score += 5

                # Legal pages are a bonus, not a requirement
                if has_privacy:
                    score += 5
                if has_terms:
                    score += 5

                score = min(100, score)
            else:
                # Consumer/merchant scoring (existing logic)
                found = sum([has_privacy, has_terms, has_contact])
                if found == 0:
                    score = 0
                elif found == 1:
                    score = 30
                elif found == 2:
                    score = 50
                else:
                    score = 70

                # Maturity bonuses (up to +30)
                if has_robots:
                    score += 5
                if has_security_txt:
                    score += 10
                if sec_header_count >= 3:
                    score += 15
                elif sec_header_count >= 1:
                    score += 5

                score = min(100, score)

            result = ContentSignal(
                privacyPolicy=has_privacy,
                termsOfService=has_terms,
                contactInfo=has_contact,
                score=score,
            )
            result._hsts = hsts_detected
            result._has_security_txt = has_security_txt
            result._has_robots = has_robots
            result._has_sitemap = has_sitemap
            result._security_header_count = sec_header_count
            result._payment_processors = payment_processors
            result._tech_stack = tech_stack
            result._has_cookie_consent = has_cookie_consent
            result._redirect_count = redirect_count
            result._response_time_ms = response_time_ms
            result._social_links = social_links
            result._structured_data = structured_data
            result._site_category = site_category
            result._has_api_docs = is_api_docs
            result._has_api_paths = has_api_paths
            result._has_status_page = has_status_page
            result._fetch_failed = False
            return result
    except Exception:
        # Homepage fetch succeeded but a downstream path-probe or parse blew up.
        # We have a real response in hand, so this isn't a transient fetch
        # failure -- return what we can with fetch_failed=False so the pipeline
        # treats it as an authoritative "we looked and found nothing" result.
        return _empty_signal(domain, fetch_failed=False, response_time_ms=response_time_ms)
