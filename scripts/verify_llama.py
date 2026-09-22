#!/usr/bin/env python3
"""Exercise Inflect's GGUF models, including real MTP on/off, through its public API.

This switches loaded models. Point it at an idle Inflect instance; it finishes unloaded.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:7860')
    parser.add_argument('--model', action='append', required=True, help='Exact model ID; may be repeated')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vision-fixture', type=Path)
    args = parser.parse_args()
    def request(path, body=None):
        req = urllib.request.Request(args.url + path, data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', 'X-Inflect-Local': '1'})
        try:
            return urllib.request.urlopen(req, timeout=1800)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'{path}: {exc.read().decode()}') from exc
    def api(path, body=None):
        with request(path, body) as response:
            return json.load(response)
    def chat(messages, **options):
        events = []
        with request('/api/chat', {'messages': messages, 'max_output': 512, 'temperature': 0, 'reasoning_effort': 'off', **options}) as response:
            for line in response:
                if line.startswith(b'data: '):
                    event = json.loads(line[6:])
                    if event['type'] == 'error':
                        raise RuntimeError(event['message'])
                    events.append(event)
        assert events and events[-1]['type'] == 'complete', events
        return {'text': ''.join(e.get('text', '') for e in events),
                'reasoning': ''.join(e.get('reasoning', '') for e in events if e['type'] == 'token'),
                'metrics': events[-1]}
    status = api('/api/status')['session']
    if status['busy'] or status.get('evaluation'):
        raise RuntimeError('Stop generation/benchmarks before qualification.')
    models = {m['id']: m for m in api('/api/library')['models']}
    report = {'created': datetime.now(timezone.utc).isoformat(), 'runs': [], 'passed': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    try:
        for identity in args.model:
            model = models[identity]
            assert model['format'] == 'GGUF'
            for mtp in ([False, True] if model['mtp'] else [False]):
                settings = dict(model_id=identity, context=32768, kv='Q8', vision=bool(model['vision'] and args.vision_fixture),
                    prediction='mtp' if mtp else 'off', draft_tokens=2, max_output=4096,
                    cpu_percent=10 if model['bytes'] > 26 * 2**30 else 0,
                    gguf_offload='experts' if model['experts'] else 'layers', cpu_threads=8, chunk_size=512, reasoning_effort='off')
                print(f"Loading {model['name']} / MTP {'on' if mtp else 'off'}", flush=True)
                api('/api/load', settings)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    state = api('/api/status')['session']
                    if state['state'] == 'ready':
                        break
                    if state['state'] == 'error':
                        raise RuntimeError(state['error'])
                    time.sleep(1)
                else:
                    raise RuntimeError('Model startup timed out')
                assert state['effective']['mtp_active'] == mtp
                run = {'model': model, 'settings': settings, 'effective': state['effective']}
                report['runs'].append(run)
                messages = [{'role': 'user', 'content': 'What is 17 multiplied by 23? Give the answer, then explain your calculation in one sentence.'}]
                count = api('/api/token-count', {'messages': messages, 'reasoning_effort': 'off'})
                run['text'] = chat(messages)
                assert '391' in run['text']['text'], run['text']
                usage = run['text']['metrics']['usage']
                assert count['input_tokens'] == usage['prompt_tokens']
                assert run['text']['metrics']['speed_source'] == 'engine'
                if mtp:
                    assert usage['llama_timings']['draft_n'] > 0
                effort = 'low' if 'low' in model['reasoning']['options'] else 'on'
                run['thinking'] = chat([{'role': 'user', 'content': 'What is 12 squared?'}], reasoning_effort=effort)
                assert '144' in run['thinking']['text'] and run['thinking']['reasoning']
                tool = {'type': 'function', 'function': {'name': 'get_temperature', 'description': 'Get the current temperature in a city.',
                    'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}}, 'required': ['city'], 'additionalProperties': False}}}
                tool_messages = [{'role': 'user', 'content': 'Use get_temperature to get the current temperature in Amsterdam.'}]
                response = api('/v1/chat/completions', {'messages': tool_messages, 'tools': [tool], 'max_tokens': 256, 'temperature': 0, 'reasoning_effort': 'off'})
                assistant = response['choices'][0]['message']
                call = assistant['tool_calls'][0]
                assert call['function']['name'] == 'get_temperature' and json.loads(call['function']['arguments'])['city'] == 'Amsterdam'
                run['tool_call'] = response
                run['tool_reply'] = chat(tool_messages + [assistant, {'role': 'tool', 'tool_call_id': call['id'], 'content': '{"temperature_c":14}'}])
                assert '14' in run['tool_reply']['text']
                # Exercise multiple prompt chunks and an actual occupied context.
                long_text = 'The library stores ordinary reference material about tables, paths, and measurements.\n' * 750
                long_messages = [{'role': 'user', 'content': 'Remember the access code ORBIT572.\n' + long_text + '\nReturn only the access code from the beginning.'}]
                run['long_prompt'] = chat(long_messages, max_output=128)
                assert 'ORBIT572' in run['long_prompt']['text']
                assert run['long_prompt']['metrics']['usage']['prompt_tokens'] > 8000
                if settings['vision']:
                    image = 'data:image/png;base64,' + base64.b64encode(args.vision_fixture.read_bytes()).decode()
                    run['vision'] = chat([{'role': 'user', 'content': [
                        {'type': 'text', 'text': 'Transcribe the heading and list the three shapes from left to right with their colors.'},
                        {'type': 'image_url', 'image_url': {'url': image}}]}])
                    assert all(x in run['vision']['text'].lower() for x in ('orbit', '572', 'red', 'blue', 'yellow'))
                run['passed'] = True
                print(json.dumps({'passed': True, 'mtp': mtp, 'decode_tps': run['text']['metrics']['tokens_per_second'],
                    'draft': usage.get('completion_tokens_details'), 'long_prompt_tokens': run['long_prompt']['metrics']['usage']['prompt_tokens']}), flush=True)
                save()
        report['passed'] = True
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        save()
        api('/api/unload', {})


if __name__ == '__main__':
    main()
