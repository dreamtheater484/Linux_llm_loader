"""Fetch the pinned official wheel in bounded parallel ranges; verify its release digest."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request

ASSET = 'exllamav3-1.5.0+cu128.torch2.9.0-cp312-cp312-linux_x86_64.whl'
ROOT = Path(__file__).resolve().parents[1]
target = ROOT / '.runtime/wheels' / ASSET
expected = 'd8f5b483ca882c52d79a73c059b508c666663a68b4c60e420ca489e654e8104a'
if target.is_file():
    with target.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() == expected:
            print('Pinned wheel already verified:', target)
            sys.exit(0)
headers = {'User-Agent': 'Inflect-Setup/0.1'}
release = json.load(urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/turboderp-org/exllamav3/releases/tags/v1.5.0', headers=headers)))
asset = next(a for a in release['assets'] if a['name'] == ASSET)
size = asset['size']
digest = asset.get('digest', '')
if not digest.startswith('sha256:'):
    raise RuntimeError('The release did not provide a SHA256 digest.')
if digest != 'sha256:' + expected:
    raise RuntimeError('Release digest differs from the pinned, tested wheel.')
target.parent.mkdir(parents=True, exist_ok=True)
partial = target.with_suffix('.partial')
fd = os.open(partial, os.O_RDWR | os.O_CREAT, 0o600)
os.ftruncate(fd, size)


def transfer(start):
    end = min(size - 1, start + 8 * 2**20 - 1)
    request = urllib.request.Request(asset['browser_download_url'], headers={**headers, 'Range': f'bytes={start}-{end}'})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
            raise RuntimeError('Mirror did not honor the requested byte range.')
        offset = start
        while block := response.read(1024 * 1024):
            if offset + len(block) > end + 1:
                raise RuntimeError('Oversized range response')
            written = os.pwrite(fd, block, offset)
            if written != len(block):
                raise RuntimeError('Incomplete local write')
            offset += written
        if offset != end + 1:
            raise RuntimeError('Incomplete range response')
    return end - start + 1


try:
    with ThreadPoolExecutor(max_workers=8) as pool:
        done = 0
        for count in pool.map(transfer, range(0, size, 8 * 2**20)):
            done += count
            print(f'{done / 1e6:.0f} / {size / 1e6:.0f} MB', flush=True)
finally:
    os.close(fd)
with partial.open('rb') as file:
    actual = hashlib.file_digest(file, 'sha256').hexdigest()
if 'sha256:' + actual != digest:
    raise RuntimeError('Release checksum mismatch. The wheel will not be installed.')
partial.replace(target)
print('SHA256 verified:', actual)
print(target)
