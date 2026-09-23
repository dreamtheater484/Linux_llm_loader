import asyncio
import json
from pathlib import Path
import struct
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from loader.engines import Settings, launch, validate
from loader.library import gguf_metadata, native_model, within
from loader.metrics import CpuPower, parse_gpu
from loader import server


def model(**changes):
    return dict(id='test', path='/models with spaces/qwen', name='qwen', title='Qwen', engines=['exl3'], format='EXL3',
                issues=[], context=262144, vision=True, mtp=True, experts=512, layers=48, ngram=True, bytes=85_000_000_000, **changes)


def test_context_includes_answer():
    with pytest.raises(ValidationError):
        Settings(model_id='test', context=4096, max_output=4096)
    with pytest.raises(ValidationError):
        Settings(model_id='test', context=262145)


def test_unsupported_combinations_are_rejected():
    base = model()
    for settings in [Settings(model_id='test', engine='vllm'), Settings(model_id='test', context=524288)]:
        with pytest.raises(ValueError):
            validate(settings, base, False)
    base['vision'] = False
    with pytest.raises(ValueError, match='vision'):
        validate(Settings(model_id='test'), base, False)
    base['vision'] = True
    with pytest.raises(ValueError, match='Draft length'):
        validate(Settings(model_id='test', prediction='mtp', draft_tokens=5), base, False)
    base['draft_limit'] = 5
    assert validate(Settings(model_id='test', prediction='mtp', draft_tokens=5), base, False) == 'exl3'
    base['vision'], base['mtp'] = True, False
    with pytest.raises(ValueError, match='MTP'):
        validate(Settings(model_id='test', prediction='mtp'), base, False)


def test_missing_shards_prevent_load():
    m = model(); m['issues'] = ['Missing shard']
    with pytest.raises(ValueError, match='Missing shard'):
        validate(Settings(model_id='test'), m, False)


def test_exl_config_maps_capacity_vision_cache_prediction_and_ram(tmp_path):
    with patch('loader.engines.validate', return_value='exl3'):
        args, env, config = launch(Settings(model_id='test', prediction='mtp'), model(), tmp_path, 5050, 'test-key')
    assert config['model']['model_dir'] == '/models with spaces'
    assert config['model']['max_seq_len'] == config['model']['cache_size'] == 262144
    assert config['model']['vision'] is True
    assert config['model']['cache_mode'] == 'Q8'
    assert config['model']['ngram_ram'] is True
    assert config['model']['cpu_moe_split_experts'] == 416
    assert config['draft_model']['draft_mode'] == 'mtp'
    assert env['EXL3_HOST_MEM_RESERVE_MB'] == '16384'
    assert config['network']['allowed_origins'] == []
    assert config['network']['api_servers'] == ['oai']
    assert (tmp_path / 'api_tokens.yml').stat().st_mode & 0o777 == 0o600
    assert str(tmp_path / 'config.yml') in args
    with patch('loader.engines.validate', return_value='exl3'):
        _, _, streamed = launch(Settings(model_id='test', ngram_ram=False), model(), tmp_path, 5051, 'test-key')
    assert streamed['model']['ngram_ram'] is False


def test_gpu_metrics_keep_units_and_unavailable_values():
    result = parse_gpu('NVIDIA Test GPU, 1024, 32768, 37, 275.25, 575.0, 62, 570.00, 4\n')
    assert result['power_watts'] == 275.25
    assert result['used_bytes'] == 2**30
    assert result['total_bytes'] == 32768 * 2**20
    assert result['utilization'] == 37
    unknown = parse_gpu('GPU, [N/A], 100, [N/A], [N/A], [N/A], [N/A], driver, [N/A]')
    assert unknown['power_watts'] is None
    assert unknown['used_bytes'] is None


def test_cpu_power_rapl_package_delta_wrap_and_no_core_double_count(tmp_path):
    def zone(name, label, energy):
        root = tmp_path / 'class/powercap' / name
        root.mkdir(parents=True)
        (root / 'name').write_text(label)
        (root / 'energy_uj').write_text(str(energy))
        (root / 'max_energy_range_uj').write_text('100000000')
        return root
    package = zone('intel-rapl:0', 'package-0', 90000000)
    zone('intel-rapl:0:0', 'core', 1000000)
    power = CpuPower(tmp_path)
    assert power.sample(10)['watts'] is None
    (package / 'energy_uj').write_text('10000000')
    assert power.sample(12)['watts'] == 10
    (package / 'energy_uj').write_text('40000000')
    assert power.sample(13)['watts'] == 30
    (package / 'energy_uj').unlink()
    assert power.sample(14)['watts'] is None


