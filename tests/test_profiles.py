import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from loader import server
from loader.engines import Settings
from loader.profiles import compact_name, enrich_profile
from test_loader import model


def test_duplicate_order_and_restore_keep_independent_profiles(tmp_path):
    settings = Settings(model_id='test').model_dump()
    saved = [{'id': 'first', 'name': 'First', 'settings': settings}, {'id': 'second', 'name': 'Second', 'settings': settings}]
    (tmp_path / 'profiles.json').write_text(json.dumps(saved))
    headers = {'X-Inflect-Local': '1'}
    with patch.object(server, 'STATE', tmp_path), patch.object(server.supervisor, 'lookup', return_value=model()), patch.object(server.supervisor, 'refresh'):
        with TestClient(server.app) as client:
            result = client.post('/api/profiles/first/duplicate', json={}, headers=headers)
            assert result.status_code == 200
            profiles = result.json()
            clone = profiles[1]
            assert clone['name'] == 'First · copy 1' and clone['id'] not in ('first', 'second')
            assert clone['settings'] == profiles[0]['settings']
            order = ['second', clone['id'], 'first']
            assert client.post('/api/profiles/reorder', json={'ids': order}, headers=headers).status_code == 200
            assert [p['id'] for p in client.get('/api/profiles').json()] == order
            assert client.post('/api/profiles/reorder', json={'ids': ['first']*3}, headers=headers).status_code == 409
            changed = {**settings, 'cpu_percent': 45}
            assert client.put(f"/api/profiles/{clone['id']}", json={'name': 'Edited copy', 'settings': changed}, headers=headers).status_code == 200
            assert next(p for p in client.get('/api/profiles').json() if p['id'] == 'first')['settings']['cpu_percent'] == 80
            client.delete('/api/profiles/first', headers=headers)
            assert client.post('/api/profiles/first/duplicate', json={}, headers=headers).status_code == 409
            assert client.post('/api/profiles/reorder', json={'ids': [clone['id'], 'second']}, headers=headers).status_code == 200
            assert client.get('/api/profiles?trash=true').json()[0]['id'] == 'first'
            client.post('/api/profiles/first/restore', json={}, headers=headers)
            assert len(client.get('/api/profiles').json()) == 3


def test_auto_names_use_only_matching_benchmarks_and_follow_updated_settings():
    m = model()
    m.update(name='Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P', quant='Q4_K_P', engines=['gguf'])
    settings = Settings(model_id='test', context=32768, vision=False, prediction='mtp', cpu_percent=0).model_dump()
    profile = {'id': 'p', 'name': 'Old label', 'settings': settings, 'auto_name': True}
    result = {'id': 'b', 'settings': settings, 'state': 'complete', 'kind': 'speed', 'median_tps': 102.5}
    enriched = enrich_profile(profile, m, [result])
    assert 'Qwen3.8 27B Agg. · Q4_K_P · 32K/Q8 · 102 tok/s' in enriched['name']
    assert 'V:off · MTP:on/2' in enriched['name']
    assert enriched['benchmark_results'][0]['exact_settings']
    different = {**result, 'settings': {**settings, 'draft_tokens': 3}}
    enriched = enrich_profile(profile, m, [different])
    assert 'tok/s' not in enriched['name'] and not enriched['benchmark_results']
    assert len(enriched['related_results']) == 1
    assert enrich_profile(profile, None, [])['name'] == 'Old label'
    assert '28K' in compact_name(m, {**settings, 'context': 28672})


def test_live_profile_speeds_include_legacy_coding_metrics_and_ignore_deleted(tmp_path, monkeypatch):
    import sqlite3
    settings = Settings(model_id='test').model_dump()
    profile = dict(id='profile', name='Test', settings=settings)
    (tmp_path / 'profiles.json').write_text(json.dumps([profile]))
    run = dict(id='a'*16, created=20, state='complete', settings=settings, benchmark='HumanEval+',
               tasks=[{'metrics':[dict(tokens_per_second=50, prompt_tokens_per_second=200, speed_source='engine'),
                                  dict(tokens_per_second=70, prompt_tokens_per_second=400, speed_source='engine')]}])
    with sqlite3.connect(tmp_path / 'results.sqlite3') as db:
        db.execute('CREATE TABLE evaluations (id TEXT PRIMARY KEY, created REAL, data TEXT)')
        db.execute('INSERT INTO evaluations VALUES (?,?,?)', (run['id'],run['created'],json.dumps(run)))
    monkeypatch.setattr(server, 'STATE', tmp_path)
    monkeypatch.setattr(server.supervisor, 'inventory', {'models':[model()]})
    import asyncio
    result = asyncio.run(server.profiles())[0]
    assert result['performance']['decode_tps'] == 60
    assert result['performance']['prefill_tps'] == 300
    assert result['performance']['decode_source']['kind'] == 'coding'
    assert result['benchmark_results'][0]['median_prompt_tps'] == 300
    with sqlite3.connect(tmp_path / 'results.sqlite3') as db:
        run['deleted_at'] = 30
        db.execute('UPDATE evaluations SET data=?', (json.dumps(run),))
    assert asyncio.run(server.profiles())[0]['performance']['decode_tps'] is None


