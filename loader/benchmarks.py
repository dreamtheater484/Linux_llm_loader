"""Bounded local coding evaluations, durable archives, and disposable execution.

The manager and inference stay on the host. Only benchmark code/test execution
runs in Docker. No model, user checkout, credentials, or Docker socket is ever
mounted into a task container.
"""
import asyncio
from collections import Counter
from contextlib import suppress
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import grp
import json
import os
import platform
from pathlib import Path
import re
import pwd
import shlex
import secrets
import shutil
import signal
import sqlite3
import time
import zipfile

from fastapi import HTTPException
from .engines import RUNTIME, TABBY, GGUF

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'lumen-benchmark-tools:1'
PROTOCOL = 'lumen-coding-v1'
SUITES = {
    'humaneval': dict(name='Quick coding', benchmark='HumanEval+', count=20, task_seconds=90,
                     generation_seconds=60, grader_seconds=25, runner='Lumen single-answer v1'),
    'swebench': dict(name='Repository coding', benchmark='SWE-bench Lite', count=3, task_seconds=600,
                    generation_seconds=420, grader_seconds=150, runner='Lumen bash agent v1'),
}
TERMINAL_TASKS = {'passed', 'failed', 'timed_out', 'error', 'cancelled', 'unattempted'}
AGENT_PROMPT = """You are fixing a software issue in /testbed. Inspect the repository, implement the requested fix, and run relevant tests.
Respond with exactly one JSON object: {"command":"a bash command"} to inspect/edit/test, or {"finish":true} when the fix is ready.
Commands run in /testbed in a disposable container. Activate /opt/miniconda3/etc/profile.d/conda.sh and conda environment testbed when running Python tests if needed.
Do not change tests to hide failures. Do not seek reference solutions. Your patch will be graded in a fresh environment.
Each command has a 45-second limit. Keep commands and output focused. You have at most 40 turns."""
HUMAN_PROMPT = 'Implement the Python function below. Return a complete Python module including the function signature and any imports, inside one python code block. Do not include tests or explanations.'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temporary.replace(path)


