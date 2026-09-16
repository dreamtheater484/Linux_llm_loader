import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from loader import server
from loader.engines import Settings, launch
from loader.tool_calls import ToolRequest, ToolCallAccumulator, ToolResponseError, native_tool_format
from test_reasoning import ready_supervisor


TOOL = {'type': 'function', 'function': {
    'name': 'marker_lookup', 'description': 'Unique marker TOOL_SCHEMA_REACHED_7B21',
    'parameters': {'type': 'object', 'properties': {
        'count': {'type': 'integer'}, 'label': {'type': 'string'},
        'enabled': {'type': 'boolean'}, 'items': {'type': 'array', 'items': {'type': 'integer'}},
    }, 'required': ['count', 'label', 'enabled', 'items'], 'additionalProperties': False},
}}
ARGS = json.dumps({'count': 7, 'label': 'café', 'enabled': True, 'items': [1, 2]}, ensure_ascii=False)
MESSAGES = [{'role': 'user', 'content': 'Use the offered function.'}]


def tool_events(arguments=ARGS, name='marker_lookup', two=True, finish='tool_calls'):
    calls = [{'index': 0, 'id': 'call_original_1', 'type': 'function',
              'function': {'name': name[:7], 'arguments': arguments[:12]}}]
    rest = [{'index': 0, 'function': {'name': name[7:], 'arguments': arguments[12:]}}]
    if two:
        calls.append({'index': 1, 'id': 'call_original_2', 'type': 'function',
                      'function': {'name': name, 'arguments': arguments}})
    return [
        {'choices': [{'index': 0, 'delta': {'reasoning_content': 'Private reasoning.'}}]},
        {'choices': [{'index': 0, 'delta': {'tool_calls': calls}}]},
        {'choices': [{'index': 0, 'delta': {'tool_calls': rest}, 'finish_reason': finish}]},
        {'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140}},
    ]


def engine_client(calls, events):
    original = httpx.AsyncClient

    def handle(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith('/token/encode'):
            return httpx.Response(200, json={'length': 100})
        data = events(body) if callable(events) else events
        return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in data) + 'data: [DONE]\n\n')
    return lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle))


def ready():
    s = ready_supervisor()
    s.tool_format = 'qwen3_coder'
    return s


def post(client, **updates):
    return client.post('/v1/chat/completions', headers={'X-Lumen-Local': '1'}, json={
        'model': 'test', 'messages': MESSAGES, 'tools': [TOOL], 'reasoning_effort': 'off', **updates})


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith('data: ') and line != 'data: [DONE]']


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('effort', ['off', 'low'])
def test_schema_reaches_count_and_generation_and_structured_calls_survive(stream, effort):
    calls = []
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, tool_events())):
        response = post(TestClient(server.app), stream=stream, reasoning_effort=effort, max_completion_tokens=512)
    assert response.status_code == 200
    assert calls[0][1]['chat_template_kwargs']['tools'] == [TOOL]
    assert calls[1][1]['tools'] == [TOOL]
    assert calls[1][1]['tool_choice'] == 'auto'
    assert calls[1][1]['max_tokens'] == 512
    assert calls[0][1]['chat_template_kwargs']['enable_thinking'] == (effort != 'off')
    if stream:
        events = frames(response)
        accumulator = ToolCallAccumulator()
        reasons = []
        for event in events:
            for choice in event['choices']:
                delta = choice['delta']
                assert not (delta.get('reasoning_content') and delta.get('tool_calls'))
                if delta.get('tool_calls'):
                    accumulator.add(delta['tool_calls'])
                if delta.get('reasoning_content'):
                    reasons.append(delta['reasoning_content'])
        result = accumulator.finish({'tools': [TOOL], 'tool_choice': 'auto'}, events[-1]['choices'][0]['finish_reason'])
        assert ''.join(reasons) == 'Private reasoning.'
        assert response.text.endswith('data: [DONE]\n\n')
    else:
        choice = response.json()['choices'][0]
        assert choice['finish_reason'] == 'tool_calls'
        assert choice['message']['reasoning_content'] == 'Private reasoning.'
        assert choice['message']['content'] is None
        result = choice['message']['tool_calls']
        assert all('index' not in call for call in result)
    assert [call['id'] for call in result] == ['call_original_1', 'call_original_2']
    assert all(call['function'] == {'name': 'marker_lookup', 'arguments': ARGS} for call in result)


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('updates', [
    {'tool_choice': 'required'},
    {'tool_choice': {'type': 'function', 'function': {'name': 'marker_lookup'}}},
    {'parallel_tool_calls': False}, {'tool_choice': 'invalid'},
    {'reasoning_effort': 'high'}, {'reasoning_effort': 'max'}, {'reasoning_effort': 'minimal'},
    {'n': 2}, {'functions': []}, {'function_call': 'auto'},
])
def test_unsupported_modes_fail_before_upstream_or_sse(stream, updates):
    calls = []
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, tool_events())):
        response = post(TestClient(server.app), stream=stream, **updates)
    assert response.status_code == 422
    assert response.headers['content-type'].startswith('application/json')
    assert calls == []


