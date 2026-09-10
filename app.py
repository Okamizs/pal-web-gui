import json
import secrets
import sys
import threading
import time
from functools import wraps
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for, flash
from werkzeug.security import check_password_hash

import ini_settings
import notify
import rcon_client
import server_control

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'config.json'
PLAYER_EVENTS_PATH = BASE_DIR / 'player_events.json'
FAILED_LOGIN_LOG_PATH = BASE_DIR / 'failed_logins.json'


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(0o600)


CONFIG = load_config()

app = Flask(__name__)
app.secret_key = CONFIG['secret_key']
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.permanent_session_lifetime = 60 * 60 * 12  # 12 hours

@app.context_processor
def inject_static_url():
    def static_url(filename):
        path = Path(app.static_folder) / filename
        v = int(path.stat().st_mtime) if path.exists() else 0
        return f'{url_for("static", filename=filename)}?v={v}'
    return dict(static_url=static_url)


@app.after_request
def no_cache_admin(response):
    if request.path.startswith('/admin'):
        response.headers['Cache-Control'] = 'no-store'
    return response


FAILED_LOGINS = {}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


def rcon(command):
    cfg = load_config()
    return rcon_client.execute(cfg['rcon_host'], cfg['rcon_port'], cfg['rcon_password'], command)


def client_ip():
    # Behind the Cloudflare tunnel every request arrives from cloudflared on
    # loopback, so only then is CF-Connecting-IP trustworthy: a LAN client could
    # forge the header, but it arrives from its own LAN address and is ignored.
    if request.remote_addr in ('127.0.0.1', '::1'):
        return request.headers.get('CF-Connecting-IP') or request.remote_addr
    return request.remote_addr


def _form_int(field, default, minimum):
    try:
        value = int(request.form.get(field, '').strip() or default)
    except ValueError:
        value = default
    return max(minimum, value)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(16)
    return session['csrf_token']


def check_csrf():
    token = session.get('csrf_token')
    return token and request.form.get('csrf_token') == token


# --- failed login tracking (intrusion awareness) ---

_failed_login_lock = threading.Lock()


def _load_failed_logins():
    if FAILED_LOGIN_LOG_PATH.exists():
        return json.loads(FAILED_LOGIN_LOG_PATH.read_text())
    return []


def _log_failed_login(ip):
    with _failed_login_lock:
        log = _load_failed_logins()
        log.append({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'ip': ip})
        FAILED_LOGIN_LOG_PATH.write_text(json.dumps(log[-200:], indent=2))


# --- background player join/leave tracker ---

_player_events_lock = threading.Lock()


def _load_events():
    if PLAYER_EVENTS_PATH.exists():
        return json.loads(PLAYER_EVENTS_PATH.read_text())
    return []


def _save_events(events):
    PLAYER_EVENTS_PATH.write_text(json.dumps(events[-500:], indent=2))


def _initial_known():
    # Seed "who is online" from the event log so a web-GUI restart doesn't
    # reset the baseline and silently swallow the next real join/leave.
    last = {}
    for e in _load_events():
        last[e['name']] = e['event']
    return {name for name, event in last.items() if event == 'joined'}


def _poll_players_forever():
    known = _initial_known()
    while True:
        try:
            cfg = load_config()
            raw = rcon_client.execute(cfg['rcon_host'], cfg['rcon_port'], cfg['rcon_password'], 'ShowPlayers')
            current = {p['name'] for p in parse_players(raw)}
            joined = current - known
            left = known - current
            if joined or left:
                with _player_events_lock:
                    events = _load_events()
                    now = time.strftime('%Y-%m-%d %H:%M:%S')
                    for name in joined:
                        events.append({'time': now, 'name': name, 'event': 'joined'})
                    for name in left:
                        events.append({'time': now, 'name': name, 'event': 'left'})
                    _save_events(events)
                webhook = cfg.get('discord_webhook_url')
                for name in joined:
                    notify.send_discord(webhook, f'\N{LARGE GREEN CIRCLE} **{name}** joined the server.')
                for name in left:
                    notify.send_discord(webhook, f'\N{LARGE RED CIRCLE} **{name}** left the server.')
            known = current
        except Exception as e:
            print(f'player poller: {type(e).__name__}: {e}', file=sys.stderr)
        time.sleep(60)


def parse_players(raw):
    lines = [l for l in raw.strip().splitlines() if l.strip()]
    if not lines:
        return []
    players = []
    for line in lines[1:]:
        parts = line.rsplit(',', 2)  # the name itself may contain commas
        if len(parts) >= 3:
            players.append({'name': parts[0], 'playeruid': parts[1], 'steamid': parts[2]})
    return players


