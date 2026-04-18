# OpenTrustSeal Key Rotation Schedule

Version 1.0 | April 2026

## Current state

OTS uses a single Ed25519 keypair for signing all trust attestation bundles. The public key is published in the DID document at `https://opentrustseal.com/.well-known/did.json` (and `https://opentrusttoken.com/.well-known/did.json` for backward compatibility). The private key is stored at `/opt/opentrustseal/keys/signing.key` on the API box.

There is currently **no rotation schedule**. The same key has been in use since the first deployment (April 2026). This document specifies the rotation plan.

## Rotation cadence

| Event | Action |
|---|---|
| **Scheduled rotation** | Every 12 months. Next: April 2027. |
| **Suspected compromise** | Immediate emergency rotation (see below). |
| **Infrastructure migration** | Generate new key on the new infrastructure. Old key remains valid for the overlap period. |

## Rotation procedure (scheduled)

### Phase 1: Overlap period (2 weeks before rotation)

1. Generate a new Ed25519 keypair on the API box.
2. Add the new key to the DID document as a second verification method (`#signing-key-2`).
3. Continue signing with the OLD key for 2 weeks. This gives all consumers time to fetch the updated DID document and learn about the new key.
4. Announce the rotation via the `/stats` endpoint (new field: `keyRotation.pendingKeyId`).

### Phase 2: Cutover

5. Switch signing to the NEW key.
6. The DID document now lists both keys, with `#signing-key-2` as the active assertion method.
7. All new attestation bundles carry the new key's signature.

### Phase 3: Deprecation (4 weeks after cutover)

8. Remove the old key (`#signing-key-1`) from the DID document's `assertionMethod` array (but keep it in `verificationMethod` for historical verification).
9. Old signatures remain valid: anyone who cached the old public key can still verify old bundles. The DID document just stops asserting that the old key is the current signing key.

### Phase 4: Cleanup (12 months after cutover)

10. Remove the old key from the DID document entirely.
11. Old signatures are still mathematically valid (Ed25519 doesn't expire), but verifiers who fetch the DID document fresh will not find the old key. Historical verification requires the verifier to have cached the old DID document or to query an archive.

## Emergency rotation (suspected compromise)

If the signing key is suspected to be compromised:

1. **Immediately** generate a new keypair and replace the old one on the API box.
2. **Immediately** update the DID document to contain only the new key.
3. **Immediately** restart the API service.
4. Issue a public notice via the status page and API (`/stats` field: `keyRotation.emergencyRotation`).
5. All attestations signed with the compromised key are now unverifiable via the DID document. Consumers who cached the old key can still verify old signatures, but they should treat them as suspect.
6. Run a full rescore to re-sign all cached results with the new key.

**No overlap period for emergency rotations.** The old key is revoked immediately. This breaks verification for any consumer who has not yet fetched the new DID document, which is an acceptable trade against continued forgery risk.

## DID document format during overlap

```json
{
  "@context": "https://www.w3.org/ns/did/v1",
  "id": "did:web:opentrustseal.com",
  "verificationMethod": [
    {
      "id": "did:web:opentrustseal.com#signing-key-1",
      "type": "Ed25519VerificationKey2020",
      "controller": "did:web:opentrustseal.com",
      "publicKeyMultibase": "<old-key-multibase>"
    },
    {
      "id": "did:web:opentrustseal.com#signing-key-2",
      "type": "Ed25519VerificationKey2020",
      "controller": "did:web:opentrustseal.com",
      "publicKeyMultibase": "<new-key-multibase>"
    }
  ],
  "assertionMethod": [
    "did:web:opentrustseal.com#signing-key-1",
    "did:web:opentrustseal.com#signing-key-2"
  ]
}
```

During the overlap period, both keys are listed in `assertionMethod`. After cutover, only the new key remains in `assertionMethod`. After cleanup, only the new key remains in `verificationMethod`.

## Attestation bundle key reference

Each signed attestation bundle includes:
- `issuer`: `"did:web:opentrustseal.com"` (the DID)
- `signature`: the Ed25519 signature over the canonical signable payload

Currently, the bundle does not include a key ID (`kid`) field. **This is a gap.** When multiple keys are active during an overlap period, verifiers need to know which key signed the bundle. The plan:

- Add `signatureKeyId` field to the attestation response: `"did:web:opentrustseal.com#signing-key-1"`
- Verifiers match this against the DID document's `verificationMethod` array
- Ship this field BEFORE the first rotation so all consumers are prepared

## Implementation status

| Item | Status |
|---|---|
| Single key in use | Current |
| DID document published | Current |
| Rotation procedure documented | This document |
| `signatureKeyId` field in responses | Planned (before first rotation) |
| Automated rotation script | Planned |
| Emergency rotation runbook | This document |
| Key escrow / backup | Not implemented (key exists only on the API box) |
