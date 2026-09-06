import json
import secrets
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


def _poll_players_forever():
    known = set()
    first_poll = True
    while True:
        try:
            cfg = load_config()
            raw = rcon_client.execute(cfg['rcon_host'], cfg['rcon_port'], cfg['rcon_password'], 'ShowPlayers')
            current = {p['name'] for p in parse_players(raw)}
            if not first_poll:
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
            first_poll = False
        except Exception:
            pass
        time.sleep(60)


def parse_players(raw):
    lines = [l for l in raw.strip().splitlines() if l.strip()]
    if not lines:
        return []
    players = []
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) >= 3:
            players.append({'name': parts[0], 'playeruid': parts[1], 'steamid': parts[2]})
    return players


threading.Thread(target=_poll_players_forever, daemon=True).start()


# --- public landing page ---

@app.route('/')
def public_landing():
    cfg = load_config()
    pairs = ini_settings.parse(server_control.INI_PATH.read_text())
    server_name = pairs.get('ServerName', '""').strip('"')
    description = pairs.get('ServerDescription', '""').strip('"')
    max_players = pairs.get('ServerPlayerMaxNum', '32')

    try:
        players = parse_players(rcon('ShowPlayers'))
        online = True
    except rcon_client.RconError:
        players = []
        online = False

    return render_template(
        'public.html',
        server_name=server_name,
        description=description,
        max_players=max_players,
        players=players,
        online=online,
        discord_invite_url=cfg.get('discord_invite_url', ''),
        connect_host='play.bachelorpals.com',
        connect_port=8211,
    )


# --- auth ---

@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('dashboard'))

    ip = request.remote_addr
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
            next_url = request.args.get('next') or url_for('dashboard')
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
        ini_path.write_text(ini_settings.render(pairs))
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
    cfg = load_config()
    enabled = request.form.get('enabled') == 'on'
    interval_hours = int(request.form.get('interval_hours', 6))
    keep = int(request.form.get('keep', 30))
    cfg['scheduled_backup'] = {'enabled': enabled, 'interval_hours': interval_hours, 'keep': keep}
    save_config(cfg)
    try:
        server_control.write_backup_timer(enabled, interval_hours)
        flash('Backup schedule updated.', 'result')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('backups'))


# --- monitor ---

@app.route('/admin/monitor')
@login_required
def monitor():
    cfg = load_config()
    status = server_control.service_status()
    stats = server_control.process_stats(server_control.worker_pid()) if status['active'] == 'active' else None
    log_text = server_control.recent_log(150)
    events = list(reversed(_load_events()))[:40]
    failed_logins = list(reversed(_load_failed_logins()))[:20]
    active_lockouts = [
        {'ip': ip, 'until': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(locked_until))}
        for ip, (count, locked_until) in FAILED_LOGINS.items()
        if locked_until > time.time()
    ]
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
    except rcon_client.RconError:
        pass
    time.sleep(6)
    server_control.stop_server()
    flash('Server stopped.', 'result')
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
        flash(f'Restart scheduled in {seconds}s; it will come back up automatically.', 'result')
    except rcon_client.RconError as e:
        flash(str(e), 'error')
    return redirect(url_for('monitor'))


@app.route('/admin/scheduled/restart', methods=['POST'])
@login_required
def scheduled_restart():
    if not check_csrf():
        return redirect(url_for('monitor'))
    cfg = load_config()
    enabled = request.form.get('enabled') == 'on'
    time_str = request.form.get('time', '04:00')
    seconds_warning = int(request.form.get('seconds_warning', 60))
    message = request.form.get('message', 'Scheduled restart')
    cfg['scheduled_restart'] = {
        'enabled': enabled, 'time': time_str,
        'seconds_warning': seconds_warning, 'message': message,
    }
    save_config(cfg)
    try:
        server_control.write_restart_timer(enabled, time_str)
        flash('Restart schedule updated.', 'result')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('monitor'))


if __name__ == '__main__':
    cert = BASE_DIR / 'cert.pem'
    key = BASE_DIR / 'key.pem'
    ssl_context = (str(cert), str(key)) if cert.exists() and key.exists() else 'adhoc'
    app.run(host='0.0.0.0', port=CONFIG.get('web_port', 8443), ssl_context=ssl_context, threaded=True)
