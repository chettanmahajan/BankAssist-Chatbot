#!/usr/bin/env bash
# Banking Chatbot — one-shot EC2 deployment script for Ubuntu 22.04 t2.micro.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/deployment/deploy.sh -o deploy.sh
#   chmod +x deploy.sh
#   REPO_URL=https://github.com/<owner>/<repo>.git GROQ_API_KEY=gsk_xxx ./deploy.sh
#
# Re-runnable: skips steps that are already done.
set -euo pipefail

REPO_URL="${REPO_URL:-}"
APP_DIR="${APP_DIR:-/home/ubuntu/banking-chatbot}"
GROQ_API_KEY="${GROQ_API_KEY:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() { echo -e "\033[1;32m[deploy]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*"; }
die() { echo -e "\033[1;31m[err]\033[0m $*" >&2; exit 1; }

[[ "$EUID" -ne 0 ]] || die "Run as the 'ubuntu' user, not root. Use sudo only where needed."

if [[ -z "$REPO_URL" ]]; then
    read -r -p "Enter the GitHub repo URL (https://github.com/<owner>/<repo>.git): " REPO_URL
fi
if [[ -z "$GROQ_API_KEY" ]]; then
    read -r -s -p "Enter your GROQ_API_KEY (input hidden): " GROQ_API_KEY; echo
fi
[[ -n "$REPO_URL" ]] || die "REPO_URL is required."
[[ -n "$GROQ_API_KEY" ]] || die "GROQ_API_KEY is required."


log "1/9  Updating apt packages and installing system dependencies..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-pip git curl nginx redis-server build-essential


log "2/9  Configuring 2GB swap (t2.micro has only 1GB RAM — required for HF model load)..."
if ! sudo swapon --show | grep -q "/swapfile"; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    if ! grep -q '^/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    fi
    log "  swap file created at /swapfile (2G)"
else
    log "  swap already active"
fi


log "3/9  Enabling and starting redis-server..."
sudo systemctl enable --now redis-server


log "4/9  Cloning or updating repo at $APP_DIR ..."
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch --all
    git -C "$APP_DIR" pull --ff-only
else
    sudo mkdir -p "$(dirname "$APP_DIR")"
    sudo chown -R "$USER:$USER" "$(dirname "$APP_DIR")"
    git clone "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/logs"


log "5/9  Creating Python venv and installing requirements..."
if [[ ! -d "$APP_DIR/venv" ]]; then
    "$PYTHON_BIN" -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/frontend/requirements.txt"


log "6/9  Writing backend/.env ..."
cat > "$APP_DIR/backend/.env" <<EOF
GROQ_API_KEY=$GROQ_API_KEY
GROQ_MODEL=llama-3.3-70b-versatile
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
CHROMA_DIR=$APP_DIR/chroma_db
DATA_DIR=$APP_DIR/data
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_TTL_SECONDS=3600
RETRIEVAL_K=5
RETRIEVAL_FETCH_K=15
RETRIEVAL_LAMBDA=0.6
LOW_SCORE_THRESHOLD=0.35
API_HOST=127.0.0.1
API_PORT=8000
LOG_LEVEL=INFO
LOG_FILE=$APP_DIR/logs/app.log
EOF
chmod 600 "$APP_DIR/backend/.env"


log "7/9  Building the vector store (~3-5 minutes on t2.micro)..."
cd "$APP_DIR"
"$APP_DIR/venv/bin/python" -m backend.build_index --clean


log "8/9  Installing and starting systemd services..."
sudo cp "$APP_DIR/deployment/banking-backend.service" /etc/systemd/system/banking-backend.service
sudo cp "$APP_DIR/deployment/banking-frontend.service" /etc/systemd/system/banking-frontend.service
sudo systemctl daemon-reload
sudo systemctl enable banking-backend.service banking-frontend.service
sudo systemctl restart banking-backend.service
sudo systemctl restart banking-frontend.service


log "9/9  Configuring nginx ..."
sudo cp "$APP_DIR/deployment/nginx.conf" /etc/nginx/sites-available/banking-chatbot
sudo ln -sf /etc/nginx/sites-available/banking-chatbot /etc/nginx/sites-enabled/banking-chatbot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx


PUBLIC_IP=$(curl -fsS http://checkip.amazonaws.com 2>/dev/null || echo "<your-ec2-public-ip>")
echo
log "✓ Deployment complete!"
log "  Open in browser:  http://$PUBLIC_IP"
log "  Backend API:      http://$PUBLIC_IP/api/health"
log "  systemctl status banking-backend banking-frontend"
log "  tail -f $APP_DIR/logs/*.log"
