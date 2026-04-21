"""OpenTrustSeal crawler fetch service.

Single-purpose Playwright wrapper. Listens on a private VPC address only,
authenticated via shared secret, returns fetched homepage bodies + metadata
to the API box. Keeps a pool of warm headless Chromium contexts to avoid
cold-start latency on every request.

Lifecycle:
  startup  -> launch one Chromium instance, create N contexts, mark all free
  /fetch   -> acquire a free context, navigate, capture response, release
  shutdown -> close contexts and browser cleanly

The service knows nothing about trust scoring. It fetches pages and hands
back the body. Scoring stays on the API box where the DB and keys live.
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

SHARED_SECRET = os.environ.get("CRAWLER_SHARED_SECRET", "")
if not SHARED_SECRET:
    raise RuntimeError("CRAWLER_SHARED_SECRET env var must be set")

POOL_SIZE = int(os.environ.get("CRAWLER_POOL_SIZE", "2"))
DEFAULT_TIMEOUT_MS = int(os.environ.get("CRAWLER_DEFAULT_TIMEOUT_MS", "20000"))

# Resource blocking. Default on. Trust scoring reads DOM text (privacy/
# terms/contact links in footers, SSL cert org, etc) and does not need
# images, video, or web fonts. Blocking them drops bandwidth 5-10x per
# fetch and speeds up each fetch 3-5x because the page reaches DOMContent-
# Loaded without waiting for hero carousels and font files. Kept env-gated
# in case a future scorer looks at visual properties (e.g. favicon hash).
# Set CRAWLER_BLOCK_RESOURCES=false to disable.
BLOCK_RESOURCES = os.environ.get("CRAWLER_BLOCK_RESOURCES", "true").lower() in ("1", "true", "yes")
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

# Browser channel selection. Empty (default) uses Playwright's bundled
# chromium-headless-shell -- fine for sites blocked by AS reputation but
# still fingerprintable as headless by the heavier bot managers. Set to
# "chrome" to use the real Google Chrome binary installed on the system.
# NOTE: channel="chrome" currently hangs on macOS 26.2 + Chrome 147 due
# to a Playwright pipe-transport + new-CDP-handshake regression. Use
# CRAWLER_BROWSER_EXECUTABLE instead on macOS.
BROWSER_CHANNEL = os.environ.get("CRAWLER_BROWSER_CHANNEL", "").strip() or None

# Explicit browser executable path. When set, overrides the default
# Playwright browser selection (which would be headless-shell when
# headless=True) with whatever binary is at this path. Used on the
# Phase 4 MacBook Air to point at Google Chrome for Testing 138 (bundled
# with Playwright 1.58 at chromium-1208/chrome-mac-arm64/Google Chrome
# for Testing.app). That binary is a full real Chrome build with TLS
# fingerprint + HTTP/2 + V8 matching production Chrome, so Cloudflare
# Enterprise Bot Management accepts it where headless-shell gets 403.
BROWSER_EXECUTABLE = os.environ.get("CRAWLER_BROWSER_EXECUTABLE", "").strip() or None

# Realistic browser user agent and headers. Chromium already sends a real
# Chrome UA, but we override to pin a stable version and add headers that
# httpx on the API box also sends so bot walls see a consistent fingerprint
# regardless of which tier fetched.
# Windows Chrome UA aligns with playwright-stealth's default navigator.platform
# override (Win32) so bot detectors don't see a UA/platform mismatch.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_VIEWPORT = {"width": 1440, "height": 900}
_EXTRA_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

# Single stealth instance shared across all contexts. Patches ~25 bot
# detection surfaces: navigator.webdriver, chrome runtime presence,
# plugin mimic, permissions API, WebGL vendor/renderer, hairline fonts,
# media codecs, iframe contentWindow, and the various navigator.* fields
# that leak automation. Required to get past Cloudflare Bot Management
# even with a residential IP, because the IP only gets you past the AS
# reputation filter -- the fingerprint check comes next.
_STEALTH = Stealth(
    navigator_user_agent_override=_UA,
)


async def _abort_heavy_resources(route):
    """Intercept handler: drop images, media, and fonts. Let everything else
    (document, script, stylesheet, xhr, fetch, etc) through so SPAs still
    render and the DOM the scorer reads is complete."""
    try:
        if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        # Route may have already been handled or page may be navigating away.
        # Swallow; Playwright logs internally if it matters.
        pass


class ProxyConfig(BaseModel):
    """Upstream HTTP proxy settings for Playwright context.

    Mirrors Playwright's proxy option shape so we can forward directly.
    Used by the API box to escalate to tier 3 (Playwright through a
    residential proxy) for sites that block both direct httpx and direct
    Playwright on our datacenter AS.
    """
    server: str  # e.g. "http://us.decodo.com:10001"
    username: Optional[str] = None
    password: Optional[str] = None


class FetchRequest(BaseModel):
    url: str
    timeout_ms: Optional[int] = None
    wait_until: str = "domcontentloaded"  # or "networkidle" for SPAs
    proxy: Optional[ProxyConfig] = None  # if set, spawn an ephemeral proxied context


class FetchResponse(BaseModel):
    status: int
    body: str
    headers: dict
    final_url: str
    redirect_count: int
    elapsed_ms: int
    fetched_via: str = "playwright-chromium"
    error: Optional[str] = None


class ContextPool:
    """Asyncio-safe pool of Playwright contexts sharing one browser."""

    def __init__(self, size: int):
        self.size = size
        self.browser: Optional[Browser] = None
        self._playwright = None
        self._free: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._all: list[BrowserContext] = []

    async def start(self):
        self._playwright = await async_playwright().start()
        launch_kwargs: dict = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",  # avoid /dev/shm size issues on small VPS
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        }
        if BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
        if BROWSER_EXECUTABLE:
            launch_kwargs["executable_path"] = BROWSER_EXECUTABLE
        self.browser = await self._playwright.chromium.launch(**launch_kwargs)
        for _ in range(self.size):
            ctx = await self._new_context()
            self._all.append(ctx)
            await self._free.put(ctx)

    async def _new_context(self, proxy: Optional[dict] = None) -> BrowserContext:
        """Create a new browser context. If proxy is given, route through it.

        Proxied contexts are created on demand per request (not pooled) so
        they can't leak residential proxy traffic into subsequent non-proxied
        fetches and vice versa.

        Every context gets playwright-stealth applied to patch automation
        fingerprinting surfaces that Cloudflare Bot Management reads.
        """
        assert self.browser is not None
        kwargs = dict(
            user_agent=_UA,
            viewport=_VIEWPORT,
            locale="en-US",
            extra_http_headers=_EXTRA_HEADERS,
            ignore_https_errors=True,
        )
        if proxy:
            kwargs["proxy"] = proxy
        ctx = await self.browser.new_context(**kwargs)
        await _STEALTH.apply_stealth_async(ctx)
        if BLOCK_RESOURCES:
            await ctx.route("**/*", _abort_heavy_resources)
        return ctx

    async def new_ephemeral(self, proxy: dict) -> BrowserContext:
        """Create a one-shot context for a proxied fetch. Caller must close it."""
        return await self._new_context(proxy=proxy)

    @asynccontextmanager
    async def acquire(self):
        ctx = await self._free.get()
        try:
            yield ctx
        finally:
            # Clean up any pages left open so the next caller gets a blank slate
            try:
                for page in ctx.pages:
                    await page.close()
            except Exception:
                pass
            await self._free.put(ctx)

    async def replace(self, broken_ctx: BrowserContext):
        """Replace a context that raised an unexpected error."""
        try:
            if broken_ctx in self._all:
                self._all.remove(broken_ctx)
            try:
                await broken_ctx.close()
            except Exception:
                pass
        finally:
            fresh = await self._new_context()
            self._all.append(fresh)
            await self._free.put(fresh)

    async def stop(self):
        for ctx in list(self._all):
            try:
                await ctx.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass


pool = ContextPool(POOL_SIZE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.start()
    yield
    await pool.stop()


app = FastAPI(title="OTT Crawler Fetch Service", lifespan=lifespan)


def _check_secret(x_crawler_secret: Optional[str]) -> None:
    if not x_crawler_secret or x_crawler_secret != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="invalid or missing shared secret")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "pool_free": pool._free.qsize(),
        "pool_size": pool.size,
    }


async def _run_fetch_on_context(ctx: BrowserContext, req: FetchRequest, t0: float, fetched_via: str) -> FetchResponse:
    """Common Playwright navigation + response-building shared by pooled and
    ephemeral (proxied) contexts."""
    page: Optional[Page] = None
    try:
        page = await ctx.new_page()
        response = await page.goto(
            req.url,
            wait_until=req.wait_until,
            timeout=req.timeout_ms or DEFAULT_TIMEOUT_MS,
        )
        elapsed = round((time.monotonic() - t0) * 1000)
        if response is None:
            return FetchResponse(
                status=0, body="", headers={}, final_url=req.url,
                redirect_count=0, elapsed_ms=elapsed,
                fetched_via=fetched_via, error="no response",
            )
        body = await page.content()
        chain: list = []
        r = response.request
        while r and r.redirected_from is not None:
            chain.append(r.redirected_from)
            r = r.redirected_from
        return FetchResponse(
            status=response.status,
            body=body[:500_000],
            headers=dict(response.headers),
            final_url=page.url,
            redirect_count=len(chain),
            elapsed_ms=elapsed,
            fetched_via=fetched_via,
        )
    except Exception as e:
        elapsed = round((time.monotonic() - t0) * 1000)
        return FetchResponse(
            status=0, body="", headers={}, final_url=req.url,
            redirect_count=0, elapsed_ms=elapsed,
            fetched_via=fetched_via,
            error=f"{type(e).__name__}: {str(e)[:200]}",
        )
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


@app.post("/fetch", response_model=FetchResponse)
async def fetch(
    req: FetchRequest,
    x_crawler_secret: Optional[str] = Header(default=None, alias="X-Crawler-Secret"),
):
    _check_secret(x_crawler_secret)
    t0 = time.monotonic()

    if req.proxy is not None:
        # Tier 3 path: ephemeral context through an upstream proxy.
        # Create-use-destroy so no residential traffic leaks to pooled
        # contexts and so a broken proxy can't poison the pool.
        proxy_dict = req.proxy.model_dump(exclude_none=True)
        ctx = await pool.new_ephemeral(proxy_dict)
        try:
            return await _run_fetch_on_context(ctx, req, t0, "playwright-chromium-proxied")
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    else:
        async with pool.acquire() as ctx:
            return await _run_fetch_on_context(ctx, req, t0, "playwright-chromium")
