import datetime
import glob
import os
import re
import subprocess
import tarfile
import threading
import time
from pathlib import Path

PALSERVER_ROOT = Path.home() / '.local/share/Steam/steamapps/common/PalServer'
INI_PATH = PALSERVER_ROOT / 'Pal/Saved/Config/LinuxServer/PalWorldSettings.ini'
WORKER_BINARY_MATCH = 'Pal/Binaries/Linux/PalServer-Linux-Shipping'
BACKUPS_DIR = Path.home() / 'pal-web-gui/backups'
SYSTEMD_USER_DIR = Path.home() / '.config/systemd/user'
UNIT_NAME = 'palserver.service'
RESTART_TIMER_NAME = 'palserver-restart.timer'
BACKUP_TIMER_NAME = 'palserver-backup.timer'
INTENTIONAL_RESTART_MARKER = Path.home() / 'pal-web-gui/.intentional_restart'
INTENTIONAL_RESTART_MAX_AGE_SECONDS = 600
# The game rewrites the live ini from memory on a graceful exit (RCON Shutdown),
# undoing any edit made while it was running. palserver.service's ExecStartPre
# copies this staged file back over the live one right before each boot.
STAGED_INI = Path.home() / 'pal-web-gui/PalWorldSettings.desired.ini'


def write_ini(text):
    INI_PATH.write_text(text)
    STAGED_INI.write_text(text)
    STAGED_INI.chmod(0o600)


def _show_properties(unit, *props):
    """One systemctl call for several properties -> dict, instead of one call each."""
    args = ['systemctl', '--user', 'show', unit]
    for p in props:
        args += ['-p', p]
    result = _run(args)
    out = {}
    for line in result.stdout.strip().splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            out[k] = v
    return out


