#Requires -Version 5.1
<#
.SYNOPSIS
Download resumable, checksum-verified EXL3 models for later use on Ubuntu.
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\download-models.ps1
.EXAMPLE
.\download-models.ps1 -ModelRoot 'D:\Documents\AI\models' -Models Qwen3
.EXAMPLE
.\download-models.ps1 -ModelRoot 'D:\Documents\AI\models' -Models Qwen4
.NOTES
Requires Windows and Python 3.10+. No administrator rights or GPU required.
Downloads complete Hugging Face revisions, with resuming and checksum verification.
The default selection stays the original three 3-bit variants. Qwen4 selects only
the 4.05 bpw Qwen model; explicit All selects all four variants.
The default destination is ..\..\AI\models relative to this script in the project.
#>
[CmdletBinding()]
param(
    [string]$ModelRoot,
    [ValidateSet('All', 'Qwen3', 'DeepSeek3', 'DeepSeekVision3', 'Qwen4')]
    [string[]]$Models = @('Qwen3', 'DeepSeek3', 'DeepSeekVision3'),
    [ValidateRange(1, 8)]
    [int]$Workers = 2,
    [switch]$List,
    [switch]$VerifyOnly,
    [switch]$Login,
    [switch]$UseHttp
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this download script in Windows PowerShell.' }
if ($List -and $VerifyOnly) { throw 'Choose either -List or -VerifyOnly.' }

if (-not $ModelRoot) {
    $candidateRoot = Join-Path $PSScriptRoot '..\..\AI\models'
    if (-not (Test-Path -LiteralPath $candidateRoot -PathType Container)) {
        throw "Could not find the AI model folder. Supply -ModelRoot 'D:\Documents\AI\models' with your actual Windows drive letter."
    }
    $ModelRoot = (Resolve-Path -LiteralPath $candidateRoot).Path
}
$ModelRoot = [System.IO.Path]::GetFullPath($ModelRoot)
Write-Host "Model destination: $ModelRoot" -ForegroundColor Cyan
if (-not $List) {
    $activeDownloads = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
        $_.CommandLine -match 'download_models[^\s]*\.py' -and
        $_.CommandLine -match [regex]::Escape($ModelRoot)
    })
    if ($activeDownloads.Count -gt 0) {
        throw 'A downloader is already using this model folder. Press Ctrl+C in its PowerShell window and wait for it to exit before starting this version.'
    }
}

# Use a dedicated downloader environment, leaving other Python installations alone.
$toolRoot = Join-Path $env:LOCALAPPDATA 'LinuxLlmLoader\downloader'
$venvRoot = Join-Path $toolRoot 'venv'
$pythonExe = Join-Path $venvRoot 'Scripts\python.exe'
$hfExe = Join-Path $venvRoot 'Scripts\hf.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $basePython = $null
    foreach ($name in @('py', 'python', 'python3')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $probeArgs = @()
        if ($name -eq 'py') { $probeArgs += '-3' }
        $probeArgs += @('-c', 'import sys; assert sys.version_info >= (3,10); print(sys.executable)')
        try {
            $probeOutput = @(& $command.Source @probeArgs 2>$null)
            if ($LASTEXITCODE -eq 0 -and $probeOutput.Count -gt 0) {
                $basePython = [string]$probeOutput[-1]
                break
            }
        } catch { continue }
    }
    if (-not $basePython) {
        throw 'Install Python 3.10 or newer for Windows from https://www.python.org/downloads/windows/ (include the Python launcher), reopen PowerShell, and rerun this script.'
    }
    New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
    & $basePython -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the downloader Python environment.' }
}
& $pythonExe -c 'import sys; assert sys.version_info >= (3,10)'
if ($LASTEXITCODE -ne 0) { throw "The downloader at $venvRoot requires Python 3.10 or newer." }

Write-Host 'Checking downloader dependencies (no automatic upgrades)...'
& $pythonExe -c 'import huggingface_hub, httpx, filelock; from packaging.version import Version; assert (1, 3) <= Version(huggingface_hub.__version__).release < (2,)'
if ($LASTEXITCODE -ne 0) {
    & $pythonExe -m pip install --disable-pip-version-check 'huggingface_hub>=1.3,<2' 'httpx>=0.27,<1' 'filelock>=3,<4'
    if ($LASTEXITCODE -ne 0) { throw 'Could not install downloader dependencies. Check the connection and rerun.' }
}
if ($Login) {
    & $hfExe auth login
    if ($LASTEXITCODE -ne 0) { throw 'Hugging Face login failed.' }
}

