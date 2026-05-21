"""Prompt injection defense (heuristic).

A real production system layers multiple defenses: input scanning,
output scanning, separation of trusted/untrusted content, and human
review for sensitive actions. This module implements the *input
scanning* layer using a curated set of high-precision patterns.

We deliberately keep this conservative — over-blocking is worse than
under-blocking for a developer-facing gateway, so we only flag patterns
that are very rare in legitimate usage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Each pattern carries a name (for telemetry) and a risk score in [0, 1].
# Tuned by hand on the golden dataset at ``evals/datasets/injection_v1.jsonl``.
# Every change here must keep the eval gate green.
_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    (
        "ignore_instructions",
        re.compile(
            r"(ignore|disregard|forget)\s+(all\s+|the\s+)?(previous|prior|above|earlier)"
            r"(\s+(instructions|prompts|messages|context))?",
            re.I,
        ),
        0.9,
    ),
    (
        "reveal_system_prompt",
        re.compile(
            r"(reveal|show|print|dump|leak|display|tell\s+me|give\s+me)\s+"
            r"(me\s+)?(your\s+|the\s+)?"
            r"(system\s+prompt|hidden\s+(prompt|instructions)|(previous|prior)\s+instructions|"
            r"original\s+instructions|initial\s+prompt)",
            re.I,
        ),
        0.9,
    ),
    (
        "ask_about_instructions",
        # Catches "what were the previous instructions you were given" style probes.
        re.compile(
            r"what\s+(were|are|was)\s+(your|the)\s+"
            r"(previous|prior|original|initial|earlier)?\s*"
            r"(instructions|prompt|system\s+prompt|guidelines)",
            re.I,
        ),
        0.85,
    ),
    (
        "role_override",
        re.compile(
            r"you\s+are\s+now\s+(a\s+)?(different|new|unrestricted)\s+(ai|assistant|model|bot)",
            re.I,
        ),
        0.7,
    ),
    (
        "named_jailbreak_persona",
        # DAN, STAN, AIM, and similar named jailbreak personas.
        re.compile(r"\byou\s+are\s+(now\s+)?(dan|stan|aim|dude|kevin|jailbroken)\b", re.I),
        0.9,
    ),
    (
        "developer_mode",
        re.compile(r"\b(dan|developer|jailbreak|god|admin|debug)\s+mode\b", re.I),
        0.85,
    ),
    (
        "no_restrictions",
        re.compile(
            r"\b(without|no|bypass|ignore|remove)\s+"
            r"(any\s+)?(restrictions|safety|filters|guardrails|rules|guidelines|limits)\b",
            re.I,
        ),
        0.7,
    ),
    (
        "encoded_instructions",
        # Long base64-looking blocks. Heuristic, occasionally false-positives on legit data.
        re.compile(r"\b[A-Za-z0-9+/]{120,}={0,2}\b"),
        0.4,
    ),
    (
        "tool_exfil",
        re.compile(
            r"(send|email|post|fetch|curl|forward|leak|exfiltrate)\s+.+?(http|api|webhook|to\s+\S+\.\S+)",
            re.I,
        ),
        0.6,
    ),
]


@dataclass
class InjectionResult:
    score: float
    matches: list[str]
    blocked: bool


def scan(text: str, threshold: float = 0.75) -> InjectionResult:
    """Score text for likely injection patterns. ``blocked=True`` if over threshold.

    Scores from matching patterns combine via a simple noisy-or, which
    keeps the result in [0, 1] and lets multiple weak signals stack into
    a strong one.
    """
    matches: list[str] = []
    miss_product = 1.0

    for name, pattern, score in _PATTERNS:
        if pattern.search(text):
            matches.append(name)
            miss_product *= 1 - score

    combined = 1 - miss_product
    return InjectionResult(score=combined, matches=matches, blocked=combined >= threshold)
