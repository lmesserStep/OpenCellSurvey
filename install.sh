#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# install.sh — OpenCellSurvey installer / upgrader
#
# Usage:
#   sudo ./install.sh            # fresh install or auto-detect mode
#   sudo ./install.sh upgrade    # force upgrade mode
#
# Idempotent: safe to run multiple times.
# Persistent data in /var/lib/opencellsurvey is NEVER deleted.
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/opencellsurvey"
APP_DIR="${INSTALL_DIR}/app"
VENV_DIR="${INSTALL_DIR}/venv"
DATA_DIR="/var/lib/opencellsurvey"
ETC_DIR="/etc/opencellsurvey"
SERVICE_FILE="/etc/systemd/system/opencellsurvey.service"
SERVICE_NAME="opencellsurvey"
APP_USER="opencellsurvey"
PORT="8443"

# Source directory (directory containing this script)
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  ▶  $*${RESET}"; }
success() { echo -e "${GREEN}  ✔  $*${RESET}"; }
warn()    { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
die()     { echo -e "${RED}  ✖  $*${RESET}" >&2; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ "$EUID" -eq 0 ]] || die "This script must be run as root (sudo ./install.sh)"

# ── Detect mode ───────────────────────────────────────────────────────────────
if [[ "${1:-}" == "upgrade" ]] || [[ -d "$INSTALL_DIR" ]]; then
  MODE="UPGRADE"
else
  MODE="FRESH INSTALL"
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}─── OpenCellSurvey Installer ───────────────────────────${RESET}"
echo -e "  Install directory : ${INSTALL_DIR}"
echo -e "  App directory     : ${APP_DIR}"
echo -e "  Venv directory    : ${VENV_DIR}"
echo -e "  Data directory    : ${DATA_DIR}"
echo -e "  Config directory  : ${ETC_DIR}"
echo -e "  Service port      : ${PORT} (HTTPS)"
echo -e "  Mode              : ${BOLD}${MODE}${RESET}"
echo -e "${BOLD}────────────────────────────────────────────────────────${RESET}"
echo

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Stop service if upgrading
# ══════════════════════════════════════════════════════════════════════════════
if [[ "$MODE" == "UPGRADE" ]]; then
  if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    info "Stopping ${SERVICE_NAME} service for upgrade…"
    systemctl stop "$SERVICE_NAME"
    success "Service stopped"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Install system dependencies
# ══════════════════════════════════════════════════════════════════════════════
info "Checking system dependencies…"

if ! command -v apt-get &>/dev/null; then
  die "apt-get not found. This installer supports Debian/Ubuntu systems only."
fi

PACKAGES=(python3 python3-venv python3-pip sqlite3 curl git openssl rsync)
MISSING=()
for pkg in "${PACKAGES[@]}"; do
  if ! dpkg -s "$pkg" &>/dev/null; then
    MISSING+=("$pkg")
  fi
done

if [[ "${#MISSING[@]}" -gt 0 ]]; then
  info "Installing missing packages: ${MISSING[*]}"
  apt-get update -qq
  apt-get install -y "${MISSING[@]}"
  success "Packages installed"
else
  success "All system dependencies present"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Create application user
# ══════════════════════════════════════════════════════════════════════════════
info "Checking application user…"
if ! id "$APP_USER" &>/dev/null; then
  useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" -c "OpenCellSurvey service account" "$APP_USER"
  success "Created system user: ${APP_USER}"
else
  success "User ${APP_USER} already exists"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Create directory structure
# ══════════════════════════════════════════════════════════════════════════════
info "Creating directory structure…"

for dir in \
  "$INSTALL_DIR" \
  "$APP_DIR" \
  "$VENV_DIR" \
  "$DATA_DIR" \
  "${DATA_DIR}/data" \
  "${DATA_DIR}/tiles" \
  "${DATA_DIR}/data/exports" \
  "${DATA_DIR}/data/floorplans" \
  "$ETC_DIR"
do
  if [[ ! -d "$dir" ]]; then
    mkdir -p "$dir"
    echo "    created: $dir"
  fi
done

# Set ownership (never recurse into DATA_DIR to avoid wiping ACLs on upgrades)
chown -R "${APP_USER}:${APP_USER}" "$INSTALL_DIR"
chown -R "${APP_USER}:${APP_USER}" "$DATA_DIR"
chown    "${APP_USER}:${APP_USER}" "$ETC_DIR"
# ETC_DIR perms: root owns, group readable by service account
chmod 750 "$ETC_DIR"

