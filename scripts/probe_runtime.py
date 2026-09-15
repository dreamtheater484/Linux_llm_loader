"""Real CUDA smoke test, run with the isolated engine's Python."""
import importlib.metadata
import json
from pathlib import Path
import time
import torch
import exllamav3

assert torch.cuda.is_available(), 'CUDA is unavailable'
start = time.monotonic()
a = torch.randn((1024, 1024), device='cuda', dtype=torch.float16)
b = a @ a.T
torch.cuda.synchronize()
assert torch.isfinite(b).all().item(), 'CUDA matrix multiplication returned invalid values'
report = dict(torch=torch.__version__, cuda=torch.version.cuda,
              exllamav3=importlib.metadata.version('exllamav3'), gpu=torch.cuda.get_device_name(),
              capability=torch.cuda.get_device_capability(), vram_bytes=torch.cuda.get_device_properties(0).total_memory,
              smoke_seconds=time.monotonic()-start, checked_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
root = Path(__file__).resolve().parents[1]
(root / '.runtime/cuda-smoke.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
