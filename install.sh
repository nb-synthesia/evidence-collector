#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_NAME="get-vanta-evidence"
CONFIG_FILE="$REPO_DIR/config.yaml"
CONFIG_EXAMPLE="$REPO_DIR/config.example.yaml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

pass()  { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; }
info()  { echo -e "  ${BLUE}→${NC} $1"; }
header(){ echo -e "\n${BLUE}[$1]${NC}"; }

ERRORS=0
WARNINGS=0

prompt_continue() {
  local msg="$1"
  warn "$msg"
  WARNINGS=$((WARNINGS + 1))
  read -rp "    Continue anyway? [y/N] " answer
  if [[ ! "$answer" =~ ^[Yy] ]]; then
    echo "Aborting."
    exit 1
  fi
}

# ── Platform ───────────────────────────────────────────────────────────

header "Platform"

OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
  pass "macOS detected"
  CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  CHROME_PROFILE="$HOME/Library/Application Support/Google/Chrome"
elif [[ "$OS" == "Linux" ]]; then
  warn "Linux detected — Chrome paths may differ from defaults"
  WARNINGS=$((WARNINGS + 1))
  CHROME_BIN="$(command -v google-chrome 2>/dev/null || command -v google-chrome-stable 2>/dev/null || command -v chromium 2>/dev/null || echo "")"
  CHROME_PROFILE="$HOME/.config/google-chrome"
else
  prompt_continue "Unsupported OS: $OS. Chrome CDP may not work."
  CHROME_BIN=""
  CHROME_PROFILE=""
fi

# ── AI Assistants ──────────────────────────────────────────────────────

header "AI Assistants"

INSTALL_CURSOR=false
INSTALL_CLAUDE=false

# Cursor
if [[ "$OS" == "Darwin" ]] && [ -d "/Applications/Cursor.app" ]; then
  pass "Cursor installed"
  INSTALL_CURSOR=true
elif command -v cursor &>/dev/null; then
  pass "Cursor CLI available"
  INSTALL_CURSOR=true
elif [ -d "$HOME/.cursor" ]; then
  pass "Cursor config directory exists"
  INSTALL_CURSOR=true
else
  warn "Cursor not found"
  WARNINGS=$((WARNINGS + 1))
  read -rp "    Install skill to Cursor anyway (creates ~/.cursor/skills/)? [y/N] " answer
  if [[ "$answer" =~ ^[Yy] ]]; then
    INSTALL_CURSOR=true
    info "Will create Cursor skill directory"
  else
    info "Skipping Cursor installation"
  fi
fi

# Claude
if [[ "$OS" == "Darwin" ]] && [ -d "/Applications/Claude.app" ]; then
  pass "Claude Desktop installed"
  INSTALL_CLAUDE=true
elif command -v claude &>/dev/null; then
  pass "Claude CLI available"
  INSTALL_CLAUDE=true
elif [ -d "$HOME/.claude" ]; then
  pass "Claude config directory exists"
  INSTALL_CLAUDE=true
else
  warn "Claude not found"
  WARNINGS=$((WARNINGS + 1))
  read -rp "    Install skill to Claude anyway (creates ~/.claude/skills/)? [y/N] " answer
  if [[ "$answer" =~ ^[Yy] ]]; then
    INSTALL_CLAUDE=true
    info "Will create Claude skill directory"
  else
    info "Skipping Claude installation"
  fi
fi

if ! $INSTALL_CURSOR && ! $INSTALL_CLAUDE; then
  fail "No installation target selected. Need at least Cursor or Claude."
  exit 1
fi

# ── Python ─────────────────────────────────────────────────────────────

header "Python"

if command -v python3 &>/dev/null; then
  PY_VERSION="$(python3 --version 2>&1)"
  pass "python3 available ($PY_VERSION)"
else
  fail "python3 not found. Install Python 3.10+ first."
  ERRORS=$((ERRORS + 1))
fi

if command -v pip3 &>/dev/null || python3 -m pip --version &>/dev/null 2>&1; then
  pass "pip available"
else
  fail "pip not found. Install pip: python3 -m ensurepip"
  ERRORS=$((ERRORS + 1))
