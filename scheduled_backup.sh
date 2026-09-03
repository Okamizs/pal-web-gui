#!/usr/bin/env bash
cd "$HOME/pal-web-gui" || exit 1
exec .venv/bin/python -c "
import json, rcon_client, server_control
cfg = json.load(open('config.json'))
try:
    rcon_client.execute(cfg['rcon_host'], cfg['rcon_port'], cfg['rcon_password'], 'Save')
except Exception as e:
    print(f'Save before backup failed (continuing): {e}')
name = server_control.create_backup()
keep = cfg.get('scheduled_backup', {}).get('keep', 30)
server_control.prune_backups(keep)
print(f'Backed up: {name}')
"
