#!/usr/bin/env python3
"""Install a checksum-pinned, private CUDA llama.cpp runtime without sudo or administrator rights."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from inflect_platform import WINDOWS, default_runtime, hidden, lock, program, replace_link


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


def gpu_facts():
    """Driver branch and compute capability from nvidia-smi (a read-only query)."""
    try:
        output = subprocess.run(['nvidia-smi', '--query-gpu=driver_version,compute_cap', '--format=csv,noheader'],
                                capture_output=True, text=True, timeout=20, check=True, **hidden()).stdout
        driver, compute = [value.strip() for value in output.splitlines()[0].split(',')]
        return int(driver.split('.')[0]), float(compute)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None, None


def choose_variant(lock_data, requested=None):
    variants = lock_data['variants']
    if requested:
        if requested not in variants or (requested == 'linux') == WINDOWS:
            raise RuntimeError(f'No pinned llama.cpp package named {requested} for this platform.')
        return requested, variants[requested]
    if not WINDOWS:
        if platform.system() != 'Linux' or platform.machine() != 'x86_64':
            raise RuntimeError('This pinned package supports x86_64 Linux with NVIDIA CUDA.')
        return 'linux', variants['linux']
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('This pinned package supports 64-bit x86 Windows with NVIDIA CUDA.')
    # CUDA 13 builds include Blackwell (sm_120) kernels but need an R580+ driver and a Turing or newer GPU.
    driver, compute = gpu_facts()
    preferred = variants['windows']
    if ((driver is not None and driver < preferred['minimum_driver'])
            or (compute is not None and compute < preferred['minimum_compute_capability'])):
        return 'windows-cuda12', variants['windows-cuda12']
    return 'windows', preferred


def extract(archive, target):
    if archive.name.endswith('.zip'):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                path = (target / member).resolve()
                if not path.is_relative_to(target.resolve()):
                    raise RuntimeError(f'Unsafe path in {archive.name}: {member}')
            bundle.extractall(target)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(target, filter='data')


def verify(binary_dir):
    """Run the server binary: needs the GPU driver, so setup can defer it (--no-gpu-check)."""
    server = program(binary_dir / 'llama-server')
    quiet = dict(check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', **hidden())
    version_result = subprocess.run([str(server), '--version'], **quiet)
    version = (version_result.stdout or version_result.stderr).strip()
    devices = subprocess.run([str(server), '--list-devices'], **quiet)
    if 'CUDA' not in devices.stdout:
        raise RuntimeError('The installed runtime did not find a CUDA GPU: ' + devices.stderr)
    help_text = subprocess.run([str(server), '--help'], **quiet).stdout
    if 'draft-mtp' not in help_text or '--spec-draft-n-max' not in help_text:
        raise RuntimeError('The runtime lacks the required embedded MTP support.')
    return version


def install(runtime, requested=None, check_gpu=True):
    lock_data = json.loads((PROJECT / 'runtime-locks/llama-cpp.json').read_text(encoding='utf-8'))
    name, selected = choose_variant(lock_data, requested)
    release = lock_data['release']
    home = runtime / 'llama'
    home.mkdir(parents=True, exist_ok=True)
    with (home / 'install.lock').open('a') as guard:
        lock(guard)
        releases = home / 'releases'
        releases.mkdir(exist_ok=True)
        # Linux keeps its original release folder; Windows can switch between CUDA builds.
        destination = releases / (release if name == 'linux' else f"{release}-{selected['platform']}")
        receipt = dict(release=release, platform=selected['platform'], assets=selected['assets'])
        if not (destination / 'manifest.json').is_file():
            cache = runtime / 'downloads'
            cache.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(home).free < 4 * 2**30:
                raise RuntimeError('At least 4 GiB free space is needed for llama.cpp installation.')
            archives = [download(asset, release, cache) for asset in selected['assets']]
            with tempfile.TemporaryDirectory(prefix='.install-', dir=releases) as temporary:
                stage = Path(temporary)
                for i, archive in enumerate(archives):
                    extract(archive, stage / str(i))
                server = next((stage / '0').rglob(program('llama-server').name))
                binary_dir = stage / 'bin'
                shutil.move(str(server.parent), binary_dir)
                for library in (stage / '1').rglob('*.dll' if WINDOWS else '*.so*'):
                    if library.is_file():
                        shutil.copy2(library, binary_dir / library.name)
                version = verify(binary_dir) if check_gpu else None
                # Move only the completed runtime into place; failed installs leave the active one alone.
                if destination.exists():
                    raise RuntimeError(f'Incomplete release directory exists: {destination}')
                complete = stage / 'complete'
                complete.mkdir()
                shutil.move(str(binary_dir), complete / 'bin')
                (complete / 'manifest.json').write_text(json.dumps({**receipt, 'version': version or 'Not yet verified on this GPU',
                                                                    'verified': bool(version)}, indent=2) + '\n', encoding='utf-8')
                complete.replace(destination)
        elif check_gpu:
            stored = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
            stored.update(version=verify(destination / 'bin'), verified=True)
            (destination / 'manifest.json').write_text(json.dumps(stored, indent=2) + '\n', encoding='utf-8')
        replace_link(home / 'bin', destination / 'bin')
        state = 'verified' if check_gpu else 'installed; GPU verification deferred'
        print(f"llama.cpp {release} ({selected['platform']}) {state}: {program(home / 'bin/llama-server')}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, default=Path(os.environ.get('INFLECT_RUNTIME', default_runtime())))
    parser.add_argument('--variant', help='Force a pinned package from runtime-locks/llama-cpp.json')
    parser.add_argument('--no-gpu-check', action='store_true',
                        help='Install without running the binary; rerun without this flag to verify on the GPU')
    args = parser.parse_args()
    install(args.runtime_dir.expanduser().resolve(), args.variant, not args.no_gpu_check)
