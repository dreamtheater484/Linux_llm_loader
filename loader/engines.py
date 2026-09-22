"""Small common settings translated to each engine's native launch interface."""
import math
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .reasoning import ReasoningEffort, reasoning_kwargs
from .tool_calls import native_tool_format
from .llama_cpp import runtime_info

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get('INFLECT_RUNTIME', Path.home() / '.local/share/linux-llm-loader'))
EXL_PYTHON = RUNTIME / 'exl3/bin/python'
VLLM = RUNTIME / 'vllm/bin/vllm'
GGUF = Path(os.environ.get('INFLECT_GGUF_SERVER', RUNTIME / 'llama/bin/llama-server'))
_configured_tabby = os.environ.get('INFLECT_TABBY')
_legacy_tabby = PROJECT / '.runtime/sources/tabbyAPI'
TABBY = Path(_configured_tabby).expanduser() if _configured_tabby else RUNTIME / 'sources/tabbyAPI'
# Existing installations kept Tabby inside the project. Use it until setup migrates
# them, while fresh installations keep all third-party code in the private runtime.
if not TABBY.is_dir() and _legacy_tabby.is_dir():
    TABBY = _legacy_tabby


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: str
    engine: Literal['auto', 'exl3', 'vllm', 'gguf'] = 'auto'
    context: int = Field(262144, ge=2048, le=1048576, multiple_of=256)
    kv: Literal['Q8', 'FP16', 'Q4'] = 'Q8'
    vision: bool = True
    ngram_ram: bool = True
    prediction: Literal['off', 'mtp'] = 'off'
    draft_tokens: int = Field(2, ge=1, le=16)
    max_output: int = Field(4096, ge=16, le=32768)
    cpu_percent: int = Field(80, ge=0, le=100)
    gguf_offload: Literal['layers', 'experts'] = 'layers'
    cpu_threads: int = Field(8, ge=1, le=64)
    chunk_size: Literal[256, 512, 1024, 2048, 4096] = 1024
    temperature: float = Field(0.7, ge=0, le=2)
    reasoning_effort: ReasoningEffort = 'default'

    @model_validator(mode='before')
    @classmethod
    def migrate_thinking(cls, value):
        if isinstance(value, dict) and 'thinking' in value:
            value = dict(value)
            thinking = value.pop('thinking')
            value.setdefault('reasoning_effort', 'default' if thinking else 'off')
        return value

    @model_validator(mode='after')
    def answer_fits(self):
        if self.max_output >= self.context:
            raise ValueError('Maximum answer must be smaller than the context window.')
        return self


def engine_inventory():
    exl_ready = EXL_PYTHON.is_file() and TABBY.is_dir() and any((RUNTIME / 'exl3/lib').glob('python*/site-packages/exllamav3-*.dist-info'))
    return [dict(id='exl3', name='ExLlamaV3', installed=exl_ready, version='1.5.0 / TabbyAPI 53da7919', formats=['EXL3']),
            dict(id='vllm', name='vLLM', installed=VLLM.is_file(), version='Not installed' if not VLLM.is_file() else 'Local runtime', formats=['Safetensors']),
            dict(id='gguf', name='llama.cpp', formats=['GGUF'], **runtime_info(GGUF))]


def validate(settings, model, check_install=True):
    engine = model['engines'][0] if settings.engine == 'auto' else settings.engine
    if engine not in model['engines']:
        raise ValueError(f"{model['format']} requires {' or '.join(model['engines'])}.")
    reasoning_kwargs(model, settings.reasoning_effort)
    if model['issues']:
        raise ValueError('; '.join(model['issues'][:4]))
    if settings.context > model['context']:
        raise ValueError(f"This checkpoint declares a maximum of {model['context']:,} tokens.")
    if settings.vision and not model['vision']:
        raise ValueError('This checkpoint has no identified vision component.')
    if settings.prediction == 'mtp' and not model['mtp']:
        raise ValueError('This checkpoint has no identified MTP weights.')
    if settings.prediction == 'mtp' and settings.draft_tokens > model.get('draft_limit', 4):
        raise ValueError('Draft length exceeds the checkpoint’s supported prediction block.')
    if engine not in ('exl3', 'gguf') and settings.prediction != 'off':
        raise ValueError('MTP has not been qualified for this engine/checkpoint combination.')
    if engine == 'gguf':
        if settings.gguf_offload == 'experts' and not model.get('experts'):
            raise ValueError('Expert offload requires a mixture-of-experts model. Choose layer offload.')
        if settings.prediction == 'mtp' and check_install and not runtime_info(GGUF).get('mtp'):
            raise ValueError('This llama.cpp runtime lacks embedded MTP support. Run scripts/install-llama.py.')
    if engine == 'vllm' and settings.kv == 'Q4':
        raise ValueError('4-bit KV cache has not been qualified for this vLLM architecture.')
    if check_install and not next(e for e in engine_inventory() if e['id'] == engine)['installed']:
        raise ValueError(f'{engine} is not installed. Use an installed engine or run its setup first.')
    return engine


