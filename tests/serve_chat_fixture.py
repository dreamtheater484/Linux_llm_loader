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

model = dict(id='ui-exl3', path='/test/qwen', name='Qwen3.8-Flash-Next-EXL3', title='Qwen3.8 Flash Next', format='EXL3', quant='4.05 bpw', architecture='qwen', context=262144, vision=True, mtp=True, ngram=True, experts=512, draft_limit=4, bytes=80_000_000_000, issues=[], recommended=True, engines=['exl3'], reasoning=dict(options=['default','off'], toggle=True, levels=[], default_effort=None))
gguf = {**model, 'id':'ui-gguf','format':'GGUF','quant':'Q4_K_M','engines':['gguf'],'name':'Qwen-GGUF'}
server.supervisor.inventory = dict(models=[model,gguf], errors=[], root='/test/models')
server.supervisor.model = model
server.supervisor.engine = 'exl3'
server.supervisor.state = 'ready'
server.supervisor.settings = Settings(model_id='ui-exl3', context=262144, max_output=4096, vision=True)
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
