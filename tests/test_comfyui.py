import asyncio
import errno
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from loader import comfyui, server
from loader.engines import Settings


@pytest.fixture(autouse=True)
def configured_integration(monkeypatch):
    monkeypatch.setattr(server.app_settings, 'comfy_settings', lambda: dict(
        enabled=True, mode='api', url='http://127.0.0.1:8188', container=''))


def stats(reserved=0):
    return {'system': {'comfyui_version': '0.37.0'}, 'devices': [
        {'type': 'cuda', 'torch_vram_total': reserved, 'vram_free': 10*2**30}]}


def client_with(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(comfyui.httpx, 'AsyncClient',
                        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))


def test_free_is_requested_even_when_initial_allocator_is_empty(monkeypatch):
    calls, progress = [], []
    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == '/queue':
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': []})
        if request.url.path == '/free':
            return httpx.Response(200)
        return httpx.Response(200, json=stats())
    client_with(monkeypatch, handler)
    result = asyncio.run(comfyui.release_comfyui(progress.append, poll_interval=0))
    assert result['state'] == 'released' and result['backend_kept_running']
    assert calls[2] == ('POST', '/free', {'unload_models': True, 'free_memory': True})
    assert [path for method,path,_ in calls if method=='POST'] == ['/free']
    assert sum(path=='/system_stats' for _,path,_ in calls) == 3


def test_waits_for_jobs_and_allocator_then_rechecks_queue(monkeypatch):
    queues = iter([True, False, False, True, False, False, False, False])
    memory = iter([8*2**30, 4*2**30, 0, 0])
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/queue':
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': [['job']] if next(queues) else []})
        if request.url.path == '/free': return httpx.Response(200)
        return httpx.Response(200, json=stats(next(memory)))
    client_with(monkeypatch, handler)
    result = asyncio.run(comfyui.release_comfyui(lambda _:None, poll_interval=0))
    assert result['allocator_before_bytes'] == 8*2**30
    assert result['allocator_after_bytes'] == 0
    assert calls.count('/free') == 2  # A new job invalidated the first cleanup.
    assert '/interrupt' not in calls


def test_absent_comfyui_does_not_block_loading(monkeypatch):
    def handler(request):
        try: raise ConnectionRefusedError(errno.ECONNREFUSED, 'refused')
        except ConnectionRefusedError as exc: raise httpx.ConnectError('refused',request=request) from exc
    client_with(monkeypatch, handler)
    assert asyncio.run(comfyui.release_comfyui(lambda _:None))['state'] == 'not_running'


@pytest.mark.parametrize('failure', ['http', 'invalid_stats', 'invalid_queue', 'free_failed', 'connection', 'disconnect'])
def test_unknown_or_failed_cleanup_blocks_loading(monkeypatch, failure):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if failure=='connection': raise httpx.ConnectError('TLS or network failure',request=request)
        if failure=='disconnect' and len(calls)>1: raise httpx.ConnectError('gone',request=request)
        if failure=='http': return httpx.Response(503)
        if request.url.path=='/system_stats': return httpx.Response(200,json={} if failure=='invalid_stats' else stats())
        if request.url.path=='/queue': return httpx.Response(200,json={} if failure=='invalid_queue' else {'queue_running':[],'queue_pending':[]})
        return httpx.Response(500 if failure=='free_failed' else 200)
    client_with(monkeypatch, handler)
    with pytest.raises(RuntimeError,match='new LLM was not started'):
        asyncio.run(comfyui.release_comfyui(lambda _:None,poll_interval=0))


@pytest.mark.parametrize('busy', [True, False])
def test_timeout_never_clears_queue_or_starts_engine(monkeypatch, busy):
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/queue': return httpx.Response(200,json={'queue_running':[['job']] if busy else [],'queue_pending':[]})
        if request.url.path=='/free': return httpx.Response(200)
        return httpx.Response(200,json=stats(2**30))
    client_with(monkeypatch,handler)
    with pytest.raises(RuntimeError,match='not finished releasing'):
        asyncio.run(comfyui.release_comfyui(lambda _:None,timeout=.01,poll_interval=.001))
    assert not any(method=='POST' and path!='/free' for method,path in calls)
    if busy: assert ('POST','/free') not in calls


@pytest.mark.parametrize('url', ['https://example.com','http://192.168.50.23:8188','http://127.0.0.1:8188/path',
                               'http://user:secret@127.0.0.1:8188','http://127.0.0.1:8188?x=1'])
