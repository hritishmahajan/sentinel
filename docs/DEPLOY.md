# Deploying Sentinel to Fly.io

End-to-end guide — from zero to a live HTTPS URL in ~20 minutes.
All commands are copy-paste ready.

---

## What you'll end up with

```
https://sentinel-gateway.fly.dev/          → 404 (no root route)
https://sentinel-gateway.fly.dev/health    → {"status":"ok","providers":{...}}
https://sentinel-gateway.fly.dev/console   → Admin dashboard
https://sentinel-gateway.fly.dev/docs      → OpenAPI docs (disabled in prod — see below)
https://sentinel-gateway.fly.dev/metrics   → Prometheus scrape endpoint
https://sentinel-gateway.fly.dev/v1/messages → The gateway itself
```

---

## Prerequisites

1. A Fly.io account — https://fly.io (free tier is enough)
2. An Anthropic API key — https://console.anthropic.com

That's it. No cloud account, no Kubernetes, no DNS management needed.

---

## Step 1 — Install flyctl

```bash
curl -L https://fly.io/install.sh | sh
flyctl auth login   # opens browser
```

Confirm it works:
```bash
flyctl auth whoami  # should print your email
```

---

## Step 2 — Push code to GitHub

Create a new repo on GitHub (can be public or private), then:

```bash
cd sentinel
git init
git add .
git commit -m "feat: initial sentinel gateway"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/sentinel.git
git push -u origin main
```

---

## Step 3 — Create the Fly.io app

```bash
cd sentinel
flyctl apps create sentinel-gateway --org personal
```

If `sentinel-gateway` is taken, pick any unique name and update `fly.toml`:
```bash
# Edit fly.toml line 1:
app = "your-unique-name"
```

---

## Step 4 — Provision Postgres

Fly.io managed Postgres, smallest tier (~$0/mo on free plan):

```bash
flyctl postgres create \
  --name sentinel-gateway-db \
  --region sin \
  --initial-cluster-size 1 \
  --vm-size shared-cpu-1x \
  --volume-size 3
```

Attach it to the app (this automatically sets `DATABASE_URL` as a secret):

```bash
flyctl postgres attach sentinel-gateway-db --app sentinel-gateway
```

---

## Step 5 — Provision Redis (Upstash)

Upstash Redis via Fly extensions — free tier, Singapore region:

```bash
flyctl redis create \
  --name sentinel-gateway-redis \
  --region sin \
  --no-replicas
```

Get the private URL and set it as a secret:

```bash
# Get the URL (look for "Private URL" in the output)
flyctl redis status sentinel-gateway-redis

# Set it
flyctl secrets set REDIS_URL="redis://default:PASSWORD@HOST:PORT" \
  --app sentinel-gateway
```

---

## Step 6 — Set secrets

```bash
# Required: at least one provider key
flyctl secrets set ANTHROPIC_API_KEY="sk-ant-..." --app sentinel-gateway

# Optional: OpenAI for failover
flyctl secrets set OPENAI_API_KEY="sk-..." --app sentinel-gateway

# Enable auth in production (tenants need API keys to use the gateway)
flyctl secrets set REQUIRE_AUTH=true --app sentinel-gateway
```

Verify secrets are set (values are hidden):
```bash
flyctl secrets list --app sentinel-gateway
```

---

## Step 7 — Deploy

```bash
flyctl deploy \
  --app sentinel-gateway \
  --dockerfile Dockerfile.prod \
  --remote-only \
  --strategy rolling
```

What happens behind the scenes:
1. Fly builds your Docker image on their infrastructure (remote-only = no local Docker needed)
2. The `release_command` in fly.toml runs: `alembic upgrade head` — creates your tables
3. Rolling deploy starts the new instance and health-checks it before cutting over
4. Old instance is stopped only after the new one is healthy

Watch the logs in real time:
```bash
flyctl logs --app sentinel-gateway
```

---

## Step 8 — Verify

