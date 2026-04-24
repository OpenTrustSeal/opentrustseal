#!/usr/bin/env python3
"""Re-score all domains from stored raw signals using the current algorithm.

Usage:
    python3 rescore.py              # Re-score and write results to DB
    python3 rescore.py --dry-run    # Compute new scores and print deltas
                                    # WITHOUT touching scored_results

This is the payoff of storing raw signals separately from scores.
When the algorithm changes, run this to update every score without re-crawling.

Always run with --dry-run first after any scoring-model change. The script
mutates 1000+ rows in a single pass and a silent bug would be expensive to
undo. The dry-run path is identical up to the store_check call.
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from app.database import init_db, get_all_raw_signals, store_check, _get_conn
from app.scoring import compute_score, compute_flags, compute_recommendation, generate_reasoning, compute_confidence, compute_caution_reason, SCORING_MODEL, _get_institutional_bonus, is_well_known_brand, is_consensus_tier
from app.checklist import generate_checklist, checklist_summary
from app.models.signals import (
    SignalBundle, DomainAgeSignal, SSLSignal, DNSSignal,
    ContentSignal, ReputationSignal, IdentitySignal,
)
from app.signing import ensure_keys, sign_payload
from app.collectors.tranco import rank_to_score, _load, TRANCO_FILE, _loaded
from datetime import datetime, timedelta, timezone


def rebuild_signals(raw: dict, domain: str) -> tuple:
    """Reconstruct signal models from stored raw data."""
    da = raw.get("domainAge", {})
    ssl_d = raw.get("ssl", {})
    dns_d = raw.get("dns", {})
    con = raw.get("content", {})
    rep = raw.get("reputation", {})
    iden = raw.get("identity", {})

    # Recompute Tranco score with current curve
    tranco_rank = rep.get("trancoRank")
    tranco_score = rank_to_score(tranco_rank)
    if tranco_score >= 0:
        rep_score = tranco_score
    elif not rep.get("malware") and not rep.get("phishing"):
        rep_score = 80
    else:
        rep_score = 0

    # Recompute content score
    found = sum([con.get("privacyPolicy", False), con.get("termsOfService", False), con.get("contactInfo", False)])
    content_score = 0
    if found == 1: content_score = 30
    elif found == 2: content_score = 50
    elif found >= 3: content_score = 70
    if con.get("hasRobots"): content_score += 5
    if con.get("hasSecurityTxt"): content_score += 10
    shc = con.get("securityHeaderCount", 0)
    if shc >= 3: content_score += 15
    elif shc >= 1: content_score += 5
    content_score = min(100, content_score)

    # Recompute SSL score
    ssl_score = 0
    if ssl_d.get("valid"):
        ssl_score = 60
        tlsv = ssl_d.get("tlsVersion", "")
        if tlsv in ("TLSv1.2", "TLSv1.3"): ssl_score = 80
        if tlsv == "TLSv1.3": ssl_score = 90
        if tlsv == "TLSv1.3" and ssl_d.get("hsts"): ssl_score = 100

    # Recompute DNS score
    spf = dns_d.get("spf", False)
    dmarc = dns_d.get("dmarc", False)
    dnssec = dns_d.get("dnssec", False)
    caa = dns_d.get("caa", False)
    dns_score = 20
    if spf: dns_score = 40
    if spf and dmarc: dns_score = 60
    if spf and dmarc and dnssec: dns_score = 90
    if spf and dmarc and dnssec and caa: dns_score = 100

    # Recompute domain age score
    age_score = 0
    reg_date = da.get("registeredDate")
    if reg_date:
        try:
            reg = datetime.strptime(reg_date, "%Y-%m-%d")
            days = (datetime.now() - reg).days
            if days <= 30: age_score = 0
            elif days <= 90: age_score = 20
            elif days <= 180: age_score = 40
            elif days <= 365: age_score = 60
            elif days <= 730: age_score = 75
            elif days <= 1825: age_score = 90
            else: age_score = 100
        except ValueError:
            pass

    # Recompute identity score using the v1.3 expanded Tranco buckets that
    # live in app/collectors/identity_check.py. The collector's scoring logic
    # must be kept in sync with this block any time the buckets or bonuses
    # change; the duplication exists because the collector itself makes
    # network calls (WHOIS, public-company lookup) and can't be called
    # directly with stored raw data.
    id_score = 0
    if tranco_rank is not None:
        if tranco_rank <= 100: id_score += 25
        elif tranco_rank <= 1000: id_score += 20
        elif tranco_rank <= 5000: id_score += 15
        elif tranco_rank <= 10000: id_score += 12
        elif tranco_rank <= 50000: id_score += 8
        elif tranco_rank <= 100000: id_score += 5
        elif tranco_rank <= 500000: id_score += 3
    if iden.get("hasOttFile"): id_score += 20
    if iden.get("whoisDisclosed"): id_score += 15
    elif iden.get("gdprRedacted"): id_score += 5
    ssl_org = iden.get("sslSubjectOrg", "")
    if ssl_org and len(ssl_org) > 1: id_score += 25
    if iden.get("isPublicCompany"): id_score += 10
    if iden.get("contactOnSite"): id_score += 10
    if iden.get("hasSchemaOrg"): id_score += 5
    id_score = min(55, id_score)

    content_signal = ContentSignal(privacyPolicy=con.get("privacyPolicy", False), termsOfService=con.get("termsOfService", False), contactInfo=con.get("contactInfo", False), score=content_score)
    content_signal._has_security_txt = con.get("hasSecurityTxt", False)
    content_signal._has_robots = con.get("hasRobots", False)
    content_signal._security_header_count = con.get("securityHeaderCount", 0)

    signals = SignalBundle(
        domainAge=DomainAgeSignal(registeredDate=reg_date, band=da.get("band", "unknown"), score=age_score),
        ssl=SSLSignal(valid=ssl_d.get("valid", False), issuer=ssl_d.get("issuer"), tlsVersion=ssl_d.get("tlsVersion"), hsts=ssl_d.get("hsts", False), score=ssl_score),
        dns=DNSSignal(spf=spf, dmarc=dmarc, dnssec=dnssec, caa=caa, score=dns_score),
        content=content_signal,
        reputation=ReputationSignal(malware=rep.get("malware", False), phishing=rep.get("phishing", False), spamListed=rep.get("spamListed", False), score=rep_score),
        identity=IdentitySignal(verified=False, verificationTier="automated", whoisDisclosed=iden.get("whoisDisclosed", False), businessDirectory=bool(ssl_org) or iden.get("hasSchemaOrg", False), contactOnSite=iden.get("contactOnSite", False), score=id_score),
    )

    # is_well_known_brand() reads tranco rank as a private attribute on the
    # reputation signal because the live pipeline stashes it there during
    # collection. Re-stash it here so the anchor gate can fire correctly.
    signals.reputation._tranco_rank = tranco_rank

    return signals, tranco_rank


def main():
    dry_run = "--dry-run" in sys.argv

    init_db()
    ensure_keys()

    # Ensure Tranco loaded
    if not _loaded and TRANCO_FILE.exists():
        _load()

    records = get_all_raw_signals()
    banner = "DRY-RUN: " if dry_run else ""
    print(f"{banner}Re-scoring {len(records)} domains with model {SCORING_MODEL}")
    if dry_run:
        print("scored_results will NOT be written. Remove --dry-run to apply.")
    print()

    old_scores = {}
    with _get_conn() as conn:
        for row in conn.execute("SELECT domain, trust_score, recommendation FROM scored_results"):
            old_scores[row[0]] = (row[1], row[2])

    results = []
    for rec in records:
        domain = rec["domain"]
        raw = rec["signal_data"]
        signals, tranco_rank = rebuild_signals(raw, domain)

        # Compute domain_age_days BEFORE scoring so is_well_known_brand has it
        domain_age_days = -1
        reg_date = raw.get("domainAge", {}).get("registeredDate")
        if reg_date:
            try:
                domain_age_days = (datetime.now() - datetime.strptime(reg_date, "%Y-%m-%d")).days
            except ValueError:
                pass

        # v1.3 anchor gate: top Tranco + aged + clean reputation + valid SSL
        well_known = is_well_known_brand(signals, domain_age_days)

        # v1.4 consensus tier: top-100 Tranco + 10-year age
        consensus = is_consensus_tier(signals, domain_age_days)

        # Content is treated as unscorable when the stored raw says so. This
        # lets the anchor path re-normalize the weights the same way the live
        # pipeline does.
        content_unscorable = bool(raw.get("content", {}).get("_unscorable", False))

        score = compute_score(
            signals, is_registered=False, domain=domain,
            content_scorable=not content_unscorable,
            well_known_brand=well_known,
            consensus_tier=consensus,
        )

        flags = compute_flags(
            signals, score, domain_age_days,
            well_known_brand=well_known,
        )
        if content_unscorable:
            flags.append("CONTENT_UNSCORABLE")
            if well_known:
                flags.append("ANCHOR_ONLY")

        # Parent-company linkage: if raw.identity has a stored parentCompany
        # match, treat as infrastructure so cautionReason maps correctly.
        # Also consult the live registry in case this domain matched an
        # entry added AFTER its raw signals were captured.
        site_category = "consumer"
        pc_stored = raw.get("identity", {}).get("parentCompany")
        if pc_stored and pc_stored.get("category"):
            from app.collectors import parent_company as _pc_lookup
            if _pc_lookup.is_infrastructure_category(pc_stored["category"]):
                site_category = "infrastructure"
        else:
            from app.collectors import parent_company as _pc_lookup
            live_match = _pc_lookup.lookup(domain)
            if live_match and _pc_lookup.is_infrastructure_category(live_match.category):
                site_category = "infrastructure"

        recommendation = compute_recommendation(score, flags)
        confidence = compute_confidence(signals, content_scorable=not content_unscorable, domain_age_days=domain_age_days)
        caution_reason = compute_caution_reason(
            signals, score, domain_age_days,
            content_scorable=not content_unscorable,
            confidence=confidence,
            site_category=site_category,
        )
        reasoning = generate_reasoning(
            signals, score, recommendation,
            content_unscorable=content_unscorable,
            well_known_brand=well_known,
        )

        cl = generate_checklist(signals, is_registered=False)
        cl_summary = checklist_summary(cl)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=7)

        # Signed fields MUST match the live pipeline (app/pipeline.py) exactly
        # so verifiers don't need to know whether a response was generated
        # live or via rescore. Adding fields here without adding them to the
        # live pipeline (or vice versa) breaks external signature verification.
        signable = {
            "domain": domain,
            "signals": signals.model_dump(by_alias=True),
            "flags": flags,
            "trustScore": score,
            "scoringModel": SCORING_MODEL,
            "recommendation": recommendation,
            "confidence": confidence,
            "cautionReason": caution_reason,
        }
        signature = sign_payload(signable)

        response = {
            "domain": domain,
            "checkedAt": now.isoformat(timespec="seconds") + "Z",
            "expiresAt": expires.isoformat(timespec="seconds") + "Z",
            "signals": signals.model_dump(by_alias=True),
            "flags": flags,
            "trustScore": score,
            "scoringModel": SCORING_MODEL,
            "recommendation": recommendation,
            "confidence": confidence,
            "cautionReason": caution_reason,
            "reasoning": reasoning,
            "crawlability": "blocked" if content_unscorable else "ok",
            "brandTier": "well_known" if well_known else "scored",
            "checklist": cl,
            "checklistSummary": cl_summary,
            "signature": signature,
            "signatureKeyId": "did:web:opentrustseal.com#signing-key-1",
            "issuer": "did:web:opentrustseal.com",
        }
        if not dry_run:
            store_check(domain, response)

        old = old_scores.get(domain, (0, "?"))
        delta = score - old[0]
        arrow = "+" if delta > 0 else "" if delta == 0 else ""
        marker = " ***" if old[1] != recommendation else ""
        print(f"  {domain:35s} {old[0]:3d} -> {score:3d} ({arrow}{delta:+d})  {recommendation:8s}{marker}")

        results.append({"domain": domain, "old": old[0], "new": score, "delta": delta, "rec": recommendation})

    # Summary
    print()
    upgrades = [r for r in results if r["delta"] > 0]
    downgrades = [r for r in results if r["delta"] < 0]
    unchanged = [r for r in results if r["delta"] == 0]
    proceed = [r for r in results if r["rec"] == "PROCEED"]
    caution = [r for r in results if r["rec"] == "CAUTION"]
    deny = [r for r in results if r["rec"] == "DENY"]

    footer = " (dry-run, nothing written)" if dry_run else ""
    print(f"Re-scored: {len(results)}{footer}")
    print(f"Upgraded: {len(upgrades)} | Downgraded: {len(downgrades)} | Unchanged: {len(unchanged)}")
    print(f"PROCEED: {len(proceed)} ({len(proceed)/max(len(results),1)*100:.0f}%)")
    print(f"CAUTION: {len(caution)} ({len(caution)/max(len(results),1)*100:.0f}%)")
    print(f"DENY:    {len(deny)} ({len(deny)/max(len(results),1)*100:.0f}%)")

    if results:
        scores = [r["new"] for r in results]
        print(f"Score range: {min(scores)}--{max(scores)} | Average: {sum(scores)/len(scores):.1f}")


if __name__ == "__main__":
    main()