# Python handles Hub metadata and downloads; PowerShell is the only entry point.
# No downloaded model code is imported or executed.
$downloadCode = @'
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import threading
import uuid
from urllib.parse import quote

import httpx

RANGE_BYTES = 64 * 1024 * 1024
READ_BYTES = 256 * 1024
MAX_FAILURES = 12
HUB_URL = 'https://huggingface.co'
LOG_PATH = None
LOG_LOCK = threading.Lock()


def safe_error(exc):
    # Signed URLs and bearer tokens must not appear in the persistent log.
    message = re.sub(r'https?://[^\s\'\"<>]+', '[URL redacted]', str(exc))
    return re.sub(r'(?i)(?:Bearer\s+\S+|\b(?:hf_|xet_)[A-Za-z0-9_-]+)', '[token redacted]', message)


def log(message):
    line = f'[{datetime.now().astimezone():%H:%M:%S}] {message}'
    with LOG_LOCK:
        print(line, flush=True)
        if LOG_PATH is not None:
            with LOG_PATH.open('a', encoding='utf-8') as output:
                output.write(line + '\n')


class RetryTransfer(Exception):
    def __init__(self, message, delay=0):
        super().__init__(message)
        self.delay = delay


def retry_delay(value):
    try:
        return min(300, max(0, float(value)))
    except (TypeError, ValueError):
        try:
            return min(300, max(0, parsedate_to_datetime(value).timestamp() - time.time()))
        except (TypeError, ValueError, OverflowError):
            return 0


def partial_path(plan, root, entry):
    # Only our sequential HTTP data is resumable. Old Xet files may contain holes.
    key = hashlib.sha256(entry['name'].encode('utf-8')).hexdigest()
    base = root / '.linux-llm-downloads' / 'http' / plan['id'] / plan['commit']
    return base / (key + '.part')


def transfer_file(plan, root, entry, client, headers, stop):
    destination = local_file(root / plan['folder'], entry['name'])
    size = entry['size']
    label = f"{plan['id']}/{entry['name']}"
    if destination.is_file() and destination.stat().st_size == size:
        log(f'REUSE {label} ({size / 1e9:.2f} GB; checksum check follows)')
        return
    part = partial_path(plan, root, entry)
    part.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    offset = part.stat().st_size if part.exists() else 0
    if offset > size:
        raise ValueError(f'Partial file exceeds expected size; inspect {part}')
    log(f'HTTP {label}: {offset / 1e9:.2f}/{size / 1e9:.2f} GB; '
        f'{"resuming saved bytes" if offset else "starting"}')
    url = (f"{HUB_URL}/{plan['repo']}/resolve/{plan['commit']}/"
           f"{quote(entry['name'], safe='/')}")
    failures = 0
    last_report = time.monotonic()
    last_bytes = offset
    with part.open('ab') as output:
        while offset < size:
            if stop.is_set():
                raise InterruptedError('Stopped; HTTP partial files are preserved.')
            end = min(size - 1, offset + RANGE_BYTES - 1)
            request_headers = dict(headers, Range=f'bytes={offset}-{end}',
                                   **{'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
            try:
                # Start at the Hub each time, never retry an expired signed CDN URL.
                # A unique query also avoids a cached redirect with expired credentials.
                with client.stream('GET', url + f'?download=true&lll_request={uuid.uuid4().hex}',
                                   headers=request_headers) as response:
                    status = response.status_code
                    if status in (401, 403, 408, 429) or 500 <= status <= 599:
                        raise RetryTransfer(f'HTTP {status} from {response.url.host}; refreshing download link',
                                            retry_delay(response.headers.get('Retry-After')))
                    if status not in (200, 206):
                        raise ValueError(f'HTTP {status} for {label}; check repository access (use -Login if required).')
                    if response.headers.get('Content-Encoding', 'identity') != 'identity':
                        raise ValueError(f'Unexpected content encoding for {label}; partial file preserved.')
                    if status == 206:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                        if not match or tuple(map(int, match.groups())) != (offset, end, size):
                            raise ValueError(f'Invalid Content-Range for {label}; refusing to append wrong bytes.')
                        response_end = end + 1
                    elif offset:
                        raise ValueError(f'Server ignored resume Range for {label}; partial file preserved, no bytes appended.')
                    else:
                        response_end = size
                    for chunk in response.iter_raw(chunk_size=READ_BYTES):
                        if stop.is_set():
                            raise InterruptedError('Stopped; HTTP partial files are preserved.')
                        if offset + len(chunk) > response_end:
                            raise ValueError(f'Server sent too many bytes for {label}; partial file preserved.')
                        output.write(chunk)
                        offset += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 10:
                            output.flush()
                            rate = (offset - last_bytes) / (now - last_report) / 1e6
                            log(f'PROGRESS {label}: {offset / 1e9:.2f}/{size / 1e9:.2f} GB '
                                f'({100 * offset / size:.1f}%), {rate:.1f} MB/s')
                            last_report, last_bytes = now, offset
                    if offset != response_end:
                        raise RetryTransfer('Connection ended before the requested bytes arrived')
                output.flush()
                os.fsync(output.fileno())
                failures = 0
            except (httpx.TransportError, RetryTransfer) as exc:
                output.flush()
                os.fsync(output.fileno())
                failures += 1
                if failures >= MAX_FAILURES:
                    raise RuntimeError(f'{label}: {MAX_FAILURES} consecutive transfer failures; '
                                       f'{offset} bytes saved. Last error: {safe_error(exc)}') from None
                delay = max(min(60, 2 ** min(failures, 6)), getattr(exc, 'delay', 0))
                log(f'RETRY {label}: {type(exc).__name__}: {safe_error(exc)}; '
                    f'{offset} bytes saved; retry {failures}/{MAX_FAILURES} in {delay:g}s')
                if stop.wait(delay):
                    raise InterruptedError('Stopped; HTTP partial files are preserved.')
        output.flush()
        os.fsync(output.fileno())
    part.replace(destination)
    log(f'DOWNLOADED {label} ({size / 1e9:.2f} GB)')


def download_http(plan, root, workers):
    from huggingface_hub.utils import build_hf_headers
    stop = threading.Event()
    # HTTPX strips Authorization on cross-origin redirects; CDN URLs carry their own signature.
    with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=True) as client:
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = []
        try:
            headers = build_hf_headers()
            futures = [pool.submit(transfer_file, plan, root, entry, client, headers, stop)
                       for entry in plan['files']]
            errors = []
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    log(f'FILE FAILED: {safe_error(exc)}')
                    errors.append(safe_error(exc))
            if errors:
                raise RuntimeError('; '.join(errors))
        finally:
            stop.set()
            for future in futures:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)

