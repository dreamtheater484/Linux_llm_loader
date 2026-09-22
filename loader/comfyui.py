"""Release ComfyUI model/cache allocations before starting an inference engine."""
import asyncio
import errno
import ipaddress
import math
import os
import json
import re
from urllib.parse import urlsplit

import httpx


async def docker(*args):
    """Bounded CLI call; never invoke a shell or stop an inferred container."""
    proc = await asyncio.create_subprocess_exec('docker', *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 25)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError('ComfyUI container control failed: ' + err.decode(errors='replace').strip()[:500])
    return out.decode().strip()


async def container_info(name):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name):
        raise ValueError('INFLECT_COMFYUI_CONTAINER must be an explicit Docker container name or ID.')
    info = json.loads(await docker('inspect', '--format', '{{json .}}', name))
    # Pin subsequent commands to the inspected ID, not a potentially reused name.
    return info['Id'], info['State']['Running']


async def restore_comfyui(preparation, progress, *, timeout=45, poll_interval=1):
    if not preparation or not preparation.get('restart_required'):
        return
    progress('Restarting ComfyUI with empty model caches…')
    await docker('start', preparation['container_id'])
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    try:
                        response = await client.get(preparation['url'] + '/system_stats')
                        response.raise_for_status()
                        reserved_bytes(response.json())
                        preparation['restart_required'] = False
                        preparation['backend_restored'] = True
                        progress('ComfyUI restarted. Its previous models and caches are unloaded.')
                        return
                    except (httpx.HTTPError, ValueError):
                        await asyncio.sleep(poll_interval)
        except TimeoutError:
            raise RuntimeError('ComfyUI was started, but its API did not become ready within 45 seconds.') from None


async def stop_managed_comfyui(progress, url, container, *, timeout, poll_interval):
    container_id, running = await container_info(container)
    if not running:
        return dict(state='not_running', url=url, container_id=container_id)
    preparation = dict(state='stopped', url=url, container_id=container_id,
                       restart_required=True, verification='container_stopped')
    progress('Waiting for ComfyUI to become idle…')
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    response = await client.get(url + '/queue')
                    response.raise_for_status()
                    if not queue_busy(response.json()):
                        break
                    await asyncio.sleep(poll_interval)
        except TimeoutError:
            raise RuntimeError('ComfyUI still has running or queued jobs. No jobs were cancelled; '
                               'finish them and retry the LLM load.') from None
    progress('Stopping ComfyUI to release all of its GPU memory…')
    async def stop_and_verify():
        await docker('stop', '--time', '10', container_id)
        _, still_running = await container_info(container_id)
        if still_running:
            raise RuntimeError('ComfyUI container is still running; the LLM was not started.')
    # Cancellation must not abandon a Docker stop still executing in the daemon.
    stopping = asyncio.create_task(stop_and_verify())
    try:
        await asyncio.shield(stopping)
    except BaseException:
        try:
            await stopping
        finally:
            await restore_comfyui(preparation, progress)
        raise
    progress('ComfyUI stopped; its GPU memory is released. Loading the LLM next…')
    return preparation


def local_url(value):
    parsed = urlsplit(value)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
        port = parsed.port
    except (ValueError, TypeError):
        local = False
        port = None
    if (not local or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/') or port == 0):
        raise ValueError('INFLECT_COMFYUI_URL must be a local ComfyUI address, such as http://127.0.0.1:8188.')
    return value.rstrip('/')


def connection_refused(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OSError) and exc.errno == errno.ECONNREFUSED:
            return True
        if isinstance(exc, BaseExceptionGroup) and any(connection_refused(child) for child in exc.exceptions):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def reserved_bytes(stats):
    if not isinstance(stats, dict) or not isinstance(stats.get('system'), dict) or not stats['system'].get('comfyui_version'):
        raise ValueError('The configured address did not identify itself as ComfyUI.')
    devices = stats.get('devices')
    if not isinstance(devices, list):
        raise ValueError('ComfyUI did not report its GPU memory.')
    total = 0
    for device in devices:
        if not isinstance(device, dict) or 'type' not in device:
            raise ValueError('ComfyUI returned invalid device information.')
        if device['type'] != 'cuda':
            continue
        value = device.get('torch_vram_total')
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('ComfyUI did not report its CUDA allocator memory.')
        total += value
    return total


def queue_busy(queue):
    if not isinstance(queue, dict) or any(not isinstance(queue.get(k), list) for k in ('queue_running', 'queue_pending')):
        raise ValueError('ComfyUI did not report a valid execution queue.')
    return bool(queue['queue_running'] or queue['queue_pending'])


async def release_comfyui(progress, *, url=None, timeout=120, poll_interval=1):
    """Wait for an idle queue, request /free, then observe allocator release.

    /free is asynchronous. Require two idle/empty samples after yielding to
    its worker. No queue cancellation, backend shutdown or CUDA reset is used.
    """
    url = local_url(url if url is not None else os.environ.get('INFLECT_COMFYUI_URL', 'http://127.0.0.1:8188'))
    container = os.environ.get('INFLECT_COMFYUI_CONTAINER')
    if container:
        return await stop_managed_comfyui(progress, url, container, timeout=timeout, poll_interval=poll_interval)
    last_message = None
    def report(message):
        nonlocal last_message
        if message != last_message:
            last_message = message
            progress(message)

    async def get(client, path):
        response = await client.get(url + path)
        response.raise_for_status()
        return response.json()

    report('Checking ComfyUI before loading the model…')
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            try:
                before = reserved_bytes(await get(client, '/system_stats'))
            except httpx.ConnectError as exc:
                if not connection_refused(exc):
                    raise
                report('ComfyUI is not running; continuing with the model load.')
                return dict(state='not_running', url=url)
            async with asyncio.timeout(timeout):
                requested, empty_samples = False, 0
                while True:
                    if queue_busy(await get(client, '/queue')):
                        report('Waiting for ComfyUI’s running and queued jobs to finish…')
                        requested, empty_samples = False, 0
                    elif not requested:
                        report('Unloading ComfyUI models and clearing its caches…')
                        response = await client.post(url + '/free', json={'unload_models': True, 'free_memory': True})
                        response.raise_for_status()
                        requested = True
                    else:
                        remaining = reserved_bytes(await get(client, '/system_stats'))
                        empty_samples = empty_samples + 1 if remaining == 0 else 0
                        if empty_samples >= 2 and not queue_busy(await get(client, '/queue')):
                            report('ComfyUI models/cache released. Its backend and CUDA context stay running.')
                            return dict(state='released', url=url, allocator_before_bytes=before,
                                        allocator_after_bytes=remaining, backend_kept_running=True)
                    await asyncio.sleep(poll_interval)
    except TimeoutError:
        raise RuntimeError('ComfyUI has not finished releasing its models/cache, or retains allocator memory. '
                           'Configure INFLECT_COMFYUI_CONTAINER for confirmed backend cleanup. '
                           'The new LLM was not started.') from None
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f'Could not release ComfyUI memory: {exc}. The new LLM was not started.') from exc
