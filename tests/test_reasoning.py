import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from loader import server
from loader.engines import Settings, launch
from loader.reasoning import inspect_reasoning, native_reasoning, reasoning_kwargs


QWEN_TEMPLATE = """
{% if enable_thinking is undefined or enable_thinking is true %}
{% set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}
{% if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}
{{ raise_exception('Unsupported effort') }}{% endif %}{% endif %}
"""
DEEPSEEK_TEMPLATE = """
{% if enable_thinking is defined %}{% set mode = 'thinking' if enable_thinking else 'chat' %}{% endif %}
{% set effort = reasoning_effort | default('low') %}
{% if effort not in ['low', 'high', 'max'] %}{{ raise_exception('Unsupported effort') }}{% endif %}
"""


@pytest.mark.parametrize('template,levels,default', [
    (QWEN_TEMPLATE, ['low', 'medium', 'xhigh'], 'xhigh'),
    (DEEPSEEK_TEMPLATE, ['low', 'high', 'max'], 'low'),
])
def test_native_levels_and_defaults_come_from_each_template(template, levels, default):
    capabilities = inspect_reasoning(template)
    assert capabilities['levels'] == levels
    assert capabilities['default_effort'] == default
    assert capabilities['options'] == ['default', 'off', *levels]


def test_unknown_and_toggle_only_templates_do_not_advertise_invented_levels():
    assert inspect_reasoning('{# reasoning_effort low high enable_thinking #}')['options'] == ['default']
    toggle = inspect_reasoning('{% if enable_thinking %}{{ "<think>" }}{% endif %}')
    assert toggle['options'] == ['default', 'off', 'on']
    assert reasoning_kwargs({'reasoning': toggle}, 'on') == {'enable_thinking': True}
    with pytest.raises(ValueError, match='does not support'):
        reasoning_kwargs({'reasoning': inspect_reasoning(QWEN_TEMPLATE)}, 'high')


def test_template_selection_matches_tabby_precedence(tmp_path):
    (tmp_path / 'tokenizer_config.json').write_text(json.dumps({'chat_template': QWEN_TEMPLATE}))
    assert native_reasoning(tmp_path, True)['default_effort'] == 'xhigh'
    (tmp_path / 'tabby_template.jinja').write_text(DEEPSEEK_TEMPLATE)
    assert native_reasoning(tmp_path, True)['default_effort'] == 'low'
    assert native_reasoning(tmp_path, False)['default_effort'] == 'xhigh'


def test_profiles_migrate_without_changing_names_or_ids(tmp_path):
    saved = [dict(id=str(i), name=f'Existing {i}', settings={'model_id': 'test', 'thinking': on}) for i, on in enumerate([False, True])]
    (tmp_path / 'profiles.json').write_text(json.dumps(saved))
    with patch.object(server, 'STATE', tmp_path):
        result = server.read_profiles()
        assert result == server.read_profiles()
    assert [p['id'] for p in result] == ['0', '1']
    assert [p['name'] for p in result] == ['Existing 0', 'Existing 1']
    assert [p['settings']['reasoning_effort'] for p in result] == ['off', 'default']
    assert all('thinking' not in p['settings'] for p in result)
    assert Settings(model_id='test', thinking=False, reasoning_effort='low').reasoning_effort == 'low'


def ready_supervisor(engine='exl3'):
    supervisor = server.Supervisor()
    supervisor.state = 'ready'
    supervisor.model = {'id': 'test', 'path': '/models/example', 'name': 'example', 'reasoning': inspect_reasoning(QWEN_TEMPLATE)}
    supervisor.settings = Settings(model_id='test', reasoning_effort='off')
    supervisor.engine = engine
    supervisor.started = 12345
    supervisor.port = 9999
    supervisor.token = 'test-key'
    return supervisor


def engine_client(calls):
    original = httpx.AsyncClient

    def handle(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith('/token/encode'):
            return httpx.Response(200, json={'length': 25})
        events = [
            {'choices': [{'delta': {'reasoning_content': 'Reasoning'}}]},
            {'choices': [{'delta': {'content': 'Answer'}, 'finish_reason': 'stop'}]},
            {'usage': {'prompt_tokens': 25, 'completion_tokens': 1100, 'total_tokens': 1125, 'completion_tokens_per_sec': 20}},
        ]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n')

    return lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle))


@pytest.mark.parametrize('engine', ['exl3', 'vllm', 'gguf'])
def test_switch_reasoning_per_request_without_reload_or_budget(engine):
    supervisor = ready_supervisor(engine)
    initial_settings = supervisor.settings
    calls = []

    async def exercise():
        for effort in ['low', 'default', 'off', 'xhigh', None]:
            events = [event async for event in supervisor.stream([{'role': 'user', 'content': 'Test'}], reasoning_effort=effort)]
            chosen = effort if effort is not None else 'off'
            assert events[-1]['reasoning_effort'] == chosen
            assert events[-1]['usage']['completion_tokens'] == 1100
            assert events[-1]['tokens_per_second'] == 20
            assert events[-1]['speed_source'] == 'engine'
            payload = calls[-1][1]
            assert payload.get('chat_template_kwargs', {}) == reasoning_kwargs(supervisor.model, chosen)
            assert 'reasoning_budget_tokens' not in payload
            assert payload['max_tokens'] == 4096
            if engine == 'exl3':
                assert calls[-2][1]['chat_template_kwargs'] == payload.get('chat_template_kwargs', {})
            assert supervisor.settings is initial_settings
            assert supervisor.settings.reasoning_effort == 'off'
            assert supervisor.state == 'ready' and supervisor.started == 12345

    with patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls)):
        asyncio.run(exercise())


@pytest.mark.parametrize('stream', [False, True])
def test_openai_route_forwards_effort(stream):
    supervisor = ready_supervisor()
    calls = []
    with patch.object(server, 'supervisor', supervisor), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls)):
        client = TestClient(server.app)
        response = client.post('/v1/chat/completions', headers={'X-Lumen-Local': '1'},
                               json={'messages': [{'role': 'user', 'content': 'Test'}], 'reasoning_effort': 'medium', 'stream': stream})
    assert response.status_code == 200
    assert calls[-1][1]['chat_template_kwargs'] == {'enable_thinking': True, 'reasoning_effort': 'medium'}


def test_chat_and_token_count_use_the_same_requested_effort():
    supervisor = ready_supervisor()
    calls = []
    with patch.object(server, 'supervisor', supervisor), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls)):
        client = TestClient(server.app)
        for route in ['/api/token-count', '/api/chat']:
            response = client.post(route, headers={'X-Lumen-Local': '1'}, json={
                'messages': [{'role': 'user', 'content': 'Test'}], 'reasoning_effort': 'xhigh'})
            assert response.status_code == 200
    assert all(body.get('chat_template_kwargs') == {'enable_thinking': True, 'reasoning_effort': 'xhigh'} for _, body in calls)


def test_launch_does_not_freeze_reasoning_choice(tmp_path):
    model = {'path': '/models/example', 'experts': 0, 'ngram': False}
    configs = []
    for effort in ['off', 'default', 'xhigh']:
        with patch('loader.engines.validate', return_value='exl3'):
            _, _, config = launch(Settings(model_id='test', reasoning_effort=effort), model, tmp_path, 5050, 'test-key')
        configs.append(config)
        assert 'template_vars_default' not in config['model']
        assert 'reasoning_budget_tokens' not in config['model']
    assert configs[0] == configs[1] == configs[2]
