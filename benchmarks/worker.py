"""Trusted adapter to pinned upstream graders. Run ONLY in the benchmark image.

Generated Python is passed to EvalPlus's untrusted checker inside a disposable,
network-disabled container. Repository commands execute in separate containers;
this adapter only constructs official scripts and parses their test reports.
"""
import dataclasses
import hashlib
import json
from importlib.metadata import distributions
from pathlib import Path
import sys

SEED = 'inflect-coding-v1'
HUMAN_SELECTION = 'inflect-humaneval-80-v1'
# Requests' historical tests depend on public HTTP services. Keep the fixed
# offline subset explicit and versioned; never omit failing upstream tests.
SWE_SELECTION = 'inflect-swe-offline-v2'
REPOS = ('pylint-dev/pylint', 'pallets/flask', 'pytest-dev/pytest')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def choose(items, key, count):
    return sorted(items, key=lambda item: hashlib.sha256(f'{SEED}:{item[key]}'.encode()).hexdigest())[:count]


def dependencies():
    return dict(sorted((package.metadata['Name'], package.version) for package in distributions()))


def human_data():
    from evalplus.data import get_human_eval_plus, get_human_eval_plus_hash
    from evalplus.evaluate import get_groundtruth
    selected = choose(list(get_human_eval_plus().values()), 'task_id', 80)
    problems = {p['task_id']: p for p in selected}
    dataset_hash = get_human_eval_plus_hash()
    expected = get_groundtruth(problems, dataset_hash + '-inflect-80-v1', [])
    return problems, expected, dataset_hash


def human_manifest():
    problems, _, dataset_hash = human_data()
    return dict(dataset='HumanEval+', revision='v0.1.10', upstream_hash=dataset_hash,
                selection=HUMAN_SELECTION, dependencies=dependencies(),
                tasks=[dict(id=p['task_id'], prompt=p['prompt'], entry_point=p['entry_point']) for p in problems.values()])


def human_grade(body):
    from evalplus.evaluate import check_correctness
    problems, expected, _ = human_data()
    problem = problems[body['task_id']]
    result = check_correctness('humaneval', 0, problem, body['solution'], expected[body['task_id']], fast_check=False)
    base, plus = result['base'], result['plus']
    return dict(passed=base[0] == plus[0] == 'pass', base_status=base[0], plus_status=plus[0],
                base_passed=sum(bool(x) for x in base[1]), base_total=len(problem['base_input']),
                plus_passed=sum(bool(x) for x in plus[1]), plus_total=len(problem['plus_input']))


def swe_manifest():
    from datasets import load_dataset
    from huggingface_hub import HfApi
    from swebench.harness.test_spec.test_spec import make_test_spec
    dataset = 'princeton-nlp/SWE-bench_Lite'
    # Resolve the current revision ONCE during preparation, archive it forever.
    revision = HfApi().dataset_info(dataset).sha
    rows = list(load_dataset(dataset, split='test', revision=revision))
    tasks = []
    for repo in REPOS:
        candidates = [r for r in rows if r['repo'] == repo]
        if not candidates:
            raise ValueError(f'Pinned subset requires {repo}; dataset preparation stopped.')
        row = choose(candidates, 'instance_id', 1)[0]
        spec = make_test_spec(row, namespace='swebench')
        tasks.append(dict(id=row['instance_id'], repo=repo, base_commit=row['base_commit'],
                          prompt=row['problem_statement'], image=spec.instance_image_key,
                          spec=dataclasses.asdict(spec), eval_script=spec.eval_script,
                          reference_patch=row['patch']))
    return dict(dataset=dataset, revision=revision, selection=SWE_SELECTION, dependencies=dependencies(), tasks=tasks)


def swe_grade(body):
    from swebench.harness.test_spec.test_spec import TestSpec
    from swebench.harness.grading import get_eval_report, get_logs_eval
    spec = TestSpec(**body['spec'])
    path = Path('/tmp/test-output.txt')
    path.write_text(body['log'])
    statuses, found = get_logs_eval(spec, str(path))
    if not found or not statuses:
        return dict(passed=False, infrastructure_error='The official grader could not find a complete test report.')
    if not spec.FAIL_TO_PASS:
        return dict(passed=False, infrastructure_error='Task has no required fail-to-pass tests.')
    prediction = dict(instance_id=spec.instance_id, model_name_or_path='inflect', model_patch=body['patch'])
    report = get_eval_report(spec, prediction, str(path), include_tests_status=True)[spec.instance_id]
    return dict(passed=report['resolved'], report=report)


def main():
    mode = sys.argv[1]
    if mode in ('warm-human', 'manifest-human'):
        result = human_manifest()
    elif mode == 'manifest-swe':
        result = swe_manifest()
    elif mode == 'grade-human':
        result = human_grade(json.load(sys.stdin))
    elif mode == 'grade-swe':
        result = swe_grade(json.load(sys.stdin))
    elif mode == 'selftest-human':
        problems, _, _ = human_data()
        checks = {}
        for task_id, problem in problems.items():
            checks[task_id] = human_grade(dict(task_id=task_id, solution=problem['prompt'] + problem['canonical_solution']))['passed']
        first = next(iter(problems))
        rejected = not human_grade(dict(task_id=first, solution='raise RuntimeError("deliberately incorrect control")'))['passed']
        result = dict(reference_solutions=checks, incorrect_solution_rejected=rejected)
    else:
        raise ValueError('Unknown worker operation')
    # Library diagnostics can precede this line; the host accepts only this record.
    print('INFLECT_RESULT=' + json.dumps(result))


if __name__ == '__main__':
    main()
