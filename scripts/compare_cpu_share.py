"""Compare two CPU expert shares with matched, uncached short prompts."""
from datetime import datetime, timezone
import hashlib
import json
import secrets
import statistics
import time
import urllib.request

from validate_model import api, ROOT, URL


def generate(prompt, tokens):
    request = urllib.request.Request(
        URL + '/api/chat',
        data=json.dumps({'messages': [{'role': 'user', 'content': prompt}],
                         'max_output': tokens, 'temperature': 0}).encode(),
        headers={'Content-Type': 'application/json', 'X-Lumen-Local': '1'},
    )
    output, metrics = '', None
    with urllib.request.urlopen(request, timeout=1800) as response:
        for line in response:
            if not line.startswith(b'data: '):
                continue
            event = json.loads(line[6:])
            if event['type'] == 'token':
                output += event['text']
            elif event['type'] == 'complete':
                metrics = event
            elif event['type'] in ('error', 'cancelled'):
                raise RuntimeError(event)
    if metrics is None:
        raise RuntimeError('Request ended without verified completion metrics')
    return {'metrics': metrics, 'output': output,
            'output_sha256': hashlib.sha256(output.encode()).hexdigest()}


def load(settings):
    if api('/api/status')['session']['busy']:
        raise RuntimeError('Another request is active; refusing to interrupt it')
    api('/api/load', settings)
    deadline = time.monotonic() + 1800
    announced = 0
    while time.monotonic() < deadline:
        session = api('/api/status')['session']
        if session['state'] == 'ready':
            if session['settings'] != settings:
                raise RuntimeError('Loaded settings do not match the requested comparison')
            effective = session['effective']
            if (effective['max_seq_len'] != 262144 or effective['cache_mode'] != 'Q8'
                    or effective.get('draft') or effective.get('use_vision')):
                raise RuntimeError('Effective engine settings do not match the comparison')
            return session
        if session['state'] == 'error':
            raise RuntimeError(session['error'])
        if time.monotonic() - announced > 30:
            print(f"Loading {settings['cpu_percent']}% CPU: {session['state']}", flush=True)
            announced = time.monotonic()
        time.sleep(3)
    raise RuntimeError('Model startup timed out')


def main():
    initial = api('/api/status')
    original = initial['session']['settings']
    if initial['session']['state'] != 'ready' or initial['session']['busy']:
        raise RuntimeError('The starting model must be ready and idle')
    required = dict(model_id='8561e35292f73ed3', cpu_percent=90, context=262144,
                    kv='Q8', prediction='off', vision=False)
    if any(original.get(key) != value for key, value in required.items()):
        raise RuntimeError('Starting settings changed; inspect them before comparing')
    nonce = secrets.token_hex(16)
    prompts = [
        'Write a practical guide to designing a Python file indexer. Discuss traversal, incremental updates, error handling, concurrency, and a concrete implementation. Be detailed.',
        'Explain how a modern city could design reliable public transport. Cover timetables, transfers, accessibility, financing, and how success would be measured. Be detailed.',
        'Write a Python implementation of an LRU cache, followed by a detailed explanation of its invariants, complexity, and meaningful test cases. Continue until the design is fully explained.',
    ]
    # Independent identifiers prevent block-level prefix reuse between prompts.
    prompts = [f'{secrets.token_hex(16)} is a unique test identifier; ignore it.\n' + p for p in prompts]
    warmup = nonce + ' is a test identifier; ignore it. Explain how to organize a small Python project. Be detailed.'
    path = ROOT / 'validation' / f'deepseek-cpu-share-{time.strftime("%Y%m%d-%H%M%S")}.json'
    report = dict(created=datetime.now(timezone.utc).isoformat(), initial=initial,
                  method='Fresh model load per setting, identical 128-token warm-up, three identical prompts, '
                         '512 output tokens per prompt, temperature 0, MTP off. Configured 256K capacity; short inputs.',
                  prompts=prompts, warmup_prompt=warmup, profiles=[], state='running')

    def save():
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')

    save()
    print('Report:', path, flush=True)
    try:
        for cpu in (90, 80):
            settings = {**original, 'cpu_percent': cpu}
            print(f'Starting {cpu}% CPU, MTP off', flush=True)
            session = load(settings)
            profile = dict(settings=settings, effective=session['effective'], runs=[])
            report['profiles'].append(profile)
            profile['warmup'] = generate(warmup, 128)
            print(f'{cpu}% CPU warm-up complete', flush=True)
            save()
            for index, prompt in enumerate(prompts):
                if api('/api/status')['session']['settings'] != settings:
                    raise RuntimeError('Settings changed during the comparison')
                run = generate(prompt, 512)
                cached = run['metrics']['usage']['prompt_tokens_details']['cached_tokens']
                if cached:
                    raise RuntimeError(f'Expected an uncached prompt, got {cached} cached tokens')
                profile['runs'].append(run)
                save()
                m = run['metrics']
                print(json.dumps(dict(cpu=cpu, prompt=index + 1, tps=m['tokens_per_second'],
                    output_tokens=m['usage']['completion_tokens'], cached=cached,
                    vram_gib=m['hardware_peaks']['vram_bytes'] / 2**30)), flush=True)
            profile['median_tps'] = statistics.median(r['metrics']['tokens_per_second'] for r in profile['runs'])
            save()
        baseline, candidate = report['profiles']
        report['improvement_percent'] = (candidate['median_tps'] / baseline['median_tps'] - 1) * 100
        report['paired_improvement_percent'] = [
            (b['metrics']['tokens_per_second'] / a['metrics']['tokens_per_second'] - 1) * 100
            for a, b in zip(baseline['runs'], candidate['runs'])]
        report['matching_output_hashes'] = [a['output_sha256'] == b['output_sha256']
            for a, b in zip(baseline['runs'], candidate['runs'])]
        winner = candidate if candidate['median_tps'] > baseline['median_tps'] else baseline
        if winner is baseline:
            load(original)
        report['left_loaded'] = winner['settings']
        report['state'] = 'complete'
        save()
        print(json.dumps({k: report[k] for k in ('state', 'improvement_percent',
                         'paired_improvement_percent', 'matching_output_hashes', 'left_loaded')}, indent=2), flush=True)
    except Exception as exc:
        report.update(state='failed', error=str(exc))
        save()
        raise


if __name__ == '__main__':
    main()
