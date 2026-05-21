"""Unit tests for PII redaction."""

from __future__ import annotations

import pytest

from sentinel.security.pii import redact


class TestPIIRedaction:
    def test_redacts_email(self) -> None:
        result = redact("Contact me at jane.doe@example.com please")
        assert "<REDACTED:EMAIL>" in result.text
        assert "jane.doe@example.com" not in result.text
        assert "EMAIL" in result.types_redacted

    def test_redacts_ssn(self) -> None:
        result = redact("SSN: 123-45-6789")
        assert "<REDACTED:SSN>" in result.text
        assert "SSN" in result.types_redacted

    def test_redacts_aws_key(self) -> None:
        result = redact("Use AKIAIOSFODNN7EXAMPLE for access")
        assert "<REDACTED:AWS_KEY>" in result.text
        assert "AWS_KEY" in result.types_redacted

    def test_redacts_valid_credit_card(self) -> None:
        # Visa test card that passes Luhn
        result = redact("My card is 4532015112830366")
        assert "<REDACTED:CREDIT_CARD>" in result.text

    def test_skips_non_luhn_number(self) -> None:
        # 16-digit string that fails Luhn — should NOT be redacted
        result = redact("Order #1234567812345678 ships tomorrow")
        assert "<REDACTED:CREDIT_CARD>" not in result.text

    def test_clean_text_unchanged(self) -> None:
        original = "What is the capital of France?"
        result = redact(original)
        assert result.text == original
        assert result.types_redacted == []

    @pytest.mark.parametrize(
        "phone",
        [
            "555-123-4567",
            "(555) 123-4567",
            "+1 555 123 4567",
        ],
    )
    def test_redacts_phone_variants(self, phone: str) -> None:
        result = redact(f"Call me at {phone}")
        assert "<REDACTED:PHONE>" in result.text
