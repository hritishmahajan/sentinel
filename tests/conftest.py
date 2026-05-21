"""Pytest configuration shared across the test suite."""

from __future__ import annotations

import os

# Ensure tests never accidentally hit a real provider.
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")
