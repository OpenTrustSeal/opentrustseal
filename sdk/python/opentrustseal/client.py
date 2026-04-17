"""OpenTrustSeal client - sync and async HTTP client."""

import httpx
from .models import CheckResult, _parse_response

DEFAULT_BASE_URL = "https://api.opentrustseal.com"
DEFAULT_TIMEOUT = 30


class OTTClient:
    """OpenTrustSeal API client.

    Usage:
        client = OTTClient()  # free tier, no key needed
        result = client.check("merchant.com")

        # With API key (higher rate limits)
        client = OTTClient(api_key="ott_live_...")

    Async usage:
        result = await client.async_check("merchant.com")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"User-Agent": "opentrustseal-python/0.1.0"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def check(self, domain: str, refresh: bool = False) -> CheckResult:
        """Check a domain's trust score (synchronous).

        Args:
            domain: The domain to check (e.g. "merchant.com")
            refresh: Force a fresh check, bypassing cache

        Returns:
            CheckResult with trust_score, recommendation, signals, etc.
        """
        url = f"{self.base_url}/v1/check/{domain}"
        params = {"refresh": "true"} if refresh else {}

        with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return _parse_response(resp.json())

    async def async_check(self, domain: str, refresh: bool = False) -> CheckResult:
        """Check a domain's trust score (async).

        Args:
            domain: The domain to check
            refresh: Force a fresh check, bypassing cache

        Returns:
            CheckResult with trust_score, recommendation, signals, etc.
        """
        url = f"{self.base_url}/v1/check/{domain}"
        params = {"refresh": "true"} if refresh else {}

        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return _parse_response(resp.json())

    def check_multiple(self, domains: list[str]) -> list[CheckResult]:
        """Check multiple domains (synchronous, sequential).

        Args:
            domains: List of domains to check

        Returns:
            List of CheckResult objects
        """
        return [self.check(d) for d in domains]

    async def async_check_multiple(self, domains: list[str]) -> list[CheckResult]:
        """Check multiple domains (async, concurrent).

        Args:
            domains: List of domains to check

        Returns:
            List of CheckResult objects
        """
        import asyncio
        tasks = [self.async_check(d) for d in domains]
        return await asyncio.gather(*tasks)


# Module-level convenience functions using a default client
_default_client = OTTClient()


def check(domain: str, refresh: bool = False) -> CheckResult:
    """Check a domain's trust score.

    Convenience function using a default client (free tier, no API key).

    Args:
        domain: The domain to check (e.g. "merchant.com")
        refresh: Force a fresh check, bypassing cache

    Returns:
        CheckResult with trust_score, recommendation, signals, etc.

    Example:
        from opentrustseal import check

        result = check("merchant.com")
        if result.recommendation == "DENY":
            raise UntrustedMerchant(result.reasoning)

        print(result.trust_score)          # 81
        print(result.is_safe)              # True
        print(result.signals.ssl.score)    # 100
        print(result.jurisdiction.country) # "US"
    """
    return _default_client.check(domain, refresh=refresh)


async def async_check(domain: str, refresh: bool = False) -> CheckResult:
    """Check a domain's trust score (async version).

    Example:
        from opentrustseal import async_check

        result = await async_check("merchant.com")
        if result.is_blocked:
            return "Transaction refused"
    """
    return await _default_client.async_check(domain, refresh=refresh)
