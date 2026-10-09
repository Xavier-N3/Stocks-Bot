#!/usr/bin/env bash
# One-command setup on a fresh Ubuntu 22.04/24.04 server (e.g. Oracle Cloud Always Free).
#   curl -fsSL https://raw.githubusercontent.com/<you>/<repo>/main/deploy/setup_server.sh | bash -s -- https://github.com/<you>/<repo>.git
# or, after cloning:  bash deploy/setup_server.sh
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR="$HOME/stocks-bot"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y git python3 python3-venv python3-pip

if [ -n "$REPO_URL" ] && [ ! -d "$APP_DIR/.git" ]; then
  echo "==> Cloning $REPO_URL"
  git clone "$REPO_URL" "$APP_DIR"
fi
if [ -f "./run.py" ] && [ ! -d "$APP_DIR" ]; then APP_DIR="$(pwd)"; fi
cd "$APP_DIR"

echo "==> Creating the bot's Python environment"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "!!  Created .env from the template. Paste your keys into it now:  nano $APP_DIR/.env"
  echo "!!  (Alpaca paper key/secret, GROQ_API_KEY, GEMINI_API_KEY, RISK_PROFILE, STUDIO_PASSWORD)"
  echo "!!  Then run:  bash deploy/setup_server.sh   again to finish."
  exit 0
fi

echo "==> Checking connections"
.venv/bin/python selftest.py || true

echo "==> Installing the always-on service (restarts automatically, starts on boot)"
sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__USER__#$USER#g" deploy/stocksbot.service | sudo tee /etc/systemd/system/stocksbot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now stocksbot
sleep 5
sudo systemctl --no-pager status stocksbot | head -n 12

cat <<MSG

All set. Useful commands:
  sudo systemctl status stocksbot        # is it running?
  journalctl -u stocksbot -f             # live log  (or: tail -f $APP_DIR/logs/system.log)
  bash $APP_DIR/deploy/update.sh         # pull the latest code from GitHub and restart
  sudo systemctl restart stocksbot       # restart

To see the Studio from your phone/PC, follow "Reaching the Studio" in deploy/CLOUD.md
(Tailscale = private and free, or a Cloudflare Tunnel with a password).
MSG