CATALOG = [
    dict(id='Qwen3', repo='turboderp/Qwen3.8-Flash-Next-exl3',
         revision='3.05bpw_h5_ng5', folder='Qwen3.8-Flash-Next-EXL3-3.05bpw'),
    dict(id='DeepSeek3', repo='turboderp/DeepSeek-V4-Flash-0731-exl3',
         revision='3.04bpw', folder='DeepSeek-V4-Flash-0731-EXL3-3.04bpw'),
    dict(id='DeepSeekVision3', repo='turboderp/DeepSeek-V4-Flash-Vision-Exp-exl3',
         revision='3.04bpw', folder='DeepSeek-V4-Flash-Vision-Exp-EXL3-3.04bpw'),
    dict(id='Qwen4', repo='turboderp/Qwen3.8-Flash-Next-exl3',
         revision='4.05bpw_h6_ng6', folder='Qwen3.8-Flash-Next-EXL3-4.05bpw',
         required_files=['vision_k6.safetensors', 'ngram_embedding.safetensors']),
]
DEFAULT_MODELS = 'Qwen3,DeepSeek3,DeepSeekVision3'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def local_file(folder, name):
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or any(
            p in ('.', '..') or ':' in p or '\\' in p for p in parts):
        raise ValueError(f'Unsafe repository filename: {name!r}')
    path = folder.joinpath(*parts)
    if not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError(f'Repository path escapes its folder: {name!r}')
    return path


def select_models(value):
    names = {s.strip().lower() for s in value.split(',') if s.strip()}
    valid = {m['id'].lower() for m in CATALOG} | {'all'}
    if not names or names - valid:
        raise ValueError('Unknown or empty model selection.')
    return [m for m in CATALOG if 'all' in names or m['id'].lower() in names]


