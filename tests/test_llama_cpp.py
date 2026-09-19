import asyncio
import json
from pathlib import Path
import struct
from unittest.mock import patch

import httpx
import pytest

from loader.engines import Settings, launch, validate
from loader.library import scan, gguf_quant, gguf_projector
from loader.llama_cpp import count_prompt, normalize_usage, effective_settings
from loader import server
from test_reasoning import ready_supervisor


def write_gguf(path, **metadata):
    def string(value):
        raw = value.encode()
        return struct.pack('<Q', len(raw)) + raw
    data = b'GGUF' + struct.pack('<IQQ', 3, 0, len(metadata))
    for key, value in metadata.items():
        data += string(key)
        data += struct.pack('<I', 8) + string(value) if isinstance(value, str) else struct.pack('<II', 4, value)
    path.write_bytes(data)


def gguf_model(tmp_path, mtp=True):
    path = tmp_path / 'Qwen3.8-27B-Uncensored-Q4_K_P-MTP.gguf'
    write_gguf(path, **{'general.architecture': 'qwen35', 'general.type': 'model',
        'qwen35.block_count': 65, 'qwen35.nextn_predict_layers': int(mtp),
        'qwen35.context_length': 262144, 'tokenizer.chat_template': '{% if enable_thinking %}tools <tool_call>{% endif %}'})
    return scan(tmp_path)['models'][0]


def test_discovery_keeps_embedded_mtp_checkpoints_and_correct_quant(tmp_path):
    model = gguf_model(tmp_path)
    assert model['mtp'] and model['mtp_kind'] == 'embedded'
    assert model['tool_format'] == 'llama-jinja'
    assert model['quant'] == 'Q4_K_P'
    assert gguf_quant('Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q6_K_P') == 'Q6_K_P'
    assert gguf_quant('Qwen3.8-27B-Q6_K') == 'Q6_K'
    assert not gguf_model(tmp_path, mtp=False)['mtp']


def test_projector_requires_unambiguous_matching_checkpoint(tmp_path):
    model = tmp_path / 'Qwen3.6-35B-A3B-Uncensored-Q6_K_P.gguf'
    other = tmp_path / 'mmproj-Qwen3.8-27B-F16.gguf'
    other.touch()
    assert gguf_projector(model, tmp_path) is None
    expected = tmp_path / 'Qwen3.6-35B-A3B-mmproj-BF16.gguf'
    expected.touch()
    assert gguf_projector(model, tmp_path) == str(expected)
    (tmp_path / 'mmproj-Qwen3.6-35B-A3B-F16.gguf').touch()
    assert gguf_projector(model, tmp_path) is None


def test_launch_and_validation_preserve_requested_mtp_and_placement(tmp_path):
    model = gguf_model(tmp_path)
    settings = Settings(model_id=model['id'], vision=False, context=32768, prediction='mtp', cpu_percent=0, chunk_size=4096)
    assert validate(settings, model, check_install=False) == 'gguf'
    with patch('loader.engines.validate', return_value='gguf'), patch('loader.engines.runtime_info', return_value={'mtp': True}):
        args, _, effective = launch(settings, model, tmp_path, 9000, 'secret')
    for name, value in [('--spec-type', 'draft-mtp'), ('--spec-draft-n-max', '2'), ('--n-gpu-layers', 'all'),
                        ('--batch-size', '4096'), ('--parallel', '1'), ('--ctx-size', '32768'), ('--fit', 'off')]:
        assert args[args.index(name) + 1] == value
    assert '--no-context-shift' in args
    assert effective['draft_tokens'] == 2
    model['mtp'] = False
    with pytest.raises(ValueError, match='MTP weights'):
        validate(settings, model, check_install=False)
    with pytest.raises(ValueError, match='Expert offload'):
        validate(settings.model_copy(update={'prediction': 'off', 'gguf_offload': 'experts'}), model, check_install=False)
    model.update(experts=256, layers=40, nextn_layers=0)
    with patch('loader.engines.validate', return_value='gguf'), patch('loader.engines.runtime_info', return_value={}):
        args, _, _ = launch(settings.model_copy(update={'prediction': 'off', 'gguf_offload': 'experts', 'cpu_percent': 10}), model, tmp_path, 9000, 'secret')
    assert args[args.index('--n-cpu-moe') + 1] == '4'
    assert args[args.index('--spec-type') + 1] == 'none'


def test_native_timings_preserve_all_generated_tokens_and_draft_counts():
    timings = {'prompt_ms': 500, 'predicted_ms': 2500, 'prompt_per_second': 200,
        'predicted_per_second': 80, 'draft_n': 150, 'draft_n_accepted': 120}
    usage = normalize_usage({'completion_tokens': 201, 'prompt_tokens': 100, 'total_tokens': 301}, timings)
    assert usage['completion_tokens'] == 201
    assert usage['completion_time'] == 2.5 and usage['prompt_time'] == .5
    assert usage['completion_tokens_per_sec'] == 80
    assert usage['completion_tokens_details'] == {'accepted_prediction_tokens': 120, 'rejected_prediction_tokens': 30}
    invalid = normalize_usage({'completion_tokens': 201}, {'predicted_per_second': float('nan')})
    assert 'completion_tokens_per_sec' not in invalid


def test_prompt_count_includes_template_tool_schema_and_reasoning():
    seen = []
    def handle(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'prompt': '<bos>Formatted tools and conversation'} if request.url.path == '/apply-template' else {'tokens': [1, 2, 3]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            count = await count_prompt(client, 'http://engine', {}, [{'role': 'user', 'content': 'Hi'}],
                {'enable_thinking': False}, {'tools': [{'type': 'function'}], 'tool_choice': 'auto'})
            assert count == 3
            assert await count_prompt(client, 'http://engine', {}, [{'role': 'user', 'content': [{'type': 'image_url'}]}], {}, {}) is None
    asyncio.run(run())
    assert len(seen) == 2
    assert seen[0][1]['chat_template_kwargs'] == {'enable_thinking': False}
    assert seen[0][1]['tools'] == [{'type': 'function'}]
    assert seen[1][1]['add_special'] is True and seen[1][1]['parse_special'] is True


def test_ready_state_verifies_mtp_and_context_allocation():
    settings = Settings(model_id='test', vision=False, context=32768, prediction='mtp')
    def run(context, speculative):
        async def exercise():
            def handle(request):
                return httpx.Response(200, json=({'default_generation_settings': {'n_ctx': context}} if request.url.path == '/props' else [{'speculative': speculative}]))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                return await effective_settings(client, 'http://engine', {}, settings, {})
        return asyncio.run(exercise())
    assert run(32768, True)['mtp_active'] is True
    with pytest.raises(ValueError, match='activate speculative'):
        run(32768, False)
    with pytest.raises(ValueError, match='allocated'):
        run(16384, True)


def test_overflow_is_rejected_before_generation():
    supervisor = ready_supervisor('gguf')
    supervisor.settings = Settings(model_id='test', context=8192, max_output=4096)
    seen = []
    original = httpx.AsyncClient
    def handle(request):
        seen.append(request.url.path)
        if request.url.path == '/apply-template':
            return httpx.Response(200, json={'prompt': 'large prompt'})
        assert request.url.path == '/tokenize'
        return httpx.Response(200, json={'tokens': [1] * 5000})
    async def run():
        return [e async for e in supervisor.stream([{'role': 'user', 'content': 'Hello'}])]
    with patch.object(server.httpx, 'AsyncClient', side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(handle))):
        with pytest.raises(ValueError, match='history is not truncated'):
            asyncio.run(run())
    assert seen == ['/apply-template', '/tokenize']
