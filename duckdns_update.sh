#!/usr/bin/env bash
set -euo pipefail
DOMAIN="bachelorpals"
TOKEN_FILE="$HOME/.duckdns_token"
LOG="$HOME/pal-web-gui/duckdns.log"

TOKEN=$(cat "$TOKEN_FILE")

RESPONSE=$(curl -s "https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}&ip=")
echo "$(date -Iseconds) domains=${DOMAIN} response=${RESPONSE}" >> "$LOG"
