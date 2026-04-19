#!/usr/bin/env python3
"""Generate the Phase 2 completion-pass domain list from seed DBs.

Scans all ots-*.db files in a directory (or a single merged DB) and
extracts domains that need a second pass because their signals are
incomplete. Writes a domain-per-line file suitable for crawl_seed.py.

Usage:
    python3 generate_completion_list.py /path/to/data/          # scan dir
    python3 generate_completion_list.py /path/to/merged.db      # single DB
    python3 generate_completion_list.py /path/to/data/ --out completion.txt

Criteria for inclusion in the completion list:
1. Content score is 0 (content fetch failed or was skipped)
2. Identity score is 0 (WHOIS timed out or was unavailable)
3. Domain age score is 0 (WHOIS registration date not found)
4. Fewer than 4 signals have non-zero scores (minimal completeness)

These are the domains that would benefit most from a second pass with
longer timeouts, since the first pass likely hit the 60s cap before
the pipeline could collect all signals.
"""

import glob
import json
import os
import sqlite3
import sys
from pathlib import Path


def scan_db(db_path: str) -> dict:
    """Scan a single DB and return incomplete domains with their gaps."""
    results = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT domain, response_json FROM scored_results"
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"  SKIP {db_path}: {e}", file=sys.stderr)
        return {}

    for row in rows:
        domain = row["domain"]
        try:
            resp = json.loads(row["response_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        signals = resp.get("signals", {})
        scores = {
            "reputation": signals.get("reputation", {}).get("score", 0),
            "identity": signals.get("identity", {}).get("score", 0),
            "content": signals.get("content", {}).get("score", 0),
            "domainAge": signals.get("domainAge", {}).get("score", 0),
            "ssl": signals.get("ssl", {}).get("score", 0),
            "dns": signals.get("dns", {}).get("score", 0),
        }

        # Count zero-score signals
        zeros = [k for k, v in scores.items() if v == 0]
        non_zero = 6 - len(zeros)

        # Include if the domain has EVIDENCE GAPS (not just weak signals).
        # A new domain with age=0 is legitimately new, not incomplete.
        # A domain with content=0 on a successful crawl just lacks a
        # privacy policy. We only re-queue domains where the PIPELINE
        # couldn't collect data, not where the data was collected and
        # found weak.
        needs_completion = False
        reason = []

        # Content=0 is a gap ONLY if content was unscorable (blocked)
        resp_flags = resp.get("flags", [])
        if scores["content"] == 0 and "CONTENT_UNSCORABLE" in resp_flags:
            needs_completion = True
            reason.append("content_blocked")

        # Identity=0 is likely a WHOIS failure (gap), not "verified empty"
        if scores["identity"] == 0:
            needs_completion = True
            reason.append("identity=0")

        # Age=0 is a gap only if we got NO registration date at all
        age_date = signals.get("domainAge", {}).get("registeredDate", "")
        if scores["domainAge"] == 0 and not age_date:
            needs_completion = True
            reason.append("whois_failed")

        # Very few signals = pipeline couldn't run most collectors
        if non_zero < 3:
            needs_completion = True
            reason.append(f"only {non_zero}/6 signals")

        if needs_completion and domain not in results:
            results[domain] = {
                "score": resp.get("trustScore", 0),
                "zeros": zeros,
                "reason": ", ".join(reason),
            }

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_completion_list.py /path/to/data/ [--out file.txt]")
        sys.exit(1)

    source = sys.argv[1]
    out_file = "completion-domains.txt"

    for i, arg in enumerate(sys.argv):
        if arg == "--out" and i + 1 < len(sys.argv):
            out_file = sys.argv[i + 1]

    # Find DBs
    if Path(source).is_dir():
        dbs = sorted(glob.glob(str(Path(source) / "ots-*.db")))
        # Also check for ots.db
        main_db = Path(source) / "ots.db"
        if main_db.exists():
            dbs.append(str(main_db))
        # Also check for merged.db
        merged = Path(source) / "merged.db"
        if merged.exists():
            dbs.append(str(merged))
    else:
        dbs = [source]

    if not dbs:
        print(f"No DB files found in {source}")
        sys.exit(1)

    print(f"Scanning {len(dbs)} database(s)...")

    all_incomplete = {}
    for db in dbs:
        results = scan_db(db)
        for domain, info in results.items():
            if domain not in all_incomplete:
                all_incomplete[domain] = info

    # Write domain list
    domains = sorted(all_incomplete.keys())
    with open(out_file, "w") as f:
        f.write("\n".join(domains))

    print(f"\nIncomplete domains: {len(domains)}")
    print(f"Written to: {out_file}")

    # Summary of WHY they're incomplete
    reasons = {}
    for info in all_incomplete.values():
        for r in info["reason"].split(", "):
            reasons[r] = reasons.get(r, 0) + 1

    print("\nGap distribution:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count:,}")


if __name__ == "__main__":
    main()
