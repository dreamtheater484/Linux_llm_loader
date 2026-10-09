"""Profiles and history saved on Linux keep pointing at the same models elsewhere."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from loader.library import scan
from test_llama_cpp import write_gguf

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('import_linux_state', PROJECT / 'scripts/import-linux-state.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)

GGUF = {'general.architecture': 'qwen35', 'general.type': 'model', 'qwen35.block_count': 4, 'qwen35.context_length': 4096}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@pytest.fixture
def machines(tmp_path):
    drive = tmp_path / 'drive'
    (drive / 'models/Qwen-Q4').mkdir(parents=True)
    (drive / 'extra').mkdir()
    write_gguf(drive / 'models/Qwen-Q4/Qwen3.8-Flash-Next-Q4_K_M.gguf', **GGUF)
    write_gguf(drive / 'extra/Other-Q8_0.gguf', **GGUF)
    linux_root, linux_extra = '/run/media/roy/Data/models', '/run/media/roy/Data/extra'
    library_id = digest('Qwen-Q4/Qwen3.8-Flash-Next-Q4_K_M.gguf')  # Relative, POSIX: identical on both systems.
    linux_extra_id = digest(linux_extra + '/Other-Q8_0.gguf')
    source = tmp_path / 'linux-state'
    source.mkdir()
    settings = dict(model_id=library_id, engine='gguf', context=131072, kv='Q8', cpu_percent=100)
    (source / 'profiles.json').write_text(json.dumps([
        dict(id='p1', name='Flash Next Q4 · 128K', settings=settings, auto_name=False),
        dict(id='p2', name='Other', settings={**settings, 'model_id': linux_extra_id}, auto_name=False)]))
    (source / 'last-profile.json').write_text(json.dumps({**settings, 'model_id': linux_extra_id}))
    (source / 'library.json').write_text(json.dumps(dict(locations=[linux_extra], order=[linux_extra_id, library_id], sort='custom')))
    (source / 'config.json').write_text(json.dumps(dict(model_root=linux_root, port=7861, network_enabled=False)))
    with sqlite3.connect(source / 'results.sqlite3') as db:
        db.execute('CREATE TABLE benchmarks (id TEXT PRIMARY KEY, created REAL, data TEXT)')
        db.execute('INSERT INTO benchmarks VALUES (?, ?, ?)', ('b1', 1.0, json.dumps(dict(settings={'model_id': linux_extra_id}))))
    with sqlite3.connect(source / 'conversations.sqlite3') as db:
        db.execute('CREATE TABLE conversations (id TEXT PRIMARY KEY, updated REAL NOT NULL, document TEXT NOT NULL)')
        db.execute('INSERT INTO conversations VALUES (?, ?, ?)', ('c1', 1.0, json.dumps(dict(id='c1', model_id=linux_extra_id))))
    config_file = tmp_path / 'config.json'
    config_file.write_text(json.dumps(dict(model_root=str(drive / 'models'), port=7860, network_enabled=True)))
    return dict(source=source, state=tmp_path / 'state', config=config_file, drive=drive,
                library_id=library_id, local_extra_id=digest(str((drive / 'extra/Other-Q8_0.gguf').resolve())))


def test_import_keeps_library_ids_and_remaps_added_locations(machines):
    report = importer.import_state(machines['source'], machines['state'], machines['config'],
                                   [('/run/media/roy/Data', str(machines['drive']))], labels={})
    local_ids = {m['id'] for m in scan(machines['drive'] / 'models', [str((machines['drive'] / 'extra').resolve())])['models']}
    assert {machines['library_id'], machines['local_extra_id']} == local_ids
    profiles = json.loads((machines['state'] / 'profiles.json').read_text(encoding='utf-8'))
    assert [p['settings']['model_id'] for p in profiles] == [machines['library_id'], machines['local_extra_id']]
    assert profiles[0]['name'] == 'Flash Next Q4 · 128K' and profiles[0]['settings']['context'] == 131072
    library = json.loads((machines['state'] / 'library.json').read_text())
    assert library['locations'] == [str((machines['drive'] / 'extra').resolve())]
    assert library['order'] == [machines['local_extra_id'], machines['library_id']]
    assert json.loads((machines['state'] / 'last-profile.json').read_text())['model_id'] == machines['local_extra_id']
    with sqlite3.connect(machines['state'] / 'results.sqlite3') as db:
        assert machines['local_extra_id'] in db.execute('SELECT data FROM benchmarks').fetchone()[0]
    with sqlite3.connect(machines['state'] / 'conversations.sqlite3') as db:
        assert json.loads(db.execute('SELECT document FROM conversations').fetchone()[0])['model_id'] == machines['local_extra_id']
    config = json.loads(machines['config'].read_text())
    assert config['port'] == 7861 and config['listen_host'] == '127.0.0.1'
    assert any('2 saved profile' in line for line in report)
    # Importing again neither duplicates profiles nor overwrites local conversations.
    again = importer.import_state(machines['source'], machines['state'], machines['config'],
                                  [('/run/media/roy/Data', str(machines['drive']))], labels={})
    assert len(json.loads((machines['state'] / 'profiles.json').read_text(encoding='utf-8'))) == 2
    assert any('0 saved profile' in line for line in again)
    assert any('already has conversations' in line for line in again)


def test_volume_labels_translate_removable_drive_paths():
    translate = importer.translator([], {'New Volume': 'D:\\'})
    assert translate('/run/media/roy/New Volume/Documents/AI/models') == str(Path('D:\\') / 'Documents' / 'AI' / 'models')
    assert translate('/media/roy/New Volume') == str(Path('D:\\'))
    assert translate('/home/roy/models') is None


def test_import_refuses_while_inflect_runs(machines):
    import inflect_platform
    machines['state'].mkdir()
    with (machines['state'] / 'manager.lock').open('a') as running:
        inflect_platform.lock(running, blocking=False)
        with pytest.raises(SystemExit, match='Close Inflect'):
            importer.import_state(machines['source'], machines['state'], machines['config'], labels={})
