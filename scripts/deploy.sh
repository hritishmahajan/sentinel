#!/usr/bin/env bash
# deploy.sh — Deploy Sentinel to Fly.io
# Run: bash scripts/deploy.sh
set -euo pipefail

RED='\033[0;31m'
GRN='\033[0;32m'
AMB='\033[0;33m'
BLU='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLU}[sentinel]${NC} $*"; }
success() { echo -e "${GRN}[✓]${NC} $*"; }
warn()    { echo -e "${AMB}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*"; exit 1; }

banner() {
  echo ""
  echo -e "${AMB}  ╔══════════════════════════════════════╗"
  echo -e "  ║   SENTINEL — FLY.IO DEPLOY SCRIPT   ║"
  echo -e "  ╚══════════════════════════════════════╝${NC}"
  echo ""
}

banner

# ── 1. Preflight checks ────────────────────────────────────────────────────
info "Checking prerequisites..."

command -v flyctl >/dev/null 2>&1 || die \
  "flyctl not found. Install: curl -L https://fly.io/install.sh | sh"

flyctl auth whoami >/dev/null 2>&1 || die \
  "Not logged in. Run: flyctl auth login"

success "flyctl found and authenticated"

# ── 2. App name ────────────────────────────────────────────────────────────
APP_NAME="${FLY_APP_NAME:-sentinel-gateway}"
info "App name: ${APP_NAME}"
info "  (Override with: FLY_APP_NAME=my-sentinel bash scripts/deploy.sh)"

# ── 3. Create app (idempotent) ─────────────────────────────────────────────
info "Creating Fly.io app (if not exists)..."
if flyctl apps list 2>/dev/null | grep -q "^${APP_NAME}"; then
  success "App '${APP_NAME}' already exists"
else
  flyctl apps create "${APP_NAME}" --org personal
  success "App '${APP_NAME}' created"
fi

# Update fly.toml with the actual app name
sed -i.bak "s/^app = .*/app = \"${APP_NAME}\"/" fly.toml && rm fly.toml.bak

# ── 4. Postgres ────────────────────────────────────────────────────────────
PG_NAME="${APP_NAME}-db"
info "Provisioning Postgres cluster (${PG_NAME})..."
if flyctl postgres list 2>/dev/null | grep -q "${PG_NAME}"; then
  success "Postgres '${PG_NAME}' already exists"
else
  flyctl postgres create \
    --name "${PG_NAME}" \
    --region sin \
    --initial-cluster-size 1 \
    --vm-size shared-cpu-1x \
    --volume-size 3
  success "Postgres cluster created"
fi

info "Attaching Postgres to app (sets DATABASE_URL secret)..."
flyctl postgres attach "${PG_NAME}" --app "${APP_NAME}" 2>/dev/null || \
  warn "Already attached (or attach failed — check flyctl postgres attach manually)"

# ── 5. Redis (Upstash via Fly extensions) ─────────────────────────────────
info "Provisioning Upstash Redis..."
REDIS_NAME="${APP_NAME}-redis"
if flyctl redis list 2>/dev/null | grep -q "${REDIS_NAME}"; then
  success "Redis '${REDIS_NAME}' already exists"
else
  flyctl redis create \
    --name "${REDIS_NAME}" \
    --region sin \
    --no-replicas 2>/dev/null || {
    warn "flyctl redis create failed — you may need: flyctl ext redis create"
    warn "Fallback: flyctl ext redis create --name ${REDIS_NAME} --region sin"
  }
fi

info "Getting Redis URL..."
REDIS_URL=$(flyctl redis status "${REDIS_NAME}" --json 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('privateUrl',''))" 2>/dev/null || true)

if [ -z "${REDIS_URL}" ]; then
  warn "Could not auto-fetch Redis URL."
  warn "Run: flyctl redis status ${REDIS_NAME}"
  warn "Then: flyctl secrets set REDIS_URL=<url> --app ${APP_NAME}"
else
  flyctl secrets set "REDIS_URL=${REDIS_URL}" --app "${APP_NAME}"
  success "Redis URL set"
fi

# ── 6. Provider secrets ────────────────────────────────────────────────────
info "Setting provider API keys as secrets..."

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  flyctl secrets set "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" --app "${APP_NAME}"
  success "ANTHROPIC_API_KEY set from environment"
else
  warn "ANTHROPIC_API_KEY not found in environment."
  warn "Set it manually: flyctl secrets set ANTHROPIC_API_KEY=sk-ant-... --app ${APP_NAME}"
fi

if [ -n "${OPENAI_API_KEY:-}" ]; then
  flyctl secrets set "OPENAI_API_KEY=${OPENAI_API_KEY}" --app "${APP_NAME}"
  success "OPENAI_API_KEY set from environment"
fi

# Auth on by default in production
flyctl secrets set "REQUIRE_AUTH=true" --app "${APP_NAME}" 2>/dev/null || true

# ── 7. Deploy ──────────────────────────────────────────────────────────────
info "Deploying Sentinel (this runs migrations then starts the gateway)..."
flyctl deploy \
  --app "${APP_NAME}" \
  --dockerfile Dockerfile.prod \
  --remote-only \
  --strategy rolling

# ── 8. Verify ─────────────────────────────────────────────────────────────
info "Verifying deployment..."
sleep 5
APP_URL="https://${APP_NAME}.fly.dev"
if curl -sf "${APP_URL}/health" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' else 1)"; then
  success "Gateway is live at ${APP_URL}"
  success "Dashboard: ${APP_URL}/console"
  success "Metrics:   ${APP_URL}/metrics"
  success "Docs:      ${APP_URL}/docs"
else
  warn "Health check returned unexpected response. Check: flyctl logs --app ${APP_NAME}"
fi

echo ""
echo -e "${AMB}  ╔══════════════════════════════════════╗"
echo -e "  ║   DEPLOYMENT COMPLETE                ║"
echo -e "  ╠══════════════════════════════════════╣"
echo -e "  ║  Gateway:   ${APP_URL}  ║"
echo -e "  ║  Dashboard: ${APP_URL}/console  ║"
echo -e "  ╚══════════════════════════════════════╝${NC}"
echo ""
echo -e "  Next: create your first tenant"
echo -e "  ${BLU}curl -X POST ${APP_URL}/admin/tenants -H 'Content-Type: application/json' -d '{\"name\":\"my-team\"}'${NC}"
echo ""
