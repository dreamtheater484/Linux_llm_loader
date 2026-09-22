"""Archive queries and reversible deletion shared by coding and speed results."""
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import time

from fastapi import HTTPException
from .metrics import annotate_memory

TABLES = {'coding': 'evaluations', 'speed': 'benchmarks'}
ABORTED = ('cancelled', 'interrupted', 'aborted')


def run_performance(result):
    metrics = [metric for task in result.get('tasks', []) for metric in task.get('metrics', [])]
    def median(field, engine_only=False):
        values = [m.get(field) for m in metrics if not engine_only or m.get('speed_source') == 'engine']
        values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0]
        return (statistics.median(values) if values else None), len(values)
    decode, decode_count = median('tokens_per_second', True)
    prefill, prefill_count = median('prompt_tokens_per_second', True)
    first, _ = median('first_token_seconds')
    return dict(decode_tps=decode, prefill_tps=prefill, first_token_seconds=first,
                decode_samples=decode_count, prefill_samples=prefill_count, responses=len(metrics))


def with_performance(result, kind):
    result = annotate_memory(result)
    return {**result, 'performance': run_performance(result)} if kind == 'coding' else result


def connect(state):
    connection = sqlite3.connect(Path(state) / 'results.sqlite3')
    for table in TABLES.values():
        connection.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, created REAL, data TEXT)')
    return connection


def list_runs(state, kind, limit=30, offset=0, suite=None, query='', model_id=None, sort='newest', status=None, trash=False):
    table = TABLES[kind]
    base = "json_extract(data, '$.state') NOT IN ('running', 'cancelled', 'interrupted', 'aborted')"
    base += " AND json_extract(data, '$.deleted_at') IS " + ('NOT NULL' if trash else 'NULL')
    where, args = [base], []
    for field, value in (('suite', suite), ('settings.model_id', model_id), ('state', status)):
        if value:
            where.append(f"json_extract(data, '$.{field}') = ?")
            args.append(value)
    if query.strip():
        fields = ('id', 'benchmark', 'model.name', 'model.title', 'model.quant')
        where.append('(' + ' OR '.join(f"instr(lower(coalesce(json_extract(data, '$.{field}'), '')), ?) > 0" for field in fields) + ')')
        args.extend([query.strip().lower()] * len(fields))
    score = 'summary.score' if kind == 'coding' else 'median_tps'
    order = {'newest': 'created DESC', 'oldest': 'created ASC',
             'score': f"json_extract(data, '$.{score}') DESC, created DESC"}[sort] + ', id ASC'
    clause = ' WHERE ' + ' AND '.join(where)
    with connect(state) as db:
        total = db.execute(f'SELECT COUNT(*) FROM {table}' + clause, args).fetchone()[0]
        all_total = db.execute(f'SELECT COUNT(*) FROM {table} WHERE ' + base).fetchone()[0]
        rows = db.execute(f'SELECT data FROM {table}' + clause + f' ORDER BY {order} LIMIT ? OFFSET ?', (*args, limit, offset))
        return dict(items=[with_performance(json.loads(row[0]), kind) for row in rows], total=total, all_total=all_total, offset=offset, limit=limit)


def manage_runs(state, kind, ids, action):
    table = TABLES[kind]
    if not ids or len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-f0-9]{16}', identity) for identity in ids):
        raise HTTPException(422, 'Choose distinct valid benchmark runs.')
    with connect(state) as db:
        results = []
        for identity in ids:
            row = db.execute(f'SELECT data FROM {table} WHERE id=?', (identity,)).fetchone()
            if not row:
                raise HTTPException(404, 'A selected benchmark no longer exists. Refresh the archive.')
            result = json.loads(row[0])
            if result['state'] == 'running' or result['state'] in ABORTED:
                raise HTTPException(409, 'Only finished benchmark runs can be managed.')
            results.append(result)
        for result in results:
            if action == 'delete':
                result['deleted_at'] = time.time()
            else:
                result.pop('deleted_at', None)
            db.execute(f'UPDATE {table} SET data=? WHERE id=?', (json.dumps(result), result['id']))
    return {'updated': ids}


def discard_run(state, kind, identity):
    if not re.fullmatch(r'[a-f0-9]{16}', identity):
        raise ValueError('Invalid benchmark identity')
    if kind == 'coding':
        root = Path(state) / 'evaluations' / identity
        if root.is_symlink():
            root.unlink()
        elif root.exists():
            shutil.rmtree(root)
    with connect(state) as db:
        db.execute(f'DELETE FROM {TABLES[kind]} WHERE id=?', (identity,))


def discard_aborted(state):
    for kind, table in TABLES.items():
        with connect(state) as db:
            ids = [row[0] for row in db.execute(f"SELECT id FROM {table} WHERE json_extract(data, '$.state') IN ('running', 'cancelled', 'interrupted', 'aborted')")]
        for identity in ids:
            discard_run(state, kind, identity)
