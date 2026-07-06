#!/usr/bin/env bash
# Idempotent server setup / redeploy for the stratumrace.com leaderboard.
#
# Run as root on a fresh Debian/Ubuntu host from a checkout of this repo:
#   sudo DOMAIN=stratumrace.com ./web/deploy/setup.sh
#
# Re-running updates code, refreshes the web root, and restarts services.
# No credentials live in this script or anywhere in the repo.
set -euo pipefail

DOMAIN="${DOMAIN:-stratumrace.com}"
REPO_DIR="${REPO_DIR:-/opt/stratum-race}"
WEB_ROOT="${WEB_ROOT:-/var/www/stratumrace}"
DATA_DIR="${DATA_DIR:-/var/lib/stratum-race/sessions}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx python3 rsync certbot python3-certbot-nginx >/dev/null

echo "==> creating service user + directories"
id -u stratumrace >/dev/null 2>&1 || useradd --system --home /var/lib/stratum-race --shell /usr/sbin/nologin stratumrace
mkdir -p "$REPO_DIR" "$WEB_ROOT/data" "$DATA_DIR" /etc/stratum-race

echo "==> syncing code to $REPO_DIR"
if [ "$SRC_DIR" != "$REPO_DIR" ]; then
  rsync -a --delete --exclude '.git' "$SRC_DIR/" "$REPO_DIR/"
fi
chmod +x "$REPO_DIR/web/run_races.sh" "$REPO_DIR/web/deploy/setup.sh"

echo "==> publishing site to $WEB_ROOT"
rsync -a --delete --exclude 'data' "$REPO_DIR/web/site/" "$WEB_ROOT/"
mkdir -p "$WEB_ROOT/data"
chown -R stratumrace:stratumrace "$DATA_DIR" /var/lib/stratum-race "$WEB_ROOT/data"

if [ "${RESET_DATA:-no}" = "yes" ]; then
  echo "==> RESET_DATA=yes: wiping collected race sessions"
  systemctl stop stratum-racer 2>/dev/null || true
  rm -f "$DATA_DIR"/session-*.json
fi

echo "==> writing racer environment"
# Managed values are rewritten on every deploy. A VANTAGE env var wins,
# then an operator-customized value in racer.env is preserved; auto-detected
# values (including the older "cloud server (...)" format) are regenerated
# with a human-readable city.
VANTAGE_LINE="$(grep '^VANTAGE=' /etc/stratum-race/racer.env 2>/dev/null || true)"
case "$VANTAGE_LINE" in
  ""|"VANTAGE=cloud server ("*) VANTAGE_LINE="" ;;
esac
if [ -n "${VANTAGE:-}" ]; then
  VANTAGE_LINE="VANTAGE=${VANTAGE}"
fi
if [ -z "$VANTAGE_LINE" ]; then
  REGION="$(curl -fsm 3 http://169.254.169.254/metadata/v1/region 2>/dev/null || true)"
  if [ -n "$REGION" ]; then
    case "$REGION" in
      sfo*) CITY="San Francisco, US" ;;
      nyc*) CITY="New York, US" ;;
      ams*) CITY="Amsterdam, NL" ;;
      fra*) CITY="Frankfurt, DE" ;;
      lon*) CITY="London, UK" ;;
      sgp*) CITY="Singapore" ;;
      blr*) CITY="Bangalore, IN" ;;
      tor*) CITY="Toronto, CA" ;;
      syd*) CITY="Sydney, AU" ;;
      *)    CITY="cloud region ${REGION}" ;;
    esac
    VANTAGE_LINE="VANTAGE=${CITY} (DigitalOcean ${REGION})"
  fi
fi
{
  echo "SESSION_SECS=900"
  echo "FIRST_SESSION_SECS=600"
  echo "KEEP_DAYS=14"
  [ -n "$VANTAGE_LINE" ] && echo "$VANTAGE_LINE"
} > /etc/stratum-race/racer.env

echo "==> seeding leaderboard.json"
sudo -u stratumrace python3 "$REPO_DIR/web/aggregate.py" \
  --sessions "$DATA_DIR" --out "$WEB_ROOT/data/leaderboard.json" \
  --vantage "$(grep -oP '(?<=^VANTAGE=).*' /etc/stratum-race/racer.env 2>/dev/null || true)"

echo "==> installing systemd service"
cp "$REPO_DIR/web/deploy/stratum-racer.service" /etc/systemd/system/stratum-racer.service
systemctl daemon-reload
systemctl enable stratum-racer.service
systemctl restart stratum-racer.service

if [ "${WITH_PROXY:-no}" = "yes" ]; then
  echo "==> installing measurement proxy (miners point at port ${PROXY_PORT:-3333})"
  if [ "${RESET_ROTATION:-no}" = "yes" ]; then
    echo "==> RESET_ROTATION=yes: rotation restarts at the first pool"
    rm -f /var/lib/stratum-race/proxy-rotation.state
  fi
  mkdir -p /var/lib/stratum-race/active
  chown -R stratumrace:stratumrace /var/lib/stratum-race
  if [ ! -f /etc/stratum-race/proxy.env ]; then
    {
      echo "PROXY_PORT=${PROXY_PORT:-3333}"
      echo "RACES_PER_POOL=20"
      echo "MAX_MINUTES=300"
      echo "PROXY_EXTRA_ARGS="
    } > /etc/stratum-race/proxy.env
  fi
  cp "$REPO_DIR/web/deploy/stratum-proxy.service" /etc/systemd/system/stratum-proxy.service
  systemctl daemon-reload
  systemctl enable stratum-proxy.service
  systemctl restart stratum-proxy.service
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "${PROXY_PORT:-3333}/tcp" >/dev/null || true
  fi
fi

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "==> opening firewall ports 80/443"
  ufw allow 80/tcp >/dev/null || true
  ufw allow 443/tcp >/dev/null || true
fi

echo "==> configuring nginx for $DOMAIN"
# Write the vhost only on first install: certbot rewrites this file to add
# TLS, and regenerating it on redeploy would clobber the 443 server block.
NGINX_CONF=/etc/nginx/sites-available/stratumrace.conf
if [ ! -f "$NGINX_CONF" ]; then
  sed "s/stratumrace\.com/${DOMAIN}/g" \
    "$REPO_DIR/web/deploy/nginx-stratumrace.conf" > "$NGINX_CONF"
fi
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/stratumrace.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> requesting TLS certificate (best effort)"
if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "DOMAIN is a bare IP — skipping certbot; site stays HTTP-only"
elif certbot certificates 2>/dev/null | grep -q "Domains:.*${DOMAIN}"; then
  echo "certificate already present"
  if ! grep -q "listen 443" "$NGINX_CONF"; then
    echo "re-attaching existing certificate to nginx config"
    certbot install --nginx --cert-name "$DOMAIN" --non-interactive --redirect \
      || echo "WARNING: certbot install failed — site may be HTTP-only"
  fi
else
  certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email \
    -d "$DOMAIN" -d "www.${DOMAIN}" --redirect \
  || certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email \
    -d "$DOMAIN" --redirect \
  || echo "WARNING: certbot failed — site remains HTTP-only for now"
fi

echo "==> done"
systemctl --no-pager --lines 5 status stratum-racer.service || true
echo "Site: http://${DOMAIN}/ (https if certbot succeeded)"
