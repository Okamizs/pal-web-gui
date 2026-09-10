"""Background watchdog: RCON health checks (auto-restart on sustained failure),
crash detection via systemd restart-count tracking, and disk space alerts.
Runs as its own persistent systemd service, independent of the web GUI.
"""
import json
import sys
import time
from pathlib import Path

import notify
import rcon_client
import server_control

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'config.json'

CHECK_INTERVAL_SECONDS = 60
RCON_FAILURE_THRESHOLD = 3  # consecutive failed checks before restarting
DISK_FREE_GB_THRESHOLD = 10


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def log(message):
    print(f'watchdog: {message}', file=sys.stderr)


def main():
    consecutive_failures = 0
    disk_alerted = False
    last_restart_count = server_control.restart_count()

    while True:
        cfg = load_config()
        webhook = cfg.get('discord_webhook_url')

        # A deliberately stopped server (Monitor page, backup restore) is not
        # ours to relaunch — only count RCON failures while systemd says it
        # should be running.
        if server_control.service_status()['active'] != 'active':
            consecutive_failures = 0
        else:
            try:
                rcon_client.execute(cfg['rcon_host'], cfg['rcon_port'], cfg['rcon_password'], 'Info', timeout=8)
                if consecutive_failures >= RCON_FAILURE_THRESHOLD:
                    notify.send_discord(webhook, ':white_check_mark: Palworld server is responding to RCON again.')
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                log(f'RCON check failed ({consecutive_failures}/{RCON_FAILURE_THRESHOLD}): {type(e).__name__}: {e}')
                if consecutive_failures == RCON_FAILURE_THRESHOLD:
                    notify.send_discord(
                        webhook,
                        f':rotating_light: Palworld server has not responded to RCON for '
                        f'{RCON_FAILURE_THRESHOLD * CHECK_INTERVAL_SECONDS}s — restarting it.',
                    )
                    server_control.restart_server()

        try:
            current_restart_count = server_control.restart_count()
            bumps = current_restart_count - last_restart_count
            if bumps > 0:
                # Restart=always also relaunches after an RCON `Shutdown`, so an
                # admin- or timer-triggered restart bumps NRestarts exactly like
                # a crash does. The marker file accounts for exactly one bump;
                # any further bumps in the same 60s window are real crashes.
                if server_control.consume_intentional_restart():
                    log(f'server relaunched after an intentional shutdown (restart #{current_restart_count}), not alerting')
                    bumps -= 1
                if bumps > 0:
                    extra = f', {bumps} relaunches since the last check' if bumps > 1 else ''
                    notify.send_discord(
                        webhook,
                        f':warning: Palworld server crashed and was automatically restarted by systemd '
                        f'(restart #{current_restart_count}{extra}).',
                    )
            last_restart_count = current_restart_count
        except Exception as e:
            log(f'restart-count check failed: {type(e).__name__}: {e}')

        try:
            free_gb = server_control.disk_free_gb()
            if free_gb < DISK_FREE_GB_THRESHOLD and not disk_alerted:
                notify.send_discord(webhook, f':warning: Disk space is low: {free_gb}GB free.')
                disk_alerted = True
            elif free_gb >= DISK_FREE_GB_THRESHOLD:
                disk_alerted = False
        except Exception as e:
            log(f'disk check failed: {type(e).__name__}: {e}')

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