@pytest.mark.parametrize('effort,expected', [('none', {'enable_thinking': False}), ('on', {'enable_thinking': True}), ('default', {})])
def test_reasoning_aliases(effort, expected):
    calls = []
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, tool_events())):
        assert post(TestClient(server.app), reasoning_effort=effort).status_code == 200
    assert calls[-1][1].get('chat_template_kwargs', {}) == expected


@pytest.mark.parametrize('updates', [{'tool_choice': 'none'}, {'tools': []}, {'tools': None}])
def test_none_and_ordinary_chat_never_enable_tool_parsing(updates):
    calls = []
    prose = 'Example: <tool_call><function=not_executable></function></tool_call>'
    events = [{'choices': [{'delta': {'content': prose}, 'finish_reason': 'stop'}]},
              {'usage': {'prompt_tokens': 25, 'completion_tokens': 10, 'total_tokens': 35}}]
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, events)):
        response = post(TestClient(server.app), **updates)
    assert response.status_code == 200
    assert response.json()['choices'][0]['message']['content'] == prose
    assert 'tool_calls' not in response.json()['choices'][0]['message']
    assert calls[-1][1]['tool_choice'] == 'none'
    assert 'tools' not in calls[-1][1]
    assert 'tools' not in calls[0][1]['chat_template_kwargs']


def test_round_trip_preserves_ids_arguments_and_out_of_order_tool_results():
    calls = []
    @asynccontextmanager
    async def no_lifespan(app):
        yield

    def events(body):
        if any(m['role'] == 'tool' for m in body['messages']):
            return [{'choices': [{'delta': {'content': 'Results received.'}, 'finish_reason': 'stop'}]},
                    {'usage': {'prompt_tokens': 200, 'completion_tokens': 10, 'total_tokens': 210}}]
        return tool_events()
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, events)), patch.object(server.app.router, 'lifespan_context', no_lifespan):
        with TestClient(server.app) as client:
            first = post(client).json()['choices'][0]['message']
            history = [*MESSAGES, first, {'role': 'tool', 'tool_call_id': 'call_original_2', 'content': '{"value":22}'},
                       {'role': 'tool', 'tool_call_id': 'call_original_1', 'content': '{"value":11}'}]
            response = post(client, messages=history)
    assert response.json()['choices'][0]['message']['content'] == 'Results received.'
    assert calls[-1][1]['messages'] == history
    assert calls[-2][1]['text'] == history


