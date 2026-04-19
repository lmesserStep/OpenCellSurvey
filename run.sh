#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# run.sh — development launcher for OpenCellSurvey
# Usage: ./run.sh [port]
# Default port: 8443  (mirrors the production systemd service)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PORT="${1:-8443}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETC_DIR="${ETC_DIR:-/etc/opencellsurvey}"
CERT="${ETC_DIR}/cert.pem"
KEY="${ETC_DIR}/key.pem"

cd "$SCRIPT_DIR"

# Activate virtual environment if present
if   [[ -f ".venv/bin/activate" ]]; then source .venv/bin/activate
elif [[ -f "venv/bin/activate"  ]]; then source venv/bin/activate
fi

# Install / upgrade deps quietly
pip install --quiet -r requirements.txt

# Build uvicorn TLS args if certs exist
TLS_ARGS=()
if [[ -f "$CERT" && -f "$KEY" ]]; then
  TLS_ARGS=( "--ssl-certfile" "$CERT" "--ssl-keyfile" "$KEY" )
  SCHEME="https"
else
  echo "⚠  TLS certs not found at ${ETC_DIR}. Run install.sh to generate them."
  echo "   Falling back to plain HTTP for development."
  SCHEME="http"
fi

echo "──────────────────────────────────────────────────"
echo "  OpenCellSurvey"
echo "  ${SCHEME}://localhost:${PORT}"
echo "──────────────────────────────────────────────────"

exec uvicorn app:app --host 0.0.0.0 --port "${PORT}" --reload "${TLS_ARGS[@]+"${TLS_ARGS[@]}"}"
