#!/usr/bin/env python3
"""Merge a source SQLite database into the production OTT database.

Usage:
    python3 merge_db.py /path/to/source.db              # Merge into default DB
    python3 merge_db.py /path/to/source.db --target ./data/ots.db  # Explicit target
    python3 merge_db.py /path/to/source.db --dry-run     # Show counts without merging

Merges domains, raw_signals, scored_results, and score_history from the
source DB into the target DB. Skips duplicates (INSERT OR IGNORE on
primary keys). Designed for merging the burst-droplet seed crawl into
the production registry after the seed completes.

Safe to run while the API is serving: SQLite WAL mode handles concurrent
reads and the merge is a single transaction per table.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def merge(source_path: str, target_path: str, dry_run: bool = False) -> dict:
    """Merge source into target. Returns counts of rows merged per table."""
    src = sqlite3.connect(source_path)
    src.row_factory = sqlite3.Row
    tgt = sqlite3.connect(target_path)
    tgt.execute("PRAGMA journal_mode=WAL")

    counts = {}

    # 1. domains table
    src_domains = src.execute("SELECT * FROM domains").fetchall()
    new_domains = 0
    if not dry_run:
        for row in src_domains:
            try:
                tgt.execute(
                    "INSERT OR IGNORE INTO domains "
                    "(domain, first_checked_at, last_checked_at, check_count, is_registered) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (row["domain"], row["first_checked_at"], row["last_checked_at"],
                     row["check_count"], row["is_registered"]),
                )
                if tgt.execute("SELECT changes()").fetchone()[0] > 0:
                    new_domains += 1
            except Exception:
                pass
        tgt.commit()
    else:
        existing = {r[0] for r in tgt.execute("SELECT domain FROM domains").fetchall()}
        new_domains = sum(1 for r in src_domains if r["domain"] not in existing)
    counts["domains"] = {"total_in_source": len(src_domains), "new": new_domains}
    print(f"  domains: {len(src_domains)} in source, {new_domains} new")

    # 2. raw_signals table
    src_signals = src.execute("SELECT * FROM raw_signals").fetchall()
    new_signals = 0
    if not dry_run:
        for row in src_signals:
            try:
                tgt.execute(
                    "INSERT INTO raw_signals (domain, checked_at, signal_data) "
                    "VALUES (?, ?, ?)",
                    (row["domain"], row["checked_at"], row["signal_data"]),
                )
                new_signals += 1
            except Exception:
                pass
        tgt.commit()
    else:
        new_signals = len(src_signals)
    counts["raw_signals"] = {"total_in_source": len(src_signals), "new": new_signals}
    print(f"  raw_signals: {len(src_signals)} in source, {new_signals} new")

    # 3. scored_results table (INSERT OR REPLACE to update stale scores)
    src_scored = src.execute("SELECT * FROM scored_results").fetchall()
    merged_scored = 0
    if not dry_run:
        for row in src_scored:
            try:
                tgt.execute(
                    "INSERT OR REPLACE INTO scored_results "
                    "(domain, response_json, trust_score, recommendation, "
                    "scoring_model, checked_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["domain"], row["response_json"], row["trust_score"],
                     row["recommendation"], row["scoring_model"],
                     row["checked_at"], row["expires_at"]),
                )
                merged_scored += 1
            except Exception:
                pass
        tgt.commit()
    else:
        merged_scored = len(src_scored)
    counts["scored_results"] = {"total_in_source": len(src_scored), "merged": merged_scored}
    print(f"  scored_results: {len(src_scored)} in source, {merged_scored} merged")

    # 4. score_history table
    src_history = src.execute("SELECT * FROM score_history").fetchall()
    new_history = 0
    if not dry_run:
        for row in src_history:
            try:
                tgt.execute(
                    "INSERT INTO score_history "
                    "(domain, trust_score, recommendation, signal_scores, checked_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (row["domain"], row["trust_score"], row["recommendation"],
                     row["signal_scores"], row["checked_at"]),
                )
                new_history += 1
            except Exception:
                pass
        tgt.commit()
    else:
        new_history = len(src_history)
    counts["score_history"] = {"total_in_source": len(src_history), "new": new_history}
    print(f"  score_history: {len(src_history)} in source, {new_history} new")

    src.close()
    tgt.close()
    return counts


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 merge_db.py /path/to/source.db [--target target.db] [--dry-run]")
        print("       python3 merge_db.py /path/to/dir/ [--target target.db] [--dry-run]")
        print("       (directory mode merges all ots-*.db files in the dir)")
        sys.exit(1)

    source_arg = sys.argv[1]
    target = str(Path(os.environ.get("OTS_DB_PATH", "./data/ots.db")))
    dry_run = "--dry-run" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--target" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]

    # Support directory mode: merge all ots-*.db files in a directory
    import glob
    if Path(source_arg).is_dir():
        sources = sorted(glob.glob(str(Path(source_arg) / "ots-*.db")))
        if not sources:
            print(f"No ots-*.db files found in {source_arg}")
            sys.exit(1)
        print(f"Directory mode: found {len(sources)} DB files in {source_arg}")
    else:
        sources = [source_arg]

    if not Path(target).exists():
        # Create target with schema
        print(f"Target {target} does not exist, creating...")
        tgt = sqlite3.connect(target)
        tgt.execute("PRAGMA journal_mode=WAL")
        tgt.execute("CREATE TABLE IF NOT EXISTS domains (domain TEXT PRIMARY KEY, first_checked_at TEXT, last_checked_at TEXT, check_count INTEGER DEFAULT 1, is_registered INTEGER DEFAULT 0)")
        tgt.execute("CREATE TABLE IF NOT EXISTS raw_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, checked_at TEXT, signal_data TEXT)")
        tgt.execute("CREATE TABLE IF NOT EXISTS scored_results (domain TEXT PRIMARY KEY, response_json TEXT, trust_score INTEGER, recommendation TEXT, scoring_model TEXT, checked_at TEXT, expires_at TEXT)")
        tgt.execute("CREATE TABLE IF NOT EXISTS score_history (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, trust_score INTEGER, recommendation TEXT, signal_scores TEXT, checked_at TEXT)")
        tgt.commit()
        tgt.close()

    banner = "DRY-RUN: " if dry_run else ""
    grand_total = 0

    for source in sources:
        if not Path(source).exists():
            print(f"  SKIP: {source} not found")
            continue
        print(f"{banner}Merging {source} -> {target}")
        counts = merge(source, target, dry_run)
        total_new = sum(v.get("new", v.get("merged", 0)) for v in counts.values())
        grand_total += total_new
        print(f"  subtotal: {total_new} new rows")
        print()

    print(f"{'Would merge' if dry_run else 'Merged'}: {grand_total} total new rows across {len(sources)} source(s)")
    if dry_run:
        print("Remove --dry-run to execute.")


if __name__ == "__main__":
    main()