fi

# ── Python Dependencies ───────────────────────────────────────────────

header "Python Dependencies"

MISSING_DEPS=()
for dep in playwright requests reportlab yaml; do
  if python3 -c "import $dep" 2>/dev/null; then
    pass "$dep installed"
  else
    MISSING_DEPS+=("$dep")
    warn "$dep not installed"
  fi
done

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
  read -rp "    Install missing dependencies now? [Y/n] " answer
  if [[ ! "$answer" =~ ^[Nn] ]]; then
    info "Running: pip install -r $REPO_DIR/requirements.txt"
    if python3 -m pip install -r "$REPO_DIR/requirements.txt"; then
      pass "Dependencies installed"
    else
      fail "pip install failed"
      ERRORS=$((ERRORS + 1))
    fi
  else
    warn "Skipped dependency install — run later: pip install -r $REPO_DIR/requirements.txt"
    WARNINGS=$((WARNINGS + 1))
  fi
fi

# ── Playwright Browser ─────────────────────────────────────────────────

header "Playwright Browser"

if python3 -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
b.close()
p.stop()
" 2>/dev/null; then
  pass "Playwright chromium browser available"
else
  warn "Playwright chromium browser not installed"
  WARNINGS=$((WARNINGS + 1))
  read -rp "    Install Playwright chromium now? [Y/n] " answer
  if [[ ! "$answer" =~ ^[Nn] ]]; then
    info "Running: playwright install chromium"
    if python3 -m playwright install chromium; then
      pass "Playwright chromium installed"
    else
      fail "Playwright install failed"
      ERRORS=$((ERRORS + 1))
    fi
  else
    warn "Skipped — run later: playwright install chromium"
    WARNINGS=$((WARNINGS + 1))
  fi
fi

# ── Google Chrome ──────────────────────────────────────────────────────

header "Google Chrome"

if [ -n "$CHROME_BIN" ] && [ -x "$CHROME_BIN" ]; then
  CHROME_VERSION="$("$CHROME_BIN" --version 2>/dev/null || echo "unknown")"
  pass "Chrome found ($CHROME_VERSION)"
else
  fail "Google Chrome not found at ${CHROME_BIN:-<unset>}"
  info "Install from https://www.google.com/chrome/ (or set CHROME_BINARY)"
  ERRORS=$((ERRORS + 1))
fi

if [ -n "$CHROME_PROFILE" ] && [ -d "$CHROME_PROFILE/Default" ]; then
  pass "Chrome profile exists ($CHROME_PROFILE/Default)"
else
  warn "Chrome profile not found — CDP screenshot capture won't have SSO sessions"
  info "Launch Chrome and log into your services first, then re-run install"
  WARNINGS=$((WARNINGS + 1))
fi

# ── CDP Port ───────────────────────────────────────────────────────────

header "CDP Port (9222)"

if lsof -i :9222 -sTCP:LISTEN &>/dev/null 2>&1; then
  warn "Port 9222 already in use (existing CDP Chrome instance?)"
  info "The skill will reuse the existing CDP instance — this is fine"
  CDP_ALREADY_RUNNING=true
else
  pass "Port 9222 available"
  CDP_ALREADY_RUNNING=false
fi

# ── Chrome CDP Validation ──────────────────────────────────────────────

header "Chrome CDP Validation"

if [ "$ERRORS" -gt 0 ]; then
  warn "Skipping CDP validation due to earlier errors"
