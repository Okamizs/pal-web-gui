# pal-web-gui

Password-protected web GUI for administering a Palworld dedicated server over RCON, built for a SteamOS deployment.

## Layout

- `app.py` — Flask app (auth, dashboard, settings editor, backups, monitor)
- `rcon_client.py` — minimal Source RCON protocol client
- `ini_settings.py` — round-tripping parser for `PalWorldSettings.ini`'s `OptionSettings=(...)` line; only edits a curated field allowlist, passes everything else through byte-exact
- `server_control.py` — systemctl control, resource stats, backup create/restore, timer management
- `scheduled_backup.sh` / `scheduled_restart.sh` — invoked by systemd timers
- `templates/`, `static/` — Flask templates and CSS
- `systemd/` — reference copies of the systemd user units this deploys as (`pal-web-gui.service`, `palserver.service`, `cloudflared.service`, backup/restart timers). Install by copying into `~/.config/systemd/user/` and running `systemctl --user daemon-reload`.

## Not in this repo (generated per-deployment, gitignored)

- `config.json` — RCON connection details, hashed web login password, Flask secret key, schedule settings. Generate with `setup_config.py`.
- `cert.pem` / `key.pem` — self-signed TLS cert for the origin (only needed if not fronted by a tunnel/proxy that terminates TLS itself).
- `backups/` — world save backups.
- `player_events.json`, `duckdns.log` — runtime state/logs.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python flask
.venv/bin/python setup_config.py    # prints the generated web login password once
```

Reachability in this deployment is via a Cloudflare Tunnel (`systemd/cloudflared.service`), not a router port-forward — no inbound port needs to be opened.
