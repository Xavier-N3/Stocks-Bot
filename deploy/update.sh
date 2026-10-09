#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the bots (your .env and data are untouched).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart stocksbot
echo "Updated to $(git log -1 --format='%h %s') and restarted."
