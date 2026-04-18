#!/bin/bash
# Start huan-ui on port 8868 (with proper env var passing)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env if present (huan-ui's own .env)
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    source "${SCRIPT_DIR}/.env"
    set +a
fi

# ── Find hermes-agent ────────────────────────────────────────────────────────
AGENT_DIR="${HERMES_WEBUI_AGENT_DIR:-}"
if [[ -z "$AGENT_DIR" ]]; then
    for candidate in "$HOME/.hermes/hermes-agent" "$HOME/hermes-agent"; do
        if [[ -d "$candidate" && -x "$candidate/venv/bin/python" ]]; then
            AGENT_DIR="$candidate"
            break
        fi
    done
fi

if [[ -z "$AGENT_DIR" ]]; then
    echo "[error] Could not find hermes-agent. Set HERMES_WEBUI_AGENT_DIR=/path/to/hermes-agent"
    exit 1
fi

export HERMES_WEBUI_AGENT_DIR="$AGENT_DIR"

# ── Find Python ──────────────────────────────────────────────────────────────
PYTHON="${HERMES_WEBUI_PYTHON:-}"
if [[ -z "$PYTHON" && -x "$AGENT_DIR/venv/bin/python" ]]; then
    PYTHON="$AGENT_DIR/venv/bin/python"
fi

if [[ -z "$PYTHON" ]]; then
    PYTHON="$(command -v python3)"
fi

echo "[huan-ui] Using Python: $PYTHON"
echo "[huan-ui] Using hermes-agent: $AGENT_DIR"

# ── Setup virtualenv for huan-ui's own dependencies ──────────────────────────
VENV_PATH="${SCRIPT_DIR}/.venv"
if [[ ! -d "$VENV_PATH" ]]; then
    echo "[huan-ui] Creating virtualenv..."
    "$PYTHON" -m venv "$VENV_PATH"
fi

VENV_PY="$VENV_PATH/bin/python"
if [[ -x "$VENV_PY" ]]; then
    "$VENV_PY" -m pip install -q pyyaml 2>/dev/null || true
    PYTHON="$VENV_PY"
fi

# ── Load environment - CRITICAL: export everything ──────────────────────────
export HERMES_WEBUI_PORT="${HERMES_WEBUI_PORT:-8868}"
export HERMES_WEBUI_HOST="${HERMES_WEBUI_HOST:-127.0.0.1}"
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# Load hermes-agent's .env and EXPORT ALL VARIABLES
if [[ -f "$AGENT_DIR/.env" ]]; then
    set -a
    source "$AGENT_DIR/.env"
    set +a
    echo "[huan-ui] Loaded $AGENT_DIR/.env"
fi

# Debug: verify API keys are loaded
if [[ -n "${MINIMAX_API_KEY:-}" ]]; then
    echo "[huan-ui] ✓ MINIMAX_API_KEY loaded"
else
    echo "[huan-ui] ⚠ MINIMAX_API_KEY not set"
fi

echo "[huan-ui] Starting on port $HERMES_WEBUI_PORT..."
cd "$SCRIPT_DIR"
exec "$PYTHON" server.py
