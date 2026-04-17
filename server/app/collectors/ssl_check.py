"""SSL/TLS signal collector via direct probe.

Also extracts organization name from OV/EV certificates for the
identity collector to use.

Tries the apex first, then falls back to www.<domain> if the apex
refuses TCP. Many long-established sites (petsmart.com is the canonical
example) run no webserver on the apex IP at all -- their canonical URL
is www.domain.com. Without the fallback we'd score ssl=0 on every site
of that shape, which is wrong: the cert is available, we just weren't
asking at the right hostname.
"""

import ssl
import socket
from ..models.signals import SSLSignal


# Errors where falling back from apex to www makes sense: the TCP
# connection itself couldn't be established. If we got a TLS-layer error,
# the site HAS a listener and the error is real -- don't mask it by
# retrying elsewhere.
_APEX_TCP_FAILURES = (
    ConnectionRefusedError,
    TimeoutError,
    socket.timeout,
    socket.gaierror,  # DNS / resolution failure
    OSError,  # broad catchall for network-unreachable etc
)


def _probe_once(host: str, hsts: bool) -> SSLSignal:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
            protocol = ssock.version()

            issuer_name = ""
            for rdn in cert.get("issuer", ()):
                for attr_type, attr_value in rdn:
                    if attr_type == "organizationName":
                        issuer_name = attr_value

            subject_org = ""
            for rdn in cert.get("subject", ()):
                for attr_type, attr_value in rdn:
                    if attr_type == "organizationName":
                        subject_org = attr_value

            tls_version = protocol if protocol else "unknown"

            score = 60
            if tls_version in ("TLSv1.2", "TLSv1.3"):
                score = 80
            if tls_version == "TLSv1.3":
                score = 90
            if tls_version == "TLSv1.3" and hsts:
                score = 100

            result = SSLSignal(
                valid=True,
                issuer=issuer_name or "Unknown CA",
                tlsVersion=tls_version,
                hsts=hsts,
                score=score,
            )
            result._subject_org = subject_org
            result._probe_host = host
            return result


async def collect(domain: str, hsts: bool = False) -> SSLSignal:
    # Build the candidate list: apex first, then www variant if the input
    # didn't already start with www. For www.foo.com inputs we stay put.
    candidates = [domain]
    if not domain.startswith("www."):
        candidates.append(f"www.{domain}")

    last_error: Exception | None = None
    for host in candidates:
        try:
            return _probe_once(host, hsts)
        except _APEX_TCP_FAILURES as e:
            last_error = e
            continue  # apex has no listener; try www
        except Exception as e:
            # TLS-layer error (cert invalid, handshake rejected, etc).
            # This IS a real signal about this host -- don't fall through.
            last_error = e
            break

    result = SSLSignal(valid=False, score=0)
    result._subject_org = ""
    result._probe_host = ""
    return result
