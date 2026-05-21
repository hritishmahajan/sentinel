"""Prometheus metrics.

These are the gateway's golden signals. Names follow Prometheus
conventions (snake_case, ``_total`` for counters, ``_seconds`` for
durations, ``_usd`` as a unit suffix where helpful).

Adding a new metric: define it at module level so it's registered once
per process. Don't create metrics inside request handlers.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---- Request-level metrics ----

requests_total = Counter(
    "sentinel_requests_total",
    "Total gateway requests, labelled by outcome.",
    labelnames=["provider", "model", "status"],
)

request_duration_seconds = Histogram(
    "sentinel_request_duration_seconds",
    "Gateway request latency (end-to-end, including upstream).",
    labelnames=["provider", "model"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

# ---- Token + cost metrics ----

tokens_processed_total = Counter(
    "sentinel_tokens_processed_total",
    "Tokens processed, labelled by direction.",
    labelnames=["provider", "model", "direction"],  # direction = input | output
)

cost_usd_total = Counter(
    "sentinel_cost_usd_total",
    "Cumulative provider spend in USD.",
    labelnames=["provider", "model", "tenant"],
)

# ---- Reliability metrics ----

provider_failures_total = Counter(
    "sentinel_provider_failures_total",
    "Upstream provider failures.",
    labelnames=["provider", "error_code"],
)

circuit_state = Gauge(
    "sentinel_circuit_state",
    "Circuit breaker state per provider. 0=closed, 1=half_open, 2=open.",
    labelnames=["provider"],
)

# ---- Policy metrics ----

policy_decisions_total = Counter(
    "sentinel_policy_decisions_total",
    "Policy engine decisions.",
    labelnames=["decision", "reason"],  # decision = allow | deny
)

rate_limit_hits_total = Counter(
    "sentinel_rate_limit_hits_total",
    "Requests rejected by the rate limiter.",
    labelnames=["tenant"],
)

# ---- Security metrics ----

injection_blocks_total = Counter(
    "sentinel_injection_blocks_total",
    "Requests blocked by prompt-injection scanner.",
    labelnames=["pattern"],
)

pii_redactions_total = Counter(
    "sentinel_pii_redactions_total",
    "Count of PII redactions performed.",
    labelnames=["type"],
)
