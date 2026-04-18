"""Transparency log for trust attestation bundles.

Provides tamper-evident logging of every attestation OTS issues. Each
entry includes a per-domain hash chain: the entry's hash references the
previous entry for the same domain, so retroactive modification of any
entry breaks the chain and is detectable by anyone who verifies it.

See docs/TRANSPARENCY-LOG.md for the design specification.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(os.environ.get("OTS_DB_PATH", "./data/ots.db"))


@contextmanager
def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_transparency_log() -> None:
    """Create the transparency_log table if it doesn't exist."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transparency_log (
                check_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                trust_score INTEGER NOT NULL,
                recommendation TEXT NOT NULL,
                scoring_model TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                signature_key_id TEXT NOT NULL,
                signature_hash TEXT NOT NULL,
                previous_entry_hash TEXT,
                entry_hash TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tlog_domain
                ON transparency_log(domain, checked_at)
        """)


def _compute_entry_hash(entry: dict) -> str:
    """SHA-256 of the canonical JSON representation of an entry.

    The entry_hash is what the NEXT entry for the same domain will
    reference as previous_entry_hash, creating the hash chain.
    """
    canonical = json.dumps(
        {k: entry[k] for k in sorted(entry.keys()) if k != "entry_hash"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _get_previous_hash(domain: str) -> str | None:
    """Return the entry_hash of the most recent log entry for this domain."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT entry_hash FROM transparency_log "
            "WHERE domain = ? ORDER BY checked_at DESC LIMIT 1",
            (domain,),
        ).fetchone()
    return row["entry_hash"] if row else None


def log_attestation(
    check_id: str,
    domain: str,
    trust_score: int,
    recommendation: str,
    scoring_model: str,
    checked_at: str,
    signature_key_id: str,
    signature: str,
) -> str:
    """Write an entry to the transparency log. Returns the entry_hash.

    The signature_hash is the SHA-256 of the raw signature string, not
    the signature itself. This lets auditors verify the chain without
    needing the full signature bytes (which are large).
    """
    signature_hash = hashlib.sha256(signature.encode()).hexdigest()
    previous_entry_hash = _get_previous_hash(domain)

    entry = {
        "check_id": check_id,
        "domain": domain,
        "trust_score": trust_score,
        "recommendation": recommendation,
        "scoring_model": scoring_model,
        "checked_at": checked_at,
        "signature_key_id": signature_key_id,
        "signature_hash": signature_hash,
        "previous_entry_hash": previous_entry_hash,
    }
    entry_hash = _compute_entry_hash(entry)

    with _get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO transparency_log
               (check_id, domain, trust_score, recommendation, scoring_model,
                checked_at, signature_key_id, signature_hash,
                previous_entry_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                check_id, domain, trust_score, recommendation, scoring_model,
                checked_at, signature_key_id, signature_hash,
                previous_entry_hash, entry_hash,
            ),
        )

    return entry_hash


def get_log_for_domain(domain: str, limit: int = 100) -> list[dict]:
    """Return all transparency log entries for a domain, newest first."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transparency_log WHERE domain = ? "
            "ORDER BY checked_at DESC LIMIT ?",
            (domain, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_entries(limit: int = 50) -> list[dict]:
    """Return the N most recent log entries across all domains."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transparency_log ORDER BY checked_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def verify_chain(domain: str) -> dict:
    """Verify the hash chain for a domain. Returns verification result.

    Checks that each entry's previous_entry_hash matches the entry_hash
    of the chronologically preceding entry. If any link is broken, the
    chain is invalid and the specific broken link is identified.
    """
    entries = get_log_for_domain(domain, limit=10000)
    if not entries:
        return {"domain": domain, "valid": True, "entries": 0, "message": "no entries"}

    # Entries are newest-first; reverse for chronological order
    entries.reverse()

    broken_links = []
    for i in range(1, len(entries)):
        expected = entries[i - 1]["entry_hash"]
        actual = entries[i]["previous_entry_hash"]
        if actual != expected:
            broken_links.append({
                "position": i,
                "check_id": entries[i]["check_id"],
                "expected_previous_hash": expected,
                "actual_previous_hash": actual,
            })

    return {
        "domain": domain,
        "valid": len(broken_links) == 0,
        "entries": len(entries),
        "broken_links": broken_links,
        "message": "chain intact" if not broken_links else f"{len(broken_links)} broken link(s)",
    }
