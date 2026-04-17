"""Daily-crawl heartbeat reader.

The daily re-crawler writes a JSON file with its run metadata. This module
loads that file and computes a status string so /stats can surface whether
the daily data pipeline is actually running.

Status values:
    ok       -- last run completed within OK_WINDOW_HOURS
    stale    -- completed within STALE_WINDOW_HOURS but older than ok window
    dead     -- older than STALE_WINDOW_HOURS, or a started run never completed
    missing  -- no heartbeat file yet (new install, or never run)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

OK_WINDOW_HOURS = 30     # 24h cadence + 6h grace for long runs
STALE_WINDOW_HOURS = 48  # after this, escalate to "dead"

HEARTBEAT_FILENAME = ".last-daily-crawl.json"


def _heartbeat_path() -> Path:
    return Path(os.environ.get("OTT_DATA_DIR", "./data")) / HEARTBEAT_FILENAME


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def read_heartbeat() -> dict:
    """Return a dict describing the state of the daily crawl pipeline."""
    path = _heartbeat_path()
    now = datetime.now(timezone.utc)

    if not path.exists():
        return {
            "status": "missing",
            "message": "no heartbeat file yet; daily crawler has not completed a run",
            "path": str(path),
        }

    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": "dead",
            "message": f"heartbeat file unreadable: {e}",
            "path": str(path),
        }

    completed = _parse_iso(data.get("last_run_completed_at"))
    started = _parse_iso(data.get("last_run_started_at"))

    if completed is None:
        # A run started but never wrote its completion. Classify based on
        # how long ago it started.
        if started is None:
            status = "dead"
            age_hours = None
            message = "heartbeat missing both start and completion timestamps"
        else:
            age_hours = (now - started).total_seconds() / 3600.0
            if age_hours <= OK_WINDOW_HOURS:
                status = "stale"
                message = f"run started {age_hours:.1f}h ago and has not completed"
            else:
                status = "dead"
                message = f"run started {age_hours:.1f}h ago and never completed"
    else:
        age_hours = (now - completed).total_seconds() / 3600.0
        if age_hours <= OK_WINDOW_HOURS and data.get("last_run_ok") is True:
            status = "ok"
            message = f"last run completed {age_hours:.1f}h ago"
        elif age_hours <= OK_WINDOW_HOURS:
            # Completed recently but the run itself failed its success threshold.
            status = "stale"
            message = f"last run completed {age_hours:.1f}h ago but reported not-ok"
        elif age_hours <= STALE_WINDOW_HOURS:
            status = "stale"
            message = f"last run completed {age_hours:.1f}h ago (beyond ok window)"
        else:
            status = "dead"
            message = f"last run completed {age_hours:.1f}h ago (beyond stale window)"

    return {
        "status": status,
        "message": message,
        "age_hours": round(age_hours, 2) if age_hours is not None else None,
        "ok_window_hours": OK_WINDOW_HOURS,
        "stale_window_hours": STALE_WINDOW_HOURS,
        "last_run": data,
    }
