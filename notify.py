"""Minimal Discord webhook sender. Never raises — a notification failure must not break the caller."""
import json
import sys
import urllib.error
import urllib.request


def send_discord(webhook_url, content):
    if not webhook_url:
        print('notify.send_discord: no webhook_url configured, skipping', file=sys.stderr)
        return
    data = json.dumps({'content': content}).encode('utf-8')
    req = urllib.request.Request(
        webhook_url, data=data, method='POST', headers={
            'Content-Type': 'application/json',
            # Discord's front door (Cloudflare) blocks the default urllib UA
            # ("Python-urllib/3.x") as a known bot signature (HTTP 403,
            # cf error code 1010) — a normal-looking UA avoids that.
            'User-Agent': 'Mozilla/5.0 (compatible; BachelorPalsBot/1.0)',
        }
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        print(f'notify.send_discord: HTTP {e.code}: {e.read().decode(errors="replace")}', file=sys.stderr)
    except Exception as e:
        print(f'notify.send_discord: {type(e).__name__}: {e}', file=sys.stderr)
