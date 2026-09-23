"""Local GUI server. Engine processes own CUDA; this process stays responsive."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import shlex
import getpass
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import time
from typing import Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
import psutil

from .engines import PROJECT, RUNTIME, Settings, engine_inventory, launch, validate
from .library import scan
from .metrics import Telemetry, number, memory_peaks
from .llama_cpp import count_prompt as llama_count_prompt, effective_settings as llama_effective_settings, normalize_usage as llama_usage
from .reasoning import ReasoningEffort, reasoning_kwargs
from .tool_calls import ToolRequest, ToolCallAccumulator, ToolResponseError, validate_tool_history
from .benchmarks import EvaluationManager, report_markdown
from .profiles import enrich_profile, record_loaded_memory
from .live_output import LiveOutput
from .archive import ABORTED, list_runs, manage_runs, run_performance
from .comfyui import release_comfyui, restore_comfyui
from .chat_api import conversation_router
from . import engines, app_settings
from .engine_updates import EngineUpdater, engine_details
from . import discover as hub
from .discover import Discovery, engine_support
from .downloads import DownloadManager
from inflect_access import access_config, lan_interfaces, private_lan

MODEL_ROOT = Path(os.environ.get('INFLECT_MODEL_ROOT', Path.home() / 'models')).expanduser()
STATE = Path(os.environ.get('INFLECT_STATE', RUNTIME / 'state'))
PORT = int(os.environ.get('INFLECT_PORT', '7860'))
LAN_NETWORK = ipaddress.ip_network(os.environ['INFLECT_LAN_NETWORK'], strict=False) if os.environ.get('INFLECT_LAN_NETWORK') else None
ALLOWED_HOSTS = {'localhost', '127.0.0.1', 'testserver'} | {
    host.strip() for host in os.environ.get('INFLECT_ALLOWED_HOSTS', '').split(',') if host.strip()
}
telemetry = Telemetry(lambda: supervisor.proc.pid if supervisor.proc and supervisor.proc.returncode is None else None)
engine_updater = EngineUpdater()
restarting = False


def client_allowed(host):
    if host == 'testclient':
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return (LAN_NETWORK is not None and address in LAN_NETWORK
            and private_lan(str(address), str(LAN_NETWORK)))


def origin_allowed(origin):
    if not origin:
        return True
    return origin in ({f'http://{host}:{PORT}' for host in ALLOWED_HOSTS}
                      | {'http://127.0.0.1:5173'})


def save_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def library_preferences():
    try:
        data = json.loads((STATE / 'library.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def library_locations():
    return [str(p) for p in library_preferences().get('locations', []) if isinstance(p, str)]


def save_library_preferences(**changes):
    save_json(STATE / 'library.json', library_preferences() | changes)


def save_library_locations(locations):
    save_library_preferences(locations=locations)


def db():
    connection = sqlite3.connect(STATE / 'results.sqlite3')
    connection.execute('CREATE TABLE IF NOT EXISTS benchmarks (id TEXT PRIMARY KEY, created REAL, data TEXT)')
    return connection


class Supervisor:
    def __init__(self):
        self.inventory = {'models': [], 'errors': [], 'root': str(MODEL_ROOT), 'locations': [], 'revision': 0}
        self.state = 'idle'
        self.error = None
        self.proc = None
        self.task = None
        self.reader = None
        self.settings = None
        self.model = None
        self.effective = None
        self.launch_configuration = None
        self.engine = None
        self.token = None
        self.port = None
        self.started = None
        self.logs = deque(maxlen=600)
        self.operation = asyncio.Lock()
        self.generation_lock = asyncio.Lock()
        self.cancel = asyncio.Event()
        self.benchmark_task = None
        self.benchmark = None
        self.last_usage = None
        self.tool_format = None
        self.live_output = LiveOutput()
        self.ready_at = None
        self.memory_recorded_for = None
        self.load_detail = None
        self.memory_preparation = None

    async def refresh(self):
        locations = library_locations()
        inventory = await asyncio.to_thread(scan, MODEL_ROOT, locations)
        self.inventory = {**inventory, 'locations': locations, 'revision': self.inventory.get('revision', 0) + 1}

    def lookup(self, model_id):
        model = next((m for m in self.inventory['models'] if m['id'] == model_id), None)
        if model is None:
            raise ValueError('Model is no longer in the library. Refresh and select it again.')
        return model

    def snapshot(self):
        return dict(state=self.state, error=self.error, model=self.model, settings=self.settings.model_dump() if self.settings else None,
                    effective=self.effective, engine=self.engine, started=self.started,
                    elapsed_seconds=round(time.time() - self.started, 1) if self.started else 0,
                    latest_log=self.logs[-1] if self.logs else None, last_usage=self.last_usage,
                    load_detail=self.load_detail, memory_preparation=self.memory_preparation,
                    live_output=self.live_output.meta, busy=self.generation_lock.locked() or evaluations.active, benchmark=self.benchmark, evaluation=evaluations.snapshot(),
                    tool_calling={'enabled': bool(self.tool_format), 'format': self.tool_format,
                                  'choices': ['auto', 'none'] if self.tool_format else ['none']})

    async def stop(self):
        async with self.operation:
            await self._stop()

    async def _stop(self):
        self.cancel.set()
        self.state = 'stopping'
        if self.benchmark_task and not self.benchmark_task.done() and self.benchmark_task != asyncio.current_task():
            self.benchmark_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.benchmark_task
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self._terminate()
        self.state, self.error = 'idle', None
        self.model = self.settings = self.effective = self.engine = self.started = None
        self.launch_configuration = None
        self.tool_format = None
        self.load_detail = None
        self.memory_preparation = None

    async def _terminate(self):
        proc = self.proc
        if proc:
            # Every child starts its own process group. Never touch unrelated servers.
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 12)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            # A worker may survive a crashed parent; clear its owned group as well.
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        self.proc = None
        if self.reader:
            with suppress(asyncio.CancelledError):
                await self.reader
        self.reader = None

    async def start(self, settings):
        async with self.operation:
            if engine_updater.active or restarting:
                raise HTTPException(409, 'Wait for the engine update or restart to finish before loading a model.')
            if evaluations.active or evaluations.preparing:
                raise HTTPException(409, 'Stop the benchmark or its preparation before loading another model.')
            model = self.lookup(settings.model_id)
            engine = validate(settings, model)
            if telemetry.value.get('gpu') is None:
                raise ValueError('CUDA GPU access is unavailable. See the hardware status and launch from Ubuntu.')
            await self._stop()
            self.model, self.settings, self.engine = model, settings, engine
            self.state, self.error = 'loading', None
            self.logs.clear()
            self.started = time.time()
            self.last_usage = None
            self.load_detail = 'Preparing to load the model…'
            self.task = asyncio.create_task(self._load())

    async def _read_logs(self, proc, log_path):
        with log_path.open('a', encoding='utf-8') as log:
            while line := await proc.stdout.readline():
                text = line.decode('utf-8', errors='replace').rstrip()
                if self.token:
                    text = text.replace(self.token, '[internal engine key]')
                self.logs.append(text)
                log.write(text + '\n')
                log.flush()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    @property
    def headers(self):
        return {'Authorization': f'Bearer {self.token}'}

    async def _load(self):
        ready = False
        def progress(message):
            self.load_detail = message
            self.logs.append('INFLECT: ' + message)
        try:
            integration = app_settings.comfy_settings()
            if integration['enabled']:
                self.memory_preparation = await release_comfyui(progress, url=integration['url'],
                    container=integration['container'] if integration['mode'] == 'docker' else '')
            else:
                self.memory_preparation = None
            if psutil.virtual_memory().available < self.model['bytes'] * self.settings.cpu_percent / 100 + 16 * 2**30:
                raise ValueError('Insufficient available RAM for this placement plus 16 GiB headroom.')
            progress('Loading model weights and context cache…')
            run_dir = STATE / 'runs' / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
            run_dir.mkdir(parents=True)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                self.port = sock.getsockname()[1]
            self.token = secrets.token_urlsafe(32)
            args, env, requested = launch(self.settings, self.model, run_dir, self.port, self.token)
            self.launch_configuration = dict(arguments=[arg.replace(self.token, '[internal engine key]') for arg in args],
                requested=requested, environment={key: env.get(key) for key in (
                    'OMP_NUM_THREADS', 'EXL3_MOE_PINNED_ARENA', 'EXL3_HOST_MEM_RESERVE_MB', 'CC',
                    'CUDA_VISIBLE_DEVICES', 'TOKENIZERS_PARALLELISM')})
            self.tool_format = requested.get('model', {}).get('tool_format') if self.engine == 'exl3' else requested.get('tool_format')
            save_json(run_dir / 'profile.json', self.settings.model_dump())
            self.proc = await asyncio.create_subprocess_exec(*args, cwd=run_dir, env=env, start_new_session=True,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=2**20)
            self.reader = asyncio.create_task(self._read_logs(self.proc, run_dir / 'engine.log'))
            deadline = time.monotonic() + 1800
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if self.proc.returncode is not None:
                        raise RuntimeError(f'Engine exited with code {self.proc.returncode}. See the engine log.')
                    try:
                        health = await client.get(self.url + '/health')
                        if health.status_code == 200:
                            if self.engine == 'exl3':
                                response = await client.get(self.url + '/v1/model', headers=self.headers)
                                response.raise_for_status()
                                card = response.json()
                                effective = card.get('parameters', {})
                                if effective.get('max_seq_len') != self.settings.context or effective.get('cache_size') != self.settings.context:
                                    raise ValueError('The engine did not allocate the requested context/cache capacity.')
                                if effective.get('cache_mode') != self.settings.kv:
                                    raise ValueError('The engine did not use the requested KV precision.')
                                if self.settings.vision and not effective.get('use_vision'):
                                    raise ValueError('The engine disabled vision. This profile cannot be marked ready.')
                                if self.settings.prediction == 'mtp' and not effective.get('draft'):
                                    raise ValueError('MTP was requested but the engine did not load a prediction component.')
                                self.effective = {k: v for k, v in effective.items() if k != 'prompt_template_content'}
                            elif self.engine == 'gguf':
                                self.effective = await llama_effective_settings(client, self.url, self.headers, self.settings, requested)
                            else:
                                self.effective = requested
                            ready = True
                            save_json(STATE / 'last-profile.json', self.settings.model_dump())
                            return
                    except (httpx.HTTPError, OSError):
                        pass
                    await asyncio.sleep(1)
            raise RuntimeError('Startup exceeded 30 minutes. The engine has been stopped; inspect its log.')
        except asyncio.CancelledError:
            # Free the partially loaded LLM before ComfyUI recreates its CUDA context.
            await self._terminate()
            raise
        except Exception as exc:
            self.error = str(exc)
            self.logs.append('INFLECT: ' + self.error)
            await self._terminate()
            self.state = 'error'
        finally:
            async def restore():
                try:
                    await restore_comfyui(self.memory_preparation, progress)
                except Exception as exc:
                    if self.memory_preparation:
                        self.memory_preparation['restore_error'] = str(exc)
                    warning = 'ComfyUI restart needs attention: ' + str(exc)
                    self.error = (self.error + ' ' if self.error else '') + warning
                    progress(warning)
            restoring = asyncio.create_task(restore())
            try:
                await asyncio.shield(restoring)
            except asyncio.CancelledError:
                await restoring
                raise
            if ready:
                self.state = 'ready'
                self.ready_at = time.time()

    async def watch(self):
        while True:
            if self.state == 'ready' and self.proc and self.proc.returncode is not None:
                self.error = f'Engine stopped unexpectedly (exit {self.proc.returncode}).'
                await self._terminate()
                self.state = 'error'
            if (self.state == 'ready' and self.settings and self.ready_at
                    and self.memory_recorded_for != self.started
                    and not self.generation_lock.locked() and not evaluations.active
                    and not (self.benchmark_task and not self.benchmark_task.done())
                    and (not telemetry.value.get('model_memory')
                         or telemetry.value['model_memory']['sampled_at'] >= self.ready_at)
                    and (telemetry.value.get('timestamp') or 0) >= self.ready_at + 3):
                try:
                    if record_loaded_memory(STATE, self.settings.model_dump(), self.model, telemetry.value):
                        self.memory_recorded_for = self.started
                except (OSError, sqlite3.Error) as exc:
                    self.logs.append('INFLECT: Could not save loaded memory measurement: ' + str(exc))
            await asyncio.sleep(1)

    def check_request(self, messages, max_output=None, reasoning_effort=None, tool_request=None):
        if evaluations.active and asyncio.current_task() != evaluations.task:
            raise HTTPException(409, 'The model is reserved for a benchmark. Stop it before chatting.')
        if self.benchmark_task and not self.benchmark_task.done() and asyncio.current_task() != self.benchmark_task:
            raise HTTPException(409, 'The model is reserved for a speed benchmark.')
        if self.state != 'ready':
            raise ValueError('Load a model before sending a message.')
        if self.generation_lock.locked():
            raise HTTPException(409, 'A request is already running. Wait for completion.')
        effort = reasoning_effort if reasoning_effort is not None else self.settings.reasoning_effort
        template_kwargs = reasoning_kwargs(self.model, effort)
        output_limit = self.settings.max_output if max_output is None else max_output
        if output_limit >= self.settings.context:
            raise ValueError('The answer limit exceeds the context window.')
        tool_payload = (tool_request or ToolRequest()).tool_payload(self.engine, self.tool_format)
        validate_tool_history(messages)
        for message in messages:
            content = message.get('content')
            if isinstance(content, list):
                for part in content:
                    if part.get('type') == 'image_url':
                        if not self.settings.vision:
                            raise ValueError('Reload with vision enabled before adding an image.')
                        url = part.get('image_url', {}).get('url', '')
                        if not url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
                            raise ValueError('Use an uploaded PNG, JPEG or WebP image.')
                        if len(url) > 16_000_000:
                            raise ValueError('Image exceeds the 12 MB upload limit.')
        return effort, template_kwargs, output_limit, tool_payload

    async def stream(self, messages, max_output=None, temperature=None, raw=None, reasoning_effort=None, tool_request=None):
        effort, template_kwargs, output_limit, tool_payload = self.check_request(messages, max_output, reasoning_effort, tool_request)
        async with self.generation_lock:
            self.cancel.clear()
            payload = dict(model=Path(self.model['path']).name if self.engine == 'exl3' else self.model['id'],
                messages=messages, temperature=self.settings.temperature if temperature is None else temperature,
                stream=True, stream_options={'include_usage': True}, **tool_payload)
            if template_kwargs:
                payload['chat_template_kwargs'] = template_kwargs
            count_kwargs = {**template_kwargs}
            if tool_payload.get('tools'):
                count_kwargs['tools'] = tool_payload['tools']
            calls = ToolCallAccumulator()
            started, first, last, usage = time.monotonic(), None, None, None
            timings = {}
            wall_started, finish_reason = time.time(), 'stop'
            def reply_budget(count, reserve):
                # A cap is an upper bound, never a reservation: the reply may
                # stop earlier only because the context window is full.
                room = self.settings.context - count - reserve
                if room < 16:
                    raise ValueError(f'Conversation uses {count:,} of {self.settings.context:,} context tokens, leaving no room for a reply. '
                                     'Start a new conversation or reload with a larger context; history is not truncated.')
                return min(output_limit, room) if output_limit else room
            budget = output_limit or None
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10), trust_env=False) as client:
                if self.engine == 'exl3':
                    count_response = await client.post(self.url + '/v1/token/encode', headers=self.headers,
                        json={'text': messages, 'chat_template_kwargs': count_kwargs})
                    if count_response.is_error:
                        raise ValueError('Engine tokenization failed: ' + count_response.text[:2000])
                    count = count_response.json()['length']
                    # Tabby's count excludes the generation prefix; leave an explicit margin.
                    budget = reply_budget(count, 128)
                    yield {'type': 'context', 'input_tokens': count, 'prefix_reserve': 128}
                elif self.engine == 'gguf':
                    count = await llama_count_prompt(client, self.url, self.headers, messages, template_kwargs, tool_payload)
                    if count is not None:
                        reserve = 16
                        budget = reply_budget(count, reserve)
                        yield {'type': 'context', 'input_tokens': count, 'prefix_reserve': reserve}
                # No max_tokens (uncounted image prompts in Auto mode) lets llama.cpp
                # generate until its context is full, like its own web UI.
                if budget:
                    payload['max_tokens'] = budget
                async with client.stream('POST', self.url + '/v1/chat/completions', headers=self.headers, json=payload) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode(errors='replace')
                        raise ValueError(detail[:3000])
                    lines = response.aiter_lines().__aiter__()
                    next_line = asyncio.create_task(anext(lines, None))
                    cancel_wait = asyncio.create_task(self.cancel.wait())
                    try:
                        while True:
                            done, _ = await asyncio.wait([next_line, cancel_wait], return_when=asyncio.FIRST_COMPLETED)
                            if cancel_wait in done:
                                yield {'type': 'cancelled'}
                                return
                            line = next_line.result()
                            if line is None:
                                break
                            next_line = asyncio.create_task(anext(lines, None))
                            if not line.startswith('data:'):
                                continue
                            data = line[5:].strip()
                            if data == '[DONE]':
                                break
                            event = json.loads(data)
                            if event.get('error'):
                                raise ValueError(str(event['error']))
                            if raw is not None:
                                raw.append(event)
                            if event.get('usage'):
                                usage = event['usage']
                            if self.engine == 'gguf' and event.get('timings'):
                                timings.update(event['timings'])
                            for choice in event.get('choices', []):
                                if choice.get('index', 0) != 0:
                                    raise ValueError('This server supports one completion choice per request.')
                                if choice.get('finish_reason'):
                                    finish_reason = choice['finish_reason']
                                delta = choice.get('delta', {})
                                if delta.get('tool_calls') is not None:
                                    calls.add(delta['tool_calls'])
                                content, reasoning = delta.get('content') or '', delta.get('reasoning_content') or delta.get('reasoning') or ''
                                if content or reasoning:
                                    now = time.monotonic()
                                    first = first or now
                                    last = now
                                    yield {'type': 'token', 'text': content, 'reasoning': reasoning}
                    finally:
                        for pending in (next_line, cancel_wait):
                            pending.cancel()
                            with suppress(asyncio.CancelledError, StopAsyncIteration):
                                await pending
            if not usage:
                raise ValueError('The engine closed the response without token usage; this run has no verified speed measurement.')
            if self.engine == 'gguf':
                usage = llama_usage(usage, timings)
            validated_calls = calls.finish(tool_payload, finish_reason)
            if validated_calls:
                now = time.monotonic()
                first = first or now
                last = now
                # Native Tabby emits complete calls at the end of the turn. Buffer
                # fragmented engines too: validate all calls before exposing any.
                yield {'type': 'tool_calls', 'tool_calls': validated_calls}
            total_time = time.monotonic() - started
            tokens = usage.get('completion_tokens', 0)
            speed = usage.get('completion_tokens_per_sec')
            source = 'engine'
            if not isinstance(speed, (int, float)):
                speed = (tokens - 1) / (last - first) if tokens > 1 and first and last and last > first else None
                source = 'client estimate'
            result = dict(usage=usage, tokens_per_second=speed, speed_source=source,
                          prompt_tokens_per_second=number(usage.get('prompt_tokens_per_sec')),
                          prompt_seconds=number(usage.get('prompt_time')),
                          first_token_seconds=first - started if first else None, total_seconds=total_time,
                          configured_context=self.settings.context, finish_reason=finish_reason, reasoning_effort=effort,
                          output_cap=output_limit or None, output_budget=budget)
            samples = [s for s in telemetry.history if s['timestamp'] >= wall_started]
            def peak(section, key):
                values = [(s.get(section) or {}).get(key) if section else s.get(key) for s in samples]
                return max((v for v in values if v is not None), default=None)
            result['hardware_peaks'] = dict(gpu_power_watts=peak('gpu', 'power_watts'),
                cpu_power_watts=peak('cpu_power', 'watts'),
                vram_bytes=peak('gpu', 'used_bytes'), cpu_percent=peak(None, 'cpu_percent'), **memory_peaks(samples))
            self.last_usage = result
            yield {'type': 'complete', **result}


supervisor = Supervisor()
evaluations = EvaluationManager(STATE, supervisor, telemetry)
discovery = Discovery()
downloads = None


async def download_finished(job):
    try:
        await supervisor.refresh()
    except Exception as exc:
        supervisor.inventory['errors'] = [str(exc)]
    folder = (MODEL_ROOT / job['folder']).resolve()
    model = next((m for m in supervisor.inventory['models'] if Path(m['path']).resolve().is_relative_to(folder)), None)
    if model:
        job['model_id'], job['model_path'] = model['id'], model['path']


@asynccontextmanager
async def lifespan(app):
    global evaluations, downloads
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    lock = (STATE / 'manager.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Inflect is already running.')
    with db():
        pass
    evaluations = EvaluationManager(STATE, supervisor, telemetry)
    downloads = DownloadManager(STATE, MODEL_ROOT, download_finished)
    evaluations.recover()
    await evaluations.cleanup_stale()
    try:
        await supervisor.refresh()
    except Exception as exc:
        supervisor.inventory['errors'] = [str(exc)]
    tasks = [asyncio.create_task(telemetry.run()), asyncio.create_task(supervisor.watch())]
    try:
        yield
    finally:
        await engine_updater.close()
        await downloads.close()
        await discovery.close()
        await evaluations.stop()
        await supervisor.stop()
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        lock.close()


app = FastAPI(title='Inflect local model workbench', lifespan=lifespan)


@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    client = request.client.host if request.client else ''
    if host not in ALLOWED_HOSTS or not client_allowed(client):
        return JSONResponse({'detail': 'Local network access only.'}, status_code=403)
    origin = request.headers.get('origin')
    if not origin_allowed(origin):
        return JSONResponse({'detail': 'Cross-origin access denied.'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('x-inflect-local') != '1':
            return JSONResponse({'detail': 'Missing X-Inflect-Local: 1 header.'}, status_code=403)
        if int(request.headers.get('content-length', '0')) > 24_000_000:
            return JSONResponse({'detail': 'Request exceeds 24 MB.'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=422)


@app.exception_handler(ToolResponseError)
async def tool_response_error(request, exc):
    return JSONResponse({'error': {'message': str(exc), 'type': 'invalid_upstream_tool_call'}}, status_code=502)


@app.get('/api/status')
async def status():
    return {'session': supervisor.snapshot(), 'hardware': telemetry.value, 'engines': engine_inventory(),
            'library_revision': supervisor.inventory.get('revision', 0),
            'downloads': downloads.summary() if downloads else None}


def active_preferences():
    return dict(model_root=str(MODEL_ROOT), gguf_server=str(engines.GGUF),
                exl_python=str(engines.EXL_PYTHON), tabby_dir=str(engines.TABBY), port=PORT,
                listen_host=os.environ.get('INFLECT_LISTEN_HOST', '127.0.0.1'),
                lan_network=str(LAN_NETWORK) if LAN_NETWORK else '')


def preferences_snapshot():
    config = app_settings.read_config()
    active = active_preferences()
    interfaces = lan_interfaces()
    saved = app_settings.values(config, active)
    target = access_config({**config, **saved}, interfaces)
    restart_keys = [key for key in ('model_root', 'gguf_server', 'exl_python', 'tabby_dir') if saved[key] != active[key]]
    restart_keys += [key for key in ('port', 'listen_host', 'lan_network') if target[key] != active[key]]
    return dict(values=saved, revision=app_settings.revision(config), interfaces=interfaces,
                active={**active, 'url': f"http://{active['listen_host']}:{PORT}"},
                next_url=target['public_url'], next_subnet=target['lan_network'],
                restart_required=bool(restart_keys), restart_keys=restart_keys,
                storage=dict(config=str(app_settings.config_path()), runtime=str(RUNTIME), data=str(STATE)))


@app.get('/api/settings')
async def get_preferences():
    return await asyncio.to_thread(preferences_snapshot)


@app.put('/api/settings')
async def put_preferences(body: app_settings.SavePreferences):
    if engine_updater.active or restarting:
        raise HTTPException(409, 'Wait for the engine update or restart to finish before saving settings.')
    # Serialize configuration writes on the event loop; no await between busy check and save.
    app_settings.save_preferences(body, active_preferences())
    return preferences_snapshot()


@app.get('/api/settings/engines')
async def settings_engines():
    return dict(engines=await asyncio.to_thread(engine_details), update=engine_updater.snapshot())


@app.post('/api/settings/engines/{engine_id}/update', status_code=202)
async def update_engine(engine_id: Literal['gguf', 'exl3']):
    async with supervisor.operation:
        if (supervisor.state not in ('idle', 'error') or supervisor.generation_lock.locked()
                or evaluations.active or evaluations.preparing or restarting):
            raise HTTPException(409, 'Unload the model and finish any benchmark before updating an engine.')
        engine_updater.start(engine_id, STATE, active_preferences())
    return engine_updater.snapshot()


@app.get('/api/settings/comfyui/discover')
async def discover_comfyui():
    from .comfyui import docker, reserved_bytes
    containers, error = [], None
    try:
        output = await docker('ps', '-a', '--format', '{{json .}}')
        for line in output.splitlines():
            item = json.loads(line)
            if 'comfy' in (item.get('Names', '') + ' ' + item.get('Image', '')).lower():
                containers.append(dict(name=item['Names'], state=item.get('State', ''), ports=item.get('Ports', '')))
    except (OSError, ValueError, RuntimeError) as exc:
        error = str(exc)
    endpoint = app_settings.comfy_settings()['url']
    detected = False
    try:
        from .comfyui import local_url
        async with httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False) as client:
            response = await client.get(local_url(endpoint) + '/system_stats')
            response.raise_for_status()
            reserved_bytes(response.json())
            detected = True
    except (httpx.HTTPError, ValueError):
        pass
    return dict(containers=containers, url=endpoint, detected=detected, docker_error=error)


@app.post('/api/settings/restart')
async def restart_app(background: BackgroundTasks):
    global restarting
    async with supervisor.operation:
        if (engine_updater.active or restarting or supervisor.generation_lock.locked()
                or supervisor.state in ('loading', 'stopping') or evaluations.active or evaluations.preparing):
            raise HTTPException(409, 'Finish the current load, request, benchmark, or engine update before restarting.')
        next_state = preferences_snapshot()
        # The replacement waits for this process to finish releasing its model and lock.
        environment = os.environ.copy()
        for key in ('INFLECT_LISTEN_HOST', 'INFLECT_LAN_NETWORK', 'INFLECT_ALLOWED_HOSTS', 'INFLECT_PUBLIC_URL',
                    'INFLECT_PORT', 'INFLECT_MODEL_ROOT', 'INFLECT_GGUF_SERVER', 'INFLECT_EXL_PYTHON', 'INFLECT_TABBY'):
            environment.pop(key, None)
        environment['INFLECT_CONFIG_FILE'] = str(app_settings.config_path())
        with (STATE / 'manager.log').open('a') as log:
            subprocess.Popen([sys.executable, str(PROJECT / 'launch.py'), '--no-browser', '--wait-for-pid', str(os.getpid())],
                             cwd=PROJECT, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        restarting = True
    async def shutdown_for_restart():
        await asyncio.sleep(.5)
        os.kill(os.getpid(), signal.SIGTERM)
    background.add_task(shutdown_for_restart)
    return dict(status='restarting', url=next_state['next_url'])


@app.get('/api/library')
async def library():
    return supervisor.inventory


LIBRARY_SORTS = ('custom', 'name', 'size', 'fit', 'recent')


class LibraryOrder(BaseModel):
    order: list[str] = Field(default_factory=list, max_length=5000)
    sort: Literal[LIBRARY_SORTS] = 'custom'


@app.get('/api/library/order')
async def library_order():
    data = library_preferences()
    order = [k for k in data.get('order', []) if isinstance(k, str)]
    return dict(order=order, sort=data.get('sort') if data.get('sort') in LIBRARY_SORTS else 'custom')


@app.put('/api/library/order')
async def save_library_order(body: LibraryOrder):
    order = list(dict.fromkeys(k[:300] for k in body.order))
    save_library_preferences(order=order, sort=body.sort)
    return dict(order=order, sort=body.sort)


@app.post('/api/library/refresh')
async def refresh():
    await supervisor.refresh()
    return supervisor.inventory


class LibraryLocation(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


def model_entries(folder):
    """Folders and model files in one directory, for the add-to-library browser."""
    entries = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if item.name.startswith('.'):
            continue
        try:
            if item.is_dir():
                kind = 'model-folder' if (item / 'config.json').is_file() and any(item.glob('*.safetensors')) else (
                    'gguf-folder' if any(item.glob('*.gguf')) else 'folder')
                entries.append(dict(name=item.name, path=str(item), kind=kind))
            elif item.suffix.lower() == '.gguf' and 'mmproj' not in item.name.lower():
                entries.append(dict(name=item.name, path=str(item), kind='gguf', size=item.stat().st_size))
        except OSError:
            continue
        if len(entries) >= 400:
            break
    return entries


@app.get('/api/library/browse')
async def browse_library(path: str = Query('', max_length=4096)):
    folder = Path(path).expanduser() if path else Path.home()
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError('That folder is unavailable. Check the path or choose another place.')
    folder = folder.resolve()
    shortcuts = [dict(name='Home', path=str(Path.home())), dict(name='Model folder', path=str(MODEL_ROOT))]
    for mounts in (Path('/run/media') / getpass.getuser(), Path('/media') / getpass.getuser(), Path('/mnt')):
        if mounts.is_dir():
            shortcuts += [dict(name=m.name, path=str(m)) for m in sorted(mounts.iterdir()) if m.is_dir()][:8]
    entries = await asyncio.to_thread(model_entries, folder)
    return dict(path=str(folder), parent=str(folder.parent) if folder.parent != folder else None,
                entries=entries, shortcuts=shortcuts)


@app.post('/api/library/locations')
async def add_library_location(body: LibraryLocation):
    location = Path(body.path.strip()).expanduser()
    if not location.is_absolute() or not location.exists():
        raise ValueError('That location does not exist on this computer.')
    location = location.resolve()
    if location.is_relative_to(MODEL_ROOT.resolve()):
        raise ValueError('This is already inside your model folder; it is included automatically.')
    if location.is_file() and location.suffix.lower() != '.gguf':
        raise ValueError('Choose a .gguf file or a folder that contains models.')
    found = await asyncio.to_thread(scan, MODEL_ROOT, [str(location)])
    if not any(not Path(m['path']).resolve().is_relative_to(MODEL_ROOT.resolve()) for m in found['models']):
        raise ValueError('No compatible models were found there. Inflect looks for GGUF files and EXL3 or safetensors model folders.')
    locations = library_locations()
    if str(location) not in locations:
        save_library_locations(locations + [str(location)])
    await supervisor.refresh()
    return supervisor.inventory


@app.post('/api/library/locations/remove')
async def remove_library_location(body: LibraryLocation):
    save_library_locations([p for p in library_locations() if p != body.path])
    await supervisor.refresh()
    return supervisor.inventory


@app.get('/api/discover/engines')
async def discover_engines():
    support = await asyncio.to_thread(engine_support)
    try:
        free = shutil.disk_usage(MODEL_ROOT).free
    except OSError:
        free = None
    return dict(engines=support, disk_free=free, model_root=str(MODEL_ROOT), hub_token=hub.token_source() is not None)


@app.get('/api/discover/search')
async def discover_search(q: str = Query('', max_length=120)):
    return await discovery.search(await asyncio.to_thread(engine_support), q)


@app.get('/api/discover/family')
async def discover_family(key: str = Query(min_length=1, max_length=200), name: str = Query(min_length=1, max_length=200)):
    return await discovery.family(await asyncio.to_thread(engine_support), key, name)


class DownloadRequest(BaseModel):
    option_id: str = Field(min_length=1, max_length=64)


class HubToken(BaseModel):
    token: str = Field(max_length=512)


async def reset_hub_client():
    await discovery.close()
    discovery.cache.clear()
    discovery.options.clear()


@app.get('/api/huggingface')
async def huggingface_status():
    token, source = hub.hub_token(), hub.token_source()
    status = dict(connected=bool(token), source=source, masked=hub.mask_token(token) if token else None,
                  user=None, role=None, error=None, file=str(hub.TOKEN_FILE))
    if token:
        try:
            status.update(await hub.whoami(token))
        except ValueError as exc:
            status['error'] = str(exc)
        except httpx.HTTPError:
            status['error'] = 'Hugging Face could not be reached to check the token.'
    return status


@app.post('/api/huggingface')
async def huggingface_save(body: HubToken):
    token = body.token.strip()
    if not re.fullmatch(r'hf_[A-Za-z0-9]{20,}', token):
        raise ValueError('That does not look like a Hugging Face access token. It starts with hf_ and has no spaces.')
    if hub.TOKEN_FILE.resolve().is_relative_to(PROJECT.resolve()):
        raise ValueError('Refusing to store the token inside the Inflect project folder.')
    try:
        account = await hub.whoami(token)
    except httpx.HTTPError:
        raise ValueError('Hugging Face could not be reached to check the token. Try again when online.') from None
    hub.save_token(token)
    await reset_hub_client()
    return await huggingface_status() | account


@app.post('/api/huggingface/remove')
async def huggingface_remove():
    hub.remove_token()
    await reset_hub_client()
    return await huggingface_status()


@app.get('/api/downloads')
async def download_list():
    return downloads.snapshot()


@app.post('/api/downloads')
async def download_start(body: DownloadRequest):
    option = discovery.option(body.option_id)
    if option['gated'] and not hub.hub_token():
        raise ValueError('This model asks you to accept its licence on Hugging Face first. Accept it on the model page, '
                         'then add a Hugging Face access token in Settings → Model library.')
    downloads.add(option)
    return downloads.snapshot()


@app.post('/api/downloads/{job_id}/{action}')
async def download_action(job_id: str, action: Literal['pause', 'resume', 'cancel', 'dismiss', 'seen']):
    if action == 'pause':
        downloads.pause(job_id)
    elif action == 'resume':
        downloads.resume(job_id)
    elif action == 'cancel':
        await downloads.cancel(job_id)
    elif action == 'dismiss':
        downloads.dismiss(job_id)
    return downloads.snapshot()


@app.post('/api/downloads/seen')
async def downloads_seen():
    downloads.mark_seen()
    return downloads.snapshot()


@app.post('/api/load', status_code=202)
async def load(settings: Settings):
    await supervisor.start(settings)
    return supervisor.snapshot()


@app.post('/api/unload')
async def unload():
    if evaluations.active:
        raise HTTPException(409, 'Stop the benchmark before unloading its model.')
    await supervisor.stop()
    return supervisor.snapshot()


@app.post('/api/cancel')
async def cancel():
    if evaluations.active:
        await evaluations.stop()
        return {'status': 'cancelled'}
    supervisor.cancel.set()
    if supervisor.benchmark_task and not supervisor.benchmark_task.done():
        supervisor.benchmark_task.cancel()
        with suppress(asyncio.CancelledError):
            await supervisor.benchmark_task
        if supervisor.benchmark and supervisor.benchmark.get('state') == 'running':
            supervisor.benchmark['state'] = 'cancelled'
            supervisor.live_output.finish(supervisor.benchmark)
    return {'status': 'cancelling'}


@app.post('/api/quit')
async def quit_app(background: BackgroundTasks):
    if engine_updater.active or restarting:
        raise HTTPException(409, 'Wait for the engine update or restart to finish before quitting.')
    await evaluations.stop()
    await supervisor.stop()
    async def shutdown():
        await asyncio.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)
    background.add_task(shutdown)
    return {'status': 'closed'}


@app.get('/api/logs')
async def logs():
    return {'lines': list(supervisor.logs)}


class ChatRequest(ToolRequest):
    messages: list[dict] = Field(min_length=1, max_length=1000)
    max_output: int | None = Field(None, ge=0, le=32768)
    temperature: float | None = Field(None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None


@app.post('/api/token-count')
async def token_count(body: ChatRequest):
    if supervisor.state != 'ready' or supervisor.engine not in ('exl3', 'gguf'):
        raise ValueError('Exact counting is available after loading an ExLlamaV3 or llama.cpp model.')
    effort = body.reasoning_effort if body.reasoning_effort is not None else supervisor.settings.reasoning_effort
    tool_payload = body.tool_payload(supervisor.engine, supervisor.tool_format)
    validate_tool_history(body.messages)
    template_kwargs = reasoning_kwargs(supervisor.model, effort)
    if tool_payload.get('tools'):
        template_kwargs['tools'] = tool_payload['tools']
    async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
        if supervisor.engine == 'gguf':
            count = await llama_count_prompt(client, supervisor.url, supervisor.headers, body.messages, template_kwargs, tool_payload)
            if count is None:
                raise ValueError('llama.cpp reports image token usage after generation; advance counting supports text conversations only.')
            return {'input_tokens': count, 'prefix_reserve': 16, 'capacity': supervisor.settings.context}
        response = await client.post(supervisor.url + '/v1/token/encode', headers=supervisor.headers,
            json={'text': body.messages, 'chat_template_kwargs': template_kwargs})
        if response.is_error:
            raise ValueError('Engine tokenization failed: ' + response.text[:2000])
        return {'input_tokens': response.json()['length'], 'prefix_reserve': 128, 'capacity': supervisor.settings.context}


@app.post('/api/chat')
async def chat(body: ChatRequest):
    supervisor.check_request(body.messages, body.max_output, body.reasoning_effort, body)
    async def events():
        try:
            async for event in supervisor.stream(body.messages, body.max_output, body.temperature, reasoning_effort=body.reasoning_effort, tool_request=body):
                yield 'data: ' + json.dumps(event) + '\n\n'
        except Exception as exc:
            yield 'data: ' + json.dumps({'type': 'error', 'message': str(exc)}) + '\n\n'
    return StreamingResponse(events(), media_type='text/event-stream')


@app.get('/v1/models')
async def openai_models():
    return {'object': 'list', 'data': [{'id': supervisor.model['id'], 'object': 'model', 'owned_by': 'local'}] if supervisor.state == 'ready' else []}


@app.post('/v1/chat/completions')
async def openai_chat(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError('Request body must be an object.')
    if body.get('n', 1) != 1:
        raise ValueError('Only n=1 is supported.')
    for legacy in ('functions', 'function_call'):
        if legacy in body:
            raise ValueError('Legacy function calling is unsupported. Use tools and tool_choice.')
    if not supervisor.model or body.get('model') not in (supervisor.model['id'], supervisor.model['name'], None):
        raise HTTPException(404, 'Requested model is not loaded.')
    checked = ChatRequest(messages=body.get('messages', []), max_output=body.get('max_completion_tokens', body.get('max_tokens')), temperature=body.get('temperature'),
                          reasoning_effort='off' if body.get('reasoning_effort') == 'none' else body.get('reasoning_effort'),
                          tools=body.get('tools'), tool_choice=body.get('tool_choice'), parallel_tool_calls=body.get('parallel_tool_calls', True))
    # Validate before opening SSE so unsupported modes/levels are HTTP errors,
    # rather than an HTTP 200 followed by a silently ignored option.
    supervisor.check_request(checked.messages, checked.max_output, checked.reasoning_effort, checked)
    identity = {'id': 'chatcmpl-' + secrets.token_hex(12), 'created': int(time.time()), 'model': supervisor.model['id']}
    if body.get('stream'):
        async def events():
            try:
                yield 'data: ' + json.dumps({**identity, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]}) + '\n\n'
                async for event in supervisor.stream(checked.messages, checked.max_output, checked.temperature, reasoning_effort=checked.reasoning_effort, tool_request=checked):
                    if event['type'] == 'token':
                        for key, value in [('reasoning_content', event['reasoning']), ('content', event['text'])]:
                            if value:
                                yield 'data: ' + json.dumps({**identity, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {key: value}, 'finish_reason': None}]}) + '\n\n'
                    elif event['type'] == 'tool_calls':
                        deltas = [{'index': index, **call} for index, call in enumerate(event['tool_calls'])]
                        yield 'data: ' + json.dumps({**identity, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'tool_calls': deltas}, 'finish_reason': None}]}) + '\n\n'
                    elif event['type'] == 'cancelled':
                        raise ValueError('Generation was cancelled before completion.')
                    elif event['type'] == 'error':
                        raise ValueError(event['message'])
                    elif event['type'] == 'complete':
                        yield 'data: ' + json.dumps({**identity, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': event['finish_reason']}], 'usage': event['usage']}) + '\n\n'
                yield 'data: [DONE]\n\n'
            except Exception as exc:
                yield 'data: ' + json.dumps({'error': {'message': str(exc)}}) + '\n\n'
        return StreamingResponse(events(), media_type='text/event-stream')
    content, reasoning, usage, finish_reason, calls = '', '', None, 'stop', []
    async for event in supervisor.stream(checked.messages, checked.max_output, checked.temperature, reasoning_effort=checked.reasoning_effort, tool_request=checked):
        if event['type'] == 'token':
            content += event['text']
            reasoning += event['reasoning']
        elif event['type'] == 'tool_calls':
            calls = event['tool_calls']
        elif event['type'] == 'cancelled':
            raise HTTPException(409, 'Generation was cancelled before completion.')
        elif event['type'] == 'complete':
            usage = event['usage']
            finish_reason = event['finish_reason']
    message = {'role': 'assistant', 'content': content or (None if calls else ''), 'reasoning_content': reasoning}
    if calls:
        message['tool_calls'] = calls
    return {**identity, 'object': 'chat.completion', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish_reason}], 'usage': usage}


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    settings: Settings
    auto_name: bool = False

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Give this profile a name.')
        return value


def read_profiles():
    path = STATE / 'profiles.json'
    saved = json.loads(path.read_text()) if path.exists() else []
    changed = False
    for profile in saved:
        settings = Settings.model_validate(profile['settings']).model_dump()
        if settings != profile['settings']:
            profile['settings'] = settings
            changed = True
        if not profile.get('id'):
            profile.update(id=secrets.token_hex(8), updated_at=time.time())
            changed = True
    if changed:
        save_json(path, saved)
    return saved


def ensure_profile_name(saved, name, except_id=None):
    if any(p['name'].strip().casefold() == name.casefold() and p['id'] != except_id
           and not p.get('deleted_at') for p in saved):
        raise HTTPException(409, 'A profile with this name already exists. Choose another name or edit that profile.')


def next_copy(saved, name, model_id):
    active = [p for p in saved if not p.get('deleted_at')]
    names = {p['name'].casefold() for p in active}
    base = re.sub(r' · copy \d+$', '', name)[:155]
    used_numbers = {p.get('copy_number') for p in active if p['settings']['model_id'] == model_id}
    index = 1
    while f'{base} · copy {index}'.casefold() in names or index in used_numbers:
        index += 1
    return f'{base} · copy {index}', index


def find_profile(saved, profile_id):
    profile = next((p for p in saved if p['id'] == profile_id), None)
    if profile is None:
        raise HTTPException(404, 'This profile no longer exists. Refresh the profile list.')
    return profile


@app.get('/api/profiles')
async def profiles(trash: bool = False):
    saved = [p for p in read_profiles() if bool(p.get('deleted_at')) == trash]
    results = []
    with db() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('benchmarks', 'evaluations'):
            if table not in tables:
                continue
            # Read summary fields only, leaving generated answers and trajectories on disk.
            fields = ('id', 'created', 'settings', 'state', 'median_tps', 'median_prompt_tps', 'benchmark', 'summary', 'performance')
            select = ', '.join(f"json_extract(data, '$.{field}')" for field in fields)
            for row in connection.execute(f"SELECT {select} FROM {table} WHERE json_extract(data, '$.deleted_at') IS NULL AND json_extract(data, '$.state') NOT IN ('running', 'cancelled', 'interrupted', 'aborted') ORDER BY created DESC"):
                result = dict(zip(fields, row))
                result['kind'] = 'speed' if table == 'benchmarks' else 'coding'
                for field in ('settings', 'summary', 'performance'):
                    result[field] = json.loads(result[field]) if result[field] else {}
                if table == 'evaluations':
                    performance = result['performance']
                    if not performance:
                        # Older runs already contain timings. Read only their numeric metrics,
                        # rather than generated answers, grading output, or trajectories.
                        metrics = connection.execute("""SELECT json_group_array(json_object(
                            'tokens_per_second', json_extract(m.value, '$.tokens_per_second'),
                            'prompt_tokens_per_second', json_extract(m.value, '$.prompt_tokens_per_second'),
                            'speed_source', json_extract(m.value, '$.speed_source')))
                            FROM evaluations e, json_each(e.data, '$.tasks') t, json_each(t.value, '$.metrics') m
                            WHERE e.id=?""", (result['id'],)).fetchone()[0]
                        performance = run_performance({'tasks': [{'metrics': json.loads(metrics)}]})
                    result['median_tps'] = performance.get('decode_tps')
                    result['median_prompt_tps'] = performance.get('prefill_tps')
                elif result['median_prompt_tps'] is None:
                    # Early speed tests stored engine prefill timings under usage only.
                    values = connection.execute("""SELECT COALESCE(
                        json_extract(r.value, '$.prompt_tokens_per_second'),
                        json_extract(r.value, '$.usage.prompt_tokens_per_sec'))
                        FROM benchmarks b, json_each(b.data, '$.runs') r WHERE b.id=?""", (result['id'],)).fetchall()
                    measured = [v[0] for v in values if type(v[0]) in (int, float) and math.isfinite(v[0]) and v[0] > 0]
                    result['median_prompt_tps'] = statistics.median(measured) if measured else None
                results.append(result)
        loads = {key: json.loads(data) for key, data in connection.execute('SELECT key, data FROM profile_loads')} if 'profile_loads' in tables else {}
    results.sort(key=lambda item: item.get('created') or 0, reverse=True)
    models = {m['id']: m for m in supervisor.inventory['models']}
    return [enrich_profile(profile, models.get(profile['settings']['model_id']), results, loads) for profile in saved]


class ProfileOrder(BaseModel):
    ids: list[str]


@app.post('/api/profiles/reorder')
async def reorder_profiles(body: ProfileOrder):
    saved = read_profiles()
    active = {p['id']: p for p in saved if not p.get('deleted_at')}
    if len(body.ids) != len(active) or set(body.ids) != set(active):
        raise HTTPException(409, 'The profile list changed. Refresh it before reordering.')
    save_json(STATE / 'profiles.json', [active[identity] for identity in body.ids] + [p for p in saved if p.get('deleted_at')])
    return await profiles()


@app.post('/api/profiles/{profile_id}/duplicate')
async def duplicate_profile(profile_id: str):
    saved = read_profiles()
    original = find_profile(saved, profile_id)
    if original.get('deleted_at'):
        raise HTTPException(409, 'Restore this profile before duplicating it.')
    name, index = next_copy(saved, original['name'], original['settings']['model_id'])
    duplicate = {**original, 'id': secrets.token_hex(8), 'name': name,
                 'copy_number': index, 'updated_at': time.time()}
    saved.insert(saved.index(original) + 1, duplicate)
    save_json(STATE / 'profiles.json', saved)
    return await profiles()


@app.post('/api/profiles')
async def save_profile(body: ProfileRequest):
    validate(body.settings, supervisor.lookup(body.settings.model_id), check_install=False)
    saved = read_profiles()
    profile = dict(**body.model_dump(), id=secrets.token_hex(8), updated_at=time.time())
    taken = any(p['name'].strip().casefold() == body.name.casefold() for p in saved if not p.get('deleted_at'))
    if body.auto_name and taken:
        # Generated names leave out temperature and thinking, so distinct setups can share one.
        profile['name'], profile['copy_number'] = next_copy(saved, body.name, body.settings.model_id)
    else:
        ensure_profile_name(saved, body.name)
    saved.append(profile)
    save_json(STATE / 'profiles.json', saved)
    return await profiles()


@app.put('/api/profiles/{profile_id}')
async def update_profile(profile_id: str, body: ProfileRequest):
    saved = read_profiles()
    profile = find_profile(saved, profile_id)
    if profile.get('deleted_at'):
        raise HTTPException(409, 'Restore this profile before editing it.')
    # Generated names leave out output, temperature and thinking, so an edited
    # setup may share one with another setup; only chosen names must be unique.
    if not body.auto_name:
        ensure_profile_name(saved, body.name, profile_id)
    # Renaming a profile remains possible when its model drive is disconnected.
    if body.settings.model_dump() != profile['settings']:
        validate(body.settings, supervisor.lookup(body.settings.model_id), check_install=False)
    profile.update(**body.model_dump(), updated_at=time.time())
    save_json(STATE / 'profiles.json', saved)
    return await profiles()


@app.delete('/api/profiles/{profile_id}')
async def delete_profile(profile_id: str):
    saved = read_profiles()
    profile = find_profile(saved, profile_id)
    profile['deleted_at'] = time.time()
    save_json(STATE / 'profiles.json', saved)
    return await profiles()


@app.post('/api/profiles/{profile_id}/restore')
async def restore_profile(profile_id: str):
    saved = read_profiles()
    profile = find_profile(saved, profile_id)
    if not profile.get('auto_name'):
        ensure_profile_name(saved, profile['name'], profile_id)
    profile.pop('deleted_at', None)
    profile['updated_at'] = time.time()
    save_json(STATE / 'profiles.json', saved)
    return await profiles()


@app.get('/api/benchmarks')
async def benchmarks():
    return list_runs(STATE, 'speed', limit=100)['items']


class EvaluationRequest(BaseModel):
    suite: Literal['humaneval', 'swebench']
    model_id: str
    budget_minutes: int = Field(30, ge=5, le=30)
    reasoning_effort: ReasoningEffort | None = None


class PreparationRequest(BaseModel):
    suite: Literal['humaneval', 'swebench']


@app.get('/api/evaluations/status')
async def evaluation_status():
    return await evaluations.availability()


@app.get('/api/evaluations')
async def evaluation_list(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                          suite: Literal['humaneval', 'swebench'] | None = None, q: str = Query('', max_length=200),
                          model_id: str | None = None, sort: Literal['newest', 'oldest', 'score'] = 'newest',
                          status: Literal['complete', 'timed_out', 'error', 'failed'] | None = None, trash: bool = False):
    return evaluations.list(limit, offset, suite, q, model_id=model_id, sort=sort, status=status, trash=trash)


@app.get('/api/archive/speed')
async def speed_archive(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                        q: str = Query('', max_length=200), model_id: str | None = None,
                        sort: Literal['newest', 'oldest', 'score'] = 'newest',
                        status: Literal['complete', 'timed_out', 'error', 'failed'] | None = None, trash: bool = False):
    return list_runs(STATE, 'speed', limit, offset, query=q, model_id=model_id, sort=sort, status=status, trash=trash)


@app.get('/api/archive/models')
async def archive_models(q: str = Query('', max_length=200)):
    query = re.sub(r'[-_\s]+', ' ', q.lower()).strip()
    return [{'id': m['id'], 'name': m['name'], 'quant': m['quant'], 'format': m['format']}
            for m in supervisor.inventory['models']
            if all(term in re.sub(r'[-_\s]+', ' ', f"{m['name']} {m['quant']} {m['format']}".lower())
                   for term in query.split())][:50]


class ArchiveAction(BaseModel):
    kind: Literal['coding', 'speed']
    ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal['delete', 'restore']


@app.post('/api/archive/manage')
async def manage_archive(body: ArchiveAction):
    state = evaluations.state if body.kind == 'coding' else STATE
    return manage_runs(state, body.kind, body.ids, body.action)


@app.get('/api/evaluations/install-instructions')
async def evaluation_install_instructions():
    return {'command': shlex.join(['pkexec', '/bin/bash', str(PROJECT / 'scripts/install-benchmark-docker.sh'), '--user', getpass.getuser()])}


@app.post('/api/evaluations/prepare', status_code=202)
async def evaluation_prepare(body: PreparationRequest):
    return await evaluations.prepare(body.suite)


@app.get('/api/evaluations/preparation-log')
async def evaluation_preparation_log(suite: Literal['humaneval', 'swebench']):
    path = evaluations.assets / f'{suite}-setup.log'
    if not path.is_file():
        raise HTTPException(404, 'No preparation log is available for this benchmark yet.')
    return FileResponse(path, media_type='text/plain', filename=f'inflect-{suite}-setup.log')


@app.post('/api/evaluations', status_code=202)
async def evaluation_start(body: EvaluationRequest):
    return await evaluations.start(body.suite, body.model_id, body.budget_minutes * 60, body.reasoning_effort)


@app.post('/api/evaluations/stop')
async def evaluation_stop():
    await evaluations.stop()
    return {'status': 'stopped'}


@app.get('/api/evaluations/{run_id}')
async def evaluation_detail(run_id: str):
    return evaluations.get(run_id)


@app.get('/api/evaluations/{run_id}/report')
async def evaluation_report(run_id: str):
    result = evaluations.get(run_id)
    return PlainTextResponse(report_markdown(result), headers={'Content-Disposition': f'attachment; filename="inflect-{run_id}.md"'})


@app.get('/api/evaluations/{run_id}/json')
async def evaluation_json(run_id: str):
    return JSONResponse(evaluations.get(run_id), headers={'Content-Disposition': f'attachment; filename="inflect-{run_id}.json"'})


@app.get('/api/evaluations/{run_id}/archive')
async def evaluation_archive(run_id: str):
    result = evaluations.get(run_id)
    if result['state'] == 'running':
        raise HTTPException(409, 'Stop or finish this run before downloading its complete archive.')
    path = await asyncio.to_thread(evaluations.archive, run_id)
    return FileResponse(path, media_type='application/zip', filename=f'inflect-{run_id}.zip')


class BenchmarkRequest(BaseModel):
    reasoning_effort: ReasoningEffort | None = None


@app.get('/api/benchmark-output')
async def benchmark_output(after: int = Query(0, ge=0), run_id: str = ''):
    return supervisor.live_output.read(after, run_id)


@app.post('/api/benchmark', status_code=202)
async def benchmark(body: BenchmarkRequest | None = None):
    if evaluations.active or evaluations.preparing:
        raise HTTPException(409, 'Stop the coding benchmark or preparation before running a speed test.')
    if supervisor.state != 'ready' or supervisor.generation_lock.locked() or (supervisor.benchmark_task and not supervisor.benchmark_task.done()):
        raise ValueError('Load a model and finish the current request before benchmarking.')
    effort = body.reasoning_effort if body and body.reasoning_effort is not None else supervisor.settings.reasoning_effort
    reasoning_kwargs(supervisor.model, effort)
    run_id, created = secrets.token_hex(8), time.time()
    supervisor.live_output.start(run_id, 'speed', 'Speed test', supervisor.model)
    supervisor.benchmark = {'id': run_id, 'state': 'running', 'created': created,
                            'elapsed_seconds': 0, 'completed': 0, 'total': 3}
    supervisor.benchmark_task = asyncio.create_task(run_benchmark(effort))
    return supervisor.benchmark


async def run_benchmark(effort):
    started = time.monotonic()
    result = dict(id=supervisor.benchmark.get('id') or secrets.token_hex(8),
                  created=supervisor.benchmark.get('created') or time.time(), model=supervisor.model,
                  settings={**supervisor.settings.model_dump(), 'reasoning_effort': effort},
                  engine=supervisor.engine, effective=supervisor.effective, runs=[], kind='short-prompt',
                  notes='Three unique prompts with 512-token output limits and the profile temperature. Configured capacity preserved. Read cached-token counts; capacity is not a filled-context test.')
    prompts = [
        'Write a practical guide to designing a Python file indexer. Discuss traversal, incremental updates, error handling, concurrency, and a concrete implementation. Be detailed.',
        'Explain how a modern city could design reliable public transport. Cover timetables, transfers, accessibility, financing, and how success would be measured. Be detailed.',
        'Write a Python implementation of an LRU cache, followed by a detailed explanation of its invariants, complexity, and meaningful test cases. Continue until the design is fully explained.']
    try:
        for i, prompt in enumerate(prompts):
            supervisor.live_output.append('status', f'\nPrompt {i + 1}/3\n')
            supervisor.live_output.append('prompt', prompt + '\n')
            messages = [{'role': 'user', 'content': f'{secrets.token_hex(16)} is a unique test identifier; ignore it.\n' + prompt}]
            text, metrics = '', None
            async for event in supervisor.stream(messages, 512, supervisor.settings.temperature, reasoning_effort=effort):
                if event['type'] == 'token':
                    text += event['text']
                    supervisor.live_output.append('reasoning', event.get('reasoning', ''))
                    supervisor.live_output.append('model', event['text'])
                if event['type'] == 'complete':
                    metrics = event
            if metrics is None:
                raise ValueError('Benchmark cancelled or incomplete.')
            supervisor.live_output.append('status', f"\nGeneration: {metrics.get('tokens_per_second')} tok/s · Prefill: {metrics.get('prompt_tokens_per_second')} tok/s\n")
            result['runs'].append(dict(metrics, output_preview=text[:600]))
            supervisor.benchmark['completed'] = i + 1
            supervisor.benchmark['elapsed_seconds'] = round(time.monotonic() - started, 2)
        speeds = [r['tokens_per_second'] for r in result['runs'] if r['tokens_per_second'] is not None]
        result['median_tps'] = statistics.median(speeds) if speeds else None
        prompt_speeds = [r['prompt_tokens_per_second'] for r in result['runs'] if r.get('prompt_tokens_per_second') is not None]
        result['median_prompt_tps'] = statistics.median(prompt_speeds) if prompt_speeds else None
        result['state'] = 'complete'
    except asyncio.CancelledError:
        result['state'] = 'cancelled'
    except Exception as exc:
        result.update(state='cancelled' if supervisor.cancel.is_set() else 'failed', error=str(exc))
    result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    if result['state'] not in ABORTED:
        with db() as connection:
            connection.execute('INSERT INTO benchmarks VALUES (?, ?, ?)', (result['id'], result['created'], json.dumps(result)))
    supervisor.live_output.finish(result)
    supervisor.benchmark = {'id': result['id'], 'state': result['state'], 'created': result['created'],
                            'elapsed_seconds': result['elapsed_seconds'], 'completed': len(result['runs']),
                            'total': 3, 'error': result.get('error')}


app.include_router(conversation_router(lambda: STATE, supervisor))


DIST = PROJECT / 'frontend/dist'
if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')


@app.get('/inflect.svg')
async def brand_icon():
    return FileResponse(PROJECT / 'assets/inflect.svg', media_type='image/svg+xml')


@app.get('/')
async def index():
    if not (DIST / 'index.html').is_file():
        raise HTTPException(503, 'Build the frontend first.')
    return FileResponse(DIST / 'index.html')
