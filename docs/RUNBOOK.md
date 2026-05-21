# Sentinel — Runbook

Operational procedures for Sentinel. Read this before going on-call.

> If the gateway is on fire and you only have 30 seconds: check `/health`. If providers show `circuit_open`, the upstream is the problem, not us. Skip to §3.

## 1. Quick diagnostics

### Is the gateway up?

```
curl -sf http://gateway/health | jq
```

Expected response when healthy:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "providers": {
    "anthropic": "ok",
    "openai": "ok"
  }
}
```

### Is it serving requests?

```
# In Prometheus
rate(sentinel_requests_total[5m])
```

A flat-line at zero across all replicas while traffic is expected = something is wrong. Check load balancer health first; if the LB is forwarding, suspect a startup failure (see §4).

### What's the latency profile?

```
histogram_quantile(0.95, rate(sentinel_request_duration_seconds_bucket[5m]))
```

Expected p95 under nominal load: 1–3s (dominated by upstream LLM latency). Anything over 10s sustained = degraded upstream or a hung connection. See §3.

## 2. Common alerts

### `SentinelHighErrorRate` (5xx > 1% over 5min)

1. Check `sentinel_provider_failures_total` by `error_code` — is one provider dominating?
2. Check `/health` — any circuits open?
3. If a single provider is failing, the circuit breaker should isolate it within 5 failures. If failover is also failing, both providers are degraded — page upstream.
4. Check structured logs for the offending `request_id`s:
   ```
   kubectl logs -l app=sentinel | jq 'select(.level == "error")'
   ```

### `SentinelCircuitOpen` (any provider circuit in OPEN state)

1. **Don't panic.** This is the system working. The circuit will probe (half-open) after 30s.
2. Confirm by reading provider status pages (status.anthropic.com, status.openai.com).
3. Failover should be carrying traffic. Confirm with:
   ```
   sum by (provider) (rate(sentinel_requests_total[5m]))
   ```
4. If both providers are open: see §3.

### `SentinelHighLatency` (p95 > 10s)

1. Almost always upstream. Check provider status pages.
2. Confirm with `sentinel_request_duration_seconds` broken down by provider — the unhealthy one is the slow one.
3. If both providers are slow, check network egress from the gateway's deployment region.

### `SentinelRateLimitSurge`

1. A tenant is being throttled. Find which one:
   ```
   topk(5, sum by (tenant) (rate(sentinel_rate_limit_hits_total[10m])))
   ```
2. Decide: raise their limit (intentional growth) or investigate (runaway client). For the latter, check the tenant's audit log for unusual patterns.

## 3. Total provider outage

If all configured providers are down (both circuits open, both providers' status pages confirming):

1. Sentinel is correctly failing requests with 503 `service_unavailable`. There is nothing the gateway itself can do.
2. **Don't disable the circuit breaker** to "let traffic through". You will only make the upstream's recovery slower.
3. Communicate to consumers. The `Retry-After` header is set on every 503 — make sure your clients respect it.
4. When the upstream recovers, the circuit will half-open automatically. No restart needed.

## 4. Startup failure

Symptoms: pods crash-looping, `/health` never responds, `startup.failed` in logs.

Most common causes:
1. **No provider configured.** Logs will show `startup.no_providers_configured` at WARN. The app will still start but cannot serve requests. Set at least one of `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.
2. **Postgres unreachable.** SQLAlchemy connection error in logs. Check `DATABASE_URL` and network policy.
3. **Redis unreachable.** Similar — gateway will exit on startup if it can't connect.
4. **Migration not run.** New deployment without `alembic upgrade head`. The schema is missing. Run migrations as a pre-deploy job.

## 5. Rollback

Sentinel is stateless. Rolling back is a deploy rollback:

```
kubectl rollout undo deployment/sentinel    # or equivalent on your platform
```

**Database migrations are NOT automatically rolled back.** If a migration introduced a column the new code requires and the old code doesn't reference, you can roll back the code safely. If a migration *removed* something the old code needs, you'll need a forward-fix migration. This is why we never drop columns in the same release as the code change that stops using them — always two releases (stop reading → stop writing → drop).

## 6. Manual escape hatches

### Force a circuit closed (emergency)

```
redis-cli DEL cb:anthropic:opened_at cb:anthropic:failures
```

**Only do this if you have ground-truth that the upstream is actually healthy** (status page says green, you can `curl` the API directly). Otherwise you're just removing the safety net.

### Drain a single instance (deploys, debugging)

The gateway exits cleanly on SIGTERM and finishes in-flight requests before stopping (Uvicorn handles this by default). The load balancer's readiness probe will stop sending new traffic as soon as the pod is marked terminating. No manual drain needed.

## 7. Post-incident template

After any user-visible incident, write a postmortem within 5 business days. Use this template:

```
# Incident <YYYY-MM-DD> — <one-line title>

## Summary
What happened, who was affected, how long for.

## Timeline (all times UTC)
HH:MM - First alert fires
HH:MM - On-call responds
...
HH:MM - Incident resolved

## What went wrong
The specific technical failure.

## What went right
What stopped this from being worse. (This is not a celebration — it's
how we know what to keep doing.)

## Why we didn't catch it sooner
The detection gap. Almost always more useful than the root cause.

## Action items
- [ ] Owner, date — concrete change with a definition of done.

## Lessons learned
The thing the team should know next time.
```

**Blameless writing.** The postmortem is about how the system allowed a human to make a mistake, not about the human. If a deploy at 4pm Friday took the gateway down, the action item is "block deploys outside business hours," not "tell Hritish not to deploy on Fridays."

## 8. Useful commands

```bash
# Tail logs of the busiest pod
kubectl top pods -l app=sentinel | sort -k2 -h | tail -1 | awk '{print $1}' | xargs kubectl logs -f

# Get the audit log for a request
psql $DATABASE_URL -c "SELECT * FROM audit_logs WHERE request_id = '<id>'"

# Top 10 tenants by spend in the last day
psql $DATABASE_URL -c "
  SELECT tenant_id, SUM(cost_usd) AS spend
  FROM audit_logs
  WHERE created_at > NOW() - INTERVAL '1 day'
  GROUP BY tenant_id
  ORDER BY spend DESC
  LIMIT 10;
"

# Run the injection eval against a fresh dataset
python evals/run_injection_eval.py --dataset evals/datasets/injection_v2.jsonl
```
