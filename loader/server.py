"""Local GUI server. Engine processes own CUDA; this process stays responsive."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import statistics
import time
from typing import Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
import psutil

from .engines import PROJECT, RUNTIME, Settings, engine_inventory, launch, validate
from .library import scan
from .metrics import Telemetry, number

MODEL_ROOT = Path(os.environ.get('LUMEN_MODEL_ROOT', Path.home() / 'models')).expanduser()
STATE = Path(os.environ.get('LUMEN_STATE', RUNTIME / 'state'))
PORT = int(os.environ.get('LUMEN_PORT', '7860'))
LAN_NETWORK = ipaddress.ip_network(os.environ['LUMEN_LAN_NETWORK'], strict=False) if os.environ.get('LUMEN_LAN_NETWORK') else None
ALLOWED_HOSTS = {'localhost', '127.0.0.1', 'testserver'} | {
    host.strip() for host in os.environ.get('LUMEN_ALLOWED_HOSTS', '').split(',') if host.strip()
}
telemetry = Telemetry()


def client_allowed(host):
    if host == 'testclient':
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return LAN_NETWORK is not None and address in LAN_NETWORK


def origin_allowed(origin):
    if not origin:
        return True
    return origin in ({f'http://{host}:{PORT}' for host in ALLOWED_HOSTS}
                      | {'http://127.0.0.1:5173'})


def save_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def db():
    connection = sqlite3.connect(STATE / 'results.sqlite3')
    connection.execute('CREATE TABLE IF NOT EXISTS benchmarks (id TEXT PRIMARY KEY, created REAL, data TEXT)')
    return connection


class Supervisor:
    def __init__(self):
        self.inventory = {'models': [], 'errors': [], 'root': str(MODEL_ROOT)}
        self.state = 'idle'
        self.error = None
        self.proc = None
        self.task = None
        self.reader = None
        self.settings = None
        self.model = None
        self.effective = None
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

    async def refresh(self):
        self.inventory = await asyncio.to_thread(scan, MODEL_ROOT)

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
                    busy=self.generation_lock.locked(), benchmark=self.benchmark)

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
            model = self.lookup(settings.model_id)
            engine = validate(settings, model)
            if telemetry.value.get('gpu') is None:
                raise ValueError('CUDA GPU access is unavailable. See the hardware status and launch from Ubuntu.')
            await self._stop()
            if psutil.virtual_memory().available < model['bytes'] * settings.cpu_percent / 100 + 16 * 2**30:
                raise ValueError('Insufficient available RAM for this placement plus 16 GiB headroom.')
            self.model, self.settings, self.engine = model, settings, engine
            self.state, self.error = 'loading', None
            self.logs.clear()
            self.started = time.time()
            self.last_usage = None
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
        try:
            run_dir = STATE / 'runs' / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
            run_dir.mkdir(parents=True)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                self.port = sock.getsockname()[1]
            self.token = secrets.token_urlsafe(32)
            args, env, requested = launch(self.settings, self.model, run_dir, self.port, self.token)
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
                            else:
                                self.effective = requested
                            self.state = 'ready'
                            save_json(STATE / 'last-profile.json', self.settings.model_dump())
                            return
                    except (httpx.HTTPError, OSError):
                        pass
                    await asyncio.sleep(1)
            raise RuntimeError('Startup exceeded 30 minutes. The engine has been stopped; inspect its log.')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)
            self.logs.append('LUMEN: ' + self.error)
            await self._terminate()
            self.state = 'error'

    async def watch(self):
        while True:
            if self.state == 'ready' and self.proc and self.proc.returncode is not None:
                self.error = f'Engine stopped unexpectedly (exit {self.proc.returncode}).'
                await self._terminate()
                self.state = 'error'
            await asyncio.sleep(1)

    async def stream(self, messages, max_output=None, temperature=None, raw=None):
        if self.state != 'ready':
            raise ValueError('Load a model before sending a message.')
        if self.generation_lock.locked():
            raise ValueError('A request is already running. Stop it or wait for completion.')
        async with self.generation_lock:
            self.cancel.clear()
            output_limit = max_output or self.settings.max_output
            if output_limit >= self.settings.context:
                raise ValueError('The answer limit exceeds the context window.')
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
            payload = dict(model=Path(self.model['path']).name if self.engine == 'exl3' else self.model['id'],
                messages=messages, max_tokens=output_limit, temperature=self.settings.temperature if temperature is None else temperature,
                stream=True, stream_options={'include_usage': True})
            if self.engine == 'exl3':
                payload.update(chat_template_kwargs={'enable_thinking': self.settings.thinking},
                               reasoning_budget_tokens=min(1024, output_limit // 2) if self.settings.thinking else 0)
            started, first, last, usage = time.monotonic(), None, None, None
            wall_started, finish_reason = time.time(), 'stop'
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10), trust_env=False) as client:
                if self.engine == 'exl3':
                    count_response = await client.post(self.url + '/v1/token/encode', headers=self.headers,
                        json={'text': messages, 'chat_template_kwargs': {'enable_thinking': self.settings.thinking}})
                    if count_response.is_error:
                        raise ValueError('Engine tokenization failed: ' + count_response.text[:2000])
                    count = count_response.json()['length']
                    # Tabby's count excludes the generation prefix; leave an explicit margin.
                    if count + output_limit + 128 > self.settings.context:
                        raise ValueError(f'Conversation uses {count:,} tokens before the reply prefix. Reduce it or reserve a smaller answer; history is not truncated.')
                    yield {'type': 'context', 'input_tokens': count, 'prefix_reserve': 128}
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
                            for choice in event.get('choices', []):
                                if choice.get('finish_reason'):
                                    finish_reason = choice['finish_reason']
                                delta = choice.get('delta', {})
                                content, reasoning = delta.get('content') or '', delta.get('reasoning_content') or ''
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
                          configured_context=self.settings.context, finish_reason=finish_reason)
            samples = [s for s in telemetry.history if s['timestamp'] >= wall_started]
            def peak(section, key):
                values = [(s.get(section) or {}).get(key) if section else s.get(key) for s in samples]
                return max((v for v in values if v is not None), default=None)
            result['hardware_peaks'] = dict(gpu_power_watts=peak('gpu', 'power_watts'),
                cpu_power_watts=peak('cpu_power', 'watts'),
                vram_bytes=peak('gpu', 'used_bytes'), ram_bytes=peak('ram', 'used_bytes'), cpu_percent=peak(None, 'cpu_percent'))
            self.last_usage = result
            yield {'type': 'complete', **result}


supervisor = Supervisor()


@asynccontextmanager
async def lifespan(app):
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    lock = (STATE / 'manager.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Lumen is already running.')
    with db():
        pass
    try:
        await supervisor.refresh()
    except Exception as exc:
        supervisor.inventory['errors'] = [str(exc)]
    tasks = [asyncio.create_task(telemetry.run()), asyncio.create_task(supervisor.watch())]
    try:
        yield
    finally:
        await supervisor.stop()
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        lock.close()


app = FastAPI(title='Lumen local model workbench', lifespan=lifespan)


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
        if request.headers.get('x-lumen-local') != '1':
            return JSONResponse({'detail': 'Missing X-Lumen-Local: 1 header.'}, status_code=403)
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


@app.get('/api/status')
async def status():
    return {'session': supervisor.snapshot(), 'hardware': telemetry.value, 'engines': engine_inventory()}


@app.get('/api/library')
async def library():
    return supervisor.inventory


@app.post('/api/library/refresh')
async def refresh():
    await supervisor.refresh()
    return supervisor.inventory


@app.post('/api/load', status_code=202)
async def load(settings: Settings):
    await supervisor.start(settings)
    return supervisor.snapshot()


@app.post('/api/unload')
async def unload():
    await supervisor.stop()
    return supervisor.snapshot()


@app.post('/api/cancel')
async def cancel():
    supervisor.cancel.set()
    return {'status': 'cancelling'}


@app.post('/api/quit')
async def quit_app(background: BackgroundTasks):
    await supervisor.stop()
    async def shutdown():
        await asyncio.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)
    background.add_task(shutdown)
    return {'status': 'closed'}


@app.get('/api/logs')
async def logs():
    return {'lines': list(supervisor.logs)}


class ChatRequest(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=1000)
    max_output: int | None = Field(None, ge=16, le=32768)
    temperature: float | None = Field(None, ge=0, le=2)


@app.post('/api/token-count')
async def token_count(body: ChatRequest):
    if supervisor.state != 'ready' or supervisor.engine != 'exl3':
        raise ValueError('Exact counting is available after loading an ExLlamaV3 model.')
    async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
        response = await client.post(supervisor.url + '/v1/token/encode', headers=supervisor.headers,
            json={'text': body.messages, 'chat_template_kwargs': {'enable_thinking': supervisor.settings.thinking}})
        if response.is_error:
            raise ValueError('Engine tokenization failed: ' + response.text[:2000])
        return {'input_tokens': response.json()['length'], 'prefix_reserve': 128, 'capacity': supervisor.settings.context}


@app.post('/api/chat')
async def chat(body: ChatRequest):
    async def events():
        try:
            async for event in supervisor.stream(body.messages, body.max_output, body.temperature):
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
    if not supervisor.model or body.get('model') not in (supervisor.model['id'], supervisor.model['name'], None):
        raise HTTPException(404, 'Requested model is not loaded.')
    checked = ChatRequest(messages=body.get('messages', []), max_output=body.get('max_tokens'), temperature=body.get('temperature'))
    identity = {'id': 'chatcmpl-' + secrets.token_hex(12), 'created': int(time.time()), 'model': supervisor.model['id']}
    if body.get('stream'):
        async def events():
            try:
                async for event in supervisor.stream(checked.messages, checked.max_output, checked.temperature):
                    if event['type'] == 'token':
                        yield 'data: ' + json.dumps({**identity, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': event['text'], 'reasoning_content': event['reasoning']}}]}) + '\n\n'
                    elif event['type'] == 'error':
                        raise ValueError(event['message'])
                    elif event['type'] == 'complete':
                        yield 'data: ' + json.dumps({**identity, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': event['finish_reason']}], 'usage': event['usage']}) + '\n\n'
                yield 'data: [DONE]\n\n'
            except Exception as exc:
                yield 'data: ' + json.dumps({'error': {'message': str(exc)}}) + '\n\n'
        return StreamingResponse(events(), media_type='text/event-stream')
    content, reasoning, usage, finish_reason = '', '', None, 'stop'
    async for event in supervisor.stream(checked.messages, checked.max_output, checked.temperature):
        if event['type'] == 'token':
            content += event['text']
            reasoning += event['reasoning']
        elif event['type'] == 'complete':
            usage = event['usage']
            finish_reason = event['finish_reason']
    return {**identity, 'object': 'chat.completion', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content, 'reasoning_content': reasoning}, 'finish_reason': finish_reason}], 'usage': usage}


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    settings: Settings

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


def find_profile(saved, profile_id):
    profile = next((p for p in saved if p['id'] == profile_id), None)
    if profile is None:
        raise HTTPException(404, 'This profile no longer exists. Refresh the profile list.')
    return profile


@app.get('/api/profiles')
async def profiles(trash: bool = False):
    return [p for p in read_profiles() if bool(p.get('deleted_at')) == trash]


@app.post('/api/profiles')
async def save_profile(body: ProfileRequest):
    validate(body.settings, supervisor.lookup(body.settings.model_id), check_install=False)
    saved = read_profiles()
    ensure_profile_name(saved, body.name)
    saved.append(dict(**body.model_dump(), id=secrets.token_hex(8), updated_at=time.time()))
    save_json(STATE / 'profiles.json', saved)
    return await profiles()


@app.put('/api/profiles/{profile_id}')
async def update_profile(profile_id: str, body: ProfileRequest):
    saved = read_profiles()
    profile = find_profile(saved, profile_id)
    if profile.get('deleted_at'):
        raise HTTPException(409, 'Restore this profile before editing it.')
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
    ensure_profile_name(saved, profile['name'], profile_id)
    profile.pop('deleted_at', None)
    profile['updated_at'] = time.time()
    save_json(STATE / 'profiles.json', saved)
    return await profiles()


@app.get('/api/benchmarks')
async def benchmarks():
    with db() as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT data FROM benchmarks ORDER BY created DESC LIMIT 100')]


@app.post('/api/benchmark', status_code=202)
async def benchmark():
    if supervisor.state != 'ready' or supervisor.generation_lock.locked() or (supervisor.benchmark_task and not supervisor.benchmark_task.done()):
        raise ValueError('Load a model and finish the current request before benchmarking.')
    supervisor.benchmark = {'state': 'running', 'completed': 0, 'total': 3}
    supervisor.benchmark_task = asyncio.create_task(run_benchmark())
    return supervisor.benchmark


async def run_benchmark():
    result = dict(id=secrets.token_hex(8), created=time.time(), model=supervisor.model, settings=supervisor.settings.model_dump(),
                  engine=supervisor.engine, effective=supervisor.effective, runs=[], kind='short-prompt',
                  notes='Three unique prompts, configured capacity preserved. Read cached-token counts; capacity is not a filled-context test.')
    prompts = [
        'Write a practical guide to designing a Python file indexer. Discuss traversal, incremental updates, error handling, concurrency, and a concrete implementation. Be detailed.',
        'Explain how a modern city could design reliable public transport. Cover timetables, transfers, accessibility, financing, and how success would be measured. Be detailed.',
        'Write a Python implementation of an LRU cache, followed by a detailed explanation of its invariants, complexity, and meaningful test cases. Continue until the design is fully explained.']
    try:
        for i, prompt in enumerate(prompts):
            messages = [{'role': 'user', 'content': f'{secrets.token_hex(16)} is a unique test identifier; ignore it.\n' + prompt}]
            text, metrics = '', None
            async for event in supervisor.stream(messages, 512, 0.7):
                if event['type'] == 'token':
                    text += event['text']
                if event['type'] == 'complete':
                    metrics = event
            if metrics is None:
                raise ValueError('Benchmark cancelled or incomplete.')
            result['runs'].append(dict(metrics, output_preview=text[:600]))
            supervisor.benchmark['completed'] = i + 1
        speeds = [r['tokens_per_second'] for r in result['runs'] if r['tokens_per_second'] is not None]
        result['median_tps'] = statistics.median(speeds) if speeds else None
        prompt_speeds = [r['prompt_tokens_per_second'] for r in result['runs'] if r.get('prompt_tokens_per_second') is not None]
        result['median_prompt_tps'] = statistics.median(prompt_speeds) if prompt_speeds else None
        result['state'] = 'complete'
    except asyncio.CancelledError:
        result['state'] = 'cancelled'
    except Exception as exc:
        result.update(state='cancelled' if supervisor.cancel.is_set() else 'failed', error=str(exc))
    with db() as connection:
        connection.execute('INSERT INTO benchmarks VALUES (?, ?, ?)', (result['id'], result['created'], json.dumps(result)))
    supervisor.benchmark = {'state': result['state'], 'completed': len(result['runs']), 'total': 3, 'error': result.get('error')}


DIST = PROJECT / 'frontend/dist'
if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')


@app.get('/')
async def index():
    if not (DIST / 'index.html').is_file():
        raise HTTPException(503, 'Build the frontend first.')
    return FileResponse(DIST / 'index.html')
