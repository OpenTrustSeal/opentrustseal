"""3-strike gate for tier 6 commercial scraper calls.

Each domain accumulates a "strike" every time tiers 1-5 all fail to fetch
content for it in a re-crawl cycle. Tier 6 (commercial scraper API) only
fires after SCRAPER_GATE_STRIKES strikes, and any tier success resets the
counter. This prevents a transient network blip from dumping a per-domain
paid-API call on every affected row.

See docs/TIER-6-COMMERCIAL-SCRAPER-SPEC.md for the full rationale and
cost model.
"""

from datetime import datetime, timezone
from typing import Optional

from .database import _get_conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_strike(domain: str) -> int:
    """Tiers 1-5 all failed for this domain. Increment strike count.

    Returns the new strike count. Idempotent for same-cycle duplicate
    calls only in the sense that each call increments once; callers
    should only invoke this when they have actually exhausted the
    non-tier-6 path.
    """
    domain = domain.strip().lower()
    if not domain:
        return 0
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT strike_count FROM tier6_gate WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO tier6_gate (domain, strike_count, last_strike_at)
                   VALUES (?, 1, ?)""",
                (domain, _now()),
            )
            return 1
        new_count = (row["strike_count"] or 0) + 1
        conn.execute(
            """UPDATE tier6_gate
               SET strike_count = ?, last_strike_at = ?
               WHERE domain = ?""",
            (new_count, _now(), domain),
        )
        return new_count


def record_success(domain: str) -> None:
    """Any tier (including tier 6) succeeded. Reset strike counter to 0."""
    domain = domain.strip().lower()
    if not domain:
        return
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO tier6_gate (domain, strike_count, last_success_at)
               VALUES (?, 0, ?)
               ON CONFLICT(domain) DO UPDATE
               SET strike_count = 0, last_success_at = excluded.last_success_at""",
            (domain, _now()),
        )


def get_strike_count(domain: str) -> int:
    """Read the current strike count. Callers gate tier 6 dispatch on this."""
    domain = domain.strip().lower()
    if not domain:
        return 0
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT strike_count FROM tier6_gate WHERE domain = ?",
            (domain,),
        ).fetchone()
        return (row["strike_count"] or 0) if row else 0


def record_tier6_call(domain: str, status: Optional[int] = None) -> None:
    """Audit trail: log each tier 6 invocation with its resulting status."""
    domain = domain.strip().lower()
    if not domain:
        return
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO tier6_gate (domain, last_tier6_called_at,
                                       tier6_call_count, last_tier6_status)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(domain) DO UPDATE
               SET last_tier6_called_at = excluded.last_tier6_called_at,
                   tier6_call_count = tier6_call_count + 1,
                   last_tier6_status = excluded.last_tier6_status""",
            (domain, _now(), status),
        )


def preload_bootstrap_strikes(domains: list[str], strikes: int = 3) -> int:
    """Seed the gate table with a set of known-stubborn domains.

    After a big crawl that already identified the persistent tier-1-to-5
    failures, rather than waiting 3 daily cycles to rebuild the strike
    count we can preload the gate so tier 6 fires on the first attempt.
    Returns the number of rows inserted or updated.
    """
    if not domains:
        return 0
    inserted = 0
    with _get_conn() as conn:
        for d in domains:
            d = d.strip().lower()
            if not d:
                continue
            conn.execute(
                """INSERT INTO tier6_gate (domain, strike_count, last_strike_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE
                   SET strike_count = MAX(strike_count, excluded.strike_count),
                       last_strike_at = excluded.last_strike_at""",
                (d, strikes, _now()),
            )
            inserted += 1
    return inserted
