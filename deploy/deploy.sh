#!/usr/bin/env bash
# Quick deploy script: push code to VPS and restart
# Usage: bash deploy.sh user@vps-ip

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 user@vps-ip"
    exit 1
fi

VPS="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$(dirname "$SCRIPT_DIR")/server"

echo "Deploying OpenTrustToken to $VPS..."

# Sync server code (exclude venv, data, keys, pycache)
rsync -avz --delete \
    --exclude='venv/' \
    --exclude='data/' \
    --exclude='keys/' \
    --exclude='logs/' \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    "$SERVER_DIR/" "$VPS:/opt/opentrusttoken/"

# Make sure runtime dirs the systemd unit relies on still exist. rsync
# --delete with server-side state wipes any path that isn't in the source
# tree, so runtime-only directories must be recreated here.
ssh "$VPS" "mkdir -p /opt/opentrusttoken/logs && chown -R ott:ott /opt/opentrusttoken/logs"

# Restart service
ssh "$VPS" "systemctl restart opentrusttoken && echo 'Service restarted'"

# Health check
echo "Waiting for startup..."
sleep 3
ssh "$VPS" "curl -sf http://127.0.0.1:8900/health && echo ' OK'"

echo "Deploy complete."
