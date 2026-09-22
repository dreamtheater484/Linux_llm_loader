#!/usr/bin/env python3
"""Prepare and verify real Docker graders without loading or querying a model.

Run with the Inflect manager Python. --prepare downloads/builds assets; otherwise
verification is offline and requires environments already prepared in the UI.
It never creates synthetic model-performance records in the benchmark archive.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loader.benchmarks import EvaluationManager, digest


async def verify(args):
    supervisor = SimpleNamespace(generation_lock=asyncio.Lock(), benchmark_task=None)
    manager = EvaluationManager(args.state, supervisor, SimpleNamespace(value={}))
    readiness = await manager.availability()
    if not readiness['docker']:
        raise RuntimeError(readiness['message'])
    suites = ['humaneval', 'swebench'] if args.suite == 'all' else [args.suite]
    results = {}
    try:
        for suite in suites:
            if args.prepare:
                await manager.prepare(suite)
                previous = None
                while manager.preparing:
                    if manager.setup['message'] != previous:
                        previous = manager.setup['message']
                        print(previous, flush=True)
                    await asyncio.sleep(1)
                if manager.setup['state'] != 'complete':
                    raise RuntimeError(manager.setup['message'])
            manifest = manager.manifest(suite)
            print(f'Verifying {suite} against real cached environments…', flush=True)
            if suite == 'humaneval':
                control = await manager.worker('selftest-human', image=manifest['worker_image'], timeout=240)
                assert all(control['reference_solutions'].values()), control
                assert control['incorrect_solution_rejected'], control
                # Infinite generated code must be stopped and its container removed.
                source = manifest['tasks'][0]
                start = time.monotonic()
                try:
                    await manager.worker('grade-human', dict(task_id=source['id'],
                        solution='while True: pass'), image=manifest['worker_image'], timeout=4)
                    raise AssertionError('Infinite solution was not stopped by the external deadline')
                except TimeoutError:
                    pass
                assert time.monotonic() - start < 12
                control['external_timeout_enforced'] = True
                results[suite] = control
            else:
                checks = {}
                for source in manifest['tasks']:
                    baseline, log = await manager.grade_repository(source, '', manifest['worker_image'], timeout=300)
                    assert not baseline['passed'] and not baseline.get('infrastructure_error'), baseline
                    assert source.get('validation', {}).get('reference_passed'), 'Re-prepare to validate reference fixes.'
                    checks[source['id']] = dict(unfixed_tests_fail=True, reference_fix_validated_during_setup=True)
                results[suite] = checks
        await manager.cleanup()
        _, remaining = await manager.command('docker', 'ps', '-aq', '--filter',
            'label=inflect.evaluation=' + digest(str(Path(args.state).resolve()))[:16])
        assert not remaining.strip(), 'An evaluation container was left behind'
        results['containers_cleaned'] = True
        results['model_inference_used'] = False
        print(json.dumps(results, indent=2), flush=True)
        if args.output:
            Path(args.output).write_text(json.dumps(results, indent=2) + '\n')
    finally:
        await manager.stop()
        await manager.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--suite', choices=['all', 'humaneval', 'swebench'], default='all')
    parser.add_argument('--state', default=os.environ.get('INFLECT_STATE', str(Path.home() / '.local/share/linux-llm-loader/state')))
    parser.add_argument('--output')
    asyncio.run(verify(parser.parse_args()))
