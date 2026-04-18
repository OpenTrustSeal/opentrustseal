#!/usr/bin/env python3
"""Parallel seed crawler for expanding the OTT registry to 100K+ domains.

Usage:
    python3 crawl_seed.py --top 100000                # Seed from Tranco top-100K
    python3 crawl_seed.py --top 100000 --workers 4    # 4 parallel workers
    python3 crawl_seed.py --top 100000 --resume       # Resume from checkpoint
    python3 crawl_seed.py domains.txt --workers 8     # Seed from file
    python3 crawl_seed.py --top 50000 --dry-run       # Show plan without crawling
    python3 crawl_seed.py --top 100000 --fast         # Fast mode: 5s WHOIS timeout,
                                                       # skip Playwright tiers 2-4

Checkpoint: data/.seed-checkpoint.json (written every 50 domains)
Progress:   data/.seed-progress.json (live stats for monitoring)

Handles interruption gracefully. Ctrl-C saves the checkpoint so --resume
picks up where you left off. Skips domains already in the database.

At 4 workers and ~10s/domain average, 100K domains takes roughly 70 hours
(~3 days). Use a larger --workers value on a beefier box, but watch memory
(each worker holds one pipeline execution in flight, ~50-100MB peak).
"""

import asyncio
import csv
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.pipeline import run_check
from app.database import init_db, store_check, _get_conn
from app.signing import ensure_keys


CHECKPOINT_FILE = Path(os.environ.get("OTS_DATA_DIR", "./data")) / ".seed-checkpoint.json"
PROGRESS_FILE = Path(os.environ.get("OTS_DATA_DIR", "./data")) / ".seed-progress.json"


# ---------------------------------------------------------------------------
# Tranco loader (duplicated from crawl.py to keep this file self-contained)
# ---------------------------------------------------------------------------

