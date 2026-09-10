"""Rotate the in-game AdminPassword (which is also the RCON password) and restart
the server gracefully so it takes effect.

    .venv/bin/python rotate_admin_password.py [--current PASSWORD] [--warn SECONDS]

--current  the password the running server accepts, if config.json has drifted
           from it (default: the value in config.json)
--warn     in-game countdown before the restart (default 60)

The new password is written to the live ini and to the staged copy that
palserver.service restores on boot (the game rewrites the live ini from memory
on a graceful exit). config.json is only switched once the server has actually
relaunched — until then the running server still expects the old password, so
the web GUI and watchdog must keep using it. After the relaunch the script
verifies RCON accepts the new password; on any failure BOTH the ini (live +
staged) and config.json are rolled back, so nothing is left half-rotated.
The new password is printed once.
"""
import argparse
import json
import os
import secrets
import shutil
import string
import sys
import time

import ini_settings
import rcon_client
import server_control

parser = argparse.ArgumentParser()
parser.add_argument('--current')
parser.add_argument('--warn', type=int, default=60)
args = parser.parse_args()

cfg = json.load(open('config.json'))
current_password = args.current or cfg['rcon_password']
host, port = cfg['rcon_host'], cfg['rcon_port']


def rcon(password, command):
    return rcon_client.execute(host, port, password, command, timeout=5)


def save_config(password):
    cfg['rcon_password'] = password
    with open('config.json', 'w') as f:
        json.dump(cfg, f, indent=2)
    os.chmod('config.json', 0o600)


try:
    rcon(current_password, 'Info')
except rcon_client.RconAuthError:
    sys.exit('The server rejects that password; pass the one it currently accepts with --current')

new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))

live = server_control.INI_PATH
backup = live.parent / f'PalWorldSettings.ini.bak-{time.strftime("%Y%m%d-%H%M%S")}'
shutil.copy2(live, backup)

original_text = live.read_text()
pairs = ini_settings.parse(original_text)
if ini_settings.render(pairs) != original_text:
    sys.exit('ini does not round-trip byte-exact; refusing to touch it')
pairs['AdminPassword'] = ini_settings.quote(new_password)
new_text = ini_settings.render(pairs)
if ini_settings.unquote(ini_settings.parse(new_text)['AdminPassword']) != new_password:
    sys.exit('new password did not survive a parse round-trip; refusing to write')


def rollback(reason):
    # Undo everything: staged + live ini back to the original, config.json to
    # the password the server actually accepts. Leaves no half-rotated state.
    server_control.write_ini(original_text)
    save_config(current_password)
    sys.exit(f'{reason}\nRolled back: ini (live + staged) and config.json restored; the old password is still active.')


server_control.write_ini(new_text)
print(f'ini updated and staged (backup: {backup.name})')

launched_before = server_control.main_start_timestamp()
try:
    print('rcon:', rcon(current_password,
          f'Shutdown {args.warn} Admin_password_rotation_-_server_back_in_about_2_minutes'))
except Exception as e:
    rollback(f'Could not schedule the restart: {type(e).__name__}: {e}')
server_control.mark_intentional_restart()
print(f'restart scheduled in {args.warn}s; config.json keeps the old password until the relaunch...')

deadline = time.time() + args.warn + 240
while server_control.main_start_timestamp() == launched_before:
    if time.time() > deadline:
        rollback('Server did not relaunch in time.')
    time.sleep(5)
save_config(new_password)
print('relaunched; config.json switched to the new password; waiting for RCON...')

deadline = time.time() + 180
while True:
    try:
        rcon(new_password, 'Info')
        break
    except rcon_client.RconAuthError:
        rollback('The relaunched server still rejects the new password — did the ExecStartPre staged-ini '
                 'restore run? (check palserver.service)')
    except Exception:
        if time.time() > deadline:
            rollback('RCON never came up after the relaunch.')
        time.sleep(5)

print()
print(f'New admin/RCON password: {new_password}')
print('Verified: the relaunched server accepts it. Store it somewhere safe.')
