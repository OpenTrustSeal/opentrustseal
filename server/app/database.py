"""SQLite persistence layer.

Stores three things separately:
1. Raw signals (facts that don't change with algorithm updates)
2. Scored results (computed from raw signals, can be recalculated)
3. Audit log

This separation means we can change the scoring algorithm and re-score
every domain from stored raw data without re-crawling.
"""

import json
import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(os.environ.get("OTT_DB_PATH", "./data/ott.db"))


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS domains (
                domain TEXT PRIMARY KEY,
                first_checked_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                check_count INTEGER DEFAULT 1,
                is_registered INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS raw_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                signal_data JSON NOT NULL,
                FOREIGN KEY (domain) REFERENCES domains(domain)
            );

            CREATE INDEX IF NOT EXISTS idx_raw_signals_domain
                ON raw_signals(domain);
            CREATE INDEX IF NOT EXISTS idx_raw_signals_checked
                ON raw_signals(checked_at);

            CREATE TABLE IF NOT EXISTS scored_results (
                domain TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                trust_score INTEGER NOT NULL,
                recommendation TEXT NOT NULL,
                scoring_model TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (domain) REFERENCES domains(domain)
            );

            CREATE INDEX IF NOT EXISTS idx_scored_expires
                ON scored_results(expires_at);

            CREATE TABLE IF NOT EXISTS score_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                trust_score INTEGER NOT NULL,
                recommendation TEXT NOT NULL,
                signal_scores TEXT NOT NULL,
                checked_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_score_history_domain
                ON score_history(domain, checked_at);

            CREATE TABLE IF NOT EXISTS registrations (
                domain TEXT PRIMARY KEY,
                -- Public fields
                business_name TEXT NOT NULL,
                country TEXT NOT NULL,
                state_province TEXT,
                business_type TEXT NOT NULL,
                website_category TEXT NOT NULL,
                year_established INTEGER,
                -- Private fields (sensitive)
                contact_name TEXT,
                contact_email TEXT NOT NULL,
                phone TEXT,
                address TEXT,
                ein_tax_id TEXT,
                social_twitter TEXT,
                social_linkedin TEXT,
                -- Verification state
                verification_code TEXT,
                verification_method TEXT DEFAULT 'dns',
                domain_verified INTEGER DEFAULT 0,
                domain_verified_at TEXT,
                -- Cross-reference results
                email_domain_match INTEGER DEFAULT 0,
                business_name_whois_match INTEGER DEFAULT 0,
                business_name_cert_match INTEGER DEFAULT 0,
                ein_verified INTEGER DEFAULT 0,
                phone_verified INTEGER DEFAULT 0,
                address_verified INTEGER DEFAULT 0,
                social_verified INTEGER DEFAULT 0,
                business_registry_match INTEGER DEFAULT 0,
                -- Computed
                verification_score INTEGER DEFAULT 0,
                -- Metadata
                registered_at TEXT NOT NULL,
                updated_at TEXT,
                ip_address TEXT,
                status TEXT DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                domain TEXT,
                detail TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Migration: if old 'checks' table exists, migrate data
            CREATE TABLE IF NOT EXISTS _migration_done (id INTEGER PRIMARY KEY);
        """)

        # Migrate from old schema if needed
        try:
            old = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checks'").fetchone()
            migrated = conn.execute("SELECT COUNT(*) FROM _migration_done").fetchone()[0]
            if old and not migrated:
                rows = conn.execute("SELECT domain, response_json, trust_score, recommendation, checked_at, expires_at FROM checks").fetchall()
                for row in rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO domains (domain, first_checked_at, last_checked_at) VALUES (?, ?, ?)",
                        (row[0], row[4], row[4]),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO scored_results (domain, response_json, trust_score, recommendation, scoring_model, checked_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (row[0], row[1], row[2], row[3], "ott-v1-weights", row[4], row[5]),
                    )
                conn.execute("INSERT INTO _migration_done VALUES (1)")
                conn.execute("DROP TABLE checks")
        except Exception:
            pass


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


def get_cached_check(domain: str) -> dict | None:
    """Return cached scored result if it exists and hasn't expired."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT response_json, expires_at FROM scored_results WHERE domain = ?",
            (domain,),
        ).fetchone()

    if row is None:
        return None

    expires_str = row["expires_at"].rstrip("Z").split("+")[0]
    expires = datetime.fromisoformat(expires_str).replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return None

    return json.loads(row["response_json"])


def store_raw_signals(domain: str, signal_data: dict) -> None:
    """Store the raw signal facts for a domain check."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO domains (domain, first_checked_at, last_checked_at, check_count)
               VALUES (
                   ?,
                   COALESCE((SELECT first_checked_at FROM domains WHERE domain = ?), ?),
                   ?,
                   COALESCE((SELECT check_count FROM domains WHERE domain = ?) + 1, 1)
               )""",
            (domain, domain, now, now, domain),
        )
        conn.execute(
            "INSERT INTO raw_signals (domain, checked_at, signal_data) VALUES (?, ?, ?)",
            (domain, now, json.dumps(signal_data)),
        )


def store_check(domain: str, response: dict) -> None:
    """Store a scored result."""
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO scored_results
               (domain, response_json, trust_score, recommendation, scoring_model, checked_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                domain,
                json.dumps(response),
                response.get("trustScore", 0),
                response.get("recommendation", "DENY"),
                response.get("scoringModel", "unknown"),
                response.get("checkedAt", ""),
                response.get("expiresAt", ""),
            ),
        )


