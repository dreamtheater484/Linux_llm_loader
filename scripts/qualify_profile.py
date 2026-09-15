"""Load one explicit profile, warm it up, and save a reproducible benchmark."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from validate_model import api, ROOT

p=argparse.ArgumentParser()
p.add_argument('model_id')
p.add_argument('--cpu',type=int,required=True)
p.add_argument('--mtp',type=int,default=0)
p.add_argument('--vision',action='store_true')
p.add_argument('--long',type=int,default=0)
args=p.parse_args()
if api('/api/status')['session']['busy']:
    raise RuntimeError('A request is running. Finish it before starting qualification.')
profile=dict(model_id=args.model_id,cpu_percent=args.cpu,prediction='mtp' if args.mtp else 'off',
             draft_tokens=args.mtp or 2,vision=args.vision,context=262144,kv='Q8',cpu_threads=8)
api('/api/load',profile)
deadline=time.monotonic()+1800
while True:
    status=api('/api/status')['session']
    if status['state']=='ready':break
    if status['state']=='error':raise RuntimeError(status['error'])
    if time.monotonic()>deadline:raise RuntimeError('Model startup timed out')
    time.sleep(3)
print('Ready:',status['model']['title'],status['settings'],flush=True)
def check(kind,*extra):
    subprocess.run([sys.executable,str(ROOT/'scripts/validate_model.py'),kind,*map(str,extra)],check=True)
check('text')
if args.vision:check('vision')
api('/api/benchmark',{})
while api('/api/status')['session']['benchmark']['state']=='running':time.sleep(3)
results=api('/api/benchmarks')
(ROOT/'validation/benchmarks.json').write_text(json.dumps(results,indent=2))
result=results[0]
print(json.dumps({k:result.get(k) for k in ['state','median_tps','error']},indent=2),flush=True)
if result['state']!='complete':raise RuntimeError('Benchmark did not complete')
if args.long:check('long','--tokens',args.long,*(['--image'] if args.vision else []))