def make_plan(api, root, specs):
    plans = []
    for spec in specs:
        receipt = root / '.linux-llm-downloads' / (spec['id'] + '.json')
        revision = spec['revision']
        if receipt.exists():
            saved = json.loads(receipt.read_text(encoding='utf-8'))
            if any(saved.get(k) != spec[k] for k in ('repo', 'revision', 'folder')):
                raise ValueError(f'Download identity differs from saved receipt: {receipt}')
            revision = saved['commit']
            if not re.fullmatch(r'[0-9a-f]{40}', revision):
                raise ValueError(f'Invalid saved commit in {receipt}')
        print(f"Checking {spec['id']} ({spec['revision']})...", flush=True)
        for attempt in range(1, 6):
            try:
                info = api.model_info(spec['repo'], revision=revision, files_metadata=True, timeout=30)
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if attempt == 5 or (status is not None and status not in (408, 429) and status < 500):
                    raise
                delay = max(2 ** attempt, retry_delay(exc.response.headers.get('Retry-After')) if status else 0)
                log(f"METADATA RETRY {spec['id']}: {safe_error(exc)}; retrying in {delay:g}s")
                time.sleep(delay)
        if not info.sha or not re.fullmatch(r'[0-9a-f]{40}', info.sha):
            raise ValueError(f"Could not resolve a commit for {spec['repo']}")
        files = []
        folder = root / spec['folder']
        missing_bytes = 0
        for f in info.siblings or []:
            if f.size is None or f.size < 0:
                raise ValueError(f'Hub did not return a size for {f.rfilename}')
            local = local_file(folder, f.rfilename)
            files.append(dict(name=f.rfilename, size=f.size))
            # Our stable partials are sequential and belong to this pinned commit.
            if not local.is_file() or local.stat().st_size != f.size:
                part = partial_path(dict(spec, commit=info.sha), root, files[-1])
                saved_bytes = min(part.stat().st_size, f.size) if part.is_file() else 0
                missing_bytes += f.size - saved_bytes
        names = {f['name'] for f in files}
        if not {'config.json', 'tokenizer_config.json'} <= names:
            raise ValueError(f"Missing basic model metadata in {spec['id']}")
        if not set(spec.get('required_files', [])) <= names:
            raise ValueError(f"Missing required vision/embedding files in {spec['id']}")
        if not any(n.endswith('.safetensors') for n in names):
            raise ValueError(f"No safetensors model weights in {spec['id']}")
        plans.append(dict(**spec, commit=info.sha, files=files,
                          total_bytes=sum(f['size'] for f in files),
                          missing_bytes=missing_bytes))
    return plans


def check_files(plan, folder):
    for f in plan['files']:
        path = local_file(folder, f['name'])
        if not path.is_file() or path.stat().st_size != f['size']:
            raise ValueError(f'Missing or wrong-size file: {path}')
    index = folder / 'model.safetensors.index.json'
    if index.exists():
        mapping = json.loads(index.read_text(encoding='utf-8'))['weight_map']
        for name in set(mapping.values()):
            if not local_file(folder, name).is_file():
                raise ValueError(f'Index references a missing shard: {name}')


