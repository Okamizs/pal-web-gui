"""
Run once against a brand-new, EMPTY Discord server to build the full
Bachelor Pals structure (roles, categories, channels, webhook, invite) via
the bot API, then wire the results into config.json.

Requires:
  - A Discord application + bot (discord.com/developers/applications),
    invited to the target server with the "Administrator" permission
    (simplest — role hierarchy quirks otherwise make Manage Roles/Channels
    fiddly for a one-shot setup script like this).
  - DISCORD_BOT_TOKEN and DISCORD_GUILD_ID set in the environment. Never
    pass the token on the command line (shows up in shell history/ps).

    export DISCORD_BOT_TOKEN='...'
    export DISCORD_GUILD_ID='...'   # Discord Developer Mode -> right-click
                                     # server icon -> Copy Server ID
    python3 setup_discord_server.py

Re-running this against a server that already has the structure will
duplicate everything — it's a one-shot builder, not idempotent.

After it finishes: open the server and manually assign yourself the
"Admin" role (Server Settings -> Members). The bot deliberately never
assigns roles to members, to avoid role-hierarchy failures.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = 'https://discord.com/api/v10'
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'config.json'

TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
GUILD_ID = os.environ.get('DISCORD_GUILD_ID')
if not TOKEN or not GUILD_ID:
    sys.exit('Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID in the environment first.')

# Permission bit flags (Discord API v10)
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
MANAGE_MESSAGES = 1 << 13
EMBED_LINKS = 1 << 14
ATTACH_FILES = 1 << 15
READ_MESSAGE_HISTORY = 1 << 16
ADD_REACTIONS = 1 << 6
CONNECT = 1 << 20
SPEAK = 1 << 21
KICK_MEMBERS = 1 << 1
BAN_MEMBERS = 1 << 2
MANAGE_NICKNAMES = 1 << 27
MODERATE_MEMBERS = 1 << 40
ADMINISTRATOR = 1 << 3

TEXT, VOICE, CATEGORY = 0, 2, 4
ROLE, MEMBER = 0, 1


def api(method, path, payload=None):
    url = f'{API}{path}'
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': f'Bot {TOKEN}',
        'Content-Type': 'application/json',
        'User-Agent': 'BachelorPalsSetup (https://bachelorpals.com, 1.0)',
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 429:
                retry_after = json.loads(body).get('retry_after', 1)
                time.sleep(retry_after + 0.5)
                continue
            sys.exit(f'{method} {path} -> {e.code}: {body.decode(errors="replace")}')
    sys.exit(f'{method} {path} -> gave up after repeated rate limiting')


def overwrite(id_, type_, allow=0, deny=0):
    return {'id': id_, 'type': type_, 'allow': str(allow), 'deny': str(deny)}


print('Creating roles...')
moderator = api('POST', f'/guilds/{GUILD_ID}/roles', {
    'name': 'Moderator', 'color': 0x3498DB, 'hoist': True, 'mentionable': True,
    'permissions': str(KICK_MEMBERS | BAN_MEMBERS | MANAGE_MESSAGES | MODERATE_MEMBERS | MANAGE_NICKNAMES),
})
time.sleep(0.3)
admin_role = api('POST', f'/guilds/{GUILD_ID}/roles', {
    'name': 'Admin', 'color': 0xE67E22, 'hoist': True, 'mentionable': True,
    'permissions': str(ADMINISTRATOR),
})
time.sleep(0.3)

everyone = GUILD_ID  # @everyone role id == guild id

print('Creating categories...')


def make_category(name, overwrites):
    cat = api('POST', f'/guilds/{GUILD_ID}/channels', {
        'name': name, 'type': CATEGORY, 'permission_overwrites': overwrites,
    })
    time.sleep(0.3)
    return cat['id']

info_cat = make_category('INFORMATION', [
    overwrite(everyone, ROLE, allow=VIEW_CHANNEL | READ_MESSAGE_HISTORY | ADD_REACTIONS, deny=SEND_MESSAGES),
])
community_cat = make_category('COMMUNITY', [
    overwrite(everyone, ROLE, allow=VIEW_CHANNEL | SEND_MESSAGES | READ_MESSAGE_HISTORY | ADD_REACTIONS | ATTACH_FILES | EMBED_LINKS),
])
voice_cat = make_category('VOICE', [
    overwrite(everyone, ROLE, allow=VIEW_CHANNEL | CONNECT | SPEAK),
])
admin_cat = make_category('ADMIN', [
    overwrite(everyone, ROLE, deny=VIEW_CHANNEL),
    overwrite(moderator['id'], ROLE, allow=VIEW_CHANNEL | SEND_MESSAGES | READ_MESSAGE_HISTORY | ADD_REACTIONS),
])

print('Creating channels...')


def make_channel(name, parent_id, type_=TEXT, topic=None):
    payload = {'name': name, 'type': type_, 'parent_id': parent_id}
    if topic:
        payload['topic'] = topic
    ch = api('POST', f'/guilds/{GUILD_ID}/channels', payload)
    time.sleep(0.3)
    return ch['id']

welcome_ch = make_channel('welcome-rules', info_cat, topic='Read this first.')
announcements_ch = make_channel('announcements', info_cat, topic='Server updates, downtime, wipes.')
status_ch = make_channel('server-status', info_cat, topic='Live feed: joins/leaves, crashes, backups, alerts.')
connect_ch = make_channel('how-to-connect', info_cat, topic='Everything you need to join Bachelor Pals.')

make_channel('general', community_cat)
make_channel('screenshots-clips', community_cat, topic='Show off your base, your Pals, your wins.')
make_channel('suggestions', community_cat, topic='Ideas for server settings, mods, events.')

make_channel('General', voice_cat, type_=VOICE)

make_channel('admin-chat', admin_cat)

print('Posting welcome + connect info...')
welcome_msg = api('POST', f'/channels/{welcome_ch}/messages', {'content': (
    '**Welcome to Bachelor Pals.**\n\n'
    '- Server is public, no password.\n'
    '- PvP and player-to-player damage are off — you can\'t hurt other players or their Pals.\n'
    '- Chest/base looting is controlled by each player\'s own Palbox "Access Permission" setting '
    'in-game — set yours the way you want it.\n'
    '- Be decent to each other. Admins/mods can act on anyone who isn\'t.\n\n'
    'Connection info is in <#%s>. Server status/alerts post automatically in <#%s>.'
) % (connect_ch, status_ch)})
api('PUT', f'/channels/{welcome_ch}/pins/{welcome_msg["id"]}')
time.sleep(0.3)

connect_msg = api('POST', f'/channels/{connect_ch}/messages', {'content': (
    '**Bachelor Pals — how to connect**\n\n'
    'Direct connect: `play.bachelorpals.com:8211` (UDP)\n'
    'No server password.\n'
    'Server website: https://bachelorpals.com'
)})
api('PUT', f'/channels/{connect_ch}/pins/{connect_msg["id"]}')
time.sleep(0.3)

print('Creating webhook + invite...')
webhook = api('POST', f'/channels/{status_ch}/webhooks', {'name': 'Bachelor Pals Server'})
webhook_url = f'https://discord.com/api/webhooks/{webhook["id"]}/{webhook["token"]}'
time.sleep(0.3)

invite = api('POST', f'/channels/{welcome_ch}/invites', {'max_age': 0, 'max_uses': 0, 'unique': False})
invite_url = f'https://discord.gg/{invite["code"]}'

print()
print('Done. Webhook URL:', webhook_url)
print('Invite URL:', invite_url)

if CONFIG_PATH.exists():
    cfg = json.loads(CONFIG_PATH.read_text())
    cfg['discord_webhook_url'] = webhook_url
    cfg['discord_invite_url'] = invite_url
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(0o600)
    print(f'Wrote both URLs into {CONFIG_PATH}. Restart pal-web-gui.service to pick them up.')
else:
    print(f'{CONFIG_PATH} not found — set these two values manually via /admin/settings.')

print()
print('Manual step: assign yourself the "Admin" role in Server Settings -> Members.')
