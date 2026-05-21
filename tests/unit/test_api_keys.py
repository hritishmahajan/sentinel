"""Unit tests for API key generation + verification."""

from __future__ import annotations

from sentinel.security.api_keys import (
    KEY_PREFIX,
    extract_prefix,
    generate_api_key,
    verify_api_key,
)


class TestApiKeys:
    def test_generated_key_has_correct_prefix(self) -> None:
        key = generate_api_key()
        assert key.plaintext.startswith(KEY_PREFIX)
        assert key.prefix == key.plaintext[:16]

    def test_verify_matches_own_hash(self) -> None:
        key = generate_api_key()
        assert verify_api_key(key.plaintext, key.hash) is True

    def test_verify_rejects_wrong_plaintext(self) -> None:
        key = generate_api_key()
        wrong = generate_api_key()
        assert verify_api_key(wrong.plaintext, key.hash) is False

    def test_generated_keys_are_unique(self) -> None:
        keys = {generate_api_key().plaintext for _ in range(50)}
        assert len(keys) == 50

    def test_extract_prefix(self) -> None:
        assert extract_prefix("sk_live_abc123xyz") == "sk_live_abc123xy"
