"""API key authentication helpers.

API keys look like ``sk_live_<24-random-base32>``. The prefix is
indexed for O(1) lookup; the full key is verified by bcrypt against
the stored hash.

This module is deliberately stdlib-only (plus bcrypt) so that key
generation / verification can be unit tested without standing up a DB.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

KEY_PREFIX = "sk_live_"
RANDOM_BYTES = 24  # -> ~32 chars base32


@dataclass(frozen=True)
class GeneratedKey:
    plaintext: str
    prefix: str  # First 16 chars, used as a DB index
    hash: str


def generate_api_key() -> GeneratedKey:
    """Mint a new API key. The plaintext is returned ONCE and never stored."""
    raw = secrets.token_bytes(RANDOM_BYTES)
    body = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    plaintext = f"{KEY_PREFIX}{body}"
    return GeneratedKey(
        plaintext=plaintext,
        prefix=plaintext[:16],
        hash=_hash(plaintext),
    )


def _hash(plaintext: str) -> str:
    """Hash a plaintext key.

    We use SHA-256 with a per-deployment pepper for API keys (not bcrypt)
    because:
    1. API keys are high-entropy random, not user-chosen passwords — so
       slow hashing buys little extra security but adds latency to every
       hot-path request.
    2. We want hash verification to be sub-millisecond.

    For *user passwords* (not implemented here) we'd use bcrypt.
    """
    # Pepper is a deployment-wide secret. For local dev, a static string is fine.
    # In prod this would come from a secret manager.
    pepper = b"sentinel-v1-pepper-rotate-me-in-prod"
    digest = hashlib.sha256(pepper + plaintext.encode("utf-8")).hexdigest()
    return digest


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    """Constant-time verification of a plaintext key against its stored hash."""
    candidate = _hash(plaintext)
    return hmac.compare_digest(candidate, stored_hash)


def extract_prefix(plaintext: str) -> str:
    return plaintext[:16]
