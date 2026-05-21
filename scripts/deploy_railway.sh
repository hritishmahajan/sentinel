#!/usr/bin/env bash
# scripts/deploy_railway.sh — Deploy Sentinel to Railway
# No credit card needed. Just GitHub login.
# Docs: https://docs.railway.app/reference/cli-api
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

echo ""
echo -e "${AMB}  ╔══════════════════════════════════════════╗"
echo -e "  ║   SENTINEL — RAILWAY DEPLOY SCRIPT      ║"
echo -e "  ║   No credit card required               ║"
echo -e "  ╚══════════════════════════════════════════╝${NC}"
echo ""

# ── 1. Check railway CLI ───────────────────────────────────────────────────
if ! command -v railway >/dev/null 2>&1; then
  info "Installing Railway CLI..."
  curl -fsSL https://railway.app/install.sh | sh
fi

railway --version >/dev/null 2>&1 || die "Railway CLI install failed"
success "Railway CLI ready"

# ── 2. Login ───────────────────────────────────────────────────────────────
info "Logging into Railway (opens browser)..."
railway login
success "Logged in"

# ── 3. Create project ──────────────────────────────────────────────────────
info "Creating Railway project 'sentinel-gateway'..."
railway init --name sentinel-gateway
success "Project created"

# ── 4. Add Postgres ───────────────────────────────────────────────────────
info "Adding Postgres database..."
railway add --plugin postgresql
success "Postgres added — DATABASE_URL auto-set"

# ── 5. Add Redis ──────────────────────────────────────────────────────────
info "Adding Redis..."
railway add --plugin redis
success "Redis added — REDIS_URL auto-set"

# ── 6. Set environment variables ──────────────────────────────────────────
info "Setting environment variables..."

railway variables set \
  ENVIRONMENT=prod \
  LOG_FORMAT=json \
  LOG_LEVEL=INFO \
  REQUIRE_AUTH=false

# Provider keys
if [ -n "${XAI_API_KEY:-}" ]; then
  railway variables set XAI_API_KEY="${XAI_API_KEY}"
  success "XAI_API_KEY set"
else
  warn "XAI_API_KEY not found in environment."
  warn "Set it after deploy: railway variables set XAI_API_KEY=xai-..."
fi

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  railway variables set ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
  success "ANTHROPIC_API_KEY set"
fi

if [ -n "${OPENAI_API_KEY:-}" ]; then
  railway variables set OPENAI_API_KEY="${OPENAI_API_KEY}"
  success "OPENAI_API_KEY set"
fi

# ── 7. Deploy ─────────────────────────────────────────────────────────────
info "Deploying Sentinel (building Docker image on Railway)..."
info "This takes ~3 minutes on first deploy..."
railway up --detach
success "Deploy triggered"

# ── 8. Get URL ────────────────────────────────────────────────────────────
info "Fetching deployment URL..."
sleep 10
APP_URL=$(railway domain 2>/dev/null || echo "")

if [ -z "$APP_URL" ]; then
  info "Generating public domain..."
  railway domain generate 2>/dev/null || true
  sleep 3
  APP_URL=$(railway domain 2>/dev/null || echo "pending")
fi

echo ""
echo -e "${AMB}  ╔══════════════════════════════════════════╗"
echo -e "  ║   DEPLOYMENT TRIGGERED                  ║"
echo -e "  ╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Watch build: ${BLU}railway logs${NC}"
echo -e "  Open dashboard: ${BLU}railway open${NC}"
echo ""
echo -e "  Once live, set your API key:"
echo -e "  ${BLU}railway variables set XAI_API_KEY=xai-...${NC}"
echo ""
echo -e "  Then test:"
echo -e "  ${BLU}curl https://YOUR-APP.railway.app/health${NC}"
echo ""