threading.Thread(target=_poll_players_forever, daemon=True).start()


# --- public landing page ---

PUBLIC_CACHE_SECONDS = 15
_public_cache = {'expires': 0.0, 'data': None}
_public_cache_lock = threading.Lock()


def _public_snapshot():
    # The public page auto-refreshes every 30s for every visitor; without this
    # each one would open a fresh RCON connection against the game server.
    now = time.time()
    with _public_cache_lock:
        if _public_cache['data'] is not None and now < _public_cache['expires']:
            return _public_cache['data']
        pairs = ini_settings.parse(server_control.INI_PATH.read_text())
        try:
            players = parse_players(rcon('ShowPlayers'))
            online = True
        except rcon_client.RconError:
            players = []
            online = False
        data = {
            'server_name': ini_settings.unquote(pairs.get('ServerName', '""')),
            'description': ini_settings.unquote(pairs.get('ServerDescription', '""')),
            'max_players': pairs.get('ServerPlayerMaxNum', '32'),
            'players': players,
            'online': online,
        }
        _public_cache['data'] = data
        _public_cache['expires'] = now + PUBLIC_CACHE_SECONDS
        return data


@app.route('/')
def public_landing():
    cfg = load_config()
    return render_template(
        'public.html',
        **_public_snapshot(),
        discord_invite_url=cfg.get('discord_invite_url', ''),
        connect_host='play.bachelorpals.com',
        connect_port=8211,
    )


# --- auth ---

@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('dashboard'))

    ip = client_ip()
    count, locked_until = FAILED_LOGINS.get(ip, (0, 0))

    if request.method == 'POST':
        if time.time() < locked_until:
            wait = int(locked_until - time.time())
            flash(f'Too many failed attempts. Try again in {wait}s.', 'error')
            return render_template('login.html'), 429

        password = request.form.get('password', '')
        cfg = load_config()
        if check_password_hash(cfg['web_password_hash'], password):
            FAILED_LOGINS.pop(ip, None)
            session.clear()
            session.permanent = True
            session['authenticated'] = True
            next_url = request.args.get('next', '')
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = url_for('dashboard')
            return redirect(next_url)
        else:
            count += 1
            newly_locked = count == MAX_ATTEMPTS
            locked_until = time.time() + LOCKOUT_SECONDS if count >= MAX_ATTEMPTS else 0
            FAILED_LOGINS[ip] = (count, locked_until)
            _log_failed_login(ip)
            if newly_locked:
                notify.send_discord(
                    cfg.get('discord_webhook_url'),
                    f'\N{LOCK} Login lockout triggered for `{ip}` after {MAX_ATTEMPTS} failed attempts '
                    f'on the Pal Server admin panel.',
                )
            flash('Incorrect password.', 'error')

    return render_template('login.html')


@app.route('/admin/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- dashboard ---

@app.route('/admin')
@login_required
def dashboard():
    error = None
    players = []
    server_info = ''
    try:
        server_info = rcon('Info')
    except rcon_client.RconError as e:
        error = str(e)
    try:
        players = parse_players(rcon('ShowPlayers'))
    except rcon_client.RconError as e:
        error = error or str(e)

    return render_template(
        'dashboard.html',
        players=players,
        server_info=server_info,
        error=error,
        csrf_token=get_csrf_token(),
        active='dashboard',
    )


@app.route('/admin/action/command', methods=['POST'])
@login_required
def action_command():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('dashboard'))
    command = request.form.get('command', '').strip()
    if command:
        try:
            result = rcon(command)
            flash(f'> {command}\n{result}', 'result')
        except rcon_client.RconError as e:
            flash(str(e), 'error')
    return redirect(url_for('dashboard'))


@app.route('/admin/action/broadcast', methods=['POST'])
@login_required
def action_broadcast():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('dashboard'))
    message = request.form.get('message', '').strip()
    if message:
        try:
            rcon('Broadcast ' + message.replace(' ', '_'))
            flash('Broadcast sent.', 'result')
        except rcon_client.RconError as e:
            flash(str(e), 'error')
    return redirect(url_for('dashboard'))


@app.route('/admin/action/save', methods=['POST'])
@login_required
def action_save():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('dashboard'))
    try:
        result = rcon('Save')
        flash(f'Save: {result}', 'result')
    except rcon_client.RconError as e:
        flash(str(e), 'error')
    return redirect(url_for('dashboard'))