```bash
curl https://sentinel-gateway.fly.dev/health
# {"status":"ok","version":"0.1.0","providers":{"anthropic":"ok"}}

# Open the dashboard
open https://sentinel-gateway.fly.dev/console
```

---

## Step 9 — Create your first tenant

```bash
# Create tenant
curl -X POST https://sentinel-gateway.fly.dev/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "my-team"}'
# → {"id":"...", "name":"my-team", "is_active":true, ...}

# Issue an API key (copy the tenant id from above)
curl -X POST https://sentinel-gateway.fly.dev/admin/tenants/TENANT_ID/keys \
  -H "Content-Type: application/json" \
  -d '{"label": "production"}'
# → {"plaintext":"sk_live_...", ...}   ← COPY THIS, shown once

# Send a request through the gateway
curl -X POST https://sentinel-gateway.fly.dev/v1/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk_live_..." \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "What is the capital of France?"}]
  }'
```

---

## Step 10 — CI/CD (auto-deploy on push)

Add your Fly.io token to GitHub Secrets:

```bash
# Get your token
flyctl tokens create deploy -x 999999h

# Add to GitHub:
# Repo → Settings → Secrets → Actions → New repository secret
# Name: FLY_API_TOKEN
# Value: <paste token>
```

Now every push to `main` automatically deploys. See `.github/workflows/cd.yml`.

---

## Custom domain (optional)

```bash
# Add your domain
flyctl certs create sentinel.yourdomain.com --app sentinel-gateway

# Get the DNS record to add to your registrar
flyctl certs show sentinel.yourdomain.com --app sentinel-gateway
```

Add the CNAME record at your registrar, wait for DNS propagation (~5min), and Fly.io handles TLS automatically.

---

## Monitoring

```bash
# Live logs
flyctl logs --app sentinel-gateway

# SSH into a running instance
flyctl ssh console --app sentinel-gateway

# Scale up (when you're ready)
flyctl scale vm shared-cpu-2x --app sentinel-gateway
flyctl scale count 2 --app sentinel-gateway   # 2 replicas

# Connect to Postgres
flyctl postgres connect --app sentinel-gateway-db
```

---

## Useful one-liners

```bash
# Check spend by tenant (runs directly against prod DB)
flyctl postgres connect --app sentinel-gateway-db -c \
  "SELECT tenant_id, SUM(cost_usd) as spend FROM audit_logs GROUP BY tenant_id ORDER BY spend DESC LIMIT 10;"

# Tail only errors
flyctl logs --app sentinel-gateway | grep '"level":"error"'

# Restart the app (keeps DB/Redis)
flyctl apps restart sentinel-gateway

# Roll back to previous version
flyctl releases list --app sentinel-gateway
flyctl deploy --image registry.fly.io/sentinel-gateway:PREVIOUS_VERSION
```

---

## Cost estimate

| Resource | Tier | Monthly cost |
|---|---|---|
| Fly.io app (1x shared-cpu-1x, 512MB) | Free tier (3 shared VMs free) | $0 |
| Fly.io Postgres (shared-cpu-1x, 3GB) | ~$0–3/mo | ~$0 |
| Upstash Redis (free tier) | 10k commands/day free | $0 |
| Total | | ~$0–3/mo |

The free tier covers everything for a demo/portfolio project.

---

## Troubleshooting

**Migrations fail on first deploy:**
```bash
flyctl ssh console --app sentinel-gateway
alembic upgrade head
```

**`REDIS_URL` not connecting:**
Make sure you're using the *private* URL (starts with `redis://`), not the public one.
Private URLs only work within Fly.io's WireGuard network.

**`DATABASE_URL` not set:**
```bash
flyctl postgres attach sentinel-gateway-db --app sentinel-gateway
```

**App crashed on startup, logs say "no providers configured":**
```bash
flyctl secrets set ANTHROPIC_API_KEY="sk-ant-..." --app sentinel-gateway
flyctl apps restart sentinel-gateway
```
