# OpenTrustSeal Transparency Log Design

Version 1.0 | April 2026

## Problem

OTS signs trust attestation bundles with Ed25519. A verifier can confirm that a bundle was signed by OTS, but cannot confirm that OTS didn't issue a DIFFERENT attestation for the same domain at the same time. Without a public log, OTS could:

- Issue a PROCEED verdict to one agent and a DENY verdict to another for the same domain
- Retroactively change a score after issuing an attestation (e.g., if a merchant complains)
- Silently revoke an attestation without detection

This is the same problem Certificate Transparency (CT) solves for SSL certificates. CT requires CAs to publish every issued certificate to a public, append-only log so that domain owners and auditors can detect mis-issuance. OTS needs the same property for trust attestations.

## Design

### What gets logged

Every attestation response from the `/v1/check/{domain}` endpoint is logged as an entry containing:

| Field | Description |
|---|---|
| `checkId` | Unique identifier for this check (UUID v4) |
| `domain` | The domain that was checked |
| `trustScore` | The computed score (0-100) |
| `recommendation` | PROCEED / CAUTION / DENY |
| `scoringModel` | Model version (e.g., `ots-v1.4-weights`) |
| `checkedAt` | ISO 8601 timestamp |
| `signatureKeyId` | Which key signed the attestation |
| `signatureHash` | SHA-256 of the full signature bytes |
| `previousEntryHash` | SHA-256 of the previous log entry for this domain (hash chain) |

### Hash chain structure

Each log entry includes `previousEntryHash`, which is the SHA-256 of the previous entry for the SAME domain. This creates a per-domain hash chain:

```
Entry 1 (google.com, April 1):  previousEntryHash = null (first check)
Entry 2 (google.com, April 8):  previousEntryHash = SHA-256(Entry 1)
Entry 3 (google.com, April 15): previousEntryHash = SHA-256(Entry 2)
```

An auditor who fetches all entries for a domain can verify the chain is unbroken. If OTS retroactively modified Entry 1, the hash in Entry 2 would no longer match, and the tampering would be detectable.

This is a simpler structure than a global Merkle tree (which CT uses). The trade-off: per-domain chains are easier to implement and audit but don't provide global consistency guarantees (you can't prove that the log contains ALL entries, only that the entries you can see are internally consistent). A global Merkle tree is a future upgrade if the log grows to millions of entries.

### Storage

The log is a separate SQLite table on the API box:

```sql
CREATE TABLE transparency_log (
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
);

CREATE INDEX idx_tlog_domain ON transparency_log(domain, checked_at);
```

`entry_hash` is the SHA-256 of the canonical JSON representation of all other fields. This is the value that the NEXT entry for the same domain will reference as `previous_entry_hash`.

### Public access

The log is queryable via two new API endpoints:

**GET /v1/log/{domain}**
Returns all log entries for a domain, ordered by `checked_at`. An auditor can verify the hash chain by computing each entry's hash and confirming it matches the `previous_entry_hash` of the next entry.

**GET /v1/log/latest**
Returns the N most recent log entries across all domains. Useful for monitors that watch for anomalies (e.g., a domain whose score swings from 80 to 20 between checks).

### What the log proves

1. **Consistency.** For a given domain, every attestation OTS has ever issued is visible in the hash chain. If OTS issued two different scores for the same domain at the same time, both entries would be in the chain and auditors could detect the conflict.

2. **Append-only.** Modifying or deleting a past entry breaks the hash chain for all subsequent entries. The tampering is detectable by anyone who verifies the chain.

3. **Non-repudiation.** OTS cannot claim it never issued a particular attestation. The entry is in the log, and the signature hash matches the attestation bundle the consumer received.

### What the log does NOT prove

1. **Completeness.** The log does not prove that it contains ALL attestations OTS has issued. OTS could issue an off-log attestation that never appears in the public log. A global Merkle tree with signed tree heads (like CT) would address this, but it's architecturally heavier and planned as a future upgrade.

2. **Correctness.** The log proves that OTS issued a specific score, not that the score was correct. Auditing score correctness requires re-running the scoring pipeline on the raw signals, which is a separate verification path (supported by the raw_signals storage and rescore.py).

## Implementation plan

| Phase | Scope | Effort |
|---|---|---|
| **Phase 1** | Add `check_id` (UUID) to every attestation response | 1 hour |
| **Phase 2** | Create `transparency_log` table, write entries on every check | 2 hours |
| **Phase 3** | Implement hash chain (per-domain `previous_entry_hash`) | 2 hours |
| **Phase 4** | Add `/v1/log/{domain}` and `/v1/log/latest` endpoints | 2 hours |
| **Phase 5** | Add `signatureKeyId` field to attestation responses | 1 hour |
| **Phase 6** | Documentation and public announcement | 1 hour |

Total: approximately 1.5 days of implementation work. Phases 1-3 can ship incrementally; Phase 4 makes the log publicly queryable.

## Comparison to Certificate Transparency

| Property | CT (for SSL) | OTS Transparency Log |
|---|---|---|
| Data logged | SSL certificates | Trust attestation bundles |
| Hash structure | Global Merkle tree with signed tree heads | Per-domain hash chains (v1); global Merkle tree (future) |
| Completeness proof | Yes (signed tree heads prove the log is append-only and complete) | No (v1 does not prove completeness, only consistency) |
| Public monitors | Multiple independent monitors cross-check logs | Single log operated by OTS (v1); third-party mirrors (future) |
| Latency | Certificates must be logged before issuance (pre-certificate) | Attestations are logged at issuance time (synchronous write) |
| Revocation | CRLs and OCSP | Hash chain breakage is visible but no formal revocation protocol (v1) |

The OTS transparency log is intentionally simpler than CT for v1. CT's global Merkle tree and multi-operator log ecosystem took years and significant infrastructure to build. OTS's per-domain hash chain provides the most important property (tamper evidence) at a fraction of the complexity, and upgrades to a Merkle tree when the log scale justifies it.
