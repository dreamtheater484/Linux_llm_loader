"""Lifecycle and protocol tests; real Docker grader checks live in verify_benchmarks.py."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from loader import benchmarks as b, server
from loader.engines import Settings


def manager(tmp_path, monkeypatch):
    model_dir = tmp_path / 'model with spaces'
    model_dir.mkdir(exist_ok=True)
    (model_dir / 'config.json').write_text('{"quant":4.05}')
    settings = Settings(model_id='test', prediction='mtp', draft_tokens=3, kv='Q4', vision=True)
    supervisor = SimpleNamespace(state='ready', model=dict(id='test', title='Test model', name='Test-4.05bpw',
        quant='4.05 bpw', format='EXL3', path=str(model_dir)), settings=settings,
        generation_lock=asyncio.Lock(), benchmark_task=None, engine='exl3',
        effective={'cache_mode': 'Q4', 'api_key': 'NEVER_EXPORT', 'draft': {'draft_num_tokens': 3}})
    mgr = b.EvaluationManager(tmp_path, supervisor, SimpleNamespace(value={'gpu': {'name': 'Test GPU'}}))
    mgr.command = AsyncMock(return_value=(0, ''))
    monkeypatch.setattr(b, 'runtime_identity', lambda engine: {'engine': engine, 'version': 'test'})
    manifest = dict(dataset='HumanEval+', revision='test-revision', worker_image='sha256:fixture',
        selection=b.SUITES['humaneval']['selection'], tasks=[dict(id=f'HumanEval/{n}', prompt='def f(): ...') for n in range(3)])
    manifest['fingerprint'] = b.digest(manifest)
    b.atomic_json(mgr.assets / 'humaneval.json', manifest)
    return mgr


def test_config_snapshot_archive_and_score(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        settings = mgr.supervisor.settings.model_dump()
        (tmp_path / 'profiles.json').write_text(json.dumps([dict(id='profile1', name='My quant', settings=settings)]))
        async def human(index, source, task, manifest):
            mgr.artifact(index, 'solution.py', 'def f(): return 1')
            task.update(state='passed' if index < 2 else 'failed', grade={'passed': index < 2})
        mgr.human = human
        result = await mgr.start('humaneval', 'test', 300)
        mgr.supervisor.settings.kv = 'FP16'
        mgr.supervisor.model['quant'] = 'changed later'
        await mgr.task
        saved = mgr.get(result['id'])
        assert saved['state'] == 'complete'
        assert saved['summary']['score'] == 66.7
        assert saved['settings'] == settings and saved['settings']['kv'] == 'Q4'
        assert saved['model']['quant'] == '4.05 bpw'
        assert saved['profiles'] == [dict(id='profile1', name='My quant')]
        report = b.report_markdown(saved)
        assert 'NEVER_EXPORT' not in report
        for field in settings:
            assert f'"{field}"' in report
        assert saved['model_identity']['files'][0]['sha256']
        assert 'local subset' in report.lower()
        mgr.artifact(0, 'outside.txt', 'original')
        root = mgr.root / result['id']
        (root / 'unsafe-link').symlink_to(tmp_path / 'profiles.json')
        with zipfile.ZipFile(mgr.archive(result['id'])) as archive:
            assert 'unsafe-link' not in archive.namelist()
            assert {'report.md', 'report.json', 'task-01/solution.py'} <= set(archive.namelist())
        assert mgr.list()['total'] == 1
        assert mgr.list(query='4.05')['total'] == 1
        assert mgr.list(query='%')['total'] == 0
        assert mgr.list(suite='swebench')['total'] == 0
        assert mgr.list(query='unrelated')['all_total'] == 1
        assert mgr.list(offset=1)['items'] == []
    asyncio.run(check())


def test_timeout_errors_and_unattempted_are_not_failed_tests(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        async def human(index, source, task, manifest):
            if index == 0:
                task['state'] = 'passed'
            elif index == 1:
                raise TimeoutError()
            else:
                raise RuntimeError('Grader unavailable')
        mgr.human = human
        await mgr.start('humaneval', 'test', 300)
        await mgr.task
        s = mgr.current['summary']
        assert (s['passed'], s['failed'], s['timed_out'], s['errors'], s['score']) == (1, 0, 1, 1, None)
    asyncio.run(check())


@pytest.mark.parametrize('immediate', [True, False])
def test_cancel_discards_and_releases_model(tmp_path, monkeypatch, immediate):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        entered = asyncio.Event()
        async def human(index, source, task, manifest):
            task['state'] = 'generating'
            entered.set()
            await asyncio.Event().wait()
        mgr.human = human
        await mgr.start('humaneval', 'test', 300)
        if not immediate:
            await entered.wait()
        await mgr.stop()
        assert not mgr.active
        with pytest.raises(HTTPException) as exc:
            mgr.get(mgr.current['id'])
        assert exc.value.status_code == 404
        assert mgr.list()['total'] == 0
        assert not (mgr.root / mgr.current['id']).exists()
        saved = mgr.current
        assert saved['state'] == 'cancelled'
        assert all(t['state'] in b.TERMINAL_TASKS for t in saved['tasks'])
        assert saved['summary']['score'] is None
        assert saved['summary']['unattempted'] == (3 if immediate else 2)
    asyncio.run(check())


def test_humaneval_has_no_generation_task_or_run_deadline(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        entered = asyncio.Event()
        async def human(index, source, task, manifest):
            if index == 0:
                task['state'] = 'passed'
            else:
                task['state'] = 'generating'
                entered.set()
                await asyncio.Event().wait()
        mgr.human = human
        await mgr.start('humaneval', 'test', 20.03)
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(.05)  # The old whole-run deadline would have fired.
        assert mgr.active and mgr.current['state'] == 'running'
        assert mgr.current['deadline'] is None
        assert [t['state'] for t in mgr.current['tasks']] == ['passed', 'generating', 'unattempted']
        await mgr.stop()
    asyncio.run(check())


def test_partial_answer_survives_generation_timeout(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        async def stream(*args, **kwargs):
            yield {'type': 'token', 'text': 'def f():', 'reasoning': 'Plan'}
            await asyncio.Event().wait()
        mgr.supervisor.stream = stream
        async def human(index, source, task, manifest):
            task['state'] = 'generating'
            async with asyncio.timeout(.01):
                await mgr.generate([], task, index)
        mgr.human = human
        await mgr.start('humaneval', 'test', 300)
        await mgr.task
        answer = json.loads((mgr.root / mgr.current['id'] / 'task-01/answer.json').read_text())
        assert answer == dict(role='assistant', content='def f():', reasoning_content='Plan', complete=False)
        assert mgr.current['summary']['timed_out'] == 3
    asyncio.run(check())


def test_restart_discards_interrupted_archive(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        await mgr.start('humaneval', 'test', 300)
        await mgr.stop()
        result = mgr.current
        result['state'] = 'running'
        result['tasks'][0]['state'] = 'passed'
        result['tasks'][1]['state'] = 'generating'
        mgr.save()
        second = manager(tmp_path, monkeypatch)
        second.recover()
        with pytest.raises(HTTPException):
            second.get(result['id'])
        assert second.list()['total'] == 0
        assert not (second.root / result['id']).exists()
    asyncio.run(check())


def test_rejects_changed_assets_model_and_concurrent_runs(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match='Load the model'):
            await mgr.start('humaneval', 'wrong', 300)
        await mgr.start('humaneval', 'test', 300)
        with pytest.raises(HTTPException) as exc:
            await mgr.start('humaneval', 'test', 300)
        assert exc.value.status_code == 409
        await mgr.stop()
        path = mgr.assets / 'humaneval.json'
        data = json.loads(path.read_text())
        data['tasks'][0]['id'] = 'tampered'
        b.atomic_json(path, data)
        with pytest.raises(ValueError, match='assets changed'):
            await mgr.start('humaneval', 'test', 300)
        with pytest.raises(HTTPException):
            mgr.get('../outside')
    asyncio.run(check())


def test_setup_rechecks_busy_state_after_preflight(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        async def availability():
            await mgr.supervisor.generation_lock.acquire()
            return {'docker': True}
        mgr.availability = availability
        with pytest.raises(HTTPException):
            await mgr.prepare('humaneval')
        assert not mgr.preparing
    asyncio.run(check())


def test_command_timeout_kills_process_and_bounds_output(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        del mgr.command
        with pytest.raises(TimeoutError):
            await mgr.command(sys.executable, '-c', 'import time; time.sleep(60)', timeout=.05)
        code, output = await mgr.command(sys.executable, '-c', 'print("x" * 4100000)')
        assert code == 0 and len(output) < 4_000_100 and output.startswith('[Earlier output truncated]')
    asyncio.run(check())


def test_export_api_and_limits(tmp_path, monkeypatch):
    mgr = manager(tmp_path, monkeypatch)
    async def prepare():
        async def human(index, source, task, manifest):
            task['state'] = 'passed'
        mgr.human = human
        await mgr.start('humaneval', 'test', 300)
        await mgr.task
    asyncio.run(prepare())
    monkeypatch.setattr(server, 'evaluations', mgr)
    client = TestClient(server.app)  # Isolated routes, no production lifespan.
    run_id = mgr.current['id']
    assert client.get('/api/evaluations?limit=101').status_code == 422
    assert client.get('/api/evaluations?offset=-1').status_code == 422
    assert client.post('/api/evaluations', json={}).status_code == 403
    assert client.post('/api/evaluations', json=dict(suite='humaneval', model_id='test', budget_minutes=31), headers={'X-Lumen-Local':'1'}).status_code == 422
    assert client.get(f'/api/evaluations/{run_id}/report').status_code == 200
    assert client.get(f'/api/evaluations/{run_id}/json').json()['settings']['draft_tokens'] == 3
    assert client.get(f'/api/evaluations/{run_id}/archive').headers['content-type'] == 'application/zip'
    mgr.current['state'] = 'running'
    mgr.save()
    assert client.get(f'/api/evaluations/{run_id}/archive').status_code == 409


def test_strict_agent_action_and_python_extraction():
    assert b.parse_action('```json\n{"command":"printf hello"}\n```') == {'command': 'printf hello'}
    for value in ['{"command":"x","finish":true}', '[]', '{"command":3}', '{"finish":false}']:
        with pytest.raises(ValueError):
            b.parse_action(value)
    assert b.extract_python('Here is code:\n```python\ndef f(): return 3\n```') == 'def f(): return 3'


def test_swebench_has_no_generation_task_or_run_deadline(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        manifest = dict(dataset='SWE-bench Lite', revision='fixture', worker_image='sha256:worker', selection=b.SUITES['swebench']['selection'],
            tasks=[dict(id='repo__issue-1', prompt='Fix this', base_commit='a' * 40, image_id='sha256:repo')])
        manifest['fingerprint'] = b.digest(manifest)
        b.atomic_json(mgr.assets / 'swebench.json', manifest)
        waiting = asyncio.Event()
        async def repository(index, source, task, selected, deadline=None):
            waiting.set()
            await asyncio.Event().wait()
        mgr.repository = repository
        await mgr.start('swebench', 'test', 600)
        await asyncio.wait_for(waiting.wait(), 1)
        await asyncio.sleep(.05)
        assert mgr.current['state'] == 'running'
        assert mgr.current['deadline'] is None
        assert mgr.current['evaluation']['timing_policy'].startswith('No generation')
        await mgr.stop()
    asyncio.run(check())


@pytest.mark.parametrize('network_failure', [False, True])
def test_preparation_publishes_only_verified_subset_and_keeps_diagnostics(tmp_path, monkeypatch, network_failure):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        tasks = [dict(id=f'repo__issue-{i}', repo=f'org/repo{i}', image=f'fixture:{i}', reference_patch='reference fix') for i in range(3)]
        mgr.worker = AsyncMock(return_value=dict(dataset='SWE-bench Lite', revision='fixture', selection=b.SUITES['swebench']['selection'], tasks=tasks))
        mgr.command = AsyncMock(return_value=(0, 'sha256:fixture'))
        async def grade(source, patch, image, timeout):
            if network_failure:
                return {'passed': False, 'report': {'tests_status': {'FAIL_TO_PASS': {'failure': ['test one']}}}}, 'Temporary failure in name resolution: httpbin.org'
            return {'passed': bool(patch)}, 'Full upstream test output'
        mgr.grade_repository = AsyncMock(side_effect=grade)
        await mgr._prepare('swebench')
        log = (mgr.assets / 'swebench-setup.log').read_text()
        assert mgr.setup['log_url'] == '/api/evaluations/preparation-log?suite=swebench'
        if network_failure:
            assert mgr.setup['state'] == 'error'
            assert 'network service unavailable' in mgr.setup['message']
            assert len(mgr.setup['message']) < 300
            assert 'httpbin.org' in log and 'test one' in log
            assert not (mgr.assets / 'swebench.json').exists()
        else:
            assert mgr.setup['state'] == 'complete'
            assert mgr.grade_repository.await_count == 6
            manifest = mgr.manifest('swebench')
            assert all(t['validation'] == {'reference_passed': True, 'baseline_failed': True} for t in manifest['tasks'])
            assert 'reference_patch' not in json.dumps(manifest)
            assert 'Full upstream test output' in log
        monkeypatch.setattr(server, 'evaluations', mgr)
        client = TestClient(server.app)
        response = client.get('/api/evaluations/preparation-log?suite=swebench')
        assert response.status_code == 200 and response.text == log
        assert client.get('/api/evaluations/preparation-log?suite=../../outside').status_code == 422
    asyncio.run(check())


def test_old_network_dependent_subset_must_be_prepared_again(tmp_path, monkeypatch):
    mgr = manager(tmp_path, monkeypatch)
    legacy = dict(dataset='SWE-bench Lite', selection='lumen-coding-v1', tasks=[])
    legacy['fingerprint'] = b.digest(legacy)
    b.atomic_json(mgr.assets / 'swebench.json', legacy)
    with pytest.raises(ValueError, match='subset is outdated'):
        mgr.manifest('swebench')


def repository_manifest(mgr):
    manifest = dict(dataset='SWE-bench Lite', revision='fixture', worker_image='sha256:worker',
        selection=b.SUITES['swebench']['selection'], tasks=[dict(id=f'repo__issue-{i}',
            prompt='Fix the bug', base_commit='a' * 40, image_id='sha256:repo') for i in range(3)])
    manifest['fingerprint'] = b.digest(manifest)
    b.atomic_json(mgr.assets / 'swebench.json', manifest)
    return manifest


@pytest.mark.parametrize('minutes,count,preset', [(10, 20, 'short'), (20, 40, 'medium'), (30, 80, 'long')])
def test_humaneval_presets_select_nested_subsets_without_deadlines(tmp_path, monkeypatch, minutes, count, preset):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        manifest = mgr.manifest('humaneval')
        manifest.pop('fingerprint')
        manifest['tasks'] = [dict(id=f'HumanEval/{n}', prompt='def f(): ...') for n in range(80)]
        manifest['fingerprint'] = b.digest(manifest)
        b.atomic_json(mgr.assets / 'humaneval.json', manifest)
        async def human(index, source, task, selected):
            task['state'] = 'passed'
        mgr.human = human
        await mgr.start('humaneval', 'test', minutes * 60)
        await mgr.task
        saved = mgr.get(mgr.current['id'])
        assert saved['state'] == 'complete' and saved['summary']['total'] == count
        assert saved['preset'] == preset and saved['deadline'] is None
        assert saved['evaluation']['preset'] == preset
        assert saved['evaluation']['timing_policy'].startswith('No generation')
        assert saved['dataset']['pool_fingerprint'] == manifest['fingerprint']
        assert [t['id'] for t in saved['tasks']] == [f'HumanEval/{n}' for n in range(count)]
    asyncio.run(check())


@pytest.mark.parametrize('minutes,count', [(10, 1), (20, 2), (30, 3)])
def test_repository_presets_select_and_archive_only_budgeted_tasks(tmp_path, monkeypatch, minutes, count):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        original = repository_manifest(mgr)
        async def repository(index, source, task, manifest, deadline=None):
            task.update(state='passed', grade={'passed': True})
        mgr.repository = AsyncMock(side_effect=repository)
        await mgr.start('swebench', 'test', minutes * 60)
        await mgr.task
        saved = mgr.get(mgr.current['id'])
        expected = [t['id'] for t in original['tasks'][:count]]
        assert saved['state'] == 'complete' and saved['summary']['score'] == 100
        assert [t['id'] for t in saved['tasks']] == expected
        assert saved['evaluation']['task_ids'] == expected and saved['evaluation']['count'] == count
        assert saved['dataset']['pool_fingerprint'] == original['fingerprint']
        assert mgr.manifest('swebench') == original  # Prepared cache is unchanged.
        assert len({b.select_repository_tasks(original, n)['fingerprint'] for n in (600, 1200, 1800)}) == 3
    asyncio.run(check())


def test_repository_uses_only_action_command_and_grader_safeguards(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        repository_manifest(mgr)
        mgr.new_repository = AsyncMock(return_value='agent')
        mgr.generate = AsyncMock(return_value=dict(role='assistant', content='{"finish":true}'))
        mgr.grade_repository = AsyncMock(return_value=({'passed': False}, 'Required tests failed'))
        mgr.command = AsyncMock(return_value=(0, ''))
        await mgr.start('swebench', 'test', 600)
        await mgr.task
        task = mgr.current['tasks'][0]
        assert 'generation_budget_seconds' not in task and 'time_budget_seconds' not in task
        assert mgr.current['deadline'] is None
        assert mgr.grade_repository.await_count == 1
        assert mgr.grade_repository.await_args.kwargs['timeout'] == b.SUITES['swebench']['grader_seconds'] == 150
        assert task['state'] == 'failed' and not task['patch_present']
        assert 'No patch was produced' in task['detail']
        assert 'no run deadline' in b.report_markdown(mgr.current)
        assert mgr.current['evaluation']['command_safety_seconds'] == 45
        assert mgr.current['evaluation']['max_steps'] == 40
    asyncio.run(check())


def test_repository_feedback_is_recorded_then_discarded_on_cancellation(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        repository_manifest(mgr)
        mgr.new_repository = AsyncMock(return_value='agent')
        patch = 'diff --git a/f.py b/f.py\n+fix\n'
        async def command(*args, **kwargs):
            return 0, patch if args[-1].find('git read-tree') >= 0 else 'command completed'
        mgr.command = AsyncMock(side_effect=command)
        waiting = asyncio.Event()
        async def generate(messages, task, index, filename):
            if len(messages) == 2:
                assert '40 actions remaining' in messages[-1]['content']
                return dict(role='assistant', content='{"command":"edit f.py"}')
            assert '39 actions remaining' in messages[-1]['content'] and 'A patch is saved' in messages[-1]['content']
            waiting.set()
            await asyncio.Event().wait()
        mgr.generate = generate
        await mgr.start('swebench', 'test', 600)
        await asyncio.wait_for(waiting.wait(), 1)
        root = mgr.root / mgr.current['id'] / 'task-01'
        assert (root / 'patch.diff').read_text() == patch
        assert '39 actions remaining' in json.loads((root / 'trajectory.json').read_text())[0]['budget_feedback']
        await mgr.stop()
        assert mgr.current['tasks'][0]['patch_present'] and mgr.current['state'] == 'cancelled'
        assert not root.exists()
    asyncio.run(check())


def test_grading_deadline_includes_repository_startup(tmp_path, monkeypatch):
    async def check():
        mgr = manager(tmp_path, monkeypatch)
        async def startup(source):
            await asyncio.Event().wait()
        mgr.new_repository = startup
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(mgr.grade_repository({}, '', 'worker', timeout=.01), .5)
    asyncio.run(check())
