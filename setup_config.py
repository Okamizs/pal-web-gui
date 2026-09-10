"""Create or repair config.json.

Existing values are kept and only missing keys are filled in, so this is safe
to re-run on a live deployment (Discord URLs, schedules, etc. survive).

    python setup_config.py                          # fill in whatever is missing
    python setup_config.py --reset-password [PASS]  # also rotate the web login password

The RCON password must match AdminPassword in PalWorldSettings.ini. It is read
from $PAL_RCON_PASSWORD or prompted for — never hardcode it here, this file is
in a public repo.
"""
import getpass
import json
import os
import secrets
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'config.json'

config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}

config.setdefault('rcon_host', '127.0.0.1')
config.setdefault('rcon_port', 25575)
if not config.get('rcon_password'):
    config['rcon_password'] = (
        os.environ.get('PAL_RCON_PASSWORD')
        or getpass.getpass('RCON/Admin password (AdminPassword in PalWorldSettings.ini): ')
    )
config.setdefault('web_port', 8443)
if not config.get('secret_key'):
    config['secret_key'] = secrets.token_hex(32)
config.setdefault('discord_webhook_url', '')
config.setdefault('discord_invite_url', '')

web_password = None
if '--reset-password' in sys.argv or not config.get('web_password_hash'):
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    web_password = args[0] if args else secrets.token_urlsafe(12)
    config['web_password_hash'] = generate_password_hash(web_password)

CONFIG_PATH.write_text(json.dumps(config, indent=2))
CONFIG_PATH.chmod(0o600)

print(f'Wrote {CONFIG_PATH}')
if web_password:
    print(f'Web login password: {web_password}')