def test_only_explicit_local_service_is_accepted(url):
    with pytest.raises(ValueError):comfyui.local_url(url)


@pytest.mark.parametrize('fail', [False, True])
def test_engine_launch_is_gated_on_comfyui_cleanup(monkeypatch,tmp_path,fail):
    supervisor=server.Supervisor()
    supervisor.settings=Settings(model_id='test')
    supervisor.model={'bytes':2**30}
    supervisor.state='loading'
    supervisor._terminate=AsyncMock()
    launch=Mock(side_effect=RuntimeError('Reached engine launch'))
    monkeypatch.setattr(server,'launch',launch)
    monkeypatch.setattr(server,'STATE',tmp_path)
    monkeypatch.setattr(server.psutil,'virtual_memory',lambda:SimpleNamespace(available=200*2**30))
    async def check():
        entered,release=asyncio.Event(),asyncio.Event()
        async def cleanup(progress, **kwargs):
            entered.set()
            await release.wait()
            if fail:raise RuntimeError('Cleanup failed')
            return {'state':'released'}
        monkeypatch.setattr(server,'release_comfyui',cleanup)
        pending=asyncio.create_task(supervisor._load())
        await asyncio.wait_for(entered.wait(), 2)
        launch.assert_not_called()
        release.set()
        await pending
        assert launch.call_count == (0 if fail else 1)
        assert supervisor.error == ('Cleanup failed' if fail else 'Reached engine launch')
    asyncio.run(check())