def test_cpu_power_only_uses_qualified_amd_package_sensor(tmp_path):
    sensor = tmp_path / 'class/hwmon/hwmon4'
    (sensor / 'device').mkdir(parents=True)
    (sensor / 'name').write_text('amdgpu')
    (sensor / 'device/vendor').write_text('0x1002')
    (sensor / 'device/device').write_text('0x744c')
    (sensor / 'power1_label').write_text('PPT')
    (sensor / 'power1_input').write_text('52345000')
    power = CpuPower(tmp_path)
    assert power.sample()['watts'] is None  # A discrete GPU is not a CPU sensor.
    (sensor / 'device/device').write_text('0x164e')
    result = power.sample()
    assert result['watts'] == 52.345
    assert 'SoC' in result['detail']
    (sensor / 'power1_input').write_text('nan')
    assert power.sample()['watts'] is None


def test_lan_access_is_limited_to_configured_private_subnet():
    with patch.object(server, 'LAN_NETWORK', None):
        assert server.client_allowed('127.0.0.1')
        assert not server.client_allowed('10.42.7.40')
        assert not server.client_allowed('203.0.113.40')
    with patch.object(server, 'LAN_NETWORK', server.ipaddress.ip_network('10.42.7.0/24')):
        assert server.client_allowed('10.42.7.40')
        assert not server.client_allowed('10.42.8.40')
        assert not server.client_allowed('203.0.113.40')
    with patch.object(server, 'ALLOWED_HOSTS', {'127.0.0.1', '10.42.7.23'}):
        assert server.origin_allowed('http://10.42.7.23:7860')
        assert not server.origin_allowed('http://10.42.7.99:7860')


def test_auto_named_profile_collision_gets_copy_number(tmp_path):
    base = {'name': 'Model · 3bpw · CPU:60%', 'auto_name': True, 'settings': Settings(model_id='test').model_dump()}
    headers = {'X-Inflect-Local': '1'}
    with patch.object(server, 'STATE', tmp_path), patch.object(server.supervisor, 'lookup', return_value=model()), patch.object(server.supervisor, 'refresh'):
        with TestClient(server.app) as client:
            assert client.post('/api/profiles', json=base, headers=headers).status_code == 200
            warmer = {**base, 'settings': {**base['settings'], 'temperature': 1.0}}
            response = client.post('/api/profiles', json=warmer, headers=headers)
            assert response.status_code == 200
            copy = next(p for p in response.json() if p['settings']['temperature'] == 1.0)
            assert copy['name'] == 'Model · 3bpw · CPU:60% · copy 1' and copy['copy_number'] == 1
            assert client.post('/api/profiles', json={**base, 'auto_name': False}, headers=headers).status_code == 409


def test_profile_migration_edit_collision_delete_restore(tmp_path):
    old = {'name': 'Everyday', 'settings': Settings(model_id='test').model_dump()}
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps([old]))
    headers = {'X-Inflect-Local': '1'}
    with patch.object(server, 'STATE', tmp_path), patch.object(server.supervisor, 'lookup', return_value=model()), patch.object(server.supervisor, 'refresh'):
        with TestClient(server.app) as client:
            initial = client.get('/api/profiles').json()
            identity = initial[0]['id']
            assert initial[0]['settings'] == old['settings']
            assert client.get('/api/profiles').json()[0]['id'] == identity
            assert client.post('/api/profiles', json=old, headers=headers).status_code == 409
            assert client.post('/api/profiles', json={**old, 'name': '  '}, headers=headers).status_code == 422
            created = client.post('/api/profiles', json={**old, 'name': 'Second'}, headers=headers).json()
            second = next(p for p in created if p['name'] == 'Second')
            assert client.put(f'/api/profiles/{identity}', json={**old, 'name': ' second '}, headers=headers).status_code == 409
            changed = {**old, 'name': 'Renamed', 'settings': {**old['settings'], 'cpu_percent': 65}}
            response = client.put(f'/api/profiles/{identity}', json=changed, headers=headers)
            assert response.status_code == 200
            updated = next(p for p in response.json() if p['id'] == identity)
            assert updated['name'] == 'Renamed' and updated['settings']['cpu_percent'] == 65
            assert client.delete(f'/api/profiles/{identity}').status_code == 403
            assert len(client.delete(f'/api/profiles/{identity}', headers=headers).json()) == 1
            trash = client.get('/api/profiles?trash=true').json()
            assert trash[0]['id'] == identity and trash[0]['deleted_at']
            assert client.put(f'/api/profiles/{identity}', json=changed, headers=headers).status_code == 409
            restored = client.post(f'/api/profiles/{identity}/restore', headers=headers, json={}).json()
            assert len(restored) == 2
            assert next(p for p in restored if p['id'] == second['id'])['name'] == 'Second'
            assert client.get('/api/profiles?trash=true').json() == []
            assert client.delete('/api/profiles/missing', headers=headers).status_code == 404


