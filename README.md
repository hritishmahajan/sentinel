# Sentinel

> A governed LLM gateway for enterprises — the intelligence-layer infrastructure between your applications and the LLM providers behind them.

[![CI](https://github.com/USER/sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/USER/sentinel/actions/workflows/ci.yml)
[![CD](https://github.com/USER/sentinel/actions/workflows/cd.yml/badge.svg)](https://github.com/USER/sentinel/actions/workflows/cd.yml)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Deploy on Fly.io](https://img.shields.io/badge/deployed%20on-fly.io-purple)](https://sentinel-gateway.fly.dev/health)

**Live demo:** https://sentinel-gateway.fly.dev/console — Admin dashboard (anonymous mode)
**API health:** https://sentinel-gateway.fly.dev/health

Sentinel sits between your application code and LLM providers (Anthropic, OpenAI) and provides the governance layer that production AI systems need but most teams build twice: multi-provider routing with failover, per-tenant policies, audit logging, PII redaction, prompt-injection defense, rate limiting, and a full observability surface.

It's the kind of system that's invisible when it works and indispensable when something goes wrong at 3 AM.

---

## Why this exists

The dominant pattern in production AI today is **direct provider calls scattered across services**. Each service implements its own retries, rate limits, cost tracking, and safety checks — usually badly, almost always inconsistently. When something fails — an upstream provider degrades, a tenant blows through their cost budget, a prompt-injection attempt slips through, a regulator asks "what did this user ask the AI last quarter?" — there is nowhere to look and no consistent answer.

Sentinel collapses that surface area into one well-instrumented hop. Every request through the gateway is:

- **Authenticated** by tenant via hashed API keys
- **Rate-limited** with a sliding-window algorithm in Redis
- **Policy-checked** against per-tenant model allowlists, denied topics, token ceilings, and cost budgets
- **Security-scanned** for prompt injection and stripped of PII before it leaves the network
- **Routed** through a circuit breaker to the right provider, with automatic failover when a primary degrades
- **Audit-logged** in an append-only Postgres table with full request/response metadata
- **Observed** via Prometheus metrics, structured JSON logs, and OpenTelemetry traces

---

## Architecture at a glance

```
┌──────────────┐   ┌─────────────────────────────────────────────────┐   ┌──────────────┐
│              │   │                    Sentinel                     │   │              │
│ Application  │──▶│  ┌────┐ ┌──────┐ ┌──────┐ ┌────────┐ ┌───────┐  │──▶│  Anthropic   │
│ (SDK or HTTP)│   │  │auth│▶│limit │▶│policy│▶│security│▶│router │  │   │   OpenAI     │
│              │   │  └────┘ └──────┘ └──────┘ └────────┘ └───┬───┘  │   │   ...        │
└──────────────┘   │                                          │      │   └──────────────┘
                   │     ┌──────────┐         ┌───────────────┴───┐  │
                   │     │  audit   │◀────────│ circuit breaker   │  │
                   │     │ (postgres)│        │     (redis)       │  │
                   │     └──────────┘         └───────────────────┘  │
                   │                                                  │
                   │     ┌──────────┐    ┌─────────────┐              │
                   │     │  metrics │    │   tracing   │              │
                   │     │(prometheus)│  │  (otel)    │               │
                   │     └──────────┘    └─────────────┘              │
                   └─────────────────────────────────────────────────┘
```

For a deeper dive, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Quickstart

### 1. Run locally with Docker Compose

```bash
git clone https://github.com/USER/sentinel.git
cd sentinel
cp .env.example .env
# Edit .env and add at least one provider API key
docker compose up --build
```

The gateway will be available at `http://localhost:8000`. Prometheus is at `http://localhost:9090`.

### 2. Send your first request

```bash
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "What is the capital of France?"}]
  }'
```

### 3. Use the Python SDK

```python
from sentinel_sdk import Sentinel

client = Sentinel(api_key="sk_live_...", base_url="http://localhost:8000")

response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=500,
    messages=[{"role": "user", "content": "Hello"}],
)

print(response.content)
print(f"Cost: ${response.cost_usd:.6f}, Provider: {response.provider}")
```

---

## What's inside

### Production-grade reliability

- **Circuit breaker** — Redis-backed per-provider, with `CLOSED → OPEN → HALF_OPEN` state machine. Failures across gateway instances roll up to the same view, so a degraded provider doesn't get hammered by a single misbehaving replica.
- **Automatic failover** — when a provider's circuit opens, requests are routed to the alternate provider with best-effort model mapping (e.g., `claude-sonnet-4-5` ↔ `gpt-4o`).
- **Bounded retries** — `tenacity` with exponential backoff, but only on timeout errors. 4xx errors fail fast.
- **Append-only audit log** — every request produces exactly one row, regardless of outcome. Indexed for the dashboard's most common query pattern (tenant, time range).

### Governance & security

- **Per-tenant policies** — model allowlist, denied topics, max-tokens clamping, cost ceilings, requests-per-minute, redaction toggle. Stored as structured columns + a JSONB blob for forward-compatible extensions.
- **PII redaction** — regex-based detection of emails, phone numbers, SSNs, AWS keys, and Luhn-validated credit cards. Detected spans are replaced with typed placeholders like `<REDACTED:EMAIL>` before the request leaves your network.
- **Prompt-injection scanner** — curated pattern set with noisy-OR scoring, tuned for high precision on a labelled golden dataset. Decisions are logged with the matched pattern names.
- **API key auth** — `sk_live_*` keys, hashed with peppered SHA-256, constant-time comparison, prefix-indexed for O(1) lookup.

### Observability

- **9 Prometheus metrics** — request counts, latency histogram, tokens processed, USD spend, circuit state, policy decisions, rate-limit hits, injection blocks, PII redactions
- **Structured JSON logs** via `structlog` — every log line in a request lifetime carries the same `request_id` without manual threading
- **OpenTelemetry-ready** — optional OTLP export to any collector

### Eval harness

The `evals/` directory holds a golden dataset of labelled prompts and a runner that asserts precision and recall thresholds. **The injection scanner cannot regress without CI catching it.** Adding a new pattern means adding to the dataset first.

```bash
python evals/run_injection_eval.py --min-recall 0.70 --min-precision 0.80
```

---

## Repository layout

```
sentinel/
├── src/
│   ├── sentinel/              # The gateway service
│   │   ├── api/               # FastAPI app, routes, middleware, deps
│   │   ├── core/              # Config, logging, errors
│   │   ├── db/                # SQLAlchemy models, session
│   │   ├── providers/         # LLM provider adapters + router + circuit breaker
│   │   ├── policies/          # Policy engine + rate limiter
│   │   ├── security/          # PII redaction, injection scanner, API key handling
│   │   ├── audit/             # Append-only audit log writer
│   │   └── observability/     # Prometheus metric definitions
│   └── sentinel_sdk/          # Public Python client
├── tests/
│   ├── unit/                  # Fast, no external deps
│   ├── integration/           # Standing-up Postgres + Redis required
│   └── evals/                 # LLM-quality regression tests
├── evals/                     # Golden datasets + eval runners
├── alembic/                   # Database migrations
├── dashboard/                 # Next.js admin UI (audit logs, dashboards)
├── docs/                      # Architecture, runbooks, design notes
└── .github/workflows/         # CI: lint, typecheck, tests, eval gate, Docker
```

---

## Development

```bash
# Install with dev tooling
pip install -e ".[dev]"

# Run unit tests (no external deps)
pytest tests/unit

# Lint + typecheck
ruff check src tests
mypy src

# Run the eval gate locally
python evals/run_injection_eval.py
```

### Running migrations

```bash
alembic upgrade head        # Apply latest
alembic revision --autogenerate -m "your change"
```

---

## Operations

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for on-call procedures: how to diagnose an open circuit, drain traffic, roll back, and the post-incident template we use.

---

## Roadmap

- [ ] Streaming responses (`text/event-stream`)
- [ ] Tool-use / function-calling passthrough with policy enforcement on tool calls
- [ ] Multi-region replicas with consistent rate-limit state
- [ ] Bring-your-own classifier for the injection scanner (plugin point already exists)
- [ ] Cost-aware routing: pick the cheapest provider that satisfies the policy

---

## License

MIT. See [LICENSE](LICENSE).

---

*Sentinel is a personal project built to explore what the governance layer of enterprise AI looks like in practice. It is intentionally close to systems being built at companies like Anthropic, kAIgentic, and others in the "intelligence layer" space.*