else
  if $CDP_ALREADY_RUNNING; then
    info "CDP already running — testing connectivity"
    if curl -s --connect-timeout 3 "http://localhost:9222/json/version" >/dev/null 2>&1; then
      CDP_BROWSER="$(curl -s "http://localhost:9222/json/version" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Browser','unknown'))" 2>/dev/null || echo "unknown")"
      pass "CDP reachable ($CDP_BROWSER)"
    else
      warn "Port 9222 bound but CDP not responding — may be a different process"
      WARNINGS=$((WARNINGS + 1))
    fi
  else
    info "Launching test CDP Chrome instance (will shut down after validation)..."

    CDP_TEST_DIR="$(mktemp -d)"
    CDP_TEST_PID=""

    cleanup_cdp_test() {
      if [ -n "$CDP_TEST_PID" ]; then
        kill "$CDP_TEST_PID" 2>/dev/null || true
        wait "$CDP_TEST_PID" 2>/dev/null || true
      fi
      rm -rf "$CDP_TEST_DIR" 2>/dev/null || true
    }
    trap cleanup_cdp_test EXIT

    # Copy essential auth files from the real Chrome profile
    if [ -d "$CHROME_PROFILE/Default" ]; then
      mkdir -p "$CDP_TEST_DIR/Default/Network"
      for f in Cookies "Login Data" "Web Data" Preferences "Secure Preferences" "Extension Cookies"; do
        [ -f "$CHROME_PROFILE/Default/$f" ] && cp "$CHROME_PROFILE/Default/$f" "$CDP_TEST_DIR/Default/$f" 2>/dev/null || true
      done
      [ -f "$CHROME_PROFILE/Default/Network/Cookies" ] && cp "$CHROME_PROFILE/Default/Network/Cookies" "$CDP_TEST_DIR/Default/Network/Cookies" 2>/dev/null || true
      [ -f "$CHROME_PROFILE/Local State" ] && cp "$CHROME_PROFILE/Local State" "$CDP_TEST_DIR/Local State" 2>/dev/null || true
      pass "Copied Chrome profile auth files to test directory"
    else
      warn "No Chrome profile to copy — testing with blank profile"
    fi

    "$CHROME_BIN" \
      --remote-debugging-port=9222 \
      --user-data-dir="$CDP_TEST_DIR" \
      --no-first-run \
      --no-default-browser-check \
      --headless=new \
      &>/dev/null &
    CDP_TEST_PID=$!

    CDP_READY=false
    for i in $(seq 1 10); do
      sleep 1
      if curl -s --connect-timeout 2 "http://localhost:9222/json/version" >/dev/null 2>&1; then
        CDP_READY=true
        break
      fi
    done

    if $CDP_READY; then
      CDP_BROWSER="$(curl -s "http://localhost:9222/json/version" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Browser','unknown'))" 2>/dev/null || echo "unknown")"
      pass "CDP Chrome launched and reachable ($CDP_BROWSER)"

      # Validate Playwright can connect
      if python3 -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
print(f'contexts={len(b.contexts)}')
b.close()
p.stop()
" 2>/dev/null; then
        pass "Playwright connected to CDP Chrome successfully"
      else
        warn "Playwright could not connect to CDP Chrome"
        info "This may work in non-headless mode — continuing"
        WARNINGS=$((WARNINGS + 1))
      fi
    else
      fail "CDP Chrome failed to start within 10s"
      ERRORS=$((ERRORS + 1))
    fi

    info "Shutting down test CDP instance..."
    kill "$CDP_TEST_PID" 2>/dev/null || true
    wait "$CDP_TEST_PID" 2>/dev/null || true
    CDP_TEST_PID=""
    rm -rf "$CDP_TEST_DIR"
    pass "Test instance cleaned up"
  fi
fi

# ── Configuration (config.yaml) ────────────────────────────────────────

header "Configuration"

PLAYBOOK_BACKEND="local"

if [ -f "$CONFIG_FILE" ]; then
  pass "config.yaml already exists — leaving it untouched"
  info "Edit $CONFIG_FILE manually to change settings"
  PLAYBOOK_BACKEND="$(python3 -c "
import sys
try:
    import yaml
    c = yaml.safe_load(open('$CONFIG_FILE')) or {}
    print(((c.get('playbook') or {}).get('backend') or 'local'))
except Exception:
    print('local')
