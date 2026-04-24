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

DB_PATH = Path(os.environ.get("OTS_DB_PATH", "./data/ots.db"))


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

            -- Tier 6 strike accumulator. A domain accumulates a strike each
            -- time tiers 1-5 all fail for it. Tier 6 (commercial scraper)
            -- only fires after SCRAPER_GATE_STRIKES consecutive strikes.
            -- Any tier success resets strike_count to 0. See the tier 6
            -- integration spec at docs/TIER-6-COMMERCIAL-SCRAPER-SPEC.md.
            CREATE TABLE IF NOT EXISTS tier6_gate (
                domain TEXT PRIMARY KEY,
                strike_count INTEGER DEFAULT 0,
                last_strike_at TEXT,
                last_success_at TEXT,
                last_tier6_called_at TEXT,
                tier6_call_count INTEGER DEFAULT 0,
                last_tier6_status INTEGER
            );

            -- Outcome feedback from agents and merchants. Feeds the
            -- calibration dataset described in CLAUDE.md item #15. Schema
            -- supports two feedback sources:
            --   source='agent'     -> transaction outcome reports from API consumers
            --   source='merchant'  -> score-correction reports from registered domain owners
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                check_id TEXT,
                source TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT,
                submitter_type TEXT,
                submitter_contact TEXT,
                ip_address TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_domain
                ON feedback(domain);
            CREATE INDEX IF NOT EXISTS idx_feedback_created
                ON feedback(created_at);

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
                        (row[0], row[1], row[2], row[3], "ots-v1-weights", row[4], row[5]),
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
    """Load a registration row with its sensitive fields decrypted.

    Private fields (contact_name, contact_email, phone, address, ein_tax_id)
    are stored encrypted with NaCl SecretBox under /opt/opentrustseal/keys/
    registration_kek.bin. Rows predating the encryption landing are stored
    as plaintext; crypto.decrypt_field handles both shapes transparently.
    """
    from . import crypto as _crypto
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM registrations WHERE domain = ?", (domain,)
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    for field in _crypto.ENCRYPTED_REGISTRATION_FIELDS:
        if field in out:
            out[field] = _crypto.decrypt_field(out[field])
    return out


def save_registration(data: dict) -> None:
    """Write a registration row. Sensitive fields are encrypted at rest.

    Callers always pass plaintext for sensitive fields; encryption happens
    inside this function so the crypto boundary is one file, not sprinkled
    across the route handlers.
    """
    from . import crypto as _crypto
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
                _crypto.encrypt_field(data.get("contact_name")),
                _crypto.encrypt_field(data["contact_email"]),
                _crypto.encrypt_field(data.get("phone")),
                _crypto.encrypt_field(data.get("address")),
                _crypto.encrypt_field(data.get("ein_tax_id")),
                data.get("social_twitter"), data.get("social_linkedin"),
                data["verification_code"], data.get("verification_method", "dns"),
                data["registered_at"], "pending",
            ),
        )


def update_registration_verification(domain: str, updates: dict) -> None:
    """Update specific columns on an existing registration row.

    Automatically encrypts updates to any of the sensitive fields so a
    verification retry that sends a corrected email or phone doesn't land
    as plaintext. Non-sensitive fields pass through unchanged.
    """
    from . import crypto as _crypto
    with _get_conn() as conn:
        set_clauses = []
        values = []
        for key, val in updates.items():
            set_clauses.append(f"{key} = ?")
            if key in _crypto.ENCRYPTED_REGISTRATION_FIELDS and isinstance(val, str):
                val = _crypto.encrypt_field(val)
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


def store_feedback(
    domain: str,
    source: str,
    outcome: str,
    check_id: str | None = None,
    detail: str | None = None,
    submitter_type: str | None = None,
    submitter_contact: str | None = None,
    ip_address: str | None = None,
) -> int:
    """Insert one feedback row. Returns the generated feedback id.

    Feedback is the calibration dataset's raw input. Do not over-validate
    here; the endpoint layer validates shape. Trust the caller on content.
    """
    with _get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO feedback
                   (domain, check_id, source, outcome, detail,
                    submitter_type, submitter_contact, ip_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (domain, check_id, source, outcome, detail,
             submitter_type, submitter_contact, ip_address),
        )
        return cursor.lastrowid