def test_latest_valid_exact_measurements_keep_independent_sources():
    settings = Settings(model_id='test').model_dump()
    p = dict(id='p', name='Profile', settings=settings)
    base = dict(settings=settings, state='complete', kind='speed', id='old', created=1, median_tps=50, median_prompt_tps=500)
    newer = dict(base, kind='coding', id='new', created=2, median_tps=60, median_prompt_tps=None)
    mismatch = dict(base, created=3, settings=dict(settings, cpu_percent=10), median_tps=900)
    aborted = dict(base, created=4, state='cancelled', median_tps=1000)
    result = enrich_profile(p, model(), [mismatch, aborted, base, newer])['performance']
    assert result['decode_tps'] == 60 and result['prefill_tps'] == 500
    assert result['decode_source']['id'] == 'new' and result['prefill_source']['id'] == 'old'


def test_loaded_memory_persists_by_loading_settings(tmp_path):
    import sqlite3
    from loader.profiles import record_loaded_memory, load_key
    s = Settings(model_id='test').model_dump()
    m = model()
    hardware = dict(timestamp=123, gpu=dict(used_bytes=30*2**30, name='GPU'),
                    ram=dict(used_bytes=70*2**30, accounting='physical_including_cache_v1', cache_bytes=60*2**30),
                    model_memory=dict(resident_bytes=65*2**30))
    assert record_loaded_memory(tmp_path, s, m, hardware)
    with sqlite3.connect(tmp_path / 'results.sqlite3') as db:
        measurements = {key:json.loads(value) for key,value in db.execute('SELECT * FROM profile_loads')}
    p = dict(id='p', name='Profile', settings=s)
    value = enrich_profile(p,m,[],measurements)['loaded_memory']
    assert value['vram_bytes'] == 30*2**30 and value['ram_bytes'] == 70*2**30
    assert value['ram_cache_bytes'] == 60*2**30
    assert value['model_memory']['resident_bytes'] == 65*2**30
    assert value['scope'] == 'system_total' and value['measured_at'] == 123
    assert load_key(dict(s, temperature=1.1,max_output=8192,reasoning_effort='off'),m) == load_key(s,m)
    assert load_key(dict(s, engine='exl3'),m) == load_key(s,m)
    assert enrich_profile(dict(p,settings=dict(s,kv='Q4')),m,[],measurements)['loaded_memory'] is None
    assert not record_loaded_memory(tmp_path,s,m,dict(timestamp=124,gpu={'used_bytes':float('nan')},ram={'used_bytes':True}))
    assert record_loaded_memory(tmp_path,s,m,dict(timestamp=125,gpu=None,ram={'used_bytes':50*2**30}))


def test_legacy_profile_ram_is_not_used_for_capacity_comparisons():
    from loader.profiles import load_key
    s = Settings(model_id='test').model_dump()
    m = model()
    original = dict(ram_bytes=13*2**30, vram_bytes=8*2**30, measured_at=1)
    result = enrich_profile(dict(id='p',name='Test',settings=s), m, [], {load_key(s,m):original})
    assert result['loaded_memory']['ram_bytes'] is None
    assert result['loaded_memory']['legacy_ram_bytes'] == 13*2**30
    assert result['loaded_memory']['vram_bytes'] == 8*2**30
    assert original['ram_bytes'] == 13*2**30


def test_profile_recovers_prefill_from_old_speed_tests(tmp_path, monkeypatch):
    import asyncio, sqlite3
    settings = Settings(model_id='test').model_dump()
    (tmp_path / 'profiles.json').write_text(json.dumps([dict(id='p', name='Profile', settings=settings)]))
    run = dict(id='legacy', created=1, state='complete', settings=settings, median_tps=20,
               runs=[{'usage':{'prompt_tokens_per_sec':40}}, {'usage':{'prompt_tokens_per_sec':60}}])
    monkeypatch.setattr(server,'STATE',tmp_path)
    monkeypatch.setattr(server.supervisor,'inventory',{'models':[model()]})
    with server.db() as db:
        db.execute('INSERT INTO benchmarks VALUES (?,?,?)',(run['id'],1,json.dumps(run)))
    assert asyncio.run(server.profiles())[0]['performance']['prefill_tps']==50
