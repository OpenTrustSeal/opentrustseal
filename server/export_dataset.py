#!/usr/bin/env python3
"""Export the OTT trust registry as an open dataset.

Usage:
    python3 export_dataset.py                    # Export to exports/
    python3 export_dataset.py --out /tmp/export  # Custom output directory
    python3 export_dataset.py --format csv       # CSV only
    python3 export_dataset.py --format json      # JSON only
    python3 export_dataset.py --format both      # Both (default)

Outputs:
    ott-trust-dataset-YYYY-MM-DD.csv
    ott-trust-dataset-YYYY-MM-DD.json
    ott-trust-dataset-YYYY-MM-DD.sha256

The dataset includes every scored domain with its trust score, signal
breakdown, recommendation, brand tier, crawlability status, and scoring
model version. Raw evidence is omitted (it's available via the API for
individual lookups). The SHA-256 manifest covers all output files so
downloaders can verify integrity.

Intended for publication on Hugging Face, GitHub Releases, or direct
download from opentrusttoken.com/data/.
"""

import csv
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("OTT_DB_PATH", "./data/ott.db"))


def load_scored_results(db_path: Path) -> list[dict]:
    """Read all scored results and flatten into export rows."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT domain, response_json, trust_score, recommendation, "
        "scoring_model, checked_at FROM scored_results "
        "ORDER BY trust_score DESC"
    ).fetchall()
    conn.close()

    results = []
    for row in rows:
        try:
            resp = json.loads(row["response_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        signals = resp.get("signals", {})

        results.append({
            "domain": row["domain"],
            "trustScore": row["trust_score"],
            "recommendation": row["recommendation"],
            "scoringModel": row["scoring_model"],
            "brandTier": resp.get("brandTier", "scored"),
            "crawlability": resp.get("crawlability", "unknown"),
            "checkedAt": row["checked_at"],
            # Signal scores (0-100 each)
            "reputationScore": signals.get("reputation", {}).get("score", ""),
            "identityScore": signals.get("identity", {}).get("score", ""),
            "contentScore": signals.get("content", {}).get("score", ""),
            "domainAgeScore": signals.get("domainAge", {}).get("score", ""),
            "sslScore": signals.get("ssl", {}).get("score", ""),
            "dnsScore": signals.get("dns", {}).get("score", ""),
            # Key evidence fields
            "domainAge_registeredDate": signals.get("domainAge", {}).get("registeredDate", ""),
            "ssl_valid": signals.get("ssl", {}).get("valid", ""),
            "ssl_issuer": signals.get("ssl", {}).get("issuer", ""),
            "ssl_tlsVersion": signals.get("ssl", {}).get("tlsVersion", ""),
            "dns_spf": signals.get("dns", {}).get("spf", ""),
            "dns_dmarc": signals.get("dns", {}).get("dmarc", ""),
            "dns_dnssec": signals.get("dns", {}).get("dnssec", ""),
            "content_privacyPolicy": signals.get("content", {}).get("privacyPolicy", ""),
            "content_termsOfService": signals.get("content", {}).get("termsOfService", ""),
            "content_contactInfo": signals.get("content", {}).get("contactInfo", ""),
            "reputation_malware": signals.get("reputation", {}).get("malware", ""),
            "reputation_phishing": signals.get("reputation", {}).get("phishing", ""),
            "reputation_spamListed": signals.get("reputation", {}).get("spamListed", ""),
            # Flags
            "flags": "|".join(resp.get("flags", [])),
        })

    return results


CSV_FIELDS = [
    "domain", "trustScore", "recommendation", "scoringModel",
    "brandTier", "crawlability", "checkedAt",
    "reputationScore", "identityScore", "contentScore",
    "domainAgeScore", "sslScore", "dnsScore",
    "domainAge_registeredDate",
    "ssl_valid", "ssl_issuer", "ssl_tlsVersion",
    "dns_spf", "dns_dmarc", "dns_dnssec",
    "content_privacyPolicy", "content_termsOfService", "content_contactInfo",
    "reputation_malware", "reputation_phishing", "reputation_spamListed",
    "flags",
]


def write_csv(results: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(results)
    print(f"  CSV: {path} ({len(results)} rows, {path.stat().st_size:,} bytes)")


def write_json(results: list[dict], path: Path, meta: dict) -> None:
    payload = {
        "meta": meta,
        "domains": results,
    }
    with path.open("w") as f:
        json.dump(payload, f, indent=2)
    print(f"  JSON: {path} ({len(results)} domains, {path.stat().st_size:,} bytes)")


def write_manifest(files: list[Path], manifest_path: Path) -> None:
    lines = []
    for p in files:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        lines.append(f"{h}  {p.name}")
    manifest_path.write_text("\n".join(lines) + "\n")
    print(f"  SHA256: {manifest_path}")


def main():
    # Parse args
    out_dir = Path("exports")
    fmt = "both"
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--out" and i + 1 < len(sys.argv):
            out_dir = Path(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--format" and i + 1 < len(sys.argv):
            fmt = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = f"ott-trust-dataset-{today}"

    print(f"Exporting from {DB_PATH}...")
    results = load_scored_results(DB_PATH)
    if not results:
        print("No scored results found.")
        sys.exit(1)

    # Score distribution for meta
    scores = [r["trustScore"] for r in results]
    proceed = sum(1 for r in results if r["recommendation"] == "PROCEED")
    caution = sum(1 for r in results if r["recommendation"] == "CAUTION")
    deny = sum(1 for r in results if r["recommendation"] == "DENY")

    meta = {
        "name": "OpenTrustToken Trust Dataset",
        "description": "Trust scores and signal data for web domains, "
                       "produced by the OpenTrustToken independent trust "
                       "attestation API (api.opentrusttoken.com).",
        "version": today,
        "totalDomains": len(results),
        "scoringModel": results[0]["scoringModel"] if results else "unknown",
        "scoreRange": {"min": min(scores), "max": max(scores), "mean": round(sum(scores) / len(scores), 1)},
        "distribution": {"PROCEED": proceed, "CAUTION": caution, "DENY": deny},
        "exportedAt": datetime.now(timezone.utc).isoformat() + "Z",
        "source": "https://api.opentrusttoken.com",
        "license": "CC-BY-4.0",
        "methodology": "https://opentrusttoken.com/docs/methodology",
    }

    files_written = []

    if fmt in ("csv", "both"):
        csv_path = out_dir / f"{base}.csv"
        write_csv(results, csv_path)
        files_written.append(csv_path)

    if fmt in ("json", "both"):
        json_path = out_dir / f"{base}.json"
        write_json(results, json_path, meta)
        files_written.append(json_path)

    if files_written:
        manifest_path = out_dir / f"{base}.sha256"
        write_manifest(files_written, manifest_path)

    print()
    print(f"Dataset exported: {len(results)} domains")
    print(f"Score range: {min(scores)}-{max(scores)}, mean: {sum(scores)/len(scores):.1f}")
    print(f"PROCEED: {proceed} | CAUTION: {caution} | DENY: {deny}")


if __name__ == "__main__":
    main()
