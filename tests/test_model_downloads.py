import asyncio
import hashlib
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loader import downloads
from loader.downloads import DownloadManager, git_blob_sha1, local_path, Permanent
from loader.naming import family_key, merge_families, quant_label, split_name


# Naming ---------------------------------------------------------------------------------
@pytest.mark.parametrize('name, family, variant', [
    ('Qwen3.6-27B-Q6_K', 'Qwen3.6-27B', ''),
    ('Qwen3.6-35B-A3B-Uncensored-Heretic-GGUF', 'Qwen3.6-35B-A3B', 'Uncensored Heretic'),
    ('gemma-4-E4B-it-Q4_K_M.gguf', 'gemma-4-E4B', 'it'),
    ('GLM-4.7-Flash-exl3', 'GLM-4.7-Flash', ''),
])
def test_split_name(name, family, variant):
    assert split_name(name) == (family, variant)


def test_family_key_joins_spaced_versions():
    assert family_key('Qwen 3.5 0.8B') == family_key('Qwen3.5-0.8B')
    assert family_key('gemma-4-E4B') == family_key('gemma4 e4b')
    assert family_key('Mistral-7B') == 'mistral-7b'


def test_merge_families_joins_prefixed_and_suffixed_names():
    merged = merge_families({'qwen3.6-35b-a3b', 'huihui-qwen3.6-35b-a3b', 'qwen3.6-27b'})
    assert merged['huihui-qwen3.6-35b-a3b'] == ('qwen3.6-35b-a3b', 'huihui')
    assert merged['qwen3.6-27b'][0] == 'qwen3.6-27b'


def test_quant_label():
    assert quant_label('Model-Q4_K_M.gguf') == 'Q4_K_M'


