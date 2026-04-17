"""DNS security signal collector."""

import dns.resolver
from ..models.signals import DNSSignal


def _has_record(domain: str, rdtype: str) -> bool:
    try:
        dns.resolver.resolve(domain, rdtype)
        return True
    except Exception:
        return False


def _has_spf(domain: str) -> bool:
    try:
        answers = dns.resolver.resolve(domain, "TXT")
        for rdata in answers:
            txt = rdata.to_text().strip('"')
            if txt.startswith("v=spf1"):
                return True
    except Exception:
        pass
    return False


def _has_dmarc(domain: str) -> bool:
    try:
        answers = dns.resolver.resolve(f"_dmarc.{domain}", "TXT")
        for rdata in answers:
            txt = rdata.to_text().strip('"')
            if txt.startswith("v=DMARC1"):
                return True
    except Exception:
        pass
    return False


def _has_caa(domain: str) -> bool:
    return _has_record(domain, "CAA")


def _has_dnssec(domain: str) -> bool:
    try:
        dns.resolver.resolve(domain, "DNSKEY")
        return True
    except Exception:
        return False


async def collect(domain: str) -> DNSSignal:
    spf = _has_spf(domain)
    dmarc = _has_dmarc(domain)
    dnssec = _has_dnssec(domain)
    caa = _has_caa(domain)

    score = 20  # base: DNS resolves
    if spf:
        score = 40
    if spf and dmarc:
        score = 60
    if spf and dmarc and dnssec:
        score = 90
    if spf and dmarc and dnssec and caa:
        score = 100

    return DNSSignal(
        spf=spf,
        dmarc=dmarc,
        dnssec=dnssec,
        caa=caa,
        score=score,
    )
