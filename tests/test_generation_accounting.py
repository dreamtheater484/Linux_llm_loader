"""Replay the installed EXL3 requeue method with CPU-only sequence stand-ins."""
import ast
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from loader.engines import EXL_PYTHON, PROJECT


def runtime_job():
    files = list(EXL_PYTHON.parent.parent.glob('lib/python*/site-packages/exllamav3/generator/job.py'))
    if not files:
        pytest.skip('Optional ExLlamaV3 runtime is not installed')
    return files[0]


def requeue_method(source):
    method = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef) and n.name == 'prepare_for_requeue')
    namespace = {'PAGE_SIZE': 256}
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual EXL3 requeue>', 'exec'), namespace)
    return namespace['prepare_for_requeue']


def simulate(source):
    class SequenceIds:
        def __init__(self, size): self.size = size
        def __len__(self): return self.size
        def torch(self): return self

    class Job:
        prepare_for_requeue = requeue_method(source)

        def __init__(self, input_ids=None, rq_state=None, max_new_tokens=32000, min_new_tokens=0, **kwargs):
            state = rq_state or {}
            self.rq_new_tokens = state.get('rq_new_tokens', 0)
            self.new_tokens = 0
            self.max_new_tokens, self.min_new_tokens = max_new_tokens, min_new_tokens
            ids = input_ids or SequenceIds(1000)
            self.sequences = [SimpleNamespace(input_ids=ids, sequence_ids=SequenceIds(len(ids)))]
            self.cached_pages = self.cached_tokens = 0
            self.time_generate = state.get('time_generate', 0)
            self.accepted_draft_tokens = state.get('accepted_draft_tokens', 0)
            self.last_state = state

        def __getattr__(self, name): return None
        def prepare_for_queue(self, *args, **kwargs): pass

    job = Job()
    for size in (4096, 4096, 4096):
        job.sequences[0].sequence_ids.size += size
        job.new_tokens = size
        job.time_generate += 80
        job.accepted_draft_tokens += 2500
        job.prepare_for_requeue()
    job.new_tokens = 2048
    job.time_generate += 40
    return job.rq_new_tokens + job.new_tokens, job


def test_counter_patch_preserves_all_segments_and_generation_limits(tmp_path):
    relative = Path('exllamav3/generator/job.py')
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text(runtime_job().read_text())
    patch = PROJECT / 'patches/exl3-cumulative-output-tokens.patch'
    def apply(*args):
        return subprocess.run(['git', 'apply', *args, str(patch)], cwd=tmp_path, capture_output=True, text=True)
    # Normalize to the shipped wheel so this test proves the original failure too.
    if apply('--reverse', '--check').returncode == 0:
        assert apply('--reverse').returncode == 0
    assert simulate(target.read_text())[0] == 6144
    changed = apply()
    assert changed.returncode == 0, changed.stderr
    count, job = simulate(target.read_text())
    assert count == 14336
    assert job.time_generate == 280
    assert job.accepted_draft_tokens == 7500
    assert job.max_new_tokens == 32000 - 3 * 4096
    assert apply('--reverse', '--check').returncode == 0