def launch(settings, model, run_dir, port, token):
    engine = validate(settings, model)
    env = os.environ.copy()
    private_cc = RUNTIME / 'toolchain/usr/bin/x86_64-linux-gnu-gcc-15'
    if private_cc.is_file():
        env['CC'] = str(private_cc)
    env.update(PYTHONUNBUFFERED='1', TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS=str(settings.cpu_threads),
               EXL3_MOE_PINNED_ARENA='1', EXL3_HOST_MEM_RESERVE_MB='16384')
    if engine == 'exl3':
        import yaml
        cpu_experts = min(model['experts'], math.ceil(model['experts'] * settings.cpu_percent / 100 / 8) * 8)
        config = dict(
            network=dict(host='127.0.0.1', port=port, disable_auth=False, allowed_origins=[], disable_fetch_requests=True, api_servers=['oai']),
            logging=dict(log_prompt=False, log_live_status=False),
            model=dict(model_dir=str(Path(model['path']).parent), model_name=Path(model['path']).name,
                       backend='exllamav3', max_seq_len=settings.context, cache_size=settings.context,
                       cache_mode=settings.kv, max_batch_size=1, chunk_size=settings.chunk_size,
                       vision=settings.vision, vision_offload=False,
                       ngram_ram=bool(model.get('ngram') and settings.ngram_ram),
                       cpu_moe_split_experts=cpu_experts, cpu_moe_threads=settings.cpu_threads,
                       reasoning=True),
            draft_model=dict(draft_mode='mtp' if settings.prediction == 'mtp' else 'disabled',
                             draft_num_tokens=settings.draft_tokens, draft_cache_mode=settings.kv),
            memory=dict(sysmem_recurrent_cache=1024, sysmem_kv_cache=0, sysmem_multimodal_cache=256),
            sampling=dict(override_preset=None))
        tool_format = native_tool_format(model)
        if tool_format:
            config['model'].update(tool_format=tool_format, tool_calls_in_reasoning=False)
        (run_dir / 'config.yml').write_text(yaml.safe_dump(config), encoding='utf-8')
        key_file = run_dir / 'api_tokens.yml'
        key_file.write_text(yaml.safe_dump({'api_key': token, 'admin_key': token}), encoding='utf-8')
        key_file.chmod(0o600)
        return [str(EXL_PYTHON), str(TABBY / 'main.py'), '--config', str(run_dir / 'config.yml')], env, config
    if engine == 'vllm':
        args = [str(VLLM), 'serve', model['path'], '--host', '127.0.0.1', '--port', str(port),
                '--served-model-name', model['id'], '--api-key', token, '--max-model-len', str(settings.context),
                '--max-num-seqs', '1', '--gpu-memory-utilization', '0.9', '--max-num-batched-tokens', str(settings.chunk_size),
                '--kv-cache-dtype', 'fp8' if settings.kv == 'Q8' else 'auto']
        if not settings.vision:
            args += ['--language-model-only']
        if settings.cpu_percent:
            args += ['--cpu-offload-gb', str(round(model['bytes'] / 2**30 * settings.cpu_percent / 100, 1))]
        return args, env, {'kv_format': 'FP8' if settings.kv == 'Q8' else 'Auto (model dtype)', 'qualification': 'Unqualified profile'}
    args = [str(GGUF), '--model', model['path'], '--jinja', '--host', '127.0.0.1', '--port', str(port),
            '--alias', model['id'], '--api-key', token, '--ctx-size', str(settings.context), '--parallel', '1',
            '--threads', str(settings.cpu_threads), '--batch-size', str(max(2048, settings.chunk_size)),
            '--ubatch-size', str(settings.chunk_size), '--flash-attn', 'on', '--fit', 'off',
            '--no-context-shift', '--slots', '--metrics', '--no-webui', '--reasoning-format', 'deepseek',
            '--cache-type-k', {'Q8':'q8_0', 'Q4':'q4_0', 'FP16':'f16'}[settings.kv],
            '--cache-type-v', {'Q8':'q8_0', 'Q4':'q4_0', 'FP16':'f16'}[settings.kv]]
    layers = model['layers'] - model.get('nextn_layers', 0)
    cpu_layers = min(layers, math.ceil(layers * settings.cpu_percent / 100))
    if settings.gguf_offload == 'experts':
        args += ['--n-gpu-layers', 'all', '--n-cpu-moe', str(cpu_layers)]
    else:
        args += ['--n-gpu-layers', 'all' if not cpu_layers else str(layers - cpu_layers)]
    if settings.prediction == 'mtp':
        args += ['--spec-type', 'draft-mtp', '--spec-draft-n-max', str(settings.draft_tokens),
                 '--spec-draft-n-min', '0', '--spec-draft-p-min', '0', '--spec-draft-ngl', 'all']
    else:
        args += ['--spec-type', 'none']
    if model.get('ngram'):
        # Qwen PLE n-gram embeddings are large lazy tensors. Explicitly choose
        # whether llama.cpp keeps them resident or reads their rows via mmap.
        args += ['--lazy-mode', 'off' if settings.ngram_ram else 'on']
    # Avoid inheriting engine defaults that conflict with the Qwen model cards.
    if model['architecture'] in ('qwen35', 'qwen35moe'):
        args += ['--top-k', '20', '--top-p', '0.95', '--min-p', '0', '--repeat-penalty', '1']
    if settings.vision:
        args += ['--mmproj', model['projector']]
    return args, env, {'engine': 'llama.cpp', 'kv_format': settings.kv, 'cpu_offload': settings.gguf_offload,
                       'cpu_layers': cpu_layers, 'prediction': settings.prediction,
                       'draft_tokens': settings.draft_tokens if settings.prediction == 'mtp' else 0,
                       'ngram_residency': ('ram' if settings.ngram_ram else 'storage') if model.get('ngram') else None,
                       'tool_format': model.get('tool_format'), 'runtime': runtime_info(GGUF)}