success "Directory structure ready"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — TLS certificate
# ══════════════════════════════════════════════════════════════════════════════
CERT="${ETC_DIR}/cert.pem"
KEY="${ETC_DIR}/key.pem"

info "Checking TLS certificates…"
if [[ -f "$CERT" && -f "$KEY" ]]; then
  success "TLS certificates already exist — skipping generation"
else
  info "Generating self-signed certificate (10 years)…"
  openssl req -x509 -nodes -days 3650 \
    -newkey rsa:2048 \
    -keyout  "$KEY" \
    -out     "$CERT" \
    -subj    "/CN=OpenCellSurvey/O=OpenCellSurvey/C=US" \
    -addext  "subjectAltName=IP:127.0.0.1,DNS:localhost" \
    2>/dev/null
  chmod 640 "$KEY" "$CERT"
  chown "${APP_USER}:${APP_USER}" "$KEY" "$CERT"
  success "TLS certificate generated at ${CERT}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Install Python virtual environment
# ══════════════════════════════════════════════════════════════════════════════
info "Installing Python virtual environment…"

if [[ ! -f "${VENV_DIR}/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
  success "Virtual environment created"
fi

info "Installing Python dependencies…"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${SRC_DIR}/requirements.txt"
chown -R "${APP_USER}:${APP_USER}" "$VENV_DIR"
success "Python dependencies installed"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Copy application files
# ══════════════════════════════════════════════════════════════════════════════
info "Deploying application code to ${APP_DIR}…"

# Files/directories to sync (explicit list — never touches DATA_DIR or ETC_DIR)
APP_FILES=(
  app.py config.py export.py gps.py models.py parser.py
  storage.py survey_runner.py requirements.txt
  static templates
)

# Build rsync source list
RSYNC_SRCS=()
for f in "${APP_FILES[@]}"; do
  src="${SRC_DIR}/${f}"
  if [[ -e "$src" ]]; then
    RSYNC_SRCS+=("$src")
  else
    warn "Source not found, skipping: ${src}"
  fi
done

if [[ "${#RSYNC_SRCS[@]}" -gt 0 ]]; then
  rsync -a --delete "${RSYNC_SRCS[@]}" "${APP_DIR}/"
fi

chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
chmod -R u=rwX,g=rX,o= "$APP_DIR"
success "Application files deployed"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Install systemd service
# ══════════════════════════════════════════════════════════════════════════════
info "Installing systemd service…"

# Write service file from embedded here-doc so install.sh is self-contained
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=OpenCellSurvey RF Survey Server
Documentation=https://github.com/your-org/opencellsurvey
After=network.target
Wants=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}

ExecStart=${VENV_DIR}/bin/uvicorn app:app \\
    --host 0.0.0.0 \\
    --port ${PORT} \\
    --ssl-certfile ${CERT} \\
    --ssl-keyfile  ${KEY}

Restart=always
RestartSec=3
TimeoutStopSec=10

Environment="DATA_DIR=${DATA_DIR}"
Environment="ETC_DIR=${ETC_DIR}"

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=${DATA_DIR} ${ETC_DIR} /tmp

StandardOutput=journal
StandardError=journal
SyslogIdentifier=opencellsurvey

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_FILE"
success "Service file written to ${SERVICE_FILE}"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 9 — Enable and start service
# ══════════════════════════════════════════════════════════════════════════════
info "Enabling and starting service…"

systemctl daemon-reload
systemctl enable --quiet "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# Give it a moment to start, then verify
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
  success "Service ${SERVICE_NAME} is running"
else
  warn "Service did not start cleanly. Check logs with:"
  warn "  journalctl -u ${SERVICE_NAME} -n 50"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Done
# ══════════════════════════════════════════════════════════════════════════════
echo
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  OpenCellSurvey installed successfully.${RESET}"
echo
echo -e "  Access the UI at:"
echo -e "  ${BOLD}https://localhost:${PORT}${RESET}"
echo
echo -e "  Service management:"
echo -e "    sudo systemctl status  ${SERVICE_NAME}"
echo -e "    sudo systemctl stop    ${SERVICE_NAME}"
echo -e "    sudo systemctl restart ${SERVICE_NAME}"
echo -e "    sudo journalctl -u     ${SERVICE_NAME} -f"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${RESET}"
echo
