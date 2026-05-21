# Sentinel — Architecture

This document explains the design decisions behind Sentinel, the tradeoffs taken, and the alternatives that were considered and rejected. It is the reference for anyone modifying or extending the system.

## 1. System overview

Sentinel is a stateless HTTP service (the *gateway*) that brokers all LLM traffic for an enterprise. It has three external dependencies — Postgres (audit log + config), Redis (rate limiting, circuit breaker state), and the LLM providers themselves — and exposes a single primary route (`POST /v1/messages`) plus operational endpoints (`/health`, `/metrics`).

The gateway is designed to scale horizontally. Every instance is interchangeable; shared state lives in Postgres and Redis. A load balancer in front (typically the cloud provider's) handles connection termination, TLS, and round-robin distribution.

## 2. Request lifecycle

Every request follows the same seven-step pipeline. The ordering is deliberate — cheap checks fail first to minimise wasted work.

```
client request
     │
     ▼
┌─────────────────────────┐
│ 1. Middleware           │   Generate or propagate X-Request-ID,
│    (request context)    │   bind it to structlog context vars
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 2. Authentication       │   Look up API key by prefix, verify hash
│    (Postgres lookup)    │   in constant time. ~1ms.
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 3. Rate limiting        │   Sliding-window check in Redis via Lua.
│    (Redis Lua script)   │   ~0.5ms. Fails fast on quota exhaustion.
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 4. Policy evaluation    │   Pure-function over tenant policy +
│    (in-process)         │   request. Sub-millisecond.
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 5. Security scans       │   Injection scan (regex noisy-OR),
│    (in-process)         │   PII redaction (regex + Luhn).
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 6. Provider routing     │   Circuit breaker check → provider call
│    (HTTP to provider)   │   with retry/backoff → failover on circuit
│                         │   open. Dominates total latency.
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 7. Audit log write      │   One row appended per request, regardless
│    (Postgres insert)    │   of outcome. ~2ms.
└──────────┬──────────────┘
           ▼
       response
```

**Invariant:** every request that enters the route handler produces exactly one audit log row before returning. Errors take alternate code paths that still write the audit row before raising. This is what makes the audit log trustworthy for governance.

## 3. Provider abstraction

The `LLMProvider` protocol (`src/sentinel/providers/base.py`) defines the surface every backend implements: `complete(request) -> response` and `health()`. Each provider adapter (`anthropic_provider.py`, `openai_provider.py`) is responsible for translating the normalized `CompletionRequest` into the provider's native API shape, mapping errors into Sentinel's exception hierarchy, and computing cost from the returned token counts.

**Why one normalized shape:** allows model-agnostic policy evaluation, audit logging, and metric labels. The cost of translation per request is negligible (microseconds of Pydantic serialization).

**Why not just proxy raw bytes:** loses the ability to enforce policies on request content. Sentinel is intentionally *not* a transparent proxy — it understands what's flowing through it.

## 4. Reliability: circuit breaker + retry + failover

Three layers cooperate to handle upstream failures:

| Layer | Scope | Implementation |
|---|---|---|
| Retry | Single provider call | `tenacity` exponential backoff, only for `ProviderTimeoutError` (not 4xx) |
| Circuit breaker | Per provider, across all gateway instances | Redis-backed state machine (closed → open → half-open) |
| Failover | Across providers | `ProviderRouter` tries alternate provider on circuit open or persistent failure |

**Why Redis for circuit state:** in a multi-instance deployment, a provider that's down for one instance is down for all of them. Per-instance state would mean N instances each independently discovering the failure, hammering the upstream and wasting their failure budgets. Shared state in Redis lets the first failure inform all replicas.

**Why retries are bounded:** unbounded retries are an anti-pattern. They turn intermittent failures into outages by stacking latency, and they hide upstream problems from observability. Sentinel retries at most twice with backoff capped at 4 seconds; anything beyond fails fast.

## 5. Governance: policies + rate limits

Tenant policies live in Postgres as structured columns (`max_tokens_per_request`, `allowed_models`, `denied_topics`, ...) plus a JSONB blob (`extra`) for forward-compatible extensions.

**Policy evaluation is pure.** It's a function from `(Policy, CompletionRequest) → PolicyDecision`. No I/O, no globals. This is what makes it trivially unit-testable (see `tests/unit/test_policy_engine.py`).

**Rate limiting is separate** because it requires counter state. The implementation is a sliding-window approximation (Cloudflare's algorithm) using an atomic Lua script in Redis:

```
estimated_count = floor(previous_window * weight) + current_window
```

where `weight` is the fraction of the previous window still inside the current rolling minute. This is cheaper than a true sliding window (no sorted set, no cleanup), more accurate than a fixed window (no boundary burst), and atomic by virtue of being a single Lua invocation.

## 6. Security

### 6.1 Authentication

API keys are `sk_live_<24-byte-base32>`. The first 16 characters (the prefix) are indexed in Postgres for O(1) lookup; the full key is verified by peppered SHA-256 in constant time.

**Why SHA-256 instead of bcrypt for API keys:** API keys are high-entropy random tokens, not user-chosen passwords. The benefit of slow hashing (defense against offline brute force) doesn't apply — and we don't want to add milliseconds of CPU to every hot-path request. For user passwords (not implemented in Sentinel) we'd use bcrypt or argon2.

### 6.2 Prompt injection defense

A curated set of regex patterns, each carrying a risk score in [0, 1]. Scores combine via a noisy-OR:

```
combined = 1 - product(1 - score_i for matching patterns)
```

This keeps the result in [0, 1], lets multiple weak signals stack into a strong one, and avoids the failure mode of simple summation (where stacked weak signals can exceed 1.0 and lose calibration).

**The patterns are not the security boundary.** They are the *first* layer. Production deployments should add:
- Output-side scanning for leaked content
- Separation of trusted vs. untrusted context in the prompt itself
- Human review for sensitive actions (tool calls, destructive operations)

### 6.3 PII redaction

Regex-based detection of high-signal patterns (email, phone, SSN, AWS keys, credit cards). Credit cards are validated against the Luhn checksum before redaction — this is the difference between blocking 4532015112830366 (real Visa test card) and not blocking 1234567812345678 (order number).

PII redaction is *destructive*: the detected span is replaced with `<REDACTED:TYPE>` before the request leaves the gateway. The audit log records *which types* were redacted, never the original values.

**Why regex instead of a NER model:** dependency-free, predictable latency, no model-loading overhead. For higher recall a downstream system would add Microsoft Presidio or a small fine-tuned model; the security module is structured to make that swap a one-file change.

## 7. Audit log

Every request produces exactly one row in `audit_logs`. The table is append-only — `updated_at` is intentionally absent, so the schema enforces immutability.

Indexes:
- `(tenant_id, created_at)` — the dashboard's main query pattern
- `request_id` — for incident-response lookups

**Truncation policy:** input and output previews are truncated to 500 characters. Storing full content has data-residency implications (a request from an EU tenant cannot be replicated to a US database without legal review); 500 chars is enough for compliance review without crossing that line.

## 8. Observability

### Metrics

Nine Prometheus metrics in `observability/metrics.py`, grouped by purpose:

- **Request-level:** `requests_total`, `request_duration_seconds`
- **Cost:** `tokens_processed_total`, `cost_usd_total`
- **Reliability:** `provider_failures_total`, `circuit_state`
- **Policy:** `policy_decisions_total`, `rate_limit_hits_total`
- **Security:** `injection_blocks_total`, `pii_redactions_total`

Labels are chosen to be low-cardinality. `tenant` appears only on cost metrics because that's the one place we want per-tenant breakdowns. Putting it on `requests_total` would explode cardinality in a multi-tenant deployment.

### Logging

Structured JSON via `structlog`. The middleware binds `request_id`, `method`, `path` to the contextvar at the start of each request, so every subsequent log line in that request lifetime carries them without manual threading. JSON in production; pretty console in development.

### Tracing

OpenTelemetry-ready via `OTEL_ENABLED`. The gateway is instrumented with `opentelemetry-instrumentation-fastapi`; setting `OTEL_ENDPOINT` exports to any OTLP collector (Honeycomb, Tempo, Datadog, etc.).

## 9. Eval harness

The `evals/` directory contains labelled datasets and runners that gate every change to security-critical code. The injection scanner has a 15-sample golden dataset; the runner asserts precision ≥ 0.80 and recall ≥ 0.70 (currently the scanner hits 1.0 on both).

This is *not* a unit test. It's a property test for a heuristic. When someone changes the patterns, the eval will catch a regression that a unit test couldn't — because the unit tests check specific patterns work, not that the *overall behavior* still hits its precision/recall bar.

## 10. What's deliberately not built (yet)

- **Streaming responses.** The Anthropic and OpenAI APIs both support `text/event-stream`; Sentinel would pass these through unchanged. Not built in v1 because the audit log row needs the full token count, and capturing usage from a stream requires buffering, which complicates the simple "one request, one row" invariant.
- **Tool-use / function-calling.** Same as above — the policy engine would need to enforce per-tool allowlists, and the audit log needs to capture tool calls. Planned but out of scope for the initial system.
- **Persistent rate-limit state.** Redis is ephemeral by default. For prod, Redis should be configured with AOF or Sentinel/Cluster replication. The gateway tolerates Redis being briefly unavailable (rate limiter returns "allow" if Redis errors), which is the right safety failure mode.

## 11. Testing strategy

| Layer | What's tested | Where |
|---|---|---|
| Unit | Pure functions, isolated logic | `tests/unit/` — 34 tests, runs in <1s, no external deps |
| Integration | End-to-end through real Postgres + Redis | `tests/integration/` — runs against docker-compose |
| Eval | Heuristic quality (precision/recall) | `evals/run_*_eval.py` — gates CI |

The split is intentional. Unit tests are run on every save. Integration tests are run in CI. Evals are run on every change to security code.

## 12. Deployment

Sentinel ships as a single multi-stage Docker image (~150MB). The image is non-root, has a healthcheck, and exits cleanly on SIGTERM. It runs anywhere that runs containers — Kubernetes, ECS, Fly.io, Railway, a bare VM. The `docker-compose.yml` is for local dev; production deployment is one `docker run` plus environment variables.
