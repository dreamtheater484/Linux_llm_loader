"""Explicit, bounded engine updates to the versions shipped with Inflect."""
import asyncio
from collections import deque
from contextlib import suppress
import json
import os
from pathlib import Path
import sys

from fastapi import HTTPException

from inflect_platform import ProcessTree, exllama_lock, owned, program, venv_python
from . import engines
from .app_settings import read_config, revision, values, save_preferences, SavePreferences


def engine_details():
    llama = json.loads((engines.PROJECT / 'runtime-locks/llama-cpp.json').read_text(encoding='utf-8'))
    exl = exllama_lock(engines.PROJECT)
    result = []
    for engine in engines.engine_inventory():
        if engine['id'] not in ('gguf', 'exl3'):
            continue
        engine = dict(engine)
        if engine['id'] == 'gguf':
            managed = engines.GGUF == program(engines.RUNTIME / 'llama/bin/llama-server')
            supported = llama['release']
            current = engine['installed'] and (engine.get('receipt') or {}).get('release') == supported
            engine.update(path=str(engines.GGUF))
        else:
            supported = f"{exl['version']} / TabbyAPI {exl['tabby_revision'][:8]}"
            managed = engines.EXL_PYTHON == venv_python(engines.RUNTIME / 'exl3') or engines.EXL_PYTHON.is_relative_to(engines.RUNTIME / 'exl3-releases')
            current = (engine['installed'] and engine.get('package_version') == exl['package_version']
                       and engine.get('torch_version') == exl['torch']
                       and engine.get('tabby_revision') == exl['tabby_revision'][:8])
            engine.update(path=str(engines.EXL_PYTHON), tabby_dir=str(engines.TABBY))
            managed = managed and (engines.TABBY.is_relative_to(engines.RUNTIME) or engines.TABBY == engines.PROJECT / '.runtime/sources/tabbyAPI')
        engine.update(supported_version=supported, managed=managed, supported=current,
                      update_available=managed and not current,
                      update_label='Install supported version' if not engine['installed'] else 'Use supported version')
        result.append(engine)
    return result


class EngineUpdater:
    def __init__(self):
        self.task = None
        self.proc = None
        self.state = 'idle'
        self.engine = None
        self.error = None
        self.lines = deque(maxlen=160)

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def snapshot(self):
        return dict(state=self.state, engine=self.engine, error=self.error, lines=list(self.lines), active=self.active)

    def start(self, engine_id, state_dir, active):
        if self.active:
            raise HTTPException(409, 'An engine update is already running.')
        engine = next((item for item in engine_details() if item['id'] == engine_id), None)
        if not engine or not engine['managed']:
            raise ValueError('This engine is managed outside Inflect. Change its location or update it separately.')
        if engine['supported']:
            raise HTTPException(409, 'The supported engine version is already installed.')
        self.state, self.engine, self.error = 'running', engine_id, None
        self.lines.clear()
        self.task = asyncio.create_task(self.run(engine_id, state_dir, active))

    async def run(self, engine_id, state_dir, active):
        receipt = state_dir / 'engine-update-receipt.json'
        arguments = [sys.executable, str(engines.PROJECT / 'scripts' / ('install-llama.py' if engine_id == 'gguf' else 'install-exllama.py')),
                     '--runtime-dir', str(engines.RUNTIME)]
        if engine_id == 'exl3':
            arguments += ['--receipt', str(receipt)]
        tree = None
        try:
            before = read_config()
            state_dir.mkdir(parents=True, exist_ok=True)
            receipt.unlink(missing_ok=True)
            self.proc = await asyncio.create_subprocess_exec(*arguments, cwd=engines.PROJECT,
                env={**os.environ, 'PYTHONUNBUFFERED': '1'}, **owned(), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=2**20)
            tree = ProcessTree(self.proc.pid)
            async with asyncio.timeout(3600):
                while line := await self.proc.stdout.readline():
                    self.lines.append(line.decode(errors='replace').rstrip()[-2000:])
                code = await self.proc.wait()
            if code:
                raise RuntimeError(f'Installer exited with code {code}. The previous engine remains selected.')
            if engine_id == 'exl3':
                prepared = json.loads(receipt.read_text(encoding='utf-8'))
                preferences = {**values(before, active), **{key: prepared[key] for key in ('exl_python', 'tabby_dir')}}
                save_preferences(SavePreferences(revision=revision(before), values=preferences), active)
                self.lines.append('Installed successfully. Restart Inflect to use the prepared ExLlama runtime.')
            else:
                self.lines.append('Installed successfully. The next model load will use this version.')
            self.state = 'complete'
        except BaseException as exc:
            if self.proc and self.proc.returncode is None:
                tree = tree or ProcessTree(self.proc.pid)
                try:
                    tree.terminate()
                    await asyncio.wait_for(self.proc.wait(), 10)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        tree.kill()
                    await self.proc.wait()
                except ProcessLookupError:
                    pass
            self.state = 'error'
            self.error = str(getattr(exc, 'detail', exc)) or 'Engine update interrupted. The previous ExLlama installation was kept.'
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            if tree:
                tree.close()
            receipt.unlink(missing_ok=True)

    async def close(self):
        if self.active:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