def clean(value):
    """Effective engine config is useful; authentication material never is."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k.lower() not in {
            'api_key', 'admin_key', 'authorization', 'password', 'secret', 'api_token', 'access_token'}}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def runtime_identity(engine):
    """Record installed artifacts without importing an inference runtime."""
    packages = {}
    for metadata in sorted((RUNTIME / ('exl3' if engine == 'exl3' else 'vllm') / 'lib').glob('python*/site-packages/*.dist-info/METADATA')):
        name = version = None
        for line in metadata.read_text(errors='replace').splitlines():
            if line.startswith('Name: '):
                name = line[6:]
            elif line.startswith('Version: '):
                version = line[9:]
            elif not line:
                break
        if name and version:
            packages[name] = version
    source = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [*sorted((ROOT / 'loader').glob('*.py')), ROOT / 'benchmarks/worker.py', ROOT / 'benchmarks/Dockerfile']}
    result = dict(platform=platform.platform(), python=platform.python_version(), packages=packages,
                  lumen_sources=source, lumen_source_hash=digest(source))
    if engine == 'exl3':
        # Include the working code, including local patches, rather than a displayed version label.
        result['tabby_sources_hash'] = digest({str(p.relative_to(TABBY)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(TABBY.rglob('*.py')) if '.git' not in p.parts and 'venv' not in p.parts})
    elif engine == 'gguf' and GGUF.is_file():
        stat = GGUF.stat()
        result['llama_server'] = dict(path=str(GGUF), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    return result


def summarize(result):
    counts = Counter(t['state'] for t in result['tasks'])
    total = len(result['tasks'])
    graded = counts['passed'] + counts['failed']
    metrics = [m for task in result['tasks'] for m in task.get('metrics', [])]
    return dict(total=total, graded=graded, passed=counts['passed'], failed=counts['failed'],
                timed_out=counts['timed_out'], errors=counts['error'], cancelled=counts['cancelled'],
                unattempted=counts['unattempted'], completed=sum(counts[s] for s in TERMINAL_TASKS if s != 'unattempted'),
                # Never turn a partial run into a full-suite score.
                score=round(100 * counts['passed'] / total, 1) if graded == total and total else None,
                output_tokens=sum((m.get('usage') or {}).get('completion_tokens', 0) or 0 for m in metrics),
                input_tokens=sum((m.get('usage') or {}).get('prompt_tokens', 0) or 0 for m in metrics))


def report_markdown(result):
    model, settings, summary = result['model'], result['settings'], result['summary']
    title = model.get('title') or model.get('name', 'Unknown model')
    lines = [f"# {result['benchmark']} — {title}", '',
             f"- Run: `{result['id']}` · {datetime.fromtimestamp(result['created'], timezone.utc).isoformat()}",
             f"- State: {result['state']} · {result.get('elapsed_seconds', 0):.1f}s / {result['budget_seconds']}s budget",
             f"- Model: {model.get('name', title)} · {model.get('format', 'unknown')} · {model.get('quant', 'unknown quant')}",
             f"- Model ID: `{model.get('id')}` · fingerprint: `{result['model_fingerprint']}`",
             f"- Passed: {summary['passed']}/{summary['total']} selected tasks; graded: {summary['graded']}/{summary['total']}",
             f"- Failed: {summary['failed']} · timed out: {summary['timed_out']} · errors: {summary['errors']} · cancelled: {summary['cancelled']} · unattempted: {summary['unattempted']}",
             f"- Score: {str(summary['score']) + '%' if summary['score'] is not None else 'Incomplete — no aggregate accuracy score'}",
             f"- Recorded tokens: {summary['input_tokens']:,} input / {summary['output_tokens']:,} output (including reasoning; completed turns only)",
             f"- Runner: {result['runner']} · protocol: `{result['protocol_hash']}`",
             f"- Dataset revision: `{result['dataset']['revision']}` · subset: `{result['dataset']['fingerprint']}`",
             '', '> Local subset with fixed time limits and one attempt per task. This is not a full benchmark or official leaderboard score. Timed runs measure quality and speed together.',
             '', '## Configuration', '', '```json', json.dumps(dict(settings=settings,
                 engine=result['engine'], effective=result['effective'], launch=result.get('launch'), model=model,
                 matched_profiles=result.get('profiles', []), evaluation=result['evaluation'],
                 runtime=result.get('runtime'), hardware=result.get('hardware'), dataset=result['dataset'],
                 model_identity=result.get('model_identity')), indent=2, ensure_ascii=False), '```',
             '', '## Tasks', '', '| Task | Outcome | Seconds | Detail |', '|---|---|---:|---|']
    for task in result['tasks']:
        detail = str(task.get('detail', '')).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {task['id']} | {task['state']} | {task.get('elapsed_seconds', 0):.1f} | {detail} |")
    if result.get('error'):
        lines += ['', 'Run detail: ' + result['error']]
    return '\n'.join(lines) + '\n'


def extract_python(text):
    blocks = re.findall(r'```(?:python|py)?\s*\n(.*?)```', text, re.S | re.I)
    return '\n\n'.join(blocks).strip() if blocks else text.strip()


def parse_action(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```$', '', text).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object.')
    if value == {'finish': True}:
        return value
    if set(value) == {'command'} and isinstance(value['command'], str) and 0 < len(value['command']) <= 20000:
        return value
    raise ValueError('Use exactly {"command":"..."} or {"finish":true}.')


class EvaluationManager:
    def __init__(self, state, supervisor, telemetry):
        self.state = Path(state)
        self.root = self.state / 'evaluations'
        self.assets = self.root / 'assets'
        self.supervisor, self.telemetry = supervisor, telemetry
        self.task = None
        self.setup_task = None
        self.current = None
        self.setup = dict(state='idle', message='')
        self.containers = set()

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    @property
    def preparing(self):
        return self.setup_task is not None and not self.setup_task.done()

    def connection(self):
        self.state.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.state / 'results.sqlite3')
        db.execute('CREATE TABLE IF NOT EXISTS evaluations (id TEXT PRIMARY KEY, created REAL, data TEXT)')
        return db

    def recover(self):
        with self.connection() as db:
            rows = list(db.execute('SELECT data FROM evaluations'))
        for (raw,) in rows:
            result = json.loads(raw)
            if result['state'] == 'running':
                result.update(state='interrupted', error='Lumen stopped before this run finished. Completed task results were preserved.')
                for task in result['tasks']:
                    if task['state'] not in TERMINAL_TASKS:
                        task.update(state='cancelled', detail='Interrupted by application restart.')
                self.save(result)

    def save(self, result=None):
        result = result or self.current
        result['summary'] = summarize(result)
        atomic_json(self.root / result['id'] / 'report.json', result)
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO evaluations VALUES (?, ?, ?)',
                       (result['id'], result['created'], json.dumps(result)))

    def list(self, limit=30, offset=0, suite=None, query=''):
        where, args = [], []
        if suite:
            where.append("json_extract(data, '$.suite') = ?")
            args.append(suite)
        if query.strip():
            fields = ('id', 'benchmark', 'model.name', 'model.title', 'model.quant')
            where.append('(' + ' OR '.join(f"instr(lower(coalesce(json_extract(data, '$.{field}'), '')), ?) > 0" for field in fields) + ')')
            args.extend([query.strip().lower()] * len(fields))
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with self.connection() as db:
            total = db.execute('SELECT COUNT(*) FROM evaluations' + clause, args).fetchone()[0]
            all_total = db.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]
            results = [json.loads(row[0]) for row in db.execute('SELECT data FROM evaluations' + clause + ' ORDER BY created DESC LIMIT ? OFFSET ?', (*args, limit, offset))]
        return dict(total=total, all_total=all_total, items=results, offset=offset, limit=limit)

    def get(self, run_id):
        if not re.fullmatch(r'[a-f0-9]{16}', run_id):
            raise HTTPException(404, 'Benchmark run not found.')
        with self.connection() as db:
            row = db.execute('SELECT data FROM evaluations WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Benchmark run not found.')
        return json.loads(row[0])

    def artifact(self, index, name, value):
        path = self.root / self.current['id'] / f'task-{index + 1:02d}' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_text(value, encoding='utf-8')
        else:
            atomic_json(path, value)

    def archive(self, run_id):
        result = self.get(run_id)
        root = self.root / run_id
        archive = root / 'archive.zip'
        temporary = root / (secrets.token_hex(8) + '.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('report.md', report_markdown(result))
                for path in root.rglob('*'):
                    if path.is_file() and path.suffix not in ('.zip', '.tmp') and not path.is_symlink():
                        z.write(path, path.relative_to(root))
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        return archive

    async def command(self, *args, input=None, timeout=60, check=True):
        """Bound output and kill our own CLI process on cancellation.

        sg activates an already-granted Docker group in an older login session.
        Every argument is shell-quoted; model commands still only run in Docker.
        """
        if args[0] == 'docker':
            with suppress(KeyError):
                group = grp.getgrnam('docker')
                if shutil.which('sg') and group.gr_gid not in os.getgroups() and pwd.getpwuid(os.getuid()).pw_name in group.gr_mem:
                    args = ('sg', 'docker', '-c', shlex.join([str(a) for a in args]))
        proc = await asyncio.create_subprocess_exec(*map(str, args), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        output = bytearray()
        truncated = False
        async def read():
            nonlocal truncated
            while block := await proc.stdout.read(65536):
                output.extend(block)
                if len(output) > 4_000_000:
                    del output[:-4_000_000]
                    truncated = True
        reader = asyncio.create_task(read())
        try:
            async with asyncio.timeout(timeout):
                if input:
                    proc.stdin.write(input.encode() if isinstance(input, str) else input)
                    await proc.stdin.drain()
                proc.stdin.close()
                await proc.wait()
                await reader
        except BaseException:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
            raise
        text = ('[Earlier output truncated]\n' if truncated else '') + output.decode(errors='replace')
        if check and proc.returncode:
            raise RuntimeError(text[-4000:] or f'{args[0]} exited with code {proc.returncode}.')
        return proc.returncode, text

    def container_name(self):
        name = 'lumen-eval-' + secrets.token_hex(10)
        self.containers.add(name)  # Register before create; cancellation can race Docker.
        return name

    async def remove(self, name):
        try:
            await self.command('docker', 'rm', '-f', name, timeout=5, check=False)
        except (OSError, TimeoutError):
            return  # Startup cleanup also finds containers carrying our label.
        self.containers.discard(name)

    async def cleanup(self):
        await asyncio.gather(*(self.remove(name) for name in list(self.containers)))

    async def cleanup_stale(self):
        if not shutil.which('docker'):
            return
        try:
            _, names = await self.command('docker', 'ps', '-aq', '--filter', 'label=lumen.evaluation=' + digest(str(self.state.resolve()))[:16], timeout=5)
            for name in names.splitlines():
                if re.fullmatch(r'[a-f0-9]{12,64}', name):
                    await self.remove(name)
        except (OSError, RuntimeError, TimeoutError):
            pass

    def restrictions(self):
        return ['--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '256', '--memory', '4g', '--memory-swap', '4g', '--cpus', '2',
                '--label', 'lumen.evaluation=' + digest(str(self.state.resolve()))[:16]]

    async def worker(self, mode, body=None, image=None, network=False, timeout=1200):
        name = self.container_name()
        restrictions = self.restrictions()
        if network:
            restrictions = restrictions[2:]  # Network only for explicit dataset preparation.
        try:
            args = ['docker', 'run', '--name', name, '-i', *restrictions,
                    '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=1g', image or IMAGE, mode]
            # HF download caches need writes only during preparation, never during grading.
            if mode == 'manifest-swe':
                args[args.index('--read-only'):args.index('--read-only') + 1] = ['--tmpfs', '/opt/cache:rw,nosuid,size=1g']
            _, output = await self.command(*args, input=json.dumps(body) if body is not None else None, timeout=timeout)
            records = [line[len('LUMEN_RESULT='):] for line in output.splitlines() if line.startswith('LUMEN_RESULT=')]
            if len(records) != 1:
                raise RuntimeError('Grader returned no unambiguous result. See benchmark setup/runtime logs.')
            return json.loads(records[0])
        finally:
            await self.remove(name)

    def manifest(self, suite):
        if suite not in SUITES:
            raise ValueError('Unknown benchmark suite.')
        path = self.assets / (suite + '.json')
        if not path.is_file():
            raise ValueError('Prepare this benchmark before starting a timed run.')
        result = json.loads(path.read_text())
        fingerprint = result.pop('fingerprint')
        if digest(result) != fingerprint:
            raise ValueError('Benchmark assets changed. Prepare them again before running.')
        result['fingerprint'] = fingerprint
        return result

    async def availability(self):
        docker = bool(shutil.which('docker'))
        message = 'Install Docker Engine and give your user access to its daemon, then recheck.'
        if docker:
            try:
                await self.command('docker', 'info', '--format', '{{.ServerVersion}}', timeout=5)
                message = ''
            except (OSError, RuntimeError, TimeoutError):
                docker, message = False, 'Docker is installed but its daemon is unavailable to Lumen. Start Docker and check user permissions.'
        suites = {}
        for suite, config in SUITES.items():
            try:
                manifest = self.manifest(suite)
                if docker:
                    images = {manifest['worker_image'], *(task['image_id'] for task in manifest['tasks'] if 'image_id' in task)}
                    await self.command('docker', 'image', 'inspect', *images, timeout=5)
                suites[suite] = dict(**config, prepared=True, revision=manifest['revision'], fingerprint=manifest['fingerprint'])
            except (OSError, ValueError, KeyError, RuntimeError, TimeoutError):
                suites[suite] = dict(**config, prepared=False)
        return dict(docker=docker, message=message, suites=suites, setup=self.setup, active=self.snapshot())

    def snapshot(self):
        if not self.current:
            return None
        return dict(id=self.current['id'], state=self.current['state'], suite=self.current['suite'],
                    name=self.current['benchmark'], created=self.current['created'], deadline=self.current['deadline'],
                    summary=self.current['summary'], tasks=self.current['tasks'], model=self.current['model'])

    def assert_idle(self):
        if self.active or self.preparing:
            raise HTTPException(409, 'A benchmark or its preparation is already running.')
        if self.supervisor.generation_lock.locked() or (self.supervisor.benchmark_task and not self.supervisor.benchmark_task.done()):
            raise HTTPException(409, 'Finish the current request before benchmarking.')

    async def prepare(self, suite):
        if suite not in SUITES:
            raise ValueError('Unknown benchmark suite.')
        self.assert_idle()
        if not (await self.availability())['docker']:
            raise ValueError('Docker Engine is required for isolated code execution. Install/start Docker, then recheck.')
        self.assert_idle()
        self.setup = dict(state='running', suite=suite, message='Preparing evaluation tools. Initial downloads may take several minutes.')
        self.setup_task = asyncio.create_task(self._prepare(suite))
        return self.setup

    async def _prepare(self, suite):
        lock = None
        try:
            self.assets.mkdir(parents=True, exist_ok=True)
            lock = (self.assets / 'preparation.lock').open('a')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _, log = await self.command('docker', 'build', '-t', IMAGE, str(ROOT / 'benchmarks'), timeout=3600)
            self.assets.mkdir(parents=True, exist_ok=True)
            (self.assets / 'build.log').write_text(log)
            _, identity = await self.command('docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}')
            worker_image = identity.strip()
            self.setup['message'] = 'Downloading the fixed task subset and pinning its dataset revision.'
            manifest = await self.worker('manifest-human' if suite == 'humaneval' else 'manifest-swe',
                                         image=worker_image, network=suite == 'swebench')
            manifest.update(worker_image=worker_image, tools={'evalplus': '0.3.1', 'swebench': '3.0.15'})
            if suite == 'swebench':
                for index, task in enumerate(manifest['tasks']):
                    self.setup['message'] = f"Preparing repository {index + 1}/3: {task['repo']}"
                    await self.command('docker', 'pull', task['image'], timeout=1800)
                    _, identity = await self.command('docker', 'image', 'inspect', task['image'], '--format', '{{.Id}}')
                    task['image_id'] = identity.strip()
                    self.setup['message'] = f"Checking official tests for {task['id']} with its reference fix."
                    grade, _ = await self.grade_repository(task, task['reference_patch'], worker_image, timeout=300)
                    if not grade.get('passed'):
                        raise RuntimeError(f"Reference fix failed validation for {task['id']}: {grade}")
                    baseline, _ = await self.grade_repository(task, '', worker_image, timeout=300)
                    if baseline.get('passed') or baseline.get('infrastructure_error'):
                        raise RuntimeError(f"Unfixed baseline did not fail cleanly for {task['id']}: {baseline}")
                    task['validation'] = dict(reference_passed=True, baseline_failed=True)
                    del task['reference_patch']  # Never expose a reference patch during inference.
            manifest['fingerprint'] = digest(manifest)
            atomic_json(self.assets / (suite + '.json'), manifest)
            self.setup.update(state='complete', message='Ready. Task environments are cached; timed runs do not download anything.')
        except asyncio.CancelledError:
            self.setup.update(state='cancelled', message='Preparation stopped. Completed Docker layers can be reused.')
        except Exception as exc:
            self.setup.update(state='error', message=str(exc))
        finally:
            await self.cleanup()
            if lock:
                lock.close()

    async def start(self, suite, model_id, budget_seconds=1800):
        self.assert_idle()
        if self.supervisor.state != 'ready' or not self.supervisor.model or self.supervisor.model['id'] != model_id:
            raise ValueError('Load the model shown in the benchmark configuration before starting.')
        manifest = self.manifest(suite)
        # Fast, offline preflight. A missing image never triggers a timed download.
        for image in {manifest['worker_image'], *(t['image_id'] for t in manifest['tasks'] if 'image_id' in t)}:
            await self.command('docker', 'image', 'inspect', image, timeout=5)
        self.assert_idle()  # Another request could have started during preflight.
        if self.supervisor.state != 'ready' or self.supervisor.model['id'] != model_id:
            raise HTTPException(409, 'The loaded model changed during benchmark preflight.')
        settings = copy.deepcopy(self.supervisor.settings.model_dump())
        model = clean(copy.deepcopy(self.supervisor.model))
        # Metadata identity does not pretend to be a multi-gigabyte weight checksum.
        identity = dict(model=model, files=[])
        path = Path(model['path'])
        candidates = [path] if path.is_file() else sorted(path.iterdir()) if path.is_dir() else []
        for file in candidates:
            if file.is_file():
                stat = file.stat()
                identity['files'].append(dict(name=file.name, size=stat.st_size, mtime_ns=stat.st_mtime_ns))
                if file.suffix in ('.json', '.jinja', '.jinja2') and stat.st_size < 30_000_000:
                    identity['files'][-1]['sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
        profiles_path = self.state / 'profiles.json'
        profiles = json.loads(profiles_path.read_text()) if profiles_path.exists() else []
        matched = [dict(id=p.get('id'), name=p['name']) for p in profiles if p.get('settings') == settings]
        now, run_id = time.time(), secrets.token_hex(8)
        config = SUITES[suite]
        protocol = dict(version=PROTOCOL, **config, human_prompt=HUMAN_PROMPT, agent_prompt=AGENT_PROMPT,
                        command_seconds=45, max_steps=40, output=settings['max_output'], temperature=settings['temperature'],
                        reasoning=settings['reasoning_effort'], budget_seconds=budget_seconds,
                        implementation=digest({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                            for name in ('loader/benchmarks.py', 'benchmarks/worker.py')}),
                        sandbox=dict(network=False, cpus=2, memory_bytes=4 * 2**30, pids=256),
                        attempts_per_task=1, sampling_note='No seed, top_p, top_k, or min_p override; installed engine defaults apply.')
        self.current = dict(schema_version=1, id=run_id, suite=suite, benchmark=config['benchmark'], state='running',
            created=now, deadline=now + budget_seconds, budget_seconds=budget_seconds, elapsed_seconds=0,
            model=model, model_fingerprint=digest(identity), model_identity=identity,
            settings=settings, profiles=matched, engine=self.supervisor.engine,
            effective=clean(copy.deepcopy(self.supervisor.effective)), hardware=clean(copy.deepcopy(self.telemetry.value)),
            runtime=runtime_identity(self.supervisor.engine),
            launch=clean(copy.deepcopy(getattr(self.supervisor, 'launch_configuration', None))),
            runner=config['runner'], protocol_hash=digest(protocol), evaluation=protocol,
            dataset={k: v for k, v in manifest.items() if k != 'tasks'},
            tasks=[dict(id=t['id'], state='unattempted', detail='', metrics=[]) for t in manifest['tasks']])
        self.save()
        self.task = asyncio.create_task(self.run(manifest))
        return self.current

    async def generate(self, messages, task, index, filename='answer.json'):
        settings = self.current['settings']
        content, reasoning, complete = '', '', False
        try:
            async for event in self.supervisor.stream(messages, settings['max_output'], settings['temperature'],
                                                  reasoning_effort=settings['reasoning_effort']):
                if event['type'] == 'token':
                    content += event['text']
                    reasoning += event.get('reasoning', '')
                elif event['type'] == 'complete':
                    task['metrics'].append(event)
                    complete = True
                elif event['type'] in ('cancelled', 'error'):
                    raise RuntimeError(event.get('message', 'Inference cancelled.'))
        finally:
            self.artifact(index, filename, dict(role='assistant', content=content, reasoning_content=reasoning, complete=complete))
        if not complete:
            raise RuntimeError('The model returned no completion record.')
        return dict(role='assistant', content=content, reasoning_content=reasoning)

    async def new_repository(self, task):
        name = self.container_name()
        await self.command('docker', 'create', '--name', name, *self.restrictions(),
                           '--workdir', '/testbed', '--entrypoint', '/bin/bash', task['image_id'],
                           '-c', 'while true; do sleep 3600; done', timeout=10)
        await self.command('docker', 'start', name, timeout=10)
        base = task['base_commit']
        if not re.fullmatch(r'[a-f0-9]{40}', base):
            raise ValueError('Invalid benchmark base commit.')
        # Only a disposable container is reset. No user checkout is mounted.
        await self.command('docker', 'exec', name, 'bash', '-lc',
            f'set -e\ncd /testbed\ngit reset --hard {base}\ngit clean -fd\n'
            'git remote remove origin 2>/dev/null || true\n'
            'git config user.email benchmark@localhost\ngit config user.name Benchmark', timeout=15)
        return name

    async def grade_repository(self, task, patch, image, timeout=150):
        name = await self.new_repository(task)
        try:
            async with asyncio.timeout(timeout):
                if patch.strip():
                    code, output = await self.command('docker', 'exec', '-i', name, 'git', '-C', '/testbed', 'apply', '--whitespace=nowarn', '-', input=patch, check=False)
                    if code:
                        return dict(passed=False, patch_error='Generated patch could not be applied to a fresh checkout.'), output
                _, output = await self.command('docker', 'exec', '-i', name, 'bash', '-s', input=task['eval_script'], timeout=timeout, check=False)
                result = await self.worker('grade-swe', dict(spec=task['spec'], log=output, patch=patch), image=image, timeout=30)
                return result, output
        finally:
            await self.remove(name)

    async def human(self, index, source, task, manifest):
        task.update(state='generating', detail='Writing one answer; reasoning counts toward the time limit.')
        self.save()
        messages = [dict(role='system', content=HUMAN_PROMPT), dict(role='user', content=source['prompt'])]
        self.artifact(index, 'prompt.json', messages)
        async with asyncio.timeout(SUITES['humaneval']['generation_seconds']):
            answer = await self.generate(messages, task, index)
        solution = extract_python(answer['content'])
        self.artifact(index, 'solution.py', solution)
        task.update(state='grading', detail='Running the original and extended EvalPlus tests.')
        self.save()
        grade = await self.worker('grade-human', dict(task_id=source['id'], solution=solution), image=manifest['worker_image'], timeout=25)
        self.artifact(index, 'grading.json', grade)
        task['grade'] = grade
        task.update(state='timed_out' if 'timeout' in (grade['base_status'], grade['plus_status']) else 'passed' if grade['passed'] else 'failed',
                    detail=f"Original tests: {grade['base_status']}; extended tests: {grade['plus_status']}.")

    async def repository(self, index, source, task, manifest):
        task.update(state='starting', detail='Opening an isolated repository checkout.')
        self.save()
        name = await self.new_repository(source)
        messages = [dict(role='system', content=AGENT_PROMPT), dict(role='user', content=source['prompt'])]
        transcript = []
        self.artifact(index, 'prompt.json', messages)
        try:
            task.update(state='generating', detail='Inspecting, editing, and testing the repository.')
            self.save()
            try:
                async with asyncio.timeout(SUITES['swebench']['generation_seconds']):
                    for step in range(40):
                        answer = await self.generate(messages, task, index, f'answer-{step + 1:02d}.json')
                        messages.append(answer)
                        transcript.append(dict(step=step + 1, answer=answer))
                        self.artifact(index, 'trajectory.json', transcript)
                        task['steps'] = step + 1
                        try:
                            action = parse_action(answer['content'])
                        except (ValueError, TypeError) as exc:
                            messages.append(dict(role='user', content=f'Invalid action: {exc}. Return one JSON action.'))
                            continue
                        if action.get('finish'):
                            break
                        code, output = await self.command('docker', 'exec', name, 'timeout', '--kill-after=2s', '40s',
                            'bash', '-lc', 'cd /testbed\n' + action['command'], timeout=45, check=False)
                        output = f'Exit code: {code}\n' + output
                        transcript[-1]['command_output'] = output
                        self.artifact(index, 'trajectory.json', transcript)
                        messages.append(dict(role='user', content=output[-12000:] or '(Command produced no output.)'))
                        self.save()
                    else:
                        task['agent_limit'] = '40-turn limit reached; grading the current patch.'
            except TimeoutError:
                # Stop every process, then reopen only the idle container entrypoint.
                # Its filesystem is preserved, so the grading reserve can test the
                # latest patch without racing an unfinished model command.
                task['agent_limit'] = 'Agent time limit reached; grading the current patch.'
                await self.command('docker', 'kill', name, timeout=5, check=False)
                await self.command('docker', 'start', name, timeout=10)
            _, patch = await self.command('docker', 'exec', name, 'bash', '-lc',
                'cd /testbed && rm -f .git/index.lock && git add -A && git diff --cached --binary ' + source['base_commit'], timeout=15)
            self.artifact(index, 'patch.diff', patch)
        finally:
            await self.remove(name)
        task.update(state='grading', detail='Applying the patch and running official tests in a fresh container.')
        self.save()
        grade, output = await self.grade_repository(source, patch, manifest['worker_image'], timeout=150)
        self.artifact(index, 'test-output.txt', output)
        self.artifact(index, 'grading.json', grade)
        task['grade'] = grade
        if grade.get('infrastructure_error'):
            task.update(state='error', detail=grade['infrastructure_error'])
        else:
            task.update(state='passed' if grade['passed'] else 'failed', detail=grade.get('patch_error') or
                        ('Required tests and regressions passed.' if grade['passed'] else 'One or more required tests failed.'))

    async def run(self, manifest):
        start = time.monotonic()
        try:
            # Reserve time for container termination and final archive writes.
            async with asyncio.timeout(max(0.01, self.current['budget_seconds'] - 20)):
                for index, (source, task) in enumerate(zip(manifest['tasks'], self.current['tasks'])):
                    task_start = time.monotonic()
                    try:
                        async with asyncio.timeout(SUITES[self.current['suite']]['task_seconds'] - 10):
                            if self.current['suite'] == 'humaneval':
                                await self.human(index, source, task, manifest)
                            else:
                                await self.repository(index, source, task, manifest)
                    except TimeoutError:
                        task.update(state='timed_out', detail='Task time limit reached; this is not a verified test failure.')
                    except Exception as exc:
                        task.update(state='error', detail=str(exc))
                    finally:
                        task['elapsed_seconds'] = round(time.monotonic() - task_start, 2)
                        self.current['elapsed_seconds'] = round(time.monotonic() - start, 2)
                        self.save()
                self.current['state'] = 'complete'
        except TimeoutError:
            self.current.update(state='timed_out', error='The total time budget was reached. Remaining tasks were not attempted.')
        except asyncio.CancelledError:
            self.current.update(state='cancelled', error='Stopped by the user. Completed results are archived.')
        except Exception as exc:
            self.current.update(state='error', error=str(exc))
        finally:
            await self.cleanup()
            for task in self.current['tasks']:
                if task['state'] not in TERMINAL_TASKS:
                    task.update(state='timed_out' if self.current['state'] == 'timed_out' else 'cancelled',
                                detail='Stopped before a grading result was available.')
            self.current['elapsed_seconds'] = round(time.monotonic() - start, 2)
            self.save()

    async def stop(self):
        if self.active:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            if self.current and self.current['state'] == 'running':
                # A task cancelled before its first event-loop turn never enters run().
                self.current.update(state='cancelled', error='Stopped before the first task started.')
                self.save()
        if self.preparing:
            self.setup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.setup_task
            if self.setup['state'] == 'running':
                self.setup.update(state='cancelled', message='Preparation stopped.')
