"""PII redaction.

Rules-based PII detection for inputs. Regex covers high-signal patterns
(email, phone, SSN, credit card via Luhn check, AWS keys). For higher
recall, a downstream system would add a NER model (e.g. presidio); we
keep the v1 dependency-free for predictable latency and to avoid loading
a model on every request.

The redaction is destructive: detected spans are replaced with a typed
placeholder ``<REDACTED:EMAIL>`` etc. The audit log records what types
were redacted, not the original values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
# Generic 13-19 digit numbers — candidates for credit cards, validated by Luhn.
CC_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _luhn_valid(digits: str) -> bool:
    """Return True if the digit string passes the Luhn checksum."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@dataclass
class RedactionResult:
    text: str
    types_redacted: list[str]


def redact(text: str) -> RedactionResult:
    """Redact PII from ``text``. Returns the cleaned text and the types seen.

    Order matters: redact specific patterns before generic numeric ones,
    so phone numbers aren't mistaken for credit cards.
    """
    types_seen: list[str] = []

    def _sub(name: str, pattern: re.Pattern[str], s: str) -> str:
        def _replace(_match: re.Match[str]) -> str:
            if name not in types_seen:
                types_seen.append(name)
            return f"<REDACTED:{name}>"

        return pattern.sub(_replace, s)

    text = _sub("EMAIL", EMAIL_RE, text)
    text = _sub("SSN", SSN_RE, text)
    text = _sub("AWS_KEY", AWS_KEY_RE, text)
    text = _sub("PHONE", PHONE_RE, text)

    # Credit cards: validate Luhn before redacting to avoid false positives
    # on things like order numbers.
    def _cc_replace(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            if "CREDIT_CARD" not in types_seen:
                types_seen.append("CREDIT_CARD")
            return "<REDACTED:CREDIT_CARD>"
        return match.group()

    text = CC_CANDIDATE_RE.sub(_cc_replace, text)

    return RedactionResult(text=text, types_redacted=types_seen)