@pytest.mark.parametrize('history', [
    [*MESSAGES, {'role': 'tool', 'tool_call_id': 'unknown', 'content': 'No'}],
    [*MESSAGES, {'role': 'assistant', 'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {'name': 'f', 'arguments': '{}'}}]}],
])
def test_unmatched_tool_history_rejected(history):
    with patch.object(server, 'supervisor', ready()):
        assert post(TestClient(server.app), messages=history).status_code == 422


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('events', [
    tool_events(arguments='{"count":"wrong type"}'),
    tool_events(arguments='{"count":'),
    tool_events(name='not_offered'),
    tool_events(finish='length'),
    [{'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}, {'usage': {'completion_tokens': 5}}],
])
def test_malformed_calls_never_reach_client_as_executable_calls(stream, events):
    calls = []
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, events)):
        response = post(TestClient(server.app), stream=stream)
    if stream:
        data = frames(response)
        assert 'error' in data[-1]
        assert not any(c['delta'].get('tool_calls') for event in data for c in event.get('choices', []))
        assert '[DONE]' not in response.text
    else:
        assert response.status_code == 502


def test_unknown_parser_rejected_without_affecting_plain_chat():
    with patch.object(server, 'supervisor', ready_supervisor()):
        assert post(TestClient(server.app)).status_code == 422


def test_request_schemas_and_strict_mode():
    for tool in [dict(TOOL, type='custom'), {'type': 'function', 'function': {**TOOL['function'], 'strict': True}},
                 {'type': 'function', 'function': {**TOOL['function'], 'parameters': {'type': 'imaginary'}}}]:
        with patch.object(server, 'supervisor', ready()):
            assert post(TestClient(server.app), tools=[tool]).status_code == 422


@pytest.mark.parametrize('arguments', [
    ARGS.replace('"count": 7', '"count": "7"'),
    ARGS.replace('"count": 7', '"count": NaN'),
    ARGS.replace('"count": 7', '"count": 7, "count": 8'),
])
def test_invalid_typed_or_ambiguous_json_arguments_are_rejected(arguments):
    calls = []
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, tool_events(arguments=arguments))):
        assert post(TestClient(server.app)).status_code == 502


def test_remote_schema_references_never_fetch_external_resources():
    tool = {'type': 'function', 'function': {**TOOL['function'], 'parameters': {'$ref': 'https://example.invalid/private-schema'}}}
    calls = []
    with patch.object(server, 'supervisor', ready()), patch.object(server.httpx, 'AsyncClient', side_effect=engine_client(calls, tool_events())), patch('urllib.request.urlopen') as external:
        assert post(TestClient(server.app), tools=[tool]).status_code == 502
        external.assert_not_called()


def test_duplicate_ids_and_missing_indexes_are_rejected():
    for broken in [
        [{'index': 0, 'id': 'same', 'function': {'name': 'marker_lookup', 'arguments': ARGS}},
         {'index': 1, 'id': 'same', 'function': {'name': 'marker_lookup', 'arguments': ARGS}}],
        [{'id': 'call_1', 'function': {'name': 'marker_lookup', 'arguments': ARGS}}],
    ]:
        accumulator = ToolCallAccumulator()
        with pytest.raises(ToolResponseError):
            accumulator.add(broken)
            accumulator.finish({'tools': [TOOL], 'tool_choice': 'auto'}, 'tool_calls')


def test_parser_selection_uses_architecture_and_actual_selected_template(tmp_path):
    model = {'path': str(tmp_path), 'architecture': 'Qwen4ExpForConditionalGeneration', 'experts': 0, 'ngram': True}
    (tmp_path / 'chat_template.jinja').write_text('<tool_call><function=example><parameter=value>')
    assert native_tool_format(model) == 'qwen3_coder'
    with patch('loader.engines.validate', return_value='exl3'):
        _, _, config = launch(Settings(model_id='test'), model, tmp_path, 5050, 'test-key')
    assert config['model']['tool_format'] == 'qwen3_coder'
    assert config['model']['tool_calls_in_reasoning'] is False
    assert config['network']['host'] == '127.0.0.1'
    assert config['network']['disable_auth'] is False
    (tmp_path / 'tabby_template.jinja').write_text('An incompatible custom template.')
    assert native_tool_format(model) is None
    assert native_tool_format({**model, 'architecture': 'UnverifiedModel'}) is None


def test_lock_and_local_header_remain_required():
    s = ready()
    async def hold():
        await s.generation_lock.acquire()
    asyncio.run(hold())
    with patch.object(server, 'supervisor', s):
        client = TestClient(server.app)
        assert post(client).status_code == 409
        assert client.post('/v1/chat/completions', json={'messages': MESSAGES}).status_code == 403