" 2>/dev/null || echo local)"
else
  info "Let's create config.yaml (org-wide, not secret — commit it to your fork)."

  # IdP / SSO dashboard URL
  read -rp "    IdP / SSO dashboard URL (e.g. https://your-idp.example.com): " SSO_URL
  SSO_URL="${SSO_URL:-}"

  # Vanta region
  echo "    Vanta API region:  1) us   2) eu   3) gov"
  read -rp "    Choose [1-3, default 1]: " REGION_CHOICE
  case "${REGION_CHOICE:-1}" in
    2) VANTA_REGION="eu" ;;
    3) VANTA_REGION="gov" ;;
    *) VANTA_REGION="us" ;;
  esac

  # Playbook base document backend
  echo ""
  info "Where do your detailed playbooks (the 'base document') live?"
  echo "      1) Local markdown in this repo   (default — zero external deps, versioned in Git)"
  echo "      2) Notion"
  echo "      3) Confluence"
  echo "      4) Google Doc"
  echo "      5) Other URL"
  read -rp "    Choose [1-5, default 1]: " PB_CHOICE

  BASE_URL=""
  BASE_ID=""
  case "${PB_CHOICE:-1}" in
    2)
      PLAYBOOK_BACKEND="notion"
      read -rp "    Notion base/root page URL (optional): " BASE_URL
      read -rp "    Notion base/root page ID (optional): " BASE_ID
      ;;
    3)
      PLAYBOOK_BACKEND="confluence"
      read -rp "    Confluence base page URL: " BASE_URL
      read -rp "    Confluence base page ID (optional): " BASE_ID
      ;;
    4)
      PLAYBOOK_BACKEND="google_doc"
      read -rp "    Google Doc base URL: " BASE_URL
      ;;
    5)
      PLAYBOOK_BACKEND="url"
      read -rp "    Base document URL: " BASE_URL
      ;;
    *)
      PLAYBOOK_BACKEND="local"
      ;;
  esac

  cat > "$CONFIG_FILE" <<EOF
# Vanta Evidence Collector — organization configuration
# Generated by install.sh. NOT secret — safe to commit to your fork.
# Secrets (Vanta OAuth) live in ~/.vanta/credentials.json (never committed).

sso_url: "${SSO_URL}"
vanta_region: "${VANTA_REGION}"

playbook:
  backend: "${PLAYBOOK_BACKEND}"
  path: "knowledge/playbooks"
  base_document_url: "${BASE_URL}"
  base_document_id: "${BASE_ID}"
  notes: ""
EOF

  pass "Wrote $CONFIG_FILE (backend: $PLAYBOOK_BACKEND, region: $VANTA_REGION)"
  if [ "$PLAYBOOK_BACKEND" = "local" ]; then
    info "Add your playbooks as markdown under knowledge/playbooks/ and reference them from index.yaml"
  else
    info "Reference each playbook from knowledge/playbooks/index.yaml via 'playbook_ref'"
  fi
fi

# ── MCP Servers (optional) ─────────────────────────────────────────────

header "MCP Servers (optional)"

# MCP servers are OPTIONAL. Notion (or another knowledge-system MCP) is only
# needed if your playbook backend is that system, or if you want the agent to
# do extra "how does our org handle X?" research. The core pipeline works
# without any MCP server.

NOTION_FOUND=false
NOTION_LOCATIONS=()

CLAUDE_DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
CURSOR_MCP="$HOME/.cursor/mcp.json"
CLAUDE_CODE_SETTINGS="$HOME/.claude/settings.json"

check_notion_in() {
  local file="$1" label="$2"
  [ -f "$file" ] || return 0
  if python3 -c "
import json, sys
c = json.load(open(sys.argv[1]))
servers = c.get('mcpServers', c.get('mcp', {}).get('servers', {}))
if isinstance(servers, dict):
    for k in servers:
        if 'notion' in k.lower():
            sys.exit(0)
sys.exit(1)
" "$file" 2>/dev/null; then
    NOTION_FOUND=true
    NOTION_LOCATIONS+=("$label")
  fi
}

check_notion_in "$CLAUDE_DESKTOP_CONFIG" "Claude Desktop config"
check_notion_in "$CURSOR_MCP" "Cursor mcp.json"
check_notion_in "$CLAUDE_CODE_SETTINGS" "Claude Code settings"
[ -d "$HOME/.cursor/plugins/cache/cursor-public/notion-workspace" ] && { NOTION_FOUND=true; NOTION_LOCATIONS+=("Cursor plugin: notion-workspace"); }

