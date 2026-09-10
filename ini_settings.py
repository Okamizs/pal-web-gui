"""Round-tripping parser for PalWorldSettings.ini's single OptionSettings=(...) line.

Every key is kept as a raw (unparsed) string value so fields we don't expose in the
UI pass through byte-for-byte unchanged. Only the curated fields below are actually
edited/typed.
"""
import re
from collections import OrderedDict

HEADER = '[/Script/Pal.PalGameWorldSettings]'

# field_name -> ('float' | 'int' | 'bool' | 'string', label)
EDITABLE_FIELDS = OrderedDict([
    ('ServerName', ('string', 'Server name')),
    ('ServerDescription', ('string', 'Server description')),
    ('ServerPassword', ('string', 'Server password (blank = open)')),
    # AdminPassword is deliberately NOT editable here: it is also the RCON
    # password in config.json, and the two must change together with a server
    # restart in between. Use rotate_admin_password.py.
    ('Difficulty', ('rawtext', 'Difficulty (advanced, see Palworld wiki for valid values)')),
    ('DeathPenalty', ('rawtext', 'Death penalty (None / Item / ItemAndEquipment / All)')),
    ('DayTimeSpeedRate', ('float', 'Day speed rate')),
    ('NightTimeSpeedRate', ('float', 'Night speed rate')),
    ('ExpRate', ('float', 'EXP rate')),
    ('PalCaptureRate', ('float', 'Pal capture rate')),
    ('PalSpawnNumRate', ('float', 'Pal spawn rate')),
    ('PalDamageRateAttack', ('float', 'Pal attack damage rate')),
    ('PalDamageRateDefense', ('float', 'Pal defense damage rate')),
    ('PlayerDamageRateAttack', ('float', 'Player attack damage rate')),
    ('PlayerDamageRateDefense', ('float', 'Player defense damage rate')),
    ('WorkSpeedRate', ('float', 'Work speed rate')),
    ('CollectionDropRate', ('float', 'Collection drop rate')),
    ('EnemyDropItemRate', ('float', 'Enemy drop item rate')),
    ('DropItemMaxNum', ('int', 'Max dropped items on ground')),
    ('ServerPlayerMaxNum', ('int', 'Max players')),
    ('CoopPlayerMaxNum', ('int', 'Max party size')),
    ('GuildPlayerMaxNum', ('int', 'Max guild size')),
    ('bIsPvP', ('bool', 'PvP enabled')),
    ('bEnablePlayerToPlayerDamage', ('bool', 'Player-to-player damage')),
    ('bEnableFriendlyFire', ('bool', 'Friendly fire')),
    ('bHardcore', ('bool', 'Hardcore (pal loss on death)')),
    ('bShowPlayerList', ('bool', 'Show player list publicly')),
    ('bIsUseBackupSaveData', ('bool', "Palworld's own auto backup-on-save")),
])


def _split_top_level(s):
    """Split on commas at paren-depth 0, outside double-quoted strings."""
    parts = []
    depth = 0
    in_quotes = False
    current = []
    i = 0
    while i < len(s):
        ch = s[i]
        if in_quotes and ch == '\\' and i + 1 < len(s):
            # escaped char inside a string (\" or \\): keep both, never toggle on it
            current.append(ch)
            current.append(s[i + 1])
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif not in_quotes and ch == '(':
            depth += 1
            current.append(ch)
        elif not in_quotes and ch == ')':
            depth -= 1
            current.append(ch)
        elif not in_quotes and ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append(''.join(current))
    return parts


def parse(text):
    """Returns an OrderedDict of key -> raw value string (still quoted/formatted as in the file)."""
    m = re.search(r'OptionSettings=\((.*)\)\s*$', text, re.S)
    if not m:
        raise ValueError('Could not find OptionSettings=(...) in ini file')
    body = m.group(1)
    pairs = OrderedDict()
    for part in _split_top_level(body):
        if '=' not in part:
            continue
        key, value = part.split('=', 1)
        pairs[key.strip()] = value
    return pairs


def render(pairs):
    body = ','.join(f'{k}={v}' for k, v in pairs.items())
    return f'{HEADER}\nOptionSettings=({body})\n\n'


def unquote(raw):
    """Raw ini string value ("...", with \\" and \\\\ escapes) -> plain text."""
    if raw.startswith('"') and raw.endswith('"'):
        return re.sub(r'\\(["\\])', r'\1', raw[1:-1])
    return raw


def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def to_display(pairs):
    """Curated fields only, typed for form rendering. Missing keys are skipped."""
    fields = []
    for key, (kind, label) in EDITABLE_FIELDS.items():
        if key not in pairs:
            continue
        raw = pairs[key]
        if kind == 'string':
            value = unquote(raw)
        elif kind == 'bool':
            value = raw.strip() == 'True'
        else:
            value = raw
        fields.append({'key': key, 'kind': kind, 'label': label, 'value': value})
    return fields


def apply_updates(pairs, form):
    """Mutates pairs in place from submitted form data (checkboxes absent = False).
    Returns the labels of numeric fields whose value was rejected and left unchanged."""
    rejected = []
    for key, (kind, label) in EDITABLE_FIELDS.items():
        if key not in pairs:
            continue
        if kind == 'bool':
            pairs[key] = 'True' if form.get(key) == 'on' else 'False'
        elif kind == 'string':
            pairs[key] = quote(form.get(key, ''))
        elif kind == 'int':
            raw = form.get(key, '').strip()
            if raw:
                try:
                    pairs[key] = str(int(raw))
                except ValueError:
                    rejected.append(label)
        elif kind == 'float':
            raw = form.get(key, '').strip()
            if raw:
                try:
                    pairs[key] = f'{float(raw):.6f}'
                except ValueError:
                    rejected.append(label)
        elif kind == 'rawtext':
            raw = form.get(key, '').strip()
            if raw:
                pairs[key] = raw
    return rejected