@app.route('/admin/action/kick', methods=['POST'])
@login_required
def action_kick():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('dashboard'))
    steamid = request.form.get('steamid', '').strip()
    if steamid:
        try:
            result = rcon(f'KickPlayer {steamid}')
            flash(f'Kick: {result}', 'result')
        except rcon_client.RconError as e:
            flash(str(e), 'error')
    return redirect(url_for('dashboard'))


@app.route('/admin/action/ban', methods=['POST'])
@login_required
def action_ban():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('dashboard'))
    steamid = request.form.get('steamid', '').strip()
    if steamid:
        try:
            result = rcon(f'BanPlayer {steamid}')
            flash(f'Ban: {result}', 'result')
        except rcon_client.RconError as e:
            flash(str(e), 'error')
    return redirect(url_for('dashboard'))


@app.route('/admin/action/shutdown', methods=['POST'])
@login_required
def action_shutdown():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('dashboard'))
    seconds = request.form.get('seconds', '60').strip()
    message = request.form.get('message', 'Server restarting').strip().replace(' ', '_')
    try:
        result = rcon(f'Shutdown {seconds} {message}')
        server_control.mark_intentional_restart()
        flash(f'Shutdown scheduled: {result}', 'result')
    except rcon_client.RconError as e:
        flash(str(e), 'error')
    return redirect(url_for('dashboard'))


# --- settings editor ---

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def settings():
    ini_path = server_control.INI_PATH
    text = ini_path.read_text()
    pairs = ini_settings.parse(text)

    if request.method == 'POST':
        if not check_csrf():
            flash('Session expired, please retry.', 'error')
            return redirect(url_for('settings'))
        ini_settings.apply_updates(pairs, request.form)
        server_control.write_ini(ini_settings.render(pairs))
        flash('Settings saved. Restart the server (Monitor page) for changes to take effect.', 'result')
        return redirect(url_for('settings'))

    fields = ini_settings.to_display(pairs)
    cfg = load_config()
    return render_template(
        'settings.html',
        fields=fields,
        discord_webhook_url=cfg.get('discord_webhook_url', ''),
        discord_invite_url=cfg.get('discord_invite_url', ''),
        csrf_token=get_csrf_token(),
        active='settings',
    )


@app.route('/admin/settings/notifications', methods=['POST'])
@login_required
def settings_notifications():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('settings'))
    cfg = load_config()
    cfg['discord_webhook_url'] = request.form.get('discord_webhook_url', '').strip()
    save_config(cfg)
    flash('Notification settings saved.', 'result')
    return redirect(url_for('settings'))


@app.route('/admin/settings/public', methods=['POST'])
@login_required
def settings_public():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('settings'))
    cfg = load_config()
    cfg['discord_invite_url'] = request.form.get('discord_invite_url', '').strip()
    save_config(cfg)
    flash('Public page settings saved.', 'result')
    return redirect(url_for('settings'))


# --- backups ---

@app.route('/admin/backups', methods=['GET'])
@login_required
def backups():
    cfg = load_config()
    return render_template(
        'backups.html',
        backups=server_control.list_backups(),
        csrf_token=get_csrf_token(),
        scheduled_backup=cfg.get('scheduled_backup', {'enabled': False, 'interval_hours': 6, 'keep': 30}),
        backup_timer=server_control.timer_status(server_control.BACKUP_TIMER_NAME),
        active='backups',
    )


@app.route('/admin/backups/create', methods=['POST'])
@login_required
def backups_create():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('backups'))
    try:
        rcon('Save')
    except rcon_client.RconError:
        pass
    try:
        name = server_control.create_backup()
        keep = load_config().get('scheduled_backup', {}).get('keep', 30)
        server_control.prune_backups(keep)
        flash(f'Created {name}', 'result')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('backups'))


@app.route('/admin/backups/restore', methods=['POST'])
@login_required
def backups_restore():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('backups'))
    name = request.form.get('name', '')
    try:
        server_control.restore_backup(name)
        flash(f'Restored {name}. Start the server from the Monitor page.', 'result')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('backups'))


@app.route('/admin/backups/schedule', methods=['POST'])
@login_required
def backups_schedule():
    if not check_csrf():
        flash('Session expired, please retry.', 'error')
        return redirect(url_for('backups'))
    enabled = request.form.get('enabled') == 'on'
    interval_hours = _form_int('interval_hours', 6, 1)
    keep = _form_int('keep', 30, 1)
    try:
        server_control.write_backup_timer(enabled, interval_hours)
    except Exception as e:
        flash(str(e), 'error')
        return redirect(url_for('backups'))
    cfg = load_config()
    cfg['scheduled_backup'] = {'enabled': enabled, 'interval_hours': interval_hours, 'keep': keep}
    save_config(cfg)
    flash('Backup schedule updated.', 'result')
    return redirect(url_for('backups'))


