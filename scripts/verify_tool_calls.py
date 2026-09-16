"""Exercise the LAN API using synthetic tools; never execute model-generated code."""
import argparse
import json
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Lumen base URL, without /v1')
    parser.add_argument('--output', type=Path, required=True, help='Private JSON results file')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    headers = {'Content-Type': 'application/json', 'X-Lumen-Local': '1'}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path):
        with opener.open(base + path, timeout=20) as response:
            return json.load(response)

    initial = get('/api/status')['session']
    if initial['state'] != 'ready' or initial['busy']:
        raise RuntimeError('The model must be ready and idle. No active request will be interrupted.')
    if not initial.get('tool_calling', {}).get('enabled'):
        raise RuntimeError('Native tool parsing is not enabled in the running server.')
    original_profiles = get('/api/profiles')
    identity = initial['model']['id']
    marker = 'SCHEMA_MARKER_' + secrets.token_hex(8)
    function = 'lumen_probe_' + secrets.token_hex(6)
    tool = {'type': 'function', 'function': {
        'name': function, 'description': 'Return a diagnostic receipt. Use this description marker as the label argument: ' + marker + '. Harmless mock; no real operation is performed.',
        'parameters': {'type': 'object', 'properties': {
            'label': {'type': 'string'}, 'count': {'type': 'integer'},
            'enabled': {'type': 'boolean'},
            'items': {'type': 'array', 'items': {'type': 'integer'}},
        }, 'required': ['label', 'count', 'enabled', 'items'], 'additionalProperties': False},
    }}
    report = {'created': time.time(), 'model': identity, 'settings': initial['settings'],
              'tool_calling': initial['tool_calling'], 'reasoning': initial['model']['reasoning'],
              'marker': marker, 'function': function, 'tests': [], 'state': 'running',
              'scope': 'API-level synthetic functions only. No OpenCode native tools or subagents executed.'}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        args.output.chmod(0o600)

    def chat(messages, stream=False, **changes):
        status = get('/api/status')['session']
        if status['busy'] or status['settings'] != initial['settings']:
            raise RuntimeError('Another request is running or the loaded settings changed; stopping verification.')
        body = {'model': identity, 'messages': messages, 'tools': [tool], 'tool_choice': 'auto',
                'max_tokens': 2048, 'temperature': 0, 'reasoning_effort': 'off', 'stream': stream, **changes}
        request = urllib.request.Request(base + '/v1/chat/completions', data=json.dumps(body).encode(), headers=headers)
        try:
            with opener.open(request, timeout=300) as response:
                if not stream:
                    value = json.load(response)
                    choice = value['choices'][0]
                    return {'status': response.status, 'message': choice['message'], 'finish_reason': choice['finish_reason'], 'usage': value.get('usage')}
                message = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
                calls, events, finish, usage, done = {}, [], None, None, False
                for line in response:
                    if not line.startswith(b'data: '):
                        continue
                    if line.strip() == b'data: [DONE]':
                        done = True
                        continue
                    event = json.loads(line[6:])
                    events.append(event)
                    if 'error' in event:
                        raise RuntimeError(event['error'])
                    usage = event.get('usage') or usage
                    for choice in event.get('choices', []):
                        assert choice['index'] == 0
                        delta = choice['delta']
                        assert not (delta.get('reasoning_content') and delta.get('tool_calls'))
                        for key in ('content', 'reasoning_content'):
                            message[key] += delta.get(key) or ''
                        for call in delta.get('tool_calls', []):
                            index = call['index']
                            assert type(index) is int
                            result = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                            if call.get('id'):
                                assert not result['id'] or result['id'] == call['id']
                                result['id'] = call['id']
                            assert call.get('type', 'function') == 'function'
                            for key in ('name', 'arguments'):
                                result['function'][key] += call.get('function', {}).get(key, '')
                        finish = choice.get('finish_reason') or finish
                assert done and finish
                if calls:
                    assert sorted(calls) == list(range(len(calls)))
                    message['tool_calls'] = [calls[k] for k in sorted(calls)]
                    message['content'] = message['content'] or None
                return {'status': 200, 'message': message, 'finish_reason': finish, 'usage': usage, 'events': events}
        except urllib.error.HTTPError as exc:
            return {'status': exc.code, 'error': json.load(exc)}

    def record(name, result):
        report['tests'].append({'name': name, **result})
        save()
        print(name + ': passed', flush=True)

    def check_calls(result, counts):
        assert result['status'] == 200 and result['finish_reason'] == 'tool_calls', result
        calls = result['message'].get('tool_calls', [])
        assert len(calls) == len(counts), result
        assert len({c['id'] for c in calls}) == len(calls)
        for call, count in zip(calls, counts):
            assert call['id'] and call['type'] == 'function' and call['function']['name'] == function
            actual = json.loads(call['function']['arguments'])
            assert actual == {'label': marker, 'count': count, 'enabled': True, 'items': [2, 4]}, actual
        assert '<tool_call>' not in (result['message'].get('content') or '')

    save()
    try:
        # Demonstrate schema visibility through actual use. Some checkpoints
        # decline introspective questions about their tool definitions.
        visible_messages = [{'role': 'user', 'content': f'If {function} is available, call it with label equal to the marker in its description, count=7, enabled=true, items=[2,4]. If it is unavailable, return exactly NO_FUNCTION.'}]
        absent = chat(visible_messages, tools=None, max_tokens=128)
        visible = chat(visible_messages, max_tokens=512)
        assert absent['status'] == visible['status'] == 200, {'absent': absent, 'visible': visible}
        assert 'NO_FUNCTION' in absent['message']['content']
        check_calls(visible, [7])
        assert visible['usage']['prompt_tokens'] > absent['usage']['prompt_tokens'] + 50
        record('tool schema marker and increased prompt-token count', {'without_tools': absent, 'with_tools': visible})

        for stream in (False, True):
            for mode in ('required', {'type': 'function', 'function': {'name': function}}):
                result = chat(visible_messages, stream=stream, tool_choice=mode)
                assert result['status'] == 422, result
                record(f'explicit unsupported choice {mode!r}, stream={stream}', result)
            none = chat(visible_messages, stream=stream, tool_choice='none', max_tokens=128)
            assert none['status'] == 200 and none['finish_reason'] == 'stop'
            assert not none['message'].get('tool_calls') and 'NO_FUNCTION' in none['message']['content']
            record(f'none, stream={stream}', none)

        prompt = f'Call {function} exactly once. Set label to its description marker, count to 7, enabled to true, and items to [2, 4]. Do not invent a tool result. After the tool responds, return only its receipt value.'
        for effort in ('off', 'low'):
            for stream in (False, True):
                messages = [{'role': 'user', 'content': prompt + '\nTest identifier: ' + secrets.token_hex(4)}]
                result = chat(messages, stream=stream, reasoning_effort=effort)
                check_calls(result, [7])
                if effort == 'off':
                    assert not result['message'].get('reasoning_content')
                else:
                    assert result['message'].get('reasoning_content'), 'Expected a separate reasoning channel'
                record(f'auto structured call, reasoning={effort}, stream={stream}', result)
                call = result['message']['tool_calls'][0]
                receipt = 'SYNTHETIC_RECEIPT_' + secrets.token_hex(8)
                followup = chat([*messages, result['message'], {'role': 'tool', 'tool_call_id': call['id'],
                    'content': json.dumps({'receipt': receipt})}], stream=stream, reasoning_effort=effort, tool_choice='none')
                assert followup['status'] == 200 and followup['finish_reason'] == 'stop', followup
                assert not followup['message'].get('tool_calls') and receipt in followup['message']['content'], followup
                record(f'round trip, reasoning={effort}, stream={stream}', followup)

        multiple_messages = [{'role': 'user', 'content': f'Make two independent parallel calls to {function} in this single assistant turn. First count=7, second count=8. In both, label must be the description marker, enabled=true, items=[2,4]. Both calls have all arguments available and neither depends on the other. Emit BOTH calls now, before waiting for either result. Do not combine them or invent results. After receiving both results, return both receipt values in call order.'}]
        multiple = chat(multiple_messages, stream=True, reasoning_effort='low')
        check_calls(multiple, [7, 8])
        record('two calls with indexes and distinct IDs', multiple)
        first, second = multiple['message']['tool_calls']
        receipts = ['FIRST_RECEIPT_' + secrets.token_hex(4), 'SECOND_RECEIPT_' + secrets.token_hex(4)]
        followup = chat([*multiple_messages, multiple['message'],
            {'role': 'tool', 'tool_call_id': second['id'], 'content': json.dumps({'receipt': receipts[1]})},
            {'role': 'tool', 'tool_call_id': first['id'], 'content': json.dumps({'receipt': receipts[0]})}], tool_choice='none')
        assert followup['status'] == 200 and followup['finish_reason'] == 'stop'
        text = followup['message']['content']
        assert all(value in text for value in receipts) and text.index(receipts[0]) < text.index(receipts[1])
        record('parallel tool results matched by ID in reversed arrival order', followup)

        for effort in ('high', 'max', 'minimal'):
            result = chat(visible_messages, reasoning_effort=effort, stream=True)
            assert result['status'] == 422
            record('unsupported Qwen reasoning effort: ' + effort, result)
        final = get('/api/status')['session']
        assert final['settings'] == initial['settings'] and not final['busy']
        assert get('/api/profiles') == original_profiles
        report.update(state='passed', profiles_unchanged=True, settings_unchanged=True, ended=time.time())
        save()
        print(f"Verified {len(report['tests'])} API checks; no real tools executed.", flush=True)
    except Exception as exc:
        report.update(state='failed', error=str(exc), ended=time.time())
        save()
        raise


if __name__ == '__main__':
    main()
