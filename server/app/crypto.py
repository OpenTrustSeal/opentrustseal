"""At-rest encryption for sensitive registration fields.

Uses NaCl SecretBox (XSalsa20 + Poly1305 MAC). One symmetric key per
deployment, stored at ${OTS_KEY_DIR}/registration_kek.bin with 600 perms.
Ciphertexts are stored as base64(nonce||ct) with a 1-byte version tag
so we can rotate the KEK without breaking existing rows.

Encrypted shape in the DB:
    "enc:v1:" + base64(nonce || ciphertext)

The "enc:v1:" prefix is the tell. Any value without that prefix is
treated as legacy plaintext (returned as-is), so the migration can run
async and the code never crashes on a half-migrated table.

Fields that get encrypted (declared in registrations table as sensitive):
    contact_name, contact_email, phone, address, ein_tax_id

Fields that stay in the clear:
    business_name, country, state_province, business_type,
    website_category, year_established, social_twitter, social_linkedin,
    verification_code (transient), all *_verified booleans, timestamps

Rotation: generate a new key, re-encrypt every row with decrypt() then
encrypt_field() under the new key, atomic swap. Not implemented yet
because we have 1 row; adding when we hit the first real volume.
"""

import base64
import os
from pathlib import Path
from typing import Optional

import nacl.secret
import nacl.utils
import nacl.exceptions


_KEY_DIR = Path(os.environ.get("OTS_KEY_DIR", "./keys"))
_KEK_FILE = _KEY_DIR / "registration_kek.bin"
_PREFIX = "enc:v1:"


def _ensure_kek() -> bytes:
    """Load the registration KEK, generating it on first call if missing.

    Returns 32 raw bytes. File is mode 600; caller must not log them.
    """
    if _KEK_FILE.exists():
        return _KEK_FILE.read_bytes()
    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    key = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
    _KEK_FILE.write_bytes(key)
    os.chmod(_KEK_FILE, 0o600)
    return key


def _box() -> nacl.secret.SecretBox:
    return nacl.secret.SecretBox(_ensure_kek())


def encrypt_field(plaintext: Optional[str]) -> Optional[str]:
    """Return an encrypted representation suitable for SQLite TEXT storage.

    None passes through unchanged so callers can pipe through optional
    fields without a guard. Empty string is treated as None (registrations
    table uses NULL for missing fields; no point encrypting empty string).
    """
    if plaintext is None or plaintext == "":
        return plaintext
    if plaintext.startswith(_PREFIX):
        # Already encrypted; idempotent re-save.
        return plaintext
    nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
    ct = _box().encrypt(plaintext.encode("utf-8"), nonce).ciphertext
    return _PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt_field(encoded: Optional[str]) -> Optional[str]:
    """Decrypt a field encrypted with encrypt_field.

    Values without the "enc:v1:" prefix are treated as legacy plaintext
    and returned as-is. This keeps the code safe during the migration
    window when the DB has mixed plaintext + encrypted rows.

    Corrupted or unauthenticated ciphertext raises
    nacl.exceptions.CryptoError (same as a tampered DB would).
    """
    if encoded is None:
        return None
    if not encoded.startswith(_PREFIX):
        # Legacy plaintext row; return as-is.
        return encoded
    blob = base64.b64decode(encoded[len(_PREFIX):])
    nonce, ct = blob[:nacl.secret.SecretBox.NONCE_SIZE], blob[nacl.secret.SecretBox.NONCE_SIZE:]
    pt = _box().decrypt(ct, nonce)
    return pt.decode("utf-8")


# Fields in the registrations table that must round-trip through encrypt/decrypt.
ENCRYPTED_REGISTRATION_FIELDS = (
    "contact_name",
    "contact_email",
    "phone",
    "address",
    "ein_tax_id",
)