def test_cancelling_load_while_waiting_never_starts_engine(monkeypatch):
    supervisor=server.Supervisor()
    launch=Mock()
    monkeypatch.setattr(server,'launch',launch)
    async def check():
        entered=asyncio.Event()
        async def cleanup(progress, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(server,'release_comfyui',cleanup)
        pending=asyncio.create_task(supervisor._load())
        await asyncio.wait_for(entered.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):await pending
        launch.assert_not_called()
    asyncio.run(check())


def test_managed_cleanup_ignores_allocator_residue_and_verifies_stop(monkeypatch):
    monkeypatch.setenv('INFLECT_COMFYUI_CONTAINER','comfy-test')
    commands=[]
    async def command(*args):
        commands.append(args)
        if args[0]=='inspect':
            return json.dumps({'Id':'fixed-id','State':{'Running':len(commands)==1}})
        return 'fixed-id'
    monkeypatch.setattr(comfyui,'docker',command)
    def handler(request):
        assert request.url.path=='/queue'  # No allocator-zero gate.
        return httpx.Response(200,json={'queue_running':[],'queue_pending':[]})
    client_with(monkeypatch,handler)
    result=asyncio.run(comfyui.release_comfyui(lambda _:None,poll_interval=0))
    assert result['verification']=='container_stopped'
    assert result['restart_required']
    assert commands==[('inspect','--format','{{json .}}','comfy-test'),
                      ('stop','--time','10','fixed-id'),('inspect','--format','{{json .}}','fixed-id')]


def test_managed_already_stopped_service_stays_stopped(monkeypatch):
    monkeypatch.setenv('INFLECT_COMFYUI_CONTAINER','comfy-test')
    command=AsyncMock(return_value=json.dumps({'Id':'fixed-id','State':{'Running':False}}))
    monkeypatch.setattr(comfyui,'docker',command)
    result=asyncio.run(comfyui.release_comfyui(lambda _:None))
    asyncio.run(comfyui.restore_comfyui(result,lambda _:None))
    assert result['state']=='not_running'
    assert command.await_count==1


def test_managed_busy_queue_is_not_stopped(monkeypatch):
    monkeypatch.setenv('INFLECT_COMFYUI_CONTAINER','comfy-test')
    command=AsyncMock(return_value=json.dumps({'Id':'fixed-id','State':{'Running':True}}))
    monkeypatch.setattr(comfyui,'docker',command)
    client_with(monkeypatch,lambda _:httpx.Response(200,json={'queue_running':[['job']],'queue_pending':[]}))
    with pytest.raises(RuntimeError,match='running or queued jobs'):
        asyncio.run(comfyui.release_comfyui(lambda _:None,timeout=.01,poll_interval=.001))
    assert command.await_count==1


def test_managed_cancel_during_stop_waits_for_stop_then_restores(monkeypatch):
    monkeypatch.setenv('INFLECT_COMFYUI_CONTAINER','comfy-test')
    client_with(monkeypatch,lambda _:httpx.Response(200,json={'queue_running':[],'queue_pending':[]}))
    restore=AsyncMock()
    monkeypatch.setattr(comfyui,'restore_comfyui',restore)
    async def check():
        stopping,finish=asyncio.Event(),asyncio.Event()
        commands=[]
        async def command(*args):
            commands.append(args)
            if args[0]=='stop':
                stopping.set();await finish.wait();return 'fixed-id'
            return json.dumps({'Id':'fixed-id','State':{'Running':len(commands)==1}})
        monkeypatch.setattr(comfyui,'docker',command)
        task=asyncio.create_task(comfyui.release_comfyui(lambda _:None))
        await stopping.wait();task.cancel();await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert restore.await_count==1
        assert commands[-1][0]=='inspect'
    asyncio.run(check())


def test_managed_stop_failure_restores_and_blocks(monkeypatch):
    monkeypatch.setenv('INFLECT_COMFYUI_CONTAINER','comfy-test')
    client_with(monkeypatch,lambda _:httpx.Response(200,json={'queue_running':[],'queue_pending':[]}))
    async def command(*args):
        if args[0]=='stop':raise RuntimeError('Docker failed')
        return json.dumps({'Id':'fixed-id','State':{'Running':True}})
    monkeypatch.setattr(comfyui,'docker',command)
    restore=AsyncMock();monkeypatch.setattr(comfyui,'restore_comfyui',restore)
    with pytest.raises(RuntimeError,match='Docker failed'):
        asyncio.run(comfyui.release_comfyui(lambda _:None))
    restore.assert_awaited_once()


def test_restart_waits_for_api_and_accepts_nonzero_baseline(monkeypatch):
    command=AsyncMock();monkeypatch.setattr(comfyui,'docker',command)
    responses=iter([httpx.Response(503),httpx.Response(200,json=stats(64*2**20))])
    client_with(monkeypatch,lambda _:next(responses))
    preparation={'restart_required':True,'container_id':'fixed-id','url':'http://127.0.0.1:8188'}
    asyncio.run(comfyui.restore_comfyui(preparation,lambda _:None,poll_interval=0))
    command.assert_awaited_once_with('start','fixed-id')
    assert preparation['backend_restored'] and not preparation['restart_required']


@pytest.mark.parametrize('cancel', [True,False])
def test_supervisor_restores_comfyui_after_load_error_or_cancel(monkeypatch,tmp_path,cancel):
    supervisor=server.Supervisor()
    supervisor.settings=Settings(model_id='test');supervisor.model={'bytes':2**30}
    events=[]
    supervisor.state='loading';supervisor._terminate=AsyncMock(side_effect=lambda:events.append('terminate'))
    monkeypatch.setattr(server,'STATE',tmp_path)
    monkeypatch.setattr(server.psutil,'virtual_memory',lambda:SimpleNamespace(available=200*2**30))
    monkeypatch.setattr(server,'release_comfyui',AsyncMock(return_value={'restart_required':True}))
    restore=AsyncMock(side_effect=lambda *args:events.append('restore'));monkeypatch.setattr(server,'restore_comfyui',restore)
    monkeypatch.setattr(server,'launch',Mock(side_effect=asyncio.CancelledError() if cancel else RuntimeError('Engine failure')))
    if cancel:
        with pytest.raises(asyncio.CancelledError):asyncio.run(supervisor._load())
    else:
        asyncio.run(supervisor._load());assert supervisor.error=='Engine failure'
    restore.assert_awaited_once()
    assert events==['terminate','restore']


def test_restart_failure_remains_visible_without_hiding_engine_error(monkeypatch,tmp_path):
    supervisor=server.Supervisor()
    supervisor.settings=Settings(model_id='test');supervisor.model={'bytes':2**30}
    supervisor._terminate=AsyncMock()
    monkeypatch.setattr(server,'STATE',tmp_path)
    monkeypatch.setattr(server.psutil,'virtual_memory',lambda:SimpleNamespace(available=200*2**30))
    monkeypatch.setattr(server,'release_comfyui',AsyncMock(return_value={'restart_required':True}))
    monkeypatch.setattr(server,'restore_comfyui',AsyncMock(side_effect=RuntimeError('restart failed')))
    monkeypatch.setattr(server,'launch',Mock(side_effect=RuntimeError('engine failed')))
    asyncio.run(supervisor._load())
    assert 'engine failed' in supervisor.error and 'restart failed' in supervisor.error
    assert supervisor.memory_preparation['restore_error']=='restart failed'