def get_feedback_summary(domain: str, limit: int = 50) -> dict:
    """Return aggregated feedback for a domain, suitable for the dashboard."""
    with _get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE domain = ?", (domain,)
        ).fetchone()[0]
        by_outcome = conn.execute(
            """SELECT source, outcome, COUNT(*) AS n
               FROM feedback WHERE domain = ?
               GROUP BY source, outcome""",
            (domain,),
        ).fetchall()
        recent = conn.execute(
            """SELECT source, outcome, detail, created_at
               FROM feedback WHERE domain = ?
               ORDER BY id DESC LIMIT ?""",
            (domain, limit),
        ).fetchall()

    counts: dict = {}
    for row in by_outcome:
        counts.setdefault(row["source"], {})[row["outcome"]] = row["n"]

    return {
        "domain": domain,
        "total": total,
        "bySource": counts,
        "recent": [
            {
                "source": r["source"],
                "outcome": r["outcome"],
                "detail": r["detail"],
                "createdAt": r["created_at"],
            }
            for r in recent
        ],
    }


def get_coverage(domain: str) -> dict:
    """Check if a domain is in the scored dataset.

    Returns the minimum useful surface for the merchant-facing "is my
    domain in the dataset" self-serve check: whether we have a score,
    the scoring model that produced it, last-checked timestamp, and the
    headline recommendation. Does NOT return the full signed bundle;
    that is what /v1/check is for.
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return {"inDataset": False, "domain": domain}

    with _get_conn() as conn:
        row = conn.execute(
            """SELECT trust_score, recommendation, scoring_model, checked_at,
                      json_extract(response_json, '$.confidence') AS confidence,
                      json_extract(response_json, '$.cautionReason') AS caution_reason
               FROM scored_results WHERE domain = ?""",
            (domain,),
        ).fetchone()

    if row is None:
        return {"inDataset": False, "domain": domain}

    return {
        "inDataset": True,
        "domain": domain,
        "trustScore": row["trust_score"],
        "recommendation": row["recommendation"],
        "scoringModel": row["scoring_model"],
        "checkedAt": row["checked_at"],
        "confidence": row["confidence"] or "unknown",
        "cautionReason": row["caution_reason"],
    }


def get_dataset_stats() -> dict:
    """Dataset-shaped stats: breakdowns by confidence and cautionReason.

    Extracts confidence and cautionReason from response_json. Used by the
    dataset publication card, merchant outreach targeting, and health
    monitors that want to see how much of the registry is agent-usable
    vs incomplete-evidence vs actually-weak.
    """
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM scored_results").fetchone()[0]

        rec_by_conf = conn.execute("""
            SELECT
                recommendation,
                COALESCE(json_extract(response_json, '$.confidence'), 'unknown') AS confidence,
                COUNT(*) AS n
            FROM scored_results
            GROUP BY recommendation, confidence
        """).fetchall()

        by_caution_reason = conn.execute("""
            SELECT
                COALESCE(json_extract(response_json, '$.cautionReason'), 'not_caution') AS reason,
                COUNT(*) AS n
            FROM scored_results
            WHERE recommendation = 'CAUTION'
            GROUP BY reason
        """).fetchall()

        by_brand_tier = conn.execute("""
            SELECT
                COALESCE(json_extract(response_json, '$.brandTier'), 'scored') AS brand_tier,
                COUNT(*) AS n
            FROM scored_results
            GROUP BY brand_tier
        """).fetchall()

        agent_safe = conn.execute("""
            SELECT COUNT(*) FROM scored_results
            WHERE recommendation = 'PROCEED'
              AND COALESCE(json_extract(response_json, '$.confidence'), 'high') != 'low'
        """).fetchone()[0]

        incomplete_evidence = conn.execute("""
            SELECT COUNT(*) FROM scored_results
            WHERE json_extract(response_json, '$.cautionReason') = 'incomplete_evidence'
        """).fetchone()[0]

    confidence_totals = {}
    recommendation_x_confidence = {}
    for rec, conf, n in rec_by_conf:
        confidence_totals[conf] = confidence_totals.get(conf, 0) + n
        recommendation_x_confidence.setdefault(rec, {})[conf] = n

    return {
        "totalDomains": total,
        "agentSafe": agent_safe,
        "agentSafePercent": round(agent_safe / total * 100, 1) if total else 0,
        "incompleteEvidence": incomplete_evidence,
        "incompleteEvidencePercent": round(incomplete_evidence / total * 100, 1) if total else 0,
        "byConfidence": confidence_totals,
        "byCautionReason": {row[0]: row[1] for row in by_caution_reason},
        "byBrandTier": {row[0]: row[1] for row in by_brand_tier},
        "recommendationByConfidence": recommendation_x_confidence,
    }