def download_one(plan, root, hf_exe, downloader, workers, verify_only):
    folder = root / plan['folder']
    receipt = root / '.linux-llm-downloads' / (plan['id'] + '.json')
    record = dict(plan, status='verifying' if verify_only else 'downloading')
    write_json(receipt, record)
    if not verify_only:
        downloader(plan, root, workers)
    check_files(plan, folder)
    record['status'] = 'verifying'
    write_json(receipt, record)
    log(f"VERIFYING {plan['id']}: reading files for Hub checksum verification (this can take several minutes)...")
    subprocess.run([hf_exe, 'cache', 'verify', plan['repo'],
                    '--revision', plan['commit'], '--local-dir', str(folder),
                    '--fail-on-missing-files'], check=True)
    record.update(status='verified', verified_utc=datetime.now(timezone.utc).isoformat())
    write_json(receipt, record)
    log(f"VERIFIED: {plan['id']} -> {folder}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--models', default=DEFAULT_MODELS)
    p.add_argument('--hf', required=True)
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--list', action='store_true')
    p.add_argument('--verify-only', action='store_true')
    p.add_argument('--http', action='store_true')
    args = p.parse_args()
    if not 1 <= args.workers <= 8:
        p.error('--workers must be between 1 and 8')
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.list:
        return run_downloads(args, root)
    from filelock import FileLock, Timeout
    state = root / '.linux-llm-downloads'
    state.mkdir(parents=True, exist_ok=True)
    lock = FileLock(state / 'download.lock', timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise RuntimeError('Another downloader is already using this model folder. Stop it first.') from None
    global LOG_PATH
    LOG_PATH = state / 'download.log'
    try:
        return run_downloads(args, root)
    finally:
        LOG_PATH = None
        lock.release()


def run_downloads(args, root):
    # Keep transfer data on the model drive. Authentication keeps using the
    # normal HF location, so a prior login or HF_TOKEN remains effective.
    os.environ['HF_XET_CACHE'] = str(root / '.linux-llm-downloads' / 'xet')
    os.environ['HF_XET_CHUNK_CACHE_SIZE_BYTES'] = '0'
    os.environ['HF_HUB_DOWNLOAD_TIMEOUT'] = '120'
    os.environ['HF_HUB_ETAG_TIMEOUT'] = '60'
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    from huggingface_hub import HfApi
    log(f'TRANSPORT: resumable HTTP v2; Xet is not used; {args.workers} file worker(s).')
    log('Transfer inactivity timeout: 30s; persistent 64 MiB ranges; up to 12 consecutive failures.')
    if LOG_PATH is not None:
        log(f'Log: {LOG_PATH}')
    plans = make_plan(HfApi(), root, select_models(args.models))
    total = sum(m['total_bytes'] for m in plans)
    remaining = sum(m['missing_bytes'] for m in plans)
    free = shutil.disk_usage(root).free
    print('\nSelected downloads:')
    for m in plans:
        print(f"  {m['id']:18} {m['total_bytes'] / 1e9:7.1f} GB  {m['folder']}")
        print(f"    Commit: {m['commit']}")
    print(f'\nTotal: {total / 1e9:.1f} GB ({total / 2**30:.1f} GiB)')
    print(f'Additional space budget: {remaining / 2**30:.1f} GiB; '
          f'free: {free / 2**30:.1f} GiB')
    print('Existing NVFP4/GGUF models and GLM are outside this download list.\n', flush=True)
    if args.list:
        return 0
    if not args.verify_only and remaining and free < remaining + 10 * 2**30:
        raise RuntimeError('Not enough free space for the remaining downloads plus '
                           '10 GiB headroom. Select fewer models or another drive. '
                           'Saved HTTP partial bytes have been deducted from this budget.')
    # Pin ALL revisions before downloading any weights, including models later
    # in the queue. Subsequent runs reuse these commits even if branches change.
    for plan in plans:
        receipt = root / '.linux-llm-downloads' / (plan['id'] + '.json')
        if not receipt.exists():
            write_json(receipt, dict(plan, status='pending'))
    failed = []
    for plan in plans:
        try:
            download_one(plan, root, args.hf, download_http, args.workers, args.verify_only)
        except KeyboardInterrupt:
            write_json(root / '.linux-llm-downloads' / (plan['id'] + '.json'),
                       dict(plan, status='interrupted'))
            raise
        except Exception as exc:
            failed.append(plan['id'])
            write_json(root / '.linux-llm-downloads' / (plan['id'] + '.json'),
                       dict(plan, status='failed', error_type=type(exc).__name__, error=safe_error(exc)))
            log(f"FAILED: {plan['id']}: {safe_error(exc)}")
    if failed:
        print('\nNot complete. Failed models: ' + ', '.join(failed), file=sys.stderr)
        print('Keep partial files and rerun after fixing the reported problem. '
              'A checksum mismatch requires repairing the file named by hf.', file=sys.stderr)
        return 1
    print('\nAll selected repositories downloaded and checksum-verified.')
    print('Receipts: ' + str(root / '.linux-llm-downloads'))
    print('The files are portable to Ubuntu; engine, vision and speed tests come next.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nStopped. Keep the partial files and rerun to resume.', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f'ERROR: {safe_error(exc)}', file=sys.stderr)
        print('No successful completion is recorded for this operation. '
              'For access errors, check the repository and rerun with -Login.', file=sys.stderr)
        sys.exit(1)
'@

$helperPath = Join-Path $toolRoot 'download_models_http_v2.py'
[System.IO.File]::WriteAllText($helperPath, $downloadCode, [System.Text.UTF8Encoding]::new($false))
$runArgs = @('-u', $helperPath, '--root', $ModelRoot, '--models', ($Models -join ','), '--hf', $hfExe, '--workers', [string]$Workers)
if ($List) { $runArgs += '--list' }
if ($VerifyOnly) { $runArgs += '--verify-only' }
if ($UseHttp) { $runArgs += '--http' }
& $pythonExe @runArgs
if ($LASTEXITCODE -ne 0) { throw "Download/verification did not complete (exit $LASTEXITCODE). Read the errors above; partial downloads have been preserved." }
