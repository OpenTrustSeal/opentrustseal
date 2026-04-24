"""Ed25519 signing service for trust tokens.

v1: key stored as file on disk. Future: HSM-backed.
"""

import json
import hashlib
import os
from pathlib import Path

import nacl.signing
import nacl.encoding


KEY_DIR = Path(os.environ.get("OTS_KEY_DIR", "./keys"))
PRIVATE_KEY_FILE = KEY_DIR / "signing.key"
PUBLIC_KEY_FILE = KEY_DIR / "signing.pub"


def ensure_keys() -> None:
    """Generate Ed25519 keypair if none exists."""
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY_FILE.exists():
        return

    signing_key = nacl.signing.SigningKey.generate()
    PRIVATE_KEY_FILE.write_bytes(signing_key.encode())
    PUBLIC_KEY_FILE.write_bytes(
        signing_key.verify_key.encode(encoder=nacl.encoding.RawEncoder)
    )
    # Restrict permissions on private key
    os.chmod(PRIVATE_KEY_FILE, 0o600)


def _load_signing_key() -> nacl.signing.SigningKey:
    raw = PRIVATE_KEY_FILE.read_bytes()
    return nacl.signing.SigningKey(raw)


def _load_verify_key() -> nacl.signing.VerifyKey:
    raw = PUBLIC_KEY_FILE.read_bytes()
    return nacl.signing.VerifyKey(raw)


def sign_payload(payload: dict) -> str:
    """Sign a JSON payload using Ed25519. Returns a multibase-encoded string.

    Process:
    1. JSON-canonicalize the payload (sorted keys, no whitespace)
    2. SHA-256 hash
    3. Ed25519 sign the digest
    4. Return the signature as multibase base64pad (prefix 'M')

    Multibase is a W3C/IPFS convention that self-describes the encoding of
    a binary value with a single-character prefix. 'M' means "base64pad"
    (padded standard base64). 'z' means "base58btc"; do NOT use 'z' for
    base64 data because external multibase decoders will try base58btc and
    get garbage. (Earlier versions of this code had that bug.)

    For backward compat, verify_signature below still accepts the legacy
    'z'+base64pad shape so any cached response issued before this fix
    continues to verify locally. External VC/DID consumers should only
    see 'M' going forward.
    """
    import base64
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()

    signing_key = _load_signing_key()
    signed = signing_key.sign(digest)
    signature_bytes = signed.signature
    b64 = base64.b64encode(signature_bytes).decode("ascii")
    return f"M{b64}"


def get_public_key_multibase() -> str:
    """Return the Ed25519 public key as multibase base64pad ('M' prefix)."""
    import base64
    verify_key = _load_verify_key()
    raw = verify_key.encode(encoder=nacl.encoding.RawEncoder)
    return f"M{base64.b64encode(raw).decode('ascii')}"


def verify_signature(payload: dict, signature: str) -> bool:
    """Verify a signature against a payload.

    Accepts both:
    - 'M'+base64pad (current correct multibase encoding)
    - 'z'+base64pad (legacy, buggy pre-2026-04-24 encoding)

    Both prefixes accept the same base64pad payload. The 'z' path exists
    only to keep legacy cached responses verifying until the next rescore.
    """
    import base64
    if not signature or signature[0] not in ("M", "z"):
        return False
    sig_bytes = base64.b64decode(signature[1:])

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()

    verify_key = _load_verify_key()
    try:
        verify_key.verify(digest, sig_bytes)
        return True
    except nacl.exceptions.BadSignatureError:
        return False