def get_tranco_top(n: int) -> list[str]:
    """Return the top N domains from the Tranco CSV."""
    data_dir = Path(os.environ.get("OTS_DATA_DIR", "./data"))
    tranco_file = data_dir / "tranco.csv"
    if not tranco_file.exists():
        print(f"FATAL: Tranco list not found at {tranco_file}", flush=True)
        sys.exit(1)
    rank_cache: dict[str, int] = {}
    with open(tranco_file, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                try:
                    rank_cache[row[1].strip().lower()] = int(row[0].strip())
                except (ValueError, IndexError):
                    continue
    print(f"Loaded {len(rank_cache)} domains from Tranco list", flush=True)
    sorted_domains = sorted(rank_cache.items(), key=lambda x: x[1])
    return [d[0] for d in sorted_domains[:n]]


# ---------------------------------------------------------------------------
# Already-checked filter
# ---------------------------------------------------------------------------

def get_already_in_db() -> set[str]:
    """Return the set of domains that already have a row in the domains table."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT domain FROM domains").fetchall()
    return {r["domain"] for r in rows}


# ---------------------------------------------------------------------------
# Checkpoint / progress
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w") as f:
        json.dump(data, f)
    tmp.replace(path)


def load_checkpoint() -> set[str]:
    if CHECKPOINT_FILE.exists():
        try:
            with CHECKPOINT_FILE.open() as f:
                return set(json.load(f).get("completed", []))
        except (OSError, json.JSONDecodeError):
            return set()
    return set()


def save_checkpoint(completed: set[str]) -> None:
    _atomic_write(CHECKPOINT_FILE, {
        "completed": list(completed),
        "count": len(completed),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    })


def save_progress(stats: dict, total: int) -> None:
    elapsed = time.time() - stats["start_time"]
    done = stats["total_done"]
    rate = done / max(elapsed, 1)
    remaining = total - done
    eta_s = remaining / max(rate, 0.001)
    _atomic_write(PROGRESS_FILE, {
        "total": total,
        "done": done,
        "ok": stats["ok"],
        "errors": stats["errors"],
        "elapsed_hours": round(elapsed / 3600, 2),
        "rate_per_min": round(rate * 60, 1),
        "eta_hours": round(eta_s / 3600, 1),
        "proceed": stats["PROCEED"],
        "caution": stats["CAUTION"],
        "deny": stats["DENY"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def worker(
    name: str,
    queue: asyncio.Queue,
    total: int,
    stats: dict,
    sem: asyncio.Semaphore,
    delay_s: float,
    stop_event: asyncio.Event,
) -> None:
    """Pull domains from queue, run the pipeline, store results."""
    while not stop_event.is_set():
        try:
            idx, domain = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        async with sem:
            if stop_event.is_set():
                break
            start = time.time()
            try:
                # Per-domain timeout: kill any domain that takes >60s.
                # Prevents slow WHOIS lookups or tier escalation chains
                # from blocking a worker for minutes. The checkpoint
                # records completed domains, so skipped ones get retried
                # on the next --resume run (or picked up by the daily
                # re-crawler with full tier escalation enabled).
                result = await asyncio.wait_for(run_check(domain), timeout=60.0)
                result_dict = result.model_dump(by_alias=True)
                store_check(domain, result_dict)
                elapsed = time.time() - start
                score = result_dict["trustScore"]
                rec = result_dict["recommendation"]
                stats["ok"] += 1
                stats[rec] = stats.get(rec, 0) + 1

                if idx % 10 == 0 or elapsed > 30:
                    print(
                        f"  [{idx}/{total}] {domain:40s} {score:3d} {rec:8s}"
                        f"  ({elapsed:.1f}s) [{name}]",
                        flush=True,
                    )
            except asyncio.TimeoutError:
                elapsed = time.time() - start
                stats["errors"] += 1
                if idx % 50 == 0:
                    print(
                        f"  [{idx}/{total}] {domain:40s} TIMEOUT ({elapsed:.0f}s)"
                        f"  [{name}]",
                        flush=True,
                    )
            except Exception as e:
                elapsed = time.time() - start
                stats["errors"] += 1
                if idx % 10 == 0:
                    print(
                        f"  [{idx}/{total}] {domain:40s} ERROR: {str(e)[:40]}"
                        f"  ({elapsed:.1f}s) [{name}]",
                        flush=True,
                    )

            stats["completed"].add(domain)
            stats["total_done"] += 1

            # Checkpoint and progress every 50 domains
            if stats["total_done"] % 50 == 0:
                save_checkpoint(stats["completed"])
                save_progress(stats, total)
                print(
                    f"  -- checkpoint: {stats['total_done']}/{total}"
                    f"  ok={stats['ok']} err={stats['errors']}"
                    f"  P={stats.get('PROCEED',0)} C={stats.get('CAUTION',0)} D={stats.get('DENY',0)}",
                    flush=True,
                )

            # Polite delay between requests from each worker
            if delay_s > 0 and not stop_event.is_set():
                await asyncio.sleep(delay_s)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> dict:
    args = {
        "top_n": None,
        "domain_file": None,
        "workers": 4,
        "resume": "--resume" in sys.argv,
        "dry_run": "--dry-run" in sys.argv,
        "fast": "--fast" in sys.argv,
        "delay_ms": 500,
    }
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--top" and i + 1 < len(sys.argv):
            args["top_n"] = int(sys.argv[i + 1])
            i += 2
        elif a == "--workers" and i + 1 < len(sys.argv):
            args["workers"] = int(sys.argv[i + 1])
            i += 2
        elif a == "--delay" and i + 1 < len(sys.argv):
            args["delay_ms"] = int(sys.argv[i + 1])
            i += 2
        elif a not in ("--resume", "--dry-run", "--fast") and os.path.isfile(a):
            args["domain_file"] = a
            i += 1
        else:
            i += 1
    if args["top_n"] is None and args["domain_file"] is None:
        args["top_n"] = 100000
    return args


async def main() -> int:
    cfg = parse_args()

    # ---- Fast mode: optimize for coverage over completeness ---------------
    # Caps WHOIS at 5s (instead of 30s), disables Playwright tiers 2-4
    # (which each add 30-60s of timeout on failure), keeps tier 1 direct
    # httpx + probe + Wayback. Domains with incomplete signals get filled
    # in by the daily re-crawler later.
    if cfg["fast"]:
        import socket
        socket.setdefaulttimeout(5)
        # Disable Playwright-based tiers by clearing their URLs. The
        # fetch_escalation module reads these at import time, so we must
        # set them before any import triggers the module load. Since the
        # module may already be loaded (crawl_seed imports app.pipeline
        # which imports fetch_escalation), we also patch the module vars
        # directly.
        os.environ["OTS_CRAWLER_URL"] = ""
        os.environ["OTS_CRAWLER_SECRET"] = ""
        os.environ["OTS_MACBOOK_URL"] = ""
        try:
            from app import fetch_escalation as fe
            fe.CRAWLER_ENABLED = False
            fe.MACBOOK_ENABLED = False
            fe.DECODO_ENABLED = False
        except ImportError:
            pass
        print("FAST MODE: WHOIS timeout=5s, Playwright tiers disabled", flush=True)

    init_db()
    ensure_keys()

    # ---- Build domain list ------------------------------------------------
    if cfg["top_n"]:
        raw_domains = get_tranco_top(cfg["top_n"])
    else:
        with open(cfg["domain_file"]) as f:
            raw_domains = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]

    # Deduplicate
    seen: set[str] = set()
    domains: list[str] = []
    for d in raw_domains:
        d = d.lower().strip()
        if d not in seen:
            seen.add(d)
            domains.append(d)

    # ---- Filter out already-checked and checkpointed ----------------------
    already = get_already_in_db()
    checkpoint = load_checkpoint() if cfg["resume"] else set()

    skip = already | checkpoint
    to_crawl = [d for d in domains if d not in skip]

    print(f"Tranco source: {len(domains)} unique domains", flush=True)
    print(f"Already in DB: {len(already & set(domains))}", flush=True)
    if cfg["resume"]:
        print(f"In checkpoint: {len(checkpoint & set(domains))}", flush=True)
    print(f"To crawl:      {len(to_crawl)}", flush=True)
    print(f"Workers:       {cfg['workers']}", flush=True)
    est_hours = len(to_crawl) * 10 / max(cfg["workers"], 1) / 3600
    print(f"Est. time:     {est_hours:.1f} hours ({est_hours/24:.1f} days)", flush=True)
    print(flush=True)

    if cfg["dry_run"]:
        print("DRY-RUN: would crawl the above. Remove --dry-run to execute.", flush=True)
        return 0

    if not to_crawl:
        print("Nothing to crawl. All domains already in DB or checkpoint.", flush=True)
        return 0

    # ---- Set up graceful shutdown -----------------------------------------
    stop_event = asyncio.Event()

    def handle_signal(sig, frame):
        print(f"\n  Caught signal {sig}. Saving checkpoint and shutting down...", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # ---- Build queue and run workers --------------------------------------
    queue: asyncio.Queue = asyncio.Queue()
    for i, d in enumerate(to_crawl, 1):
        queue.put_nowait((i, d))

    sem = asyncio.Semaphore(cfg["workers"])
    stats = {
        "ok": 0,
        "errors": 0,
        "total_done": 0,
        "completed": set(checkpoint),
        "start_time": time.time(),
        "PROCEED": 0,
        "CAUTION": 0,
        "DENY": 0,
    }

    started_at = datetime.now(timezone.utc)
    print(f"Starting seed crawl at {started_at.isoformat()}", flush=True)
    print(flush=True)

    tasks = [
        asyncio.create_task(
            worker(f"w{i}", queue, len(to_crawl), stats, sem, cfg["delay_ms"] / 1000.0, stop_event)
        )
        for i in range(cfg["workers"])
    ]
    await asyncio.gather(*tasks)

    # ---- Final checkpoint and summary -------------------------------------
    save_checkpoint(stats["completed"])
    save_progress(stats, len(to_crawl))

    elapsed = time.time() - stats["start_time"]
    print(flush=True)
    print("=" * 70, flush=True)
    done = stats["total_done"]
    ok = stats["ok"]
    err = stats["errors"]
    print(f"Seed crawl complete: {done} domains in {elapsed/3600:.1f} hours", flush=True)
    print(f"OK: {ok} | Errors: {err} | Success rate: {ok/max(done,1)*100:.1f}%", flush=True)
    print(
        f"PROCEED: {stats.get('PROCEED',0)} | CAUTION: {stats.get('CAUTION',0)}"
        f" | DENY: {stats.get('DENY',0)}",
        flush=True,
    )

    from app.database import get_stats
    db_stats = get_stats()
    print(
        f"Registry: {db_stats['totalDomains']} domains,"
        f" {db_stats['rawSignalRecords']} signal records",
        flush=True,
    )

    if stop_event.is_set():
        print(flush=True)
        print("Interrupted. Use --resume to continue from checkpoint.", flush=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
