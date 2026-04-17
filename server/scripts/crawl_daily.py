#!/usr/bin/env python3
"""Daily re-crawler. Picks the stalest N domains from the registry and
re-checks them via run_check(), updating raw_signals and scored_results.

Writes a heartbeat JSON on completion so /stats can surface liveness.

Usage:
    python3 crawl_daily.py              # Re-crawl OTS_DAILY_BATCH (default 200)
    python3 crawl_daily.py --batch 500  # Override batch size

Environment:
    OTS_DAILY_BATCH     How many stalest domains to re-check per run (default 200)
    OTS_DAILY_DELAY_MS  Delay between checks in ms (default 1000)
    OTS_DATA_DIR        Data directory; heartbeat lives here (default ./data)
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline import run_check
from app.database import init_db, store_check, _get_conn
from app.signing import ensure_keys


HEARTBEAT_FILENAME = ".last-daily-crawl.json"


def data_dir() -> Path:
    return Path(os.environ.get("OTS_DATA_DIR", "./data"))


def heartbeat_path() -> Path:
    return data_dir() / HEARTBEAT_FILENAME


def pick_stalest_domains(batch: int) -> list[str]:
    """Return the N domains with the oldest last_checked_at."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT domain FROM domains ORDER BY last_checked_at ASC LIMIT ?",
            (batch,),
        ).fetchall()
    return [r["domain"] for r in rows]


def write_heartbeat(payload: dict) -> None:
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2)
    tmp.replace(path)


async def crawl_one(domain: str, idx: int, total: int) -> dict:
    start = time.time()
    try:
        result = await run_check(domain)
        result_dict = result.model_dump(by_alias=True)
        store_check(domain, result_dict)
        elapsed = time.time() - start
        score = result_dict["trustScore"]
        rec = result_dict["recommendation"]
        print(f"  [{idx}/{total}] {domain:40s} {score:3d}  {rec:8s}  ({elapsed:.1f}s)", flush=True)
        return {"domain": domain, "ok": True, "score": score, "recommendation": rec}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [{idx}/{total}] {domain:40s} ERROR: {str(e)[:60]}  ({elapsed:.1f}s)", flush=True)
        return {"domain": domain, "ok": False, "error": str(e)[:200]}


async def main() -> int:
    batch = int(os.environ.get("OTS_DAILY_BATCH", "200"))
    delay_ms = int(os.environ.get("OTS_DAILY_DELAY_MS", "1000"))

    # --batch N override
    if "--batch" in sys.argv:
        i = sys.argv.index("--batch")
        if i + 1 < len(sys.argv):
            batch = int(sys.argv[i + 1])

    init_db()
    ensure_keys()

    started_at = datetime.now(timezone.utc)
    started_iso = started_at.isoformat().replace("+00:00", "Z")

    # Best-effort heartbeat BEFORE the run so we can see "started but not finished"
    write_heartbeat({
        "last_run_started_at": started_iso,
        "last_run_completed_at": None,
        "last_run_ok": None,
        "batch_requested": batch,
        "domains_attempted": 0,
        "domains_ok": 0,
        "domains_errored": 0,
        "duration_seconds": None,
        "next_expected_run_at": (started_at + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
    })

    domains = pick_stalest_domains(batch)
    if not domains:
        print("No domains in registry. Run the seed crawler first.", flush=True)
        write_heartbeat({
            "last_run_started_at": started_iso,
            "last_run_completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_run_ok": True,
            "batch_requested": batch,
            "domains_attempted": 0,
            "domains_ok": 0,
            "domains_errored": 0,
            "duration_seconds": 0,
            "note": "registry empty",
            "next_expected_run_at": (started_at + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        })
        return 0

    print(f"Daily re-crawl: {len(domains)} stalest domains, started at {started_iso}", flush=True)

    results: list[dict] = []
    for i, d in enumerate(domains, 1):
        results.append(await crawl_one(d, i, len(domains)))
        if delay_ms > 0 and i < len(domains):
            await asyncio.sleep(delay_ms / 1000.0)

    completed_at = datetime.now(timezone.utc)
    ok_count = sum(1 for r in results if r["ok"])
    err_count = len(results) - ok_count
    duration = (completed_at - started_at).total_seconds()

    # A run is considered "ok" if at least 80% of domains completed without error.
    # Lower than that and the next crawl is likely hitting a systemic issue
    # (crawler down, DB locked, network gone) and the heartbeat should reflect it.
    run_ok = (ok_count / max(len(results), 1)) >= 0.80

    payload = {
        "last_run_started_at": started_iso,
        "last_run_completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "last_run_ok": run_ok,
        "batch_requested": batch,
        "domains_attempted": len(results),
        "domains_ok": ok_count,
        "domains_errored": err_count,
        "duration_seconds": round(duration, 1),
        "success_rate": round(ok_count / max(len(results), 1), 3),
        "next_expected_run_at": (started_at + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
    }
    write_heartbeat(payload)

    print("", flush=True)
    print(f"Done: {ok_count}/{len(results)} ok, {err_count} errors, {duration:.0f}s", flush=True)
    print(f"Heartbeat written to {heartbeat_path()}", flush=True)

    return 0 if run_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
