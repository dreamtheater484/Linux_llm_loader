"""Resumable model downloads into one dedicated folder per model.

Files are fetched into a hidden staging folder inside the model library, in
parallel byte ranges, then checksum-verified against the Hub and moved into
place in one rename. A download interrupted by a pause, a network failure or
a restart continues from its saved byte positions."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import time
from urllib.parse import quote

import httpx

from .discover import HUB, hub_headers

GIB = 2**30
SEGMENT_MIN = 256 * 2**20
SEGMENTS = 4
READ = 1 << 20
MAX_FAILURES = 12
PUBLIC = ('id', 'title', 'family', 'repo', 'owner', 'variant', 'quant', 'engine', 'format', 'folder', 'total',
          'done', 'verified', 'speed', 'eta', 'state', 'error', 'created', 'finished', 'file_count', 'model_path', 'vision')


class Permanent(Exception):
    pass


class Stop(Exception):
    pass


def local_path(folder, name, flatten):
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or any(p in ('.', '..') or '\\' in p or ':' in p for p in parts):
        raise Permanent(f'Unsafe file name in repository: {name}')
    path = folder / parts[-1] if flatten else folder.joinpath(*parts)
    if not path.resolve().is_relative_to(folder.resolve()):
        raise Permanent(f'Repository file escapes its folder: {name}')
    return path


def git_blob_sha1(path):
    digest = hashlib.sha1(b'blob %d\0' % path.stat().st_size)
    with path.open('rb') as f:
        while chunk := f.read(READ):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadManager:
    def __init__(self, state, root, on_complete=None):
        self.path = Path(state) / 'downloads.json'
        self.root = Path(root)
        self.on_complete = on_complete
        self.jobs = []
        self.task = None
        self.stop_requested = {}
        self.last_save = 0
        try:
            self.jobs = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.jobs = []
        for job in self.jobs:
            if job['state'] in ('downloading', 'verifying', 'queued'):
                job.update(state='paused', speed=None, eta=None)
                job['note'] = 'Paused when Inflect closed. Resume to continue where it stopped.'

    # Persistence and public state ------------------------------------------------
    def save(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_save < 3:
            return
        self.last_save = now
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.jobs, indent=1), encoding='utf-8')
        temporary.replace(self.path)

    def public(self, job):
        return {k: job.get(k) for k in PUBLIC} | {'note': job.get('note'), 'model_id': job.get('model_id')}

    def snapshot(self):
        return [self.public(job) for job in reversed(self.jobs)]

    def summary(self):
        active = [j for j in self.jobs if j['state'] in ('downloading', 'verifying', 'queued')]
        current = next((j for j in self.jobs if j['state'] in ('downloading', 'verifying')), None)
        return dict(active=len(active), current=self.public(current) if current else None,
                    finished=sum(1 for j in self.jobs if j['state'] == 'complete' and not j.get('seen')))

    def find(self, job_id):
        job = next((j for j in self.jobs if j['id'] == job_id), None)
        if job is None:
            raise ValueError('This download is no longer listed.')
        return job

    # Commands --------------------------------------------------------------------------
    def add(self, option):
        if any(j['option_id'] == option['id'] and j['state'] not in ('cancelled', 'failed') for j in self.jobs):
            raise ValueError('This model is already in your downloads.')
        self.root.mkdir(parents=True, exist_ok=True)
        folder = option['folder']
        taken = {j['folder'] for j in self.jobs if j['state'] not in ('cancelled', 'failed')}
        base, index = folder, 2
        while (self.root / folder).exists() or folder in taken:
            folder, index = f'{base}-{index}', index + 1
        total = sum(f['size'] for f in option['files'])
        free = shutil.disk_usage(self.root).free
        queued = sum(j['total'] - j['done'] for j in self.jobs if j['state'] in ('queued', 'downloading', 'paused'))
        if free < total + queued + 2 * GIB:
            raise ValueError(f'Not enough free space on the model drive: this needs {total / GIB:.1f} GiB plus 2 GiB spare, '
                             f'{free / GIB:.1f} GiB is free{" after queued downloads" if queued else ""}.')
        job = dict(id=secrets.token_hex(6), option_id=option['id'], title=option['title'], family=option['family'],
                   repo=option['repo'], owner=option['owner'], variant=option['variant'], quant=option['quant'],
                   engine=option['engine'], format=option['format'], vision=option.get('vision'),
                   revision=option['revision'], commit=option['commit'], folder=folder,
                   flatten=option['engine'] == 'gguf', files=[dict(f, done=0) for f in option['files']],
                   total=total, done=0, verified=0, speed=None, eta=None, state='queued', error=None,
                   created=time.time(), finished=None, file_count=len(option['files']))
        self.jobs.append(job)
        self.save(force=True)
        self.kick()
        return job

    def pause(self, job_id):
        job = self.find(job_id)
        if job['state'] == 'queued':
            job['state'] = 'paused'
        elif job['state'] in ('downloading', 'verifying'):
            self.stop_requested[job_id] = 'paused'
        self.save(force=True)

    def resume(self, job_id):
        job = self.find(job_id)
        if job['state'] in ('paused', 'failed'):
            job.update(state='queued', error=None, note=None)
            self.save(force=True)
            self.kick()

    async def cancel(self, job_id):
        job = self.find(job_id)
        if job['state'] in ('downloading', 'verifying'):
            self.stop_requested[job_id] = 'cancelled'
            while job['state'] in ('downloading', 'verifying'):
                await asyncio.sleep(.1)
        if job['state'] != 'complete':
            job.update(state='cancelled', speed=None, eta=None)
            await asyncio.to_thread(shutil.rmtree, self.staging(job), True)
        self.save(force=True)

    def dismiss(self, job_id):
        job = self.find(job_id)
        if job['state'] in ('downloading', 'verifying', 'queued'):
            raise ValueError('Pause or cancel this download before removing it from the list.')
        if job['state'] == 'paused':
            raise ValueError('Cancel this paused download to discard its partial files first.')
        if job['state'] == 'failed':
            shutil.rmtree(self.staging(job), ignore_errors=True)
        self.jobs.remove(job)
        self.save(force=True)

    def mark_seen(self):
        for job in self.jobs:
            if job['state'] == 'complete':
                job['seen'] = True
        self.save(force=True)

    # Worker -------------------------------------------------------------------------
    def kick(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def close(self):
        for job in self.jobs:
            if job['state'] in ('downloading', 'verifying'):
                self.stop_requested[job['id']] = 'paused'
        if self.task:
            with suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self.task), 15)
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.save(force=True)

    def staging(self, job):
        return self.root / '.inflect-downloads' / job['id']

    async def run(self):
        while True:
            job = next((j for j in self.jobs if j['state'] == 'queued'), None)
            if job is None:
                return
            await self.run_job(job)

    def check_stop(self, job):
        reason = self.stop_requested.get(job['id'])
        if reason:
            raise Stop(reason)

    async def run_job(self, job):
        job.update(state='downloading', error=None, note=None, started=time.time())
        staging = self.staging(job)
        self.save(force=True)
        sampler = asyncio.create_task(self.sample_speed(job))
        try:
            staging.mkdir(parents=True, exist_ok=True)
            headers = hub_headers() | {'Accept-Encoding': 'identity'}
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                                         headers=headers, limits=httpx.Limits(max_connections=SEGMENTS + 2)) as client:
                for entry in sorted(job['files'], key=lambda f: f['size']):
                    await self.fetch_file(job, entry, staging, client)
            job.update(state='verifying', speed=None, eta=None, verified=0)
            self.save(force=True)
            for entry in job['files']:
                await self.verify(job, entry, staging)
            destination = self.root / job['folder']
            if destination.exists():
                raise Permanent(f'A folder named {job["folder"]} appeared while downloading. Rename it and retry.')
            await asyncio.to_thread(os.replace, staging, destination)
            with suppress(OSError):
                staging.parent.rmdir()
            self.write_receipt(job)
            job.update(state='complete', finished=time.time(), speed=None, eta=None, done=job['total'])
            self.save(force=True)
            if self.on_complete:
                await self.on_complete(job)
        except Stop as stop:
            job.update(state='cancelled' if stop.args[0] == 'cancelled' else 'paused', speed=None, eta=None)
            if job['state'] == 'cancelled':
                await asyncio.to_thread(shutil.rmtree, staging, True)
        except asyncio.CancelledError:
            job.update(state='paused', speed=None, eta=None)
            raise
        except (Permanent, OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            job.update(state='failed', error=str(exc) or type(exc).__name__, speed=None, eta=None)
        finally:
            self.stop_requested.pop(job['id'], None)
            sampler.cancel()
            self.save(force=True)

    async def sample_speed(self, job):
        last_bytes, last_time, speed = job['done'], time.monotonic(), None
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            rate = (job['done'] - last_bytes) / max(now - last_time, .001)
            last_bytes, last_time = job['done'], now
            speed = rate if speed is None else speed * .75 + rate * .25
            job['speed'] = speed
            remaining = job['total'] - job['done']
            job['eta'] = remaining / speed if speed and speed > 1024 else None
            self.save()

    async def fetch_file(self, job, entry, staging, client):
        target = local_path(staging, entry['path'], job['flatten'])
        if target.is_file() and target.stat().st_size == entry['size']:
            self.credit(job, entry, entry['size'])
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + '.part')
        progress_file = target.with_name(target.name + '.progress')
        size = entry['size']
        segments = None
        if part.is_file() and progress_file.is_file():
            with suppress(OSError, ValueError, KeyError):
                saved = json.loads(progress_file.read_text())
                if saved['size'] == size:
                    segments = saved['segments']
        if segments is None:
            count = 1 if size < SEGMENT_MIN else SEGMENTS
            step = math.ceil(size / count) if size else 0
            segments = [[i * step, min(size, (i + 1) * step) - 1, i * step] for i in range(count)] if size else []
            with part.open('wb') as f:
                f.truncate(size)
        self.credit(job, entry, sum(s[2] - s[0] for s in segments))
        url = f"{HUB}/{job['repo']}/resolve/{job['commit']}/{quote(entry['path'], safe='/')}"
        fd = os.open(part, os.O_RDWR)
        try:
            async def persist():
                await asyncio.to_thread(os.fsync, fd)
                progress_file.write_text(json.dumps({'size': size, 'segments': segments}))

            async def worker(segment):
                failures = 0
                while segment[2] <= segment[1]:
                    self.check_stop(job)
                    try:
                        async with client.stream('GET', url, headers={'Range': f'bytes={segment[2]}-{segment[1]}'}) as response:
                            if response.status_code in (401, 403) and failures >= 1:
                                raise Permanent('Hugging Face refused this download. The model may need you to accept its licence while signed in (huggingface-cli login).')
                            if response.status_code in (401, 403, 408, 429) or response.status_code >= 500:
                                raise httpx.TransportError(f'HTTP {response.status_code}')
                            if response.status_code not in (200, 206):
                                raise Permanent(f'Hugging Face returned HTTP {response.status_code} for {entry["path"]}.')
                            if response.status_code == 200 and (segment[2] != 0 or segment[1] != size - 1):
                                raise Permanent('The server ignored a resume request; partial data was kept.')
                            async for chunk in response.aiter_raw(READ):
                                self.check_stop(job)
                                chunk = chunk[:segment[1] + 1 - segment[2]]
                                if not chunk:
                                    break
                                # Synchronous on purpose: a cancelled thread must never write after close.
                                os.pwrite(fd, chunk, segment[2])
                                segment[2] += len(chunk)
                                self.credit(job, entry, len(chunk), add=True)
                        failures = 0
                    except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
                        failures += 1
                        if failures >= MAX_FAILURES:
                            raise RuntimeError(f'The connection kept failing ({exc}). Resume to try again; nothing downloaded is lost.') from None
                        job['note'] = f'Connection interrupted; retrying ({failures}/{MAX_FAILURES})…'
                        await asyncio.sleep(min(60, 2 ** min(failures, 6)))
                        job['note'] = None

            async def checkpoint():
                while True:
                    await asyncio.sleep(5)
                    await persist()
            saver = asyncio.create_task(checkpoint())
            tasks = [asyncio.create_task(worker(s)) for s in segments]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            finally:
                saver.cancel()
                with suppress(asyncio.CancelledError):
                    await saver
                await persist()
        finally:
            os.close(fd)
        part.replace(target)
        progress_file.unlink(missing_ok=True)

    def credit(self, job, entry, amount, add=False):
        entry['done'] = entry.get('done', 0) + amount if add else amount
        job['done'] = sum(f.get('done', 0) for f in job['files'])

    async def verify(self, job, entry, staging):
        target = local_path(staging, entry['path'], job['flatten'])
        if not target.is_file() or target.stat().st_size != entry['size']:
            raise Permanent(f'{entry["path"]} is incomplete after downloading. Resume to repair it.')
        if entry.get('sha256'):
            def digest():
                sha = hashlib.sha256()
                with target.open('rb') as f:
                    while chunk := f.read(8 * READ):
                        self.check_stop(job)
                        sha.update(chunk)
                        job['verified'] += len(chunk)
                return sha.hexdigest()
            if await asyncio.to_thread(digest) != entry['sha256']:
                target.unlink(missing_ok=True)
                entry['done'] = 0
                job['done'] = sum(f.get('done', 0) for f in job['files'])
                raise Permanent(f'{entry["path"]} failed its checksum and was removed. Resume to download it again.')
        elif entry.get('blob') and entry['size'] < 64 * 2**20:
            if await asyncio.to_thread(git_blob_sha1, target) != entry['blob']:
                target.unlink(missing_ok=True)
                raise Permanent(f'{entry["path"]} failed its checksum and was removed. Resume to download it again.')
            job['verified'] += entry['size']
        else:
            job['verified'] += entry['size']

    def write_receipt(self, job):
        folder = self.root / '.linux-llm-downloads'
        folder.mkdir(parents=True, exist_ok=True)
        receipt = dict(id=job['id'], repo=job['repo'], revision=job['revision'], commit=job['commit'],
                       folder=job['folder'], status='verified', source='inflect',
                       verified_utc=datetime.now(timezone.utc).isoformat(),
                       files=[dict(name=PurePosixPath(f['path']).name if job['flatten'] else f['path'], size=f['size'])
                              for f in job['files']])
        temporary = folder / f"inflect-{job['id']}.json.tmp"
        temporary.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        temporary.replace(folder / f"inflect-{job['id']}.json")
