import datetime
import glob
import os
import subprocess
import tarfile
import time
from pathlib import Path

PALSERVER_ROOT = Path.home() / '.local/share/Steam/steamapps/common/PalServer'
INI_PATH = PALSERVER_ROOT / 'Pal/Saved/Config/LinuxServer/PalWorldSettings.ini'
BACKUPS_DIR = Path.home() / 'pal-web-gui/backups'
SYSTEMD_USER_DIR = Path.home() / '.config/systemd/user'
UNIT_NAME = 'palserver.service'
RESTART_TIMER_NAME = 'palserver-restart.timer'
BACKUP_TIMER_NAME = 'palserver-backup.timer'
WORKER_BINARY_MATCH = 'Pal/Binaries/Linux/PalServer-Linux-Shipping'


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


def worker_pid():
    """PID of the actual game binary, not the launcher shell script systemd tracks as
    MainPID (PalServer.sh runs it as a child instead of exec'ing into it)."""
    result = _run(['pgrep', '-f', WORKER_BINARY_MATCH])
    if result.returncode == 0 and result.stdout.strip():
        return int(result.stdout.strip().splitlines()[0])
    return None


def start_server():
    return systemctl('start', UNIT_NAME)


def stop_server():
    return systemctl('stop', UNIT_NAME)


def restart_server():
    return systemctl('restart', UNIT_NAME)


def recent_log(lines=100):
    result = _run(['journalctl', '--user', '-u', UNIT_NAME, '-n', str(lines), '--no-pager', '-o', 'short-iso'])
    return result.stdout


# --- resource monitoring ---

def process_stats(pid):
    if not pid:
        return None
    result = _run(['ps', '-p', str(pid), '-o', '%cpu,%mem,etime', '--no-headers'])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    parts = result.stdout.split()
    if len(parts) < 3:
        return None
    return {'cpu_pct': parts[0], 'mem_pct': parts[1], 'elapsed': parts[2]}


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
    if status['active'] == 'active':
        raise RuntimeError('Stop the server before restoring a backup')
    archive = BACKUPS_DIR / name
    if not archive.exists() or archive.parent != BACKUPS_DIR:
        raise RuntimeError('Backup not found')
    target_parent = PALSERVER_ROOT / 'Pal/Saved/SaveGames/0'
    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(target_parent, filter='data')


# --- scheduled restart timer (writes a systemd OnCalendar timer) ---

def write_restart_timer(enabled, time_str):
    """time_str is 'HH:MM'."""
    hh, mm = time_str.split(':')
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