def get_latest_raw_content(domain: str) -> dict | None:
    """Return the content subobject from the most recent raw_signals row.

    Used by the pipeline to recover from transient content_check failures:
    when a fetch fails (timeout, bot block, 5xx), we reuse the last
    known-good content signals instead of overwriting the registry with
    zeros. Returns None if no prior row exists.
    """
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT signal_data FROM raw_signals
               WHERE domain = ? ORDER BY checked_at DESC LIMIT 1""",
            (domain,),
        ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["signal_data"])
    except (TypeError, ValueError):
        return None
    return data.get("content")


def get_all_raw_signals(domain: str = None) -> list[dict]:
    """Get raw signals, optionally filtered by domain.

    Used for re-scoring when the algorithm changes.
    """
    with _get_conn() as conn:
        if domain:
            rows = conn.execute(
                """SELECT domain, checked_at, signal_data FROM raw_signals
                   WHERE domain = ? ORDER BY checked_at DESC""",
                (domain,),
            ).fetchall()
        else:
            # Get latest raw signals for each domain
            rows = conn.execute(
                """SELECT r.domain, r.checked_at, r.signal_data
                   FROM raw_signals r
                   INNER JOIN (
                       SELECT domain, MAX(checked_at) as max_checked
                       FROM raw_signals GROUP BY domain
                   ) latest ON r.domain = latest.domain AND r.checked_at = latest.max_checked
                   ORDER BY r.domain"""
            ).fetchall()

    return [
        {
            "domain": row["domain"],
            "checked_at": row["checked_at"],
            "signal_data": json.loads(row["signal_data"]),
        }
        for row in rows
    ]


def is_registered(domain: str) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT domain_verified FROM registrations WHERE domain = ? AND status = 'active'",
            (domain,),
        ).fetchone()
    return bool(row and row["domain_verified"])


def get_registration(domain: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM registrations WHERE domain = ?", (domain,)
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def save_registration(data: dict) -> None:
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO registrations
               (domain, business_name, country, state_province, business_type,
                website_category, year_established, contact_name, contact_email,
                phone, address, ein_tax_id, social_twitter, social_linkedin,
                verification_code, verification_method, registered_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["domain"], data["business_name"], data["country"],
                data.get("state_province"), data["business_type"],
                data["website_category"], data.get("year_established"),
                data.get("contact_name"), data["contact_email"],
                data.get("phone"), data.get("address"),
                data.get("ein_tax_id"),
                data.get("social_twitter"), data.get("social_linkedin"),
                data["verification_code"], data.get("verification_method", "dns"),
                data["registered_at"], "pending",
            ),
        )


def update_registration_verification(domain: str, updates: dict) -> None:
    with _get_conn() as conn:
        set_clauses = []
        values = []
        for key, val in updates.items():
            set_clauses.append(f"{key} = ?")
            values.append(val)
        values.append(domain)
        conn.execute(
            f"UPDATE registrations SET {', '.join(set_clauses)} WHERE domain = ?",
            values,
        )


def get_registration_public(domain: str) -> dict | None:
    """Return only public fields for API responses."""
    reg = get_registration(domain)
    if reg is None:
        return None
    return {
        "domain": reg["domain"],
        "businessName": reg["business_name"],
        "country": reg["country"],
        "stateProvince": reg["state_province"],
        "businessType": reg["business_type"],
        "websiteCategory": reg["website_category"],
        "yearEstablished": reg["year_established"],
        "registeredAt": reg["registered_at"],
        "status": reg["status"],
        "domainVerified": bool(reg["domain_verified"]),
        "emailDomainMatch": bool(reg["email_domain_match"]),
        "einVerified": bool(reg["ein_verified"]),
        "phoneVerified": bool(reg["phone_verified"]),
        "addressVerified": bool(reg["address_verified"]),
        "socialVerified": bool(reg["social_verified"]),
        "businessRegistryMatch": bool(reg["business_registry_match"]),
        "businessNameWhoisMatch": bool(reg["business_name_whois_match"]),
        "businessNameCertMatch": bool(reg["business_name_cert_match"]),
        "verificationScore": reg["verification_score"],
    }


def get_score_history(domain: str, limit: int = 30) -> list[dict]:
    """Get score history from the score_history table."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT trust_score, recommendation, signal_scores, checked_at
               FROM score_history
               WHERE domain = ? ORDER BY checked_at ASC LIMIT ?""",
            (domain, limit),
        ).fetchall()

    return [
        {
            "trustScore": row["trust_score"],
            "recommendation": row["recommendation"],
            "signals": json.loads(row["signal_scores"]),
            "checkedAt": row["checked_at"],
        }
        for row in rows
    ]


def store_score_snapshot(
    domain: str, trust_score: int, recommendation: str, signal_scores: dict,
) -> None:
    """Store a score snapshot for history tracking."""
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO score_history
               (domain, trust_score, recommendation, signal_scores, checked_at)
               VALUES (?, ?, ?, ?, ?)""",
            (domain, trust_score, recommendation,
             json.dumps(signal_scores),
             datetime.now(timezone.utc).isoformat()),
        )


def log_audit(action: str, domain: str = None, detail: str = None) -> None:
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (action, domain, detail) VALUES (?, ?, ?)",
            (action, domain, detail),
        )


def get_stats() -> dict:
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        active = conn.execute(
            "SELECT COUNT(*) FROM scored_results WHERE expires_at > ?", (now,)
        ).fetchone()[0]
        by_rec = conn.execute(
            "SELECT recommendation, COUNT(*) FROM scored_results GROUP BY recommendation"
        ).fetchall()
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_signals").fetchone()[0]
        avg_score = conn.execute(
            "SELECT AVG(trust_score) FROM scored_results"
        ).fetchone()[0]

    return {
        "totalDomains": total,
        "activeDomains": active,
        "rawSignalRecords": raw_count,
        "averageScore": round(avg_score, 1) if avg_score else 0,
        "byRecommendation": {row[0]: row[1] for row in by_rec},
    }