def test_model_paths_cannot_escape(tmp_path):
    with pytest.raises(ValueError):
        within(tmp_path, '../outside')


def test_truncated_safetensors_flagged(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'architectures':['Qwen4ExpForConditionalGeneration'], 'quantization_config':{'quant_method':'exl3'}}))
    (tmp_path / 'tokenizer.json').write_text('{}')
    header = json.dumps({'tensor':{'dtype':'F16','shape':[4], 'data_offsets':[0,8]}}).encode()
    (tmp_path / 'model.safetensors').write_bytes(struct.pack('<Q',len(header))+header+b'12')
    result = native_model(tmp_path, tmp_path)
    assert result['issues'] == ['Incomplete tensor data: model.safetensors']


def test_gguf_metadata_and_truncation(tmp_path):
    path=tmp_path/'model.gguf'
    def string(text):
        data=text.encode();return struct.pack('<Q',len(data))+data
    path.write_bytes(b'GGUF'+struct.pack('<IQQ',3,0,2)+string('general.architecture')+struct.pack('<I',8)+string('llama')+string('llama.context_length')+struct.pack('<II',4,262144))
    assert gguf_metadata(path)['llama.context_length'] == 262144
    path.write_bytes(b'GGUF\x03')
    with pytest.raises(ValueError, match='Truncated'):
        gguf_metadata(path)


def test_generation_rejected_when_not_ready():
    supervisor=server.Supervisor()
    async def attempt():
        async for _ in supervisor.stream([{'role':'user','content':'Hi'}]):
            pass
    with pytest.raises(ValueError, match='Load a model'):
        asyncio.run(attempt())


def test_management_api_rejects_cross_site_calls(tmp_path):
    with patch.object(server, 'STATE', tmp_path), patch.object(server.supervisor, 'refresh'):
        with TestClient(server.app) as client:
            assert client.post('/api/cancel').status_code == 403
            assert client.post('/api/cancel', headers={'X-Inflect-Local':'1', 'Origin':'https://unrelated.example'}).status_code == 403
            assert client.post('/api/cancel', headers={'X-Inflect-Local':'1'}, json={}).status_code == 200
            assert client.get('/api/status').status_code == 200


def test_cancel_interrupts_waiting_stream_without_unloading():
    supervisor=server.Supervisor()
    supervisor.state='ready'
    supervisor.model=model()
    supervisor.settings=Settings(model_id='test')
    supervisor.engine='exl3'
    supervisor.token='test-key'
    reading=asyncio.Event()
    closed=[]
    class CountResponse:
        is_error=False
        def json(self): return {'length':10}
    class Response:
        status_code=200
        async def __aenter__(self): return self
        async def __aexit__(self,*args): closed.append('response')
        async def aiter_lines(self):
            reading.set()
            try:
                await asyncio.sleep(30)
                yield 'data: [DONE]'
            finally:
                closed.append('reader')
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): return CountResponse()
        def stream(self,*args,**kwargs): return Response()
    async def exercise():
        stream=supervisor.stream([{'role':'user','content':'A long prompt'}])
        assert (await anext(stream))['type']=='context'
        pending=asyncio.create_task(anext(stream))
        await asyncio.wait_for(reading.wait(),1)
        supervisor.cancel.set()
        assert (await asyncio.wait_for(pending,1))['type']=='cancelled'
        await stream.aclose()
        assert set(closed)=={'response','reader'}
        assert not supervisor.generation_lock.locked()
        assert supervisor.state=='ready'
    with patch.object(server.httpx,'AsyncClient',return_value=Client()):
        asyncio.run(exercise())


def test_weight_split_separates_experts_and_ngram_table(tmp_path):
    from loader.library import safetensors_weight_split
    def write(name, tensors):
        header, offset = {}, 0
        for key, size in tensors.items():
            header[key] = {'dtype': 'U8', 'shape': [size], 'data_offsets': [offset, offset + size]}
            offset += size
        data = json.dumps(header).encode()
        (tmp_path / name).write_bytes(len(data).to_bytes(8, 'little') + data + b'\0' * offset)
        return tmp_path / name
    weights = [write('model.safetensors', {'model.layers.0.mlp.experts.0.up.weight': 300, 'model.layers.0.attn.q.weight': 50}),
               write('ngram_embedding.safetensors', {'model.layers.1.ple.ple_embedding.ngram_embedding.shard_0.trellis': 700})]
    assert safetensors_weight_split(weights) == (300, 700)
