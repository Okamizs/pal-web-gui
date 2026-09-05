#!/usr/bin/env bash
set -euo pipefail
TOKEN_FILE="$HOME/.cloudflare_api_token"
ZONE_ID="3f039ece78657ffde8d62189d3e7cf07"
RECORD_ID="8a3a49a9fa99ff08d7aeb48ca619e611"
RECORD_NAME="play.bachelorpals.com"
LOG="$HOME/pal-web-gui/cloudflare_ddns.log"

TOKEN=$(cat "$TOKEN_FILE")
CURRENT_IP=$(curl -s --max-time 10 https://api.ipify.org)

RESPONSE=$(curl -s -X PATCH "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records/${RECORD_ID}" \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  --data "{\"content\":\"${CURRENT_IP}\"}")

SUCCESS=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('success'))")
echo "$(date -Iseconds) record=${RECORD_NAME} ip=${CURRENT_IP} success=${SUCCESS}" >> "$LOG"
