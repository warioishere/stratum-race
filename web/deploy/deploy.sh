#!/usr/bin/env bash
# One-command deploy for the stratum-race web leaderboard.
#
# Remote mode (from your laptop, against a fresh Debian/Ubuntu server whose
# DNS already points at it):
#
#   ./web/deploy/deploy.sh --host root@203.0.113.7 --domain stratumrace.com
#
# Local mode (run on the server itself, as root, from a checkout):
#
#   sudo ./web/deploy/deploy.sh --domain stratumrace.com
#
# Re-running is safe: it updates code, restarts services, and keeps
# collected race data (unless --reset is given). No credentials are read
# from or written to this repository; remote mode uses your normal SSH
# authentication (keys or interactive password prompt).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy.sh [--host user@server] --domain example.com [options]

  --host user@server   Deploy over SSH to this server (omit to install on
                       the machine you are running from, as root)
  --domain DOMAIN      Public domain for the site (required). DNS must
                       already point at the target server for TLS issuance.
  --vantage "LABEL"    Human-readable measurement location shown on the site,
                       e.g. "Denver basement, AS12345". Auto-detected on
                       DigitalOcean when omitted.
  --reset              Wipe all collected race sessions and restart the
                       stats from zero.
  --help               Show this help.

What it does: installs nginx + certbot + a systemd service that races the
pools continuously, publishes the site at https://DOMAIN/, and requests a
Let's Encrypt certificate. Idempotent — run it again to update.
EOF
}

HOST=""
DOMAIN=""
VANTAGE_ARG=""
RESET="no"

while [ $# -gt 0 ]; do
  case "$1" in
    --host)    HOST="${2:?--host needs a value}"; shift 2 ;;
    --domain)  DOMAIN="${2:?--domain needs a value}"; shift 2 ;;
    --vantage) VANTAGE_ARG="${2:?--vantage needs a value}"; shift 2 ;;
    --reset)   RESET="yes"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$DOMAIN" ]; then
  echo "error: --domain is required" >&2
  usage >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE_DIR="/opt/stratum-race"

if [ -z "$HOST" ]; then
  # Local mode: install this checkout onto this machine.
  if [ "$(id -u)" -ne 0 ]; then
    echo "error: local install must run as root (try: sudo $0 --domain $DOMAIN)" >&2
    exit 1
  fi
  DOMAIN="$DOMAIN" VANTAGE="$VANTAGE_ARG" RESET_DATA="$RESET" \
    bash "$REPO_ROOT/web/deploy/setup.sh"
  exit 0
fi

# Remote mode: ship this checkout over SSH, then run setup there.
# tar-over-ssh keeps the only requirements on both ends to ssh + tar.
echo "==> copying code to $HOST:$REMOTE_DIR"
tar -C "$REPO_ROOT" -czf - --exclude='.git' . \
  | ssh "$HOST" "mkdir -p '$REMOTE_DIR' && tar -xzf - -C '$REMOTE_DIR'"

echo "==> running setup on $HOST (domain: $DOMAIN)"
ssh -t "$HOST" "DOMAIN='$DOMAIN' VANTAGE='$VANTAGE_ARG' RESET_DATA='$RESET' bash '$REMOTE_DIR/web/deploy/setup.sh'"

echo "==> verifying"
sleep 3
code="$(curl -so /dev/null -w '%{http_code}' --max-time 15 "https://$DOMAIN/" || true)"
if [ "$code" = "200" ]; then
  echo "Deployed: https://$DOMAIN/ is live."
else
  echo "Site not answering over HTTPS yet (got '$code')."
  echo "If DNS for $DOMAIN was pointed at the server only recently, wait for it to propagate and re-run this script so certbot can issue the certificate."
fi
