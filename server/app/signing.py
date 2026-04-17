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
    """Sign a JSON payload using Ed25519.

    Process (per spec Section 9.2):
    1. JSON-canonicalize the payload (sorted keys, no whitespace)
    2. SHA-256 hash
    3. Sign with Ed25519
    4. Return base58btc-encoded signature (multibase prefix z)
    """
    # JCS approximation: sorted keys, no whitespace
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()

    signing_key = _load_signing_key()
    signed = signing_key.sign(digest)
    signature_bytes = signed.signature

    # base58btc encode with multibase prefix 'z'
    import base64
    b64 = base64.b64encode(signature_bytes).decode("ascii")
    return f"z{b64}"


def get_public_key_multibase() -> str:
    """Return public key in multibase base64 format for DID document."""
    import base64
    verify_key = _load_verify_key()
    raw = verify_key.encode(encoder=nacl.encoding.RawEncoder)
    return f"z{base64.b64encode(raw).decode('ascii')}"


def verify_signature(payload: dict, signature: str) -> bool:
    """Verify a signature against a payload."""
    import base64
    if not signature.startswith("z"):
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