if $NOTION_FOUND; then
  pass "Notion MCP found (${NOTION_LOCATIONS[*]})"
elif [ "$PLAYBOOK_BACKEND" = "notion" ]; then
  warn "Playbook backend is 'notion' but no Notion MCP was detected"
  info "Install a Notion MCP server so the agent can read your playbook pages:"
  info "  Cursor: install the 'Notion Workspace' plugin from the marketplace"
  info "  Claude: add a notion server to claude_desktop_config.json / settings.json"
  WARNINGS=$((WARNINGS + 1))
else
  info "No Notion MCP detected — not required for backend '$PLAYBOOK_BACKEND'."
  info "Optional: add a knowledge-system MCP (Notion/Confluence) for richer research."
fi

# ── Vanta Credentials ─────────────────────────────────────────────────

header "Vanta API"

VANTA_CREDS="$HOME/.vanta/credentials.json"
if [ -f "$VANTA_CREDS" ]; then
  if python3 -c "
import json
c = json.load(open('$VANTA_CREDS'))
assert 'client_id' in c and 'client_secret' in c
" 2>/dev/null; then
    pass "Vanta credentials found and valid"
  else
    warn "Vanta credentials file exists but format looks wrong"
    info "Expected: {\"client_id\": \"vci_...\", \"client_secret\": \"vcs_...\"}"
    WARNINGS=$((WARNINGS + 1))
  fi
else
  warn "Vanta credentials not found at $VANTA_CREDS"
  info "Create later: mkdir -p ~/.vanta && cat > ~/.vanta/credentials.json"
  info 'Format: {"client_id": "vci_...", "client_secret": "vcs_..."}'
  info "Get from: Vanta → Settings → API → OAuth Clients"
  WARNINGS=$((WARNINGS + 1))
fi

# ── Symlink Installation ──────────────────────────────────────────────

header "Skill Installation"

install_symlink() {
  local target="$1"
  local parent
  parent="$(dirname "$target")"
  mkdir -p "$parent"

  if [ -L "$target" ]; then
    existing="$(readlink "$target")"
    if [ "$existing" = "$REPO_DIR" ]; then
      pass "Already linked: $target"
      return
    else
      info "Updating symlink (was → $existing)"
      rm "$target"
    fi
  elif [ -d "$target" ]; then
    info "Backing up existing: $target → ${target}.bak"
    mv "$target" "${target}.bak"
  fi

  ln -s "$REPO_DIR" "$target"
  pass "Linked: $target → $REPO_DIR"
}

if $INSTALL_CURSOR; then
  install_symlink "$HOME/.cursor/skills/$SKILL_NAME"
fi

if $INSTALL_CLAUDE; then
  install_symlink "$HOME/.claude/skills/$SKILL_NAME"
fi

# ── Summary ────────────────────────────────────────────────────────────

echo ""
echo "─────────────────────────────────────────"

if [ "$ERRORS" -gt 0 ]; then
  echo -e "${RED}Installation completed with $ERRORS error(s) and $WARNINGS warning(s).${NC}"
  echo "Fix the errors above before using the skill."
  exit 1
elif [ "$WARNINGS" -gt 0 ]; then
  echo -e "${YELLOW}Installation completed with $WARNINGS warning(s).${NC}"
  echo "The skill is installed but some features may not work until warnings are resolved."
else
  echo -e "${GREEN}Installation complete — no issues found.${NC}"
fi

echo ""
echo "Installed to:"
$INSTALL_CURSOR && echo "  Cursor: ~/.cursor/skills/$SKILL_NAME"
$INSTALL_CLAUDE && echo "  Claude:  ~/.claude/skills/$SKILL_NAME"
echo ""
echo "Next steps:"
echo "  1. Fill in knowledge/ for your org (infrastructure.yaml, sso-tiles.yaml, playbooks/index.yaml)"
echo "  2. Add your playbooks (backend: $PLAYBOOK_BACKEND)"
echo "  3. Ensure ~/.vanta/credentials.json exists"
echo "  4. Open Cursor or Claude and say /get-vanta-evidence"
