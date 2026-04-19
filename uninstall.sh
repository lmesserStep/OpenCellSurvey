#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# uninstall.sh — Remove OpenCellSurvey
#
# Usage:
#   sudo ./uninstall.sh           # removes app; prompts about data
#   sudo ./uninstall.sh --purge   # removes app AND all survey data / certs
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/opt/opencellsurvey"
DATA_DIR="/var/lib/opencellsurvey"
ETC_DIR="/etc/opencellsurvey"
SERVICE_FILE="/etc/systemd/system/opencellsurvey.service"
SERVICE_NAME="opencellsurvey"
APP_USER="opencellsurvey"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  ▶  $*${RESET}"; }
success() { echo -e "${GREEN}  ✔  $*${RESET}"; }
warn()    { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
die()     { echo -e "${RED}  ✖  $*${RESET}" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "This script must be run as root (sudo ./uninstall.sh)"

PURGE=false
[[ "${1:-}" == "--purge" ]] && PURGE=true

echo
echo -e "${BOLD}─── OpenCellSurvey Uninstaller ─────────────────────────${RESET}"
echo -e "  Mode: $( $PURGE && echo 'PURGE (all data will be removed)' || echo 'STANDARD (data preserved)' )"
echo -e "${BOLD}────────────────────────────────────────────────────────${RESET}"
echo

# Confirm unless --purge was explicit
if ! $PURGE; then
  read -r -p "  Are you sure you want to uninstall OpenCellSurvey? [y/N] " CONFIRM
  [[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

# ── Stop and disable service ──────────────────────────────────────────────────
info "Stopping and disabling service…"
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  systemctl stop "$SERVICE_NAME"
fi
if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
  systemctl disable --quiet "$SERVICE_NAME"
fi
success "Service stopped and disabled"

# ── Remove service file ───────────────────────────────────────────────────────
if [[ -f "$SERVICE_FILE" ]]; then
  rm -f "$SERVICE_FILE"
  systemctl daemon-reload
  success "Service file removed"
fi

# ── Remove application directory ─────────────────────────────────────────────
if [[ -d "$INSTALL_DIR" ]]; then
  info "Removing application directory ${INSTALL_DIR}…"
  rm -rf "$INSTALL_DIR"
  success "Removed ${INSTALL_DIR}"
fi

# ── Handle data and config ────────────────────────────────────────────────────
if $PURGE; then
  info "Purging data directory ${DATA_DIR}…"
  [[ -d "$DATA_DIR" ]] && rm -rf "$DATA_DIR" && success "Removed ${DATA_DIR}"

  info "Purging config directory ${ETC_DIR}…"
  [[ -d "$ETC_DIR" ]] && rm -rf "$ETC_DIR" && success "Removed ${ETC_DIR}"
else
  warn "Survey data preserved at: ${DATA_DIR}"
  warn "TLS certs preserved at  : ${ETC_DIR}"
  warn "Re-run with --purge to remove them."
fi

# ── Remove system user ────────────────────────────────────────────────────────
if id "$APP_USER" &>/dev/null; then
  info "Removing system user ${APP_USER}…"
  userdel "$APP_USER" 2>/dev/null || true
  success "User removed"
fi

echo
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  OpenCellSurvey uninstalled.${RESET}"
if ! $PURGE; then
  echo
  echo -e "  Your survey data is still at: ${DATA_DIR}"
  echo -e "  To remove it: sudo ./uninstall.sh --purge"
fi
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${RESET}"
echo
