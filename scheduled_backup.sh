#!/usr/bin/env bash
cd "$HOME/pal-web-gui" || exit 1
exec .venv/bin/python -c "
import json, rcon_client, server_control, notify
cfg = json.load(open('config.json'))
webhook = cfg.get('discord_webhook_url')
try:
    rcon_client.execute(cfg['rcon_host'], cfg['rcon_port'], cfg['rcon_password'], 'Save')
except Exception as e:
    print(f'Save before backup failed (continuing): {e}')
try:
    name = server_control.create_backup()
    keep = cfg.get('scheduled_backup', {}).get('keep', 30)
    server_control.prune_backups(keep)
    print(f'Backed up: {name}')
except Exception as e:
    notify.send_discord(webhook, f'\N{WARNING SIGN} Scheduled Palworld backup failed: {e}')
    raise
"