def _run(cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def save_dir():
    matches = glob.glob(str(PALSERVER_ROOT / 'Pal/Saved/SaveGames/0/*/'))
    return Path(matches[0]) if matches else None


# --- systemd control for the game server itself ---

def systemctl(*args):
    return _run(['systemctl', '--user', *args])


def service_status():
    props = _show_properties(UNIT_NAME, 'ActiveState', 'UnitFileState', 'MainPID')
    active = props.get('ActiveState', 'unknown')
    enabled = props.get('UnitFileState', 'unknown')
    pid = None
    if active == 'active':
        pid_str = props.get('MainPID', '0')
        if pid_str and pid_str != '0':
            pid = int(pid_str)
    return {'active': active, 'enabled': enabled, 'pid': pid}


def restart_count():
    """How many times systemd has auto-restarted the game server (crash detection)."""
    props = _show_properties(UNIT_NAME, 'NRestarts')
    return int(props.get('NRestarts', 0))


def main_start_timestamp():
    """Changes whenever the game process is (re)launched."""
    return _show_properties(UNIT_NAME, 'ExecMainStartTimestamp').get('ExecMainStartTimestamp', '')


_worker_pid_cache = None


def worker_pid():
    """PID of the actual game binary, not the launcher shell script systemd tracks as
    MainPID (PalServer.sh runs it as a child instead of exec'ing into it).
    Cached and re-validated against /proc so per-second polling doesn't fork pgrep."""
    global _worker_pid_cache
    pid = _worker_pid_cache
    if pid:
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                if WORKER_BINARY_MATCH.encode() in f.read():
                    return pid
        except OSError:
            pass
    result = _run(['pgrep', '-f', WORKER_BINARY_MATCH])
    if result.returncode == 0 and result.stdout.strip():
        _worker_pid_cache = int(result.stdout.strip().splitlines()[0])
    else:
        _worker_pid_cache = None
    return _worker_pid_cache


def start_server():
    return systemctl('start', UNIT_NAME)


def stop_server():
    # --no-block: a hung server can take up to TimeoutStopSec (90s) to die,
    # longer than gunicorn's worker timeout.
    return systemctl('stop', '--no-block', UNIT_NAME)


def restart_server():
    return systemctl('restart', UNIT_NAME)


def mark_intentional_restart():
    """Call right after a successful RCON `Shutdown` so the watchdog doesn't
    report the resulting Restart=always relaunch as a crash."""
    INTENTIONAL_RESTART_MARKER.touch()


def consume_intentional_restart():
    """True if an intentional shutdown was flagged recently; clears the flag."""
    try:
        age = time.time() - INTENTIONAL_RESTART_MARKER.stat().st_mtime
    except FileNotFoundError:
        return False
    INTENTIONAL_RESTART_MARKER.unlink(missing_ok=True)
    return age < INTENTIONAL_RESTART_MAX_AGE_SECONDS


def recent_log(lines=100):
    result = _run(['journalctl', '--user', '-u', UNIT_NAME, '-n', str(lines), '--no-pager', '-o', 'short-iso'])
    return result.stdout


# --- resource monitoring ---

_CLK_TCK = os.sysconf('SC_CLK_TCK')
_cpu_sample_lock = threading.Lock()
_cpu_sample = None  # (pid, monotonic time, cpu ticks) from the previous call


def _mem_total_kb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemTotal:'):
                return int(line.split()[1])
    return None


def _format_elapsed(seconds):
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    clock = f'{hours:02}:{minutes:02}:{secs:02}'
    return f'{days}d {clock}' if days else clock


def process_stats(pid):
    """Read straight from /proc (no subprocess) so this is cheap to poll every
    second. CPU% is the delta since the previous call — an instantaneous figure
    like top's — falling back to the lifetime average on the first call."""
    global _cpu_sample
    if not pid:
        return None
    try:
        with open(f'/proc/{pid}/stat') as f:
            stat = f.read()
        with open(f'/proc/{pid}/status') as f:
            status = f.read()
        with open('/proc/uptime') as f:
            uptime = float(f.read().split()[0])
    except OSError:
        return None

    fields = stat[stat.rindex(')') + 2:].split()  # everything after "pid (comm) "
    cpu_ticks = int(fields[11]) + int(fields[12])  # utime + stime
    started = int(fields[19]) / _CLK_TCK          # starttime, seconds since boot
    elapsed = max(uptime - started, 0.0)
    now = time.monotonic()

    with _cpu_sample_lock:
        previous = _cpu_sample
        _cpu_sample = (pid, now, cpu_ticks)
    if previous and previous[0] == pid and now - previous[1] > 0.2:
        cpu_pct = (cpu_ticks - previous[2]) / _CLK_TCK / (now - previous[1]) * 100
    else:
        cpu_pct = (cpu_ticks / _CLK_TCK) / elapsed * 100 if elapsed > 0 else 0.0

    rss_kb = 0
    for line in status.splitlines():
        if line.startswith('VmRSS:'):
            rss_kb = int(line.split()[1])
            break
    total_kb = _mem_total_kb()
    mem_pct = rss_kb / total_kb * 100 if total_kb else 0.0

    return {
        'cpu_pct': f'{cpu_pct:.1f}',
        'mem_pct': f'{mem_pct:.1f}',
        'mem_mb': round(rss_kb / 1024),
        'elapsed': _format_elapsed(elapsed),
    }


def disk_free_gb():
    st = os.statvfs(str(Path.home()))
    return round(st.f_bavail * st.f_frsize / (1024 ** 3), 1)


# --- backups ---

def list_backups():
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(BACKUPS_DIR.glob('*.tar.gz'), reverse=True):
        stat = f.stat()
        items.append({
            'name': f.name,
            'size_mb': round(stat.st_size / (1024 ** 2), 2),
            'mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime)),
        })
    return items


def create_backup():
    src = save_dir()
    if not src:
        raise RuntimeError('Could not find a save folder to back up')
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    name = f'backup-{time.strftime("%Y%m%d-%H%M%S")}.tar.gz'
    dest = BACKUPS_DIR / name

    def exclude_internal_backups(tarinfo):
        # Palworld's own bIsUseBackupSaveData rotation churns this dir constantly;
        # it's redundant with our own backups and races with the archive walk.
        if '/backup/' in f'/{tarinfo.name}/':
            return None
        return tarinfo

    with tarfile.open(dest, 'w:gz') as tar:
        tar.add(src, arcname=src.name, filter=exclude_internal_backups)
    return name


def prune_backups(keep):
    backups = sorted(BACKUPS_DIR.glob('*.tar.gz'), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in backups[keep:]:
        f.unlink()


def restore_backup(name):
    status = service_status()
    if status['active'] not in ('inactive', 'failed'):
        raise RuntimeError(f"Server must be fully stopped before restoring a backup (state: {status['active']})")
    archive = BACKUPS_DIR / name
    if not archive.exists() or archive.parent != BACKUPS_DIR:
        raise RuntimeError('Backup not found')
    target_parent = PALSERVER_ROOT / 'Pal/Saved/SaveGames/0'
    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(target_parent, filter='data')


# --- scheduled restart timer (writes a systemd OnCalendar timer) ---

def write_restart_timer(enabled, time_str):
    """time_str is 'HH:MM' (a trailing ':SS' from some browsers is ignored)."""
    m = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)(?::\d{2})?', time_str)
    if not m:
        raise ValueError(f'Restart time must be HH:MM, got {time_str!r}')
    hh, mm = m.group(1), m.group(2)
    unit_path = SYSTEMD_USER_DIR / RESTART_TIMER_NAME
    service_path = SYSTEMD_USER_DIR / 'palserver-restart.service'
    service_path.write_text(
        '[Unit]\n'
        'Description=Scheduled Palworld server restart\n\n'
        '[Service]\n'
        'Type=oneshot\n'
        f'ExecStart={Path.home()}/pal-web-gui/scheduled_restart.sh\n'
    )
    unit_path.write_text(
        '[Unit]\n'
        'Description=Trigger scheduled Palworld restart\n\n'
        '[Timer]\n'
        f'OnCalendar=*-*-* {hh}:{mm}:00\n'
        'Persistent=false\n\n'
        '[Install]\n'
        'WantedBy=timers.target\n'
    )
    _run(['systemctl', '--user', 'daemon-reload'])
    if enabled:
        systemctl('enable', '--now', RESTART_TIMER_NAME)
    else:
        systemctl('disable', '--now', RESTART_TIMER_NAME)


def write_backup_timer(enabled, interval_hours):
    unit_path = SYSTEMD_USER_DIR / BACKUP_TIMER_NAME
    service_path = SYSTEMD_USER_DIR / 'palserver-backup.service'
    service_path.write_text(
        '[Unit]\n'
        'Description=Scheduled Palworld world backup\n\n'
        '[Service]\n'
        'Type=oneshot\n'
        f'ExecStart={Path.home()}/pal-web-gui/scheduled_backup.sh\n'
    )
    unit_path.write_text(
        '[Unit]\n'
        'Description=Trigger scheduled Palworld backup\n\n'
        '[Timer]\n'
        f'OnBootSec=10min\n'
        f'OnUnitActiveSec={interval_hours}h\n'
        'Persistent=true\n\n'
        '[Install]\n'
        'WantedBy=timers.target\n'
    )
    _run(['systemctl', '--user', 'daemon-reload'])
    if enabled:
        systemctl('enable', '--now', BACKUP_TIMER_NAME)
    else:
        systemctl('disable', '--now', BACKUP_TIMER_NAME)


def timer_status(name):
    props = _show_properties(name, 'ActiveState', 'UnitFileState', 'NextElapseUSecRealtime')
    active = props.get('ActiveState', 'unknown')
    enabled = props.get('UnitFileState', 'unknown')
    next_run = ''
    next_val = props.get('NextElapseUSecRealtime', '')
    if next_val and next_val not in ('0', 'n/a'):
        if next_val.isdigit():
            next_run = datetime.datetime.fromtimestamp(int(next_val) / 1_000_000).strftime('%Y-%m-%d %H:%M')
        else:
            next_run = next_val  # already a human-readable timestamp on this systemd version
    return {'active': active, 'enabled': enabled, 'next_run': next_run}
