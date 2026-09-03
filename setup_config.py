"""Run once to (re)generate config.json. Prints the web login password once."""
import json
import secrets
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'config.json'

RCON_HOST = '127.0.0.1'
RCON_PORT = 25575
RCON_PASSWORD = '1772'
WEB_PORT = 8211

web_password = sys.argv[1] if len(sys.argv) > 1 else secrets.token_urlsafe(12)

config = {
    'rcon_host': RCON_HOST,
    'rcon_port': RCON_PORT,
    'rcon_password': RCON_PASSWORD,
    'web_port': WEB_PORT,
    'web_password_hash': generate_password_hash(web_password),
    'secret_key': secrets.token_hex(32),
}

CONFIG_PATH.write_text(json.dumps(config, indent=2))
CONFIG_PATH.chmod(0o600)

print(f'Wrote {CONFIG_PATH}')
print(f'Web login password: {web_password}')