# --- monitor ---

def _active_lockouts():
    return [
        {'ip': ip, 'until': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(locked_until))}
        for ip, (count, locked_until) in FAILED_LOGINS.items()
        if locked_until > time.time()
    ]


@app.route('/admin/monitor/data')
@login_required
def monitor_data():
    # Polled every second by the Monitor page; ?full=1 (every 10th poll) also
    # refreshes the heavier sections so the page never needs a full reload.
    status = server_control.service_status()
    data = {
        'status': status,
        'stats': server_control.process_stats(server_control.worker_pid()) if status['active'] == 'active' else None,
        'disk_free_gb': server_control.disk_free_gb(),
    }
    if request.args.get('full'):
        data['log_text'] = server_control.recent_log(150)
        data['events'] = list(reversed(_load_events()))[:40]
        data['failed_logins'] = list(reversed(_load_failed_logins()))[:20]
        data['active_lockouts'] = _active_lockouts()
    return data


@app.route('/admin/monitor')
@login_required
def monitor():
    cfg = load_config()
    status = server_control.service_status()
    stats = server_control.process_stats(server_control.worker_pid()) if status['active'] == 'active' else None
    log_text = server_control.recent_log(150)
    events = list(reversed(_load_events()))[:40]
    failed_logins = list(reversed(_load_failed_logins()))[:20]
    active_lockouts = _active_lockouts()
    return render_template(
        'monitor.html',
        status=status,
        stats=stats,
        disk_free_gb=server_control.disk_free_gb(),
        log_text=log_text,
        events=events,
        failed_logins=failed_logins,
        active_lockouts=active_lockouts,
        csrf_token=get_csrf_token(),
        scheduled_restart=cfg.get('scheduled_restart', {'enabled': False, 'time': '04:00', 'seconds_warning': 60, 'message': 'Scheduled restart'}),
        restart_timer=server_control.timer_status(server_control.RESTART_TIMER_NAME),
        active='monitor',
    )


@app.route('/admin/server/start', methods=['POST'])
@login_required
def server_start():
    if not check_csrf():
        return redirect(url_for('monitor'))
    server_control.start_server()
    flash('Start requested.', 'result')
    return redirect(url_for('monitor'))


@app.route('/admin/server/stop', methods=['POST'])
@login_required
def server_stop():
    if not check_csrf():
        return redirect(url_for('monitor'))
    try:
        rcon('Save')
        rcon('Shutdown 5 Server_stopping')
        server_control.mark_intentional_restart()
    except rcon_client.RconError:
        pass
    time.sleep(6)
    server_control.stop_server()
    flash('Stop requested.', 'result')
    return redirect(url_for('monitor'))


@app.route('/admin/server/restart', methods=['POST'])
@login_required
def server_restart():
    if not check_csrf():
        return redirect(url_for('monitor'))
    seconds = request.form.get('seconds', '30').strip()
    message = request.form.get('message', 'Restarting').strip().replace(' ', '_')
    try:
        rcon(f'Shutdown {seconds} {message}')
        server_control.mark_intentional_restart()
        flash(f'Restart scheduled in {seconds}s; it will come back up automatically.', 'result')
    except rcon_client.RconError as e:
        flash(str(e), 'error')
    return redirect(url_for('monitor'))


@app.route('/admin/scheduled/restart', methods=['POST'])
@login_required
def scheduled_restart():
    if not check_csrf():
        return redirect(url_for('monitor'))
    enabled = request.form.get('enabled') == 'on'
    time_str = request.form.get('time', '').strip() or '04:00'
    seconds_warning = _form_int('seconds_warning', 60, 0)
    message = request.form.get('message', '').strip() or 'Scheduled restart'
    try:
        server_control.write_restart_timer(enabled, time_str)
    except Exception as e:
        flash(str(e), 'error')
        return redirect(url_for('monitor'))
    cfg = load_config()
    cfg['scheduled_restart'] = {
        'enabled': enabled, 'time': time_str,
        'seconds_warning': seconds_warning, 'message': message,
    }
    save_config(cfg)
    flash('Restart schedule updated.', 'result')
    return redirect(url_for('monitor'))


if __name__ == '__main__':
    cert = BASE_DIR / 'cert.pem'
    key = BASE_DIR / 'key.pem'
    ssl_context = (str(cert), str(key)) if cert.exists() and key.exists() else 'adhoc'
    app.run(host='0.0.0.0', port=CONFIG.get('web_port', 8443), ssl_context=ssl_context, threaded=True)
