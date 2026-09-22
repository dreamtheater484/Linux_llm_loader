#!/usr/bin/env python3
"""Install a checksum-pinned, private CUDA llama.cpp runtime without sudo."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(asset, release, cache):
    target = cache / asset['name']
    if target.is_file() and checksum(target) == asset['sha256']:
        return target
    url = f"https://github.com/ggml-org/llama.cpp/releases/download/{release}/{asset['name']}"
    partial = target.with_suffix('.part')
    for attempt in range(3):
        try:
            print(f"Downloading {asset['name']}", flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as output:
                shutil.copyfileobj(response, output, 2**20)
            if checksum(partial) != asset['sha256']:
                raise ValueError(f"Checksum mismatch: {asset['name']}")
            partial.replace(target)
            return target
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def install(runtime):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('This pinned package supports x86_64 Linux with NVIDIA CUDA.')
    lock = json.loads((PROJECT / 'runtime-locks/llama-cpp.json').read_text())
    home = runtime / 'llama'
    home.mkdir(parents=True, exist_ok=True)
    with (home / 'install.lock').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        releases = home / 'releases'
        releases.mkdir(exist_ok=True)
        destination = releases / lock['release']
        if not (destination / 'manifest.json').is_file():
            cache = runtime / 'downloads'
            cache.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(home).free < 4 * 2**30:
                raise RuntimeError('At least 4 GiB free space is needed for llama.cpp installation.')
            archives = [download(asset, lock['release'], cache) for asset in lock['assets']]
            with tempfile.TemporaryDirectory(prefix='.install-', dir=releases) as temporary:
                stage = Path(temporary)
                for i, archive in enumerate(archives):
                    with tarfile.open(archive) as bundle:
                        bundle.extractall(stage / str(i), filter='data')
                server = next((stage / '0').rglob('llama-server'))
                binary_dir = stage / 'bin'
                shutil.move(str(server.parent), binary_dir)
                for library in (stage / '1').rglob('*.so*'):
                    if library.is_file():
                        shutil.copy2(library, binary_dir / library.name)
                version_result = subprocess.run([str(binary_dir / 'llama-server'), '--version'],
                    check=True, capture_output=True, text=True)
                version = (version_result.stdout or version_result.stderr).strip()
                devices = subprocess.run([str(binary_dir / 'llama-server'), '--list-devices'],
                    check=True, capture_output=True, text=True)
                if 'CUDA' not in devices.stdout:
                    raise RuntimeError('The installed runtime did not find a CUDA GPU: ' + devices.stderr)
                help_text = subprocess.run([str(binary_dir / 'llama-server'), '--help'],
                    check=True, capture_output=True, text=True).stdout
                if 'draft-mtp' not in help_text or '--spec-draft-n-max' not in help_text:
                    raise RuntimeError('The runtime lacks the required embedded MTP support.')
                # Move only the completed runtime into place; failed installs leave the active one alone.
                if destination.exists():
                    raise RuntimeError(f'Incomplete release directory exists: {destination}')
                complete = stage / 'complete'
                complete.mkdir()
                shutil.move(str(binary_dir), complete / 'bin')
                (complete / 'manifest.json').write_text(json.dumps({**lock, 'version': version}, indent=2) + '\n')
                complete.replace(destination)
        else:
            receipt = json.loads((destination / 'manifest.json').read_text())
            version_result = subprocess.run([str(destination / 'bin/llama-server'), '--version'],
                check=True, capture_output=True, text=True)
            receipt['version'] = (version_result.stdout or version_result.stderr).strip()
            (destination / 'manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
        link = home / 'bin'
        if link.exists() and not link.is_symlink():
            raise RuntimeError(f'Refusing to replace an unmanaged directory: {link}')
        temporary_link = home / '.bin-next'
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(destination / 'bin', target_is_directory=True)
        temporary_link.replace(link)
        print(f"Installed llama.cpp {lock['release']}: {link / 'llama-server'}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path,
        default=Path(os.environ.get('INFLECT_RUNTIME', Path.home() / '.local/share/linux-llm-loader')))
    args = parser.parse_args()
    install(args.runtime_dir.expanduser().resolve())
