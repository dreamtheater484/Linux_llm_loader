#!/usr/bin/env python3
"""Bring saved profiles, library choices, benchmark history and chats from a Linux Inflect.

On Linux, copy the state folder and configuration to a drive this computer can
read, for example the model drive:

    mkdir -p "/run/media/$USER/<drive>/Inflect-linux-state"
    cp -a ~/.local/share/linux-llm-loader/state/. ~/.config/inflect/config.json "/run/media/$USER/<drive>/Inflect-linux-state/"

then run this with --from pointing at that copy. Inflect must be closed.
Model IDs inside the model folder are identical on both systems; models in
added library locations are matched through the translated location paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import string
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from inflect_platform import WINDOWS, config_home, default_runtime, lock, replace_file


def volume_labels():
    """Volume label -> drive root, so /run/media/<user>/<label>/... finds its drive letter."""
    if not WINDOWS:
        return {}
    import ctypes
    labels = {}
    for letter in string.ascii_uppercase:
        root = f'{letter}:\\'
        label = ctypes.create_unicode_buffer(261)
        if os.path.exists(root) and ctypes.windll.kernel32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0):
            labels.setdefault(label.value, root)
    return labels


def translator(mappings, labels):
    """Translate a Linux absolute path to this computer, or None when unknown."""
    def translate(path):
        if not isinstance(path, str) or not path.startswith('/'):
            return None
        for linux, local in mappings:
            linux = linux.rstrip('/')
            if path == linux or path.startswith(linux + '/'):
                return str(Path(local).joinpath(*PurePosixPath(path[len(linux):].lstrip('/')).parts))
        match = re.match(r'^/(?:run/)?media/[^/]+/([^/]+)(?:/(.*))?$', path)
        if match and match[1] in labels:
            return str(Path(labels[match[1]]).joinpath(*PurePosixPath(match[2] or '').parts))
        return None
    return translate


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    replace_file(temporary, path)


def remap_text(text, mapping):
    for old, new in mapping.items():
        text = text.replace(f'"{old}"', f'"{new}"')
    return text


def added_location_ids(linux_locations, translate):
    """Linux model ID -> local model ID for models in added library locations.

    Those IDs hash the absolute path, which differs between systems."""
    from loader.library import scan
    mapping, locations = {}, []
    for linux in linux_locations:
        local = translate(linux)
        if not local or not Path(local).exists():
            continue
        base = Path(local).resolve()  # Inflect stores and hashes resolved locations.
        locations.append(str(base))
        for model in scan(base if base.is_dir() else base.parent, [str(base)])['models']:
            path = Path(model['path'])
            if path == base:
                linux_path = linux
            elif path.is_relative_to(base):
                linux_path = linux.rstrip('/') + '/' + path.relative_to(base).as_posix()
            else:
                continue
            linux_id = hashlib.sha256(linux_path.encode()).hexdigest()[:16]
            mapping[linux_id] = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    return mapping, locations


def merge_rows(source, target, table, mapping):
    with sqlite3.connect(source) as old, sqlite3.connect(target) as new:
        tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table not in tables:
            return 0
        columns = [row[1] for row in old.execute(f'PRAGMA table_info({table})')]
        new.execute(f'CREATE TABLE IF NOT EXISTS {table} ({", ".join(columns)}, PRIMARY KEY ({columns[0]}))')
        added = 0
        for row in old.execute(f'SELECT {", ".join(columns)} FROM {table}'):
            row = [remap_text(value, mapping) if isinstance(value, str) else value for value in row]
            added += new.execute(f'INSERT OR IGNORE INTO {table} VALUES ({", ".join("?" * len(row))})', row).rowcount
        return added


def import_state(source, state, config_file, mappings=(), labels=None, chats=True):
    source, state = Path(source), Path(state)
    if not (source / 'profiles.json').is_file() and not (source / 'results.sqlite3').is_file():
        raise SystemExit(f'No Inflect state found in {source}. Expected profiles.json or results.sqlite3.')
    config = read_json(config_file, None)
    if not isinstance(config, dict) or 'model_root' not in config:
        raise SystemExit(f'Run Inflect setup on this computer first ({config_file} is missing).')
    state.mkdir(parents=True, exist_ok=True)
    guard = (state / 'manager.lock').open('a')
    try:
        lock(guard, blocking=False)
    except BlockingIOError:
        raise SystemExit('Close Inflect before importing.') from None
    report = []
    try:
        linux_config = read_json(source / 'config.json', {})
        translate = translator([*mappings, *([(linux_config['model_root'], config['model_root'])]
                                             if linux_config.get('model_root') else [])],
                               volume_labels() if labels is None else labels)
        library = read_json(source / 'library.json', {})
        mapping, locations = added_location_ids([p for p in library.get('locations', []) if isinstance(p, str)], translate)
        skipped_locations = [p for p in library.get('locations', []) if isinstance(p, str) and translate(p) not in locations]

        profiles = read_json(state / 'profiles.json', [])
        known = {p.get('id') for p in profiles}
        imported = [json.loads(remap_text(json.dumps(p), mapping)) for p in read_json(source / 'profiles.json', [])
                    if p.get('id') not in known]
        taken = {p['name'].strip().casefold() for p in profiles if not p.get('deleted_at')}
        for profile in imported:
            if not profile.get('deleted_at') and not profile.get('auto_name') and profile['name'].strip().casefold() in taken:
                profile['name'] = profile['name'][:165] + ' (Linux)'
        write_json(state / 'profiles.json', profiles + imported)
        report.append(f'{len(imported)} saved profile(s) imported')

        if (source / 'last-profile.json').is_file() and not (state / 'last-profile.json').exists():
            write_json(state / 'last-profile.json', json.loads(remap_text((source / 'last-profile.json').read_text(encoding='utf-8'), mapping)))
        current = read_json(state / 'library.json', {})
        merged = dict(library, **current)
        merged['locations'] = list(dict.fromkeys([*current.get('locations', []), *locations]))
        merged['order'] = list(dict.fromkeys([*current.get('order', []), *(mapping.get(k, k) for k in library.get('order', []))]))
        write_json(state / 'library.json', merged)
        if locations:
            report.append(f'{len(locations)} added library location(s) translated')
        for path in skipped_locations:
            report.append(f'Library location not found on this computer, add it again in the library: {path}')

        if (source / 'results.sqlite3').is_file():
            if not (state / 'results.sqlite3').exists():
                shutil.copy2(source / 'results.sqlite3', state / 'results.sqlite3')
                with sqlite3.connect(state / 'results.sqlite3') as db:
                    for table in ('benchmarks', 'evaluations', 'profile_loads'):
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                            for key, data in db.execute(f'SELECT rowid, data FROM {table}').fetchall():
                                db.execute(f'UPDATE {table} SET data=? WHERE rowid=?', (remap_text(data, mapping), key))
                report.append('Benchmark history and loaded-memory measurements imported')
            else:
                added = sum(merge_rows(source / 'results.sqlite3', state / 'results.sqlite3', table, mapping)
                            for table in ('benchmarks', 'evaluations', 'profile_loads'))
                report.append(f'{added} benchmark record(s) merged')
        if (source / 'evaluations').is_dir():
            for run in (source / 'evaluations').iterdir():
                if run.is_dir() and not (state / 'evaluations' / run.name).exists():
                    shutil.copytree(run, state / 'evaluations' / run.name)
        if chats and (source / 'conversations.sqlite3').is_file():
            if (state / 'conversations.sqlite3').exists():
                report.append('Conversations were not imported: this computer already has conversations.')
            else:
                shutil.copy2(source / 'conversations.sqlite3', state / 'conversations.sqlite3')
                with sqlite3.connect(state / 'conversations.sqlite3') as db:
                    for key, document in db.execute('SELECT id, document FROM conversations').fetchall():
                        db.execute('UPDATE conversations SET document=? WHERE id=?', (remap_text(document, mapping), key))
                report.append('Conversations imported')

        preferences = {key: linux_config[key] for key in ('port', 'network_enabled') if key in linux_config}
        if preferences:
            from inflect_access import access_config
            config.update(preferences)
            config.update(access_config(config))
            write_json(config_file, config)
            report.append('Port and network access preference imported')
        if linux_config.get('comfyui_enabled'):
            report.append('ComfyUI control was enabled on Linux; set it up again in Settings if ComfyUI runs here too.')
    finally:
        guard.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--from', dest='source', type=Path, required=True, help='Copied Linux state folder')
    parser.add_argument('--map', action='append', default=[], metavar='LINUX_PATH=LOCAL_PATH',
                        help='Translate a Linux folder (repeatable). /run/media/<user>/<label> is matched by volume label automatically.')
    parser.add_argument('--no-chats', action='store_true', help='Leave conversations behind')
    args = parser.parse_args()
    runtime = Path(os.environ.get('INFLECT_RUNTIME', default_runtime()))
    config_file = Path(os.environ.get('INFLECT_CONFIG_FILE', config_home() / 'inflect/config.json'))
    config = read_json(config_file, {}) if config_file.is_file() else {}
    state = Path(os.environ.get('INFLECT_STATE', Path(config.get('runtime', runtime)) / 'state'))
    mappings = [tuple(item.split('=', 1)) for item in args.map if '=' in item]
    for line in import_state(args.source, state, config_file, mappings, chats=not args.no_chats):
        print(line)
    print('Done. Start Inflect to see the imported profiles.')