# Downloads ------------------------------------------------------------------------------
class RangeHandler(SimpleHTTPRequestHandler):
    """Serves /<repo>/resolve/<commit>/<path> from a directory, honouring byte ranges."""
    def log_message(self, *args):
        pass

    def do_GET(self):
        parts = self.path.split('/resolve/', 1)[1].split('/', 1)
        path = self.directory + '/' + parts[1]
        data = open(path, 'rb').read()
        header = self.headers.get('Range')
        if header:
            start, end = header.removeprefix('bytes=').split('-')
            start, end = int(start), int(end or len(data) - 1)
            body = data[start:end + 1]
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
        else:
            body = data
            self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    source = tmp_path / 'hub'
    source.mkdir()
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(RangeHandler, directory=str(source)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(downloads, 'HUB', f'http://127.0.0.1:{server.server_address[1]}')
    monkeypatch.setattr(downloads, 'SEGMENT_MIN', 1024)
    monkeypatch.setattr(downloads, 'hub_headers', lambda: {})
    yield source
    server.shutdown()


def make_file(source, name, size, seed):
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes((i * seed + i // 7) % 251 for i in range(size)))
    return path


def option(source, files, engine='gguf', folder='Test-Model-Q4_K_M-owner', corrupt=None):
    entries = []
    for name in files:
        path = source / name
        entry = dict(path=name, size=path.stat().st_size, sha256=None, blob=None)
        if path.stat().st_size > 2048:
            entry['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            entry['blob'] = git_blob_sha1(path)
        if name == corrupt:
            entry['sha256'] = '0' * 64
        entries.append(entry)
    return dict(id='opt-' + folder, title='Test Model · Q4_K_M', family='test-model', repo='owner/Test-Model-GGUF',
                owner='owner', variant='Original', quant='Q4_K_M', engine=engine,
                format='GGUF' if engine == 'gguf' else 'EXL3', vision=False, revision='main', commit='abc123',
                folder=folder, files=entries)


def run_download(manager, spec):
    async def go():
        finished = []
        async def done(job):
            finished.append(job['id'])
        manager.on_complete = done
        job = manager.add(spec)
        await manager.task
        return job, finished
    return asyncio.run(go())


def test_segmented_download_verifies_and_moves_into_folder(hub, tmp_path):
    make_file(hub, 'model-Q4_K_M.gguf', 50_000, 3)
    make_file(hub, 'mmproj-f16.gguf', 9_000, 5)
    make_file(hub, 'config.json', 300, 7)
    root, state = tmp_path / 'models', tmp_path / 'state'
    state.mkdir()
    manager = DownloadManager(state, root)
    job, finished = run_download(manager, option(hub, ['model-Q4_K_M.gguf', 'mmproj-f16.gguf', 'config.json']))

    assert job['state'] == 'complete', job['error']
    assert finished == [job['id']]
    folder = root / 'Test-Model-Q4_K_M-owner'
    for name in ('model-Q4_K_M.gguf', 'mmproj-f16.gguf', 'config.json'):
        assert (folder / name).read_bytes() == (hub / name).read_bytes()
    assert not (root / '.inflect-downloads').exists()
    receipt = json.loads(next((root / '.linux-llm-downloads').glob('inflect-*.json')).read_text())
    assert receipt['status'] == 'verified' and receipt['repo'] == 'owner/Test-Model-GGUF'
    assert receipt['folder'] == 'Test-Model-Q4_K_M-owner'
    assert json.loads((state / 'downloads.json').read_text())[0]['state'] == 'complete'


def test_exl3_keeps_nested_paths_and_second_download_gets_unique_folder(hub, tmp_path):
    make_file(hub, 'model.safetensors', 20_000, 11)
    make_file(hub, 'sub/tokenizer.json', 500, 13)
    root, state = tmp_path / 'models', tmp_path / 'state'
    state.mkdir()
    (root / 'Test-Model-Q4_K_M-owner').mkdir(parents=True)
    manager = DownloadManager(state, root)
    job, _ = run_download(manager, option(hub, ['model.safetensors', 'sub/tokenizer.json'], engine='exl3'))
    assert job['state'] == 'complete', job['error']
    assert job['folder'] == 'Test-Model-Q4_K_M-owner-2'
    assert (root / job['folder'] / 'sub' / 'tokenizer.json').exists()


def test_checksum_mismatch_fails_and_keeps_nothing_in_library(hub, tmp_path):
    make_file(hub, 'model-Q4_K_M.gguf', 30_000, 17)
    root, state = tmp_path / 'models', tmp_path / 'state'
    state.mkdir()
    manager = DownloadManager(state, root)
    job, finished = run_download(manager, option(hub, ['model-Q4_K_M.gguf'], corrupt='model-Q4_K_M.gguf'))
    assert job['state'] == 'failed'
    assert not finished
    assert not (root / 'Test-Model-Q4_K_M-owner').exists()


def test_duplicate_download_is_refused(hub, tmp_path):
    make_file(hub, 'model-Q4_K_M.gguf', 4_000, 19)
    root, state = tmp_path / 'models', tmp_path / 'state'
    state.mkdir()
    manager = DownloadManager(state, root)
    spec = option(hub, ['model-Q4_K_M.gguf'])

    async def go():
        manager.add(spec)
        with pytest.raises(ValueError, match='already'):
            manager.add(spec)
        await manager.task
    asyncio.run(go())


def test_running_jobs_resume_as_paused_after_restart(tmp_path):
    (tmp_path / 'downloads.json').write_text(json.dumps([{'id': 'a', 'state': 'downloading'}]))
    manager = DownloadManager(tmp_path, tmp_path / 'models')
    assert manager.jobs[0]['state'] == 'paused'
    assert 'Resume' in manager.jobs[0]['note']


@pytest.mark.parametrize('name', ['../escape.gguf', '/etc/passwd', 'a/../../b', 'c:\\x'])
def test_unsafe_repository_paths_are_rejected(tmp_path, name):
    with pytest.raises(Permanent):
        local_path(tmp_path, name, flatten=False)
