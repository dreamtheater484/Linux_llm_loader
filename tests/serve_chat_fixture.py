"""Deterministic UI-only test server. Run with the manager Python; never loads a model."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['INFLECT_STATE'] = tempfile.mkdtemp(prefix='inflect-ui-check-')
os.environ['INFLECT_CONFIG_FILE'] = str(Path(os.environ['INFLECT_STATE']) / 'config.json')
os.environ['INFLECT_PORT'] = '7861'
os.environ['INFLECT_ALLOWED_HOSTS'] = '127.0.0.1,localhost'
from loader import server
from loader.engines import Settings
import uvicorn
server.MODEL_ROOT = Path(os.environ['INFLECT_STATE'])
server.lan_interfaces = server.app_settings.lan_interfaces = lambda: [dict(name='test-lan', host='192.168.8.9', subnet='192.168.8.0/24')]
server.app_settings.write_config(dict(model_root=str(Path(os.environ['INFLECT_STATE'])), comfyui_container='test-comfy', comfyui_enabled=True))

model = dict(family='Qwen3.8-Flash-Next', variant='', expert_bytes=58_000_000_000, id='ui-exl3', path='/test/qwen', name='Qwen3.8-Flash-Next-EXL3', title='Qwen3.8 Flash Next', format='EXL3', quant='4.05 bpw', architecture='qwen', context=262144, vision=True, mtp=True, ngram=True, experts=512, draft_limit=4, bytes=80_000_000_000, issues=[], recommended=True, engines=['exl3'], reasoning=dict(options=['default','off'], toggle=True, levels=[], default_effort=None))
gguf = {**model, 'id':'ui-gguf','format':'GGUF','quant':'Q4_K_M','engines':['gguf'],'name':'Qwen3.8-Flash-Next-Q4_K_M','bytes':111_000_000_000,'expert_bytes':80_000_000_000,'mtp':False,'ngram':False}
dense = dict(model, id='ui-dense', family='Qwen3.6-27B', variant='', name='Qwen3.6-27B-Q6_K', title='Qwen3.6 27B Q6_K', format='GGUF', quant='Q6_K', engines=['gguf'], bytes=22_500_000_000, expert_bytes=0, experts=0, context=262144, ngram=False, source='unsloth/Qwen3.6-27B-GGUF', recommended=False)
dense2 = dict(dense, id='ui-dense-q4', name='Qwen3.6-27B-Uncensored-Heretic-Q4_K_M', variant='Uncensored Heretic', quant='Q4_K_M', bytes=16_800_000_000, source=None)
moe = dict(model, id='ui-moe', family='Qwen3.6-35B-A3B', variant='', name='Qwen3.6-35B-A3B-UD-Q4_K_M', title='Qwen3.6 35B A3B', format='GGUF', quant='UD-Q4_K_M', engines=['gguf'], bytes=22_100_000_000, expert_bytes=19_500_000_000, experts=256, active_b=3.0, mtp=False, ngram=False, recommended=False)
deep = dict(model, id='ui-deep', family='DeepSeek-V4-Flash-0731', variant='', name='DeepSeek-V4-Flash-0731-EXL3-3.04bpw', title='DeepSeek V4 Flash 0731', quant='3.04 bpw', bytes=117_000_000_000, expert_bytes=111_000_000_000, experts=256, context=1048576, vision=False, ngram=False)
small = dict(dense, id='ui-small', family='gemma-4-E4B', variant='it qat', name='gemma-4-E4B-it-qat-UD-Q4_K_XL', title='gemma 4 E4B it qat', quant='UD-Q4_K_XL', bytes=4_200_000_000, context=131072, vision=False, mtp=False, source=None)
server.supervisor.inventory = dict(models=[model,gguf,dense,dense2,moe,deep,small], errors=[], root='/test/models', locations=['/media/archive/old-models'], revision=1)
base = Settings(model_id='ui-dense', context=131072, max_output=32768, chunk_size=4096, vision=True, prediction='mtp', cpu_percent=0, temperature=1).model_dump()
(Path(os.environ['INFLECT_STATE'])/'profiles.json').write_text(json.dumps([
  dict(id='p1', name='auto', auto_name=True, settings=base, updated_at=1),
  dict(id='p2', name='Long documents', auto_name=False, settings={**base, 'context': 262144, 'kv': 'Q4', 'max_output': 16384}, updated_at=2),
  dict(id='p3', name='auto', auto_name=True, settings={**base, 'draft_tokens': 3, 'reasoning_effort': 'off', 'temperature': 0.6}, updated_at=3),
  dict(id='p4', name='Flash everyday', auto_name=False, settings=Settings(model_id='ui-exl3', context=262144, max_output=32768, vision=True, prediction='mtp', cpu_percent=60, chunk_size=4096).model_dump(), updated_at=4)]))
from loader.downloads import DownloadManager
server.downloads = DownloadManager(Path(os.environ['INFLECT_STATE']), Path(os.environ['INFLECT_STATE'])/'library', server.download_finished)
server.MODEL_ROOT = Path(os.environ['INFLECT_STATE'])/'library'
server.MODEL_ROOT.mkdir(exist_ok=True)
server.supervisor.model = model
server.supervisor.engine = 'exl3'
server.supervisor.state = 'ready'
server.supervisor.settings = Settings(**{**json.loads((Path(os.environ['INFLECT_STATE'])/'profiles.json').read_text())[3]['settings']})
server.engine_inventory = lambda: [dict(id='exl3',name='ExLlamaV3',installed=True),dict(id='gguf',name='llama.cpp',installed=True)]
server.telemetry.value = dict(cpu_percent=13,cpu_count=16,cpu_power=dict(watts=34.7),gpu=dict(power_watts=79,power_limit_watts=525,temperature_c=41,used_bytes=28*2**30,total_bytes=32*2**30,name='NVIDIA GeForce RTX 5090'),ram=dict(accounting='physical_including_cache_v1',used_bytes=90*2**30,total_bytes=192*2**30,available_bytes=95*2**30,free_bytes=90*2**30,cache_bytes=20*2**30),model_memory=dict(resident_bytes=69*2**30,file_bytes=15*2**30))
async def stream(messages,*args,**kwargs):
    async with server.supervisor.generation_lock:
        server.supervisor.cancel.clear()
        yield dict(type='context',input_tokens=84)
        if 'slow' in json.dumps(messages[-1]['content']).lower():
            for i in range(80):
                if server.supervisor.cancel.is_set():
                    yield dict(type='cancelled');return
                yield dict(type='token',text=f'Part {i}. ',reasoning='')
                await asyncio.sleep(.2)
        else:
            answer='Here is a small, self-contained HTML app. Open **Preview** to try it.\n\n```html\n<!doctype html><html><head><style>body{background:#f3f7f3;color:#294b38;font-family:system-ui;display:grid;place-items:center;min-height:85vh}main{background:white;border:1px solid #dde6db;padding:48px;border-radius:22px;text-align:center;box-shadow:0 16px 60px #294b380a}small{letter-spacing:3px;color:#88a08d;font-size:10px}h1{font-size:34px;letter-spacing:-1px}button{background:#2f694d;color:white;padding:13px 26px;border:0;border-radius:9px;cursor:pointer}p{color:#8aa08f}</style></head><body><main><small>MAKE SPACE FOR IDEAS</small><h1>A little momentum.</h1><p id="count">0 steps forward</p><button onclick="document.getElementById(\'count\').textContent=(++window.count)+\' steps forward\'">Take a step</button></main><script>window.count=0;</script></body></html>\n```\n\nEverything runs locally inside the preview.\n\n| Feature | Status |\n| --- | --- |\n| HTML preview | Ready |\n| Offline | Yes |\n'
            for i in range(0,len(answer),65):
                yield dict(type='token',text=answer[i:i+65],reasoning='')
                await asyncio.sleep(.025)
        metrics=dict(usage=dict(total_tokens=620,completion_tokens=536),tokens_per_second=55.7,prompt_tokens_per_second=187.2,first_token_seconds=.8,finish_reason='stop')
        server.supervisor.last_usage=metrics
        yield dict(type='complete',**metrics)
server.supervisor.stream=stream
uvicorn.run(server.app,host='127.0.0.1',port=7861,lifespan='off',proxy_headers=False)
