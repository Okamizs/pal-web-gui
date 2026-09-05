"""Minimal Discord webhook sender. Never raises — a notification failure must not break the caller."""
import json
import urllib.request


def send_discord(webhook_url, content):
    if not webhook_url:
        return
    data = json.dumps({'content': content}).encode('utf-8')
    req = urllib.request.Request(
        webhook_url, data=data, headers={'Content-Type': 'application/json'}, method='POST'
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
