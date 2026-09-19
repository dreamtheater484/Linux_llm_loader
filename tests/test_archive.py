import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from loader import archive, server
from loader.engines import Settings


def seed(state, kind='coding'):
    rows = [dict(id=f'{i:016x}', created=i, state=status, suite='humaneval',
                 model=dict(id=model, name='Qwen-' + model, title=model, quant='Q8'),
                 settings=dict(model_id=model), summary=dict(score=score), median_tps=score,
                 tasks=[{'metrics':[dict(tokens_per_second=40, prompt_tokens_per_second=200, speed_source='engine')]}])
            for i, (status, model, score) in enumerate([
                ('complete','one',80), ('complete','two',100), ('timed_out','one',None),
                ('cancelled','one',None), ('interrupted','one',None), ('running','two',None)], 1)]
    with archive.connect(state) as db:
        db.executemany(f'INSERT INTO {archive.TABLES[kind]} VALUES (?,?,?)', [(r['id'],r['created'],json.dumps(r)) for r in rows])
    return rows


@pytest.mark.parametrize('kind', ['coding','speed'])
def test_archive_filters_order_and_reversible_bulk_delete(tmp_path, kind):
    rows=seed(tmp_path,kind)
    assert archive.list_runs(tmp_path,kind)['total']==3
    filtered=archive.list_runs(tmp_path,kind,model_id='one',status='complete',query='Qwen',sort='oldest')
    assert [r['id'] for r in filtered['items']]==[rows[0]['id']]
    assert archive.list_runs(tmp_path,kind,sort='score')['items'][0]['id']==rows[1]['id']
    ids=[r['id'] for r in rows[:2]]
    archive.manage_runs(tmp_path,kind,ids,'delete')
    assert archive.list_runs(tmp_path,kind)['total']==1
    assert archive.list_runs(tmp_path,kind,trash=True)['total']==2
    archive.manage_runs(tmp_path,kind,ids,'restore')
    assert archive.list_runs(tmp_path,kind)['total']==3
    with pytest.raises(HTTPException):
        archive.manage_runs(tmp_path,kind,[rows[0]['id'],rows[-1]['id']],'delete')
    assert archive.list_runs(tmp_path,kind)['total']==3  # Entire batch rejected.
    with pytest.raises(HTTPException):archive.manage_runs(tmp_path,kind,[ids[0],ids[0]],'delete')
    archive.discard_aborted(tmp_path)
    with archive.connect(tmp_path) as db:
        assert db.execute(f'SELECT COUNT(*) FROM {archive.TABLES[kind]}').fetchone()[0]==3


def test_performance_uses_engine_samples_not_grading_wall_time():
    metrics=[dict(tokens_per_second=40,prompt_tokens_per_second=200,first_token_seconds=1,speed_source='engine'),
             dict(tokens_per_second=60,prompt_tokens_per_second=400,first_token_seconds=3,speed_source='engine'),
             dict(tokens_per_second=999,prompt_tokens_per_second=999,speed_source='wall'),
             dict(tokens_per_second=float('nan'),prompt_tokens_per_second=0,speed_source='engine')]
    p=archive.run_performance({'tasks':[{'metrics':metrics}], 'elapsed_seconds':1000})
    assert p['decode_tps']==50 and p['prefill_tps']==300 and p['first_token_seconds']==2
    assert p['decode_samples']==2 and p['prefill_samples']==2
    assert archive.run_performance({})['decode_tps'] is None


def test_management_routes_and_library_autocomplete(tmp_path, monkeypatch):
    seed(tmp_path,'speed')
    monkeypatch.setattr(server,'STATE',tmp_path)
    monkeypatch.setattr(server.supervisor,'inventory',{'models':[dict(id='one',name='Qwen-27B-Q6_K',quant='Q6_K',format='GGUF')]})
    client=TestClient(server.app)
    assert client.get('/api/archive/models?q=qwen%2027b').json()[0]['id']=='one'
    assert client.get('/api/archive/models?q=missing').json()==[]
    assert client.get('/api/archive/speed?model_id=two&sort=score').json()['total']==1
    body={'kind':'speed','ids':['0000000000000001'],'action':'delete'}
    assert client.post('/api/archive/manage',json=body).status_code==403
    assert client.post('/api/archive/manage',json=body,headers={'X-Lumen-Local':'1'}).status_code==200
    assert client.get('/api/benchmarks').json()[0]['id']=='0000000000000003'
    assert client.get('/api/archive/speed?trash=true').json()['total']==1


def test_cancelled_speed_test_is_not_stored(tmp_path, monkeypatch):
    async def check():
        started=asyncio.Event()
        async def stream(*args,**kwargs):
            started.set()
            yield {'type':'token','text':'partial'}
            await asyncio.Event().wait()
        fake=SimpleNamespace(model={},settings=Settings(model_id='one'),engine='gguf',effective={},
                             stream=stream,cancel=asyncio.Event(),benchmark={'completed':0},live_output=server.LiveOutput())
        monkeypatch.setattr(server,'supervisor',fake)
        monkeypatch.setattr(server,'STATE',tmp_path)
        task=asyncio.create_task(server.run_benchmark('off'))
        await started.wait();task.cancel();await task
        assert fake.benchmark['state']=='cancelled'
        assert archive.list_runs(tmp_path,'speed')['total']==0
    asyncio.run(check())
