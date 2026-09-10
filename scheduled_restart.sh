#!/usr/bin/env bash
cd "$HOME/pal-web-gui" || exit 1
exec .venv/bin/python -c "
import json, rcon_client, server_control
cfg = json.load(open('config.json'))
sr = cfg.get('scheduled_restart', {})
seconds = sr.get('seconds_warning', 60)
message = sr.get('message', 'Scheduled_restart').replace(' ', '_')
print(rcon_client.execute(cfg['rcon_host'], cfg['rcon_port'], cfg['rcon_password'], f'Shutdown {seconds} {message}'))
server_control.mark_intentional_restart()
"
