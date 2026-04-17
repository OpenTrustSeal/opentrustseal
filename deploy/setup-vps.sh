#!/usr/bin/env bash
# OpenTrustToken VPS Setup Script
# Target: Ubuntu 24.04 LTS, 2GB RAM minimum
# Run as root: bash setup-vps.sh
#
# Prerequisites:
#   - DNS: api.opentrusttoken.com -> VPS IP
#   - Fresh Ubuntu 24.04 install

set -euo pipefail

DOMAIN="api.opentrusttoken.com"
APP_USER="ott"
APP_DIR="/opt/opentrusttoken"
VENV_DIR="$APP_DIR/venv"

echo "=== OpenTrustToken VPS Setup ==="

# 1. System updates
echo "[1/8] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

# 2. Install dependencies
echo "[2/8] Installing dependencies..."
apt-get install -y -qq python3 python3-venv python3-pip nginx certbot python3-certbot-nginx ufw

# 3. Create application user
echo "[3/8] Creating application user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$APP_USER"
fi

# 4. Set up application directory
echo "[4/8] Setting up application..."
mkdir -p "$APP_DIR"/{data,keys,logs}
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# Copy server code (assumes you've rsync'd it to /tmp/ott-server/)
if [ -d /tmp/ott-server ]; then
    cp -r /tmp/ott-server/* "$APP_DIR/"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

# 5. Create venv and install deps
echo "[5/8] Installing Python dependencies..."
sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -q \
    fastapi uvicorn[standard] pynacl httpx dnspython python-whois \
    pydantic pydantic-settings

# 6. Configure nginx
echo "[6/8] Configuring nginx..."
cat > /etc/nginx/sites-available/opentrusttoken <<NGINX
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8900;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
        proxy_connect_timeout 10s;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/opentrusttoken /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# 7. Create systemd service
echo "[7/8] Creating systemd service..."
cat > /etc/systemd/system/opentrusttoken.service <<SERVICE
[Unit]
Description=OpenTrustToken API Server
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=OTT_KEY_DIR=$APP_DIR/keys
Environment=OTT_DB_PATH=$APP_DIR/data/ott.db
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host 127.0.0.1 --port 8900 --workers 2
Restart=always
RestartSec=5

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$APP_DIR/data $APP_DIR/keys $APP_DIR/logs
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable opentrusttoken
systemctl start opentrusttoken

# 8. SSL certificate
echo "[8/8] Setting up SSL..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@opentrusttoken.com --redirect || {
    echo "Certbot failed. Make sure DNS is pointed to this server."
    echo "Run manually: certbot --nginx -d $DOMAIN"
}

# Firewall
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "=== Setup Complete ==="
echo "API:    https://$DOMAIN/v1/check/{domain}"
echo "Docs:   https://$DOMAIN/docs"
echo "Health: https://$DOMAIN/health"
echo ""
echo "To deploy updates:"
echo "  rsync -avz server/ root@VPS_IP:/tmp/ott-server/"
echo "  ssh root@VPS_IP 'cp -r /tmp/ott-server/* /opt/opentrusttoken/ && systemctl restart opentrusttoken'"
