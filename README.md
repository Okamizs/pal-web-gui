# pal-web-gui

Password-protected web GUI for administering a Palworld dedicated server over RCON, built for a SteamOS deployment.

## Layout

- `app.py` — Flask app (auth, dashboard, settings editor, backups, monitor)
- `rcon_client.py` — minimal Source RCON protocol client
- `ini_settings.py` — round-tripping parser for `PalWorldSettings.ini`'s `OptionSettings=(...)` line; only edits a curated field allowlist, passes everything else through byte-exact
- `server_control.py` — systemctl control, resource stats, backup create/restore, timer management
- `watchdog.py` — separate service: RCON health checks with auto-restart, crash detection, disk alerts
- `notify.py` — Discord webhook sender (never raises; failures are logged to stderr)
- `scheduled_backup.sh` / `scheduled_restart.sh` — invoked by systemd timers
- `setup_discord_server.py` — one-shot builder for the community Discord server (roles, channels, webhook, invite)
- `templates/`, `static/` — Flask templates and CSS
- `systemd/` — reference copies of the systemd user units this deploys as (`pal-web-gui.service`, `palserver.service`, `cloudflared.service`, backup/restart timers). Install by copying into `~/.config/systemd/user/` and running `systemctl --user daemon-reload`.

## Not in this repo (generated per-deployment, gitignored)

- `config.json` — RCON connection details, hashed web login password, Flask secret key, Discord URLs, schedule settings. Create or repair with `setup_config.py` (it keeps existing values and only fills in what's missing).
- `cert.pem` / `key.pem` — self-signed TLS cert for the origin (only needed if not fronted by a tunnel/proxy that terminates TLS itself).
- `backups/` — world save backups.
- `player_events.json`, `failed_logins.json`, `cloudflare_ddns.log`, `.intentional_restart` — runtime state/logs.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
PAL_RCON_PASSWORD='<AdminPassword from PalWorldSettings.ini>' .venv/bin/python setup_config.py
# prints the generated web login password once; re-run with --reset-password to rotate it
```

Then install the units from `systemd/` into `~/.config/systemd/user/`, `systemctl --user daemon-reload`, and enable `pal-web-gui.service` and `watchdog.service`.

## Server-side mods (UE4SS, native Linux)

The game server stays the stock native Linux binary; mods load through
[UE4SS](https://github.com/BlackBookOfficial/ue4ss-linux-palworld) via a single
`Environment=LD_PRELOAD=…/libUE4SS.so` line in `systemd/palserver.service`.
Delete that line and restart to run unmodded.

- `install_ue4ss_linux.sh` installs UE4SS next to `PalServer.sh` with the
  cheat/console mods disabled and the headless-server settings fix applied
  (the tagged release's shipped settings crash a server with no display).
- The tagged release cannot enumerate existing game objects on this binary
  (upstream issues #4/#10, fixed on the `linux-native` branch but never
  released), so the `.so` is built locally from that branch and installed with
  `UE4SS_SO=<path> ./install_ue4ss_linux.sh`.
- Mods live in `ue4ss-mods/` here and are copied to `PalServer/Mods/<Name>/`;
  enable them in `Mods/mods.txt`. `BiggerBaseArea` enlarges every base's build
  radius by 1.25x — server-side only, so console players get it too (the
  client still draws the boundary circle at the vanilla radius).
- After a Palworld update, check `PalServer/UE4SS.log`: address resolution
  lines, a `506/506` vtable sweep and the mods' own output mean it's fine; a
  `Signal=6` in the journal means disable the line and rebuild.

Reachability in this deployment is via a Cloudflare Tunnel (`systemd/cloudflared.service`), not a router port-forward — no inbound port needs to be opened.
