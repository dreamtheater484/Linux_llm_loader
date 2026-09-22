"""Compact profile names and benchmark matching by effective settings."""
import math
import re
import hashlib
import json
import sqlite3

from .engines import Settings
from .metrics import RAM_ACCOUNTING


def settings_key(settings, model):
    try:
        value = Settings.model_validate(settings).model_dump()
    except ValueError:
        return None
    engine = value.pop('engine')
    value['engine'] = model.get('engines', ['auto'])[0] if engine == 'auto' else engine
    if value['engine'] != 'gguf':
        value.pop('gguf_offload')
    if value['prediction'] == 'off':
        value.pop('draft_tokens')
    return value


def load_key(settings, model):
    value = settings_key(settings, model)
    if value is None:
        return None
    # These controls apply to a response without reloading model memory.
    for field in ('max_output', 'temperature', 'reasoning_effort'):
        value.pop(field)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def record_loaded_memory(state, settings, model, hardware):
    def used(kind):
        value = (hardware.get(kind) or {}).get('used_bytes')
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    measurement = dict(vram_bytes=used('gpu'), ram_bytes=used('ram'), measured_at=hardware['timestamp'],
                       scope='system_total', phase='idle_after_load', model_id=model['id'],
                       gpu_name=(hardware.get('gpu') or {}).get('name'))
    ram = hardware.get('ram') or {}
    measurement.update(ram_accounting=ram.get('accounting', 'legacy_total_minus_available'),
                       ram_cache_bytes=ram.get('cache_bytes'), ram_non_cache_bytes=ram.get('non_cache_bytes'),
                       model_memory=hardware.get('model_memory'))
    if measurement['vram_bytes'] is None and measurement['ram_bytes'] is None:
        return False
    with sqlite3.connect(state / 'results.sqlite3') as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS profile_loads (key TEXT PRIMARY KEY, data TEXT)')
        connection.execute('INSERT OR REPLACE INTO profile_loads VALUES (?, ?)',
                           (load_key(settings, model), json.dumps(measurement)))
    return True


def compact_name(model, settings, speed=None):
    name = model.get('name', model.get('title', 'Unavailable model'))
    name = re.sub(r'-Uncensored-HauhauCS-Aggressive', ' Agg.', name, flags=re.I)
    name = re.sub(r'[-_]?(?:EXL3|NVFP4|UD-|Q\d|IQ\d|MXFP\d).*$', '', name).replace('-', ' ').replace('_', ' ')
    quant = model.get('quant', '?').replace(' bpw', 'bpw')
    context = settings['context'] / 1024
    parts = [name, quant, f"{context:g}K/{settings['kv']}"]
    if isinstance(speed, (int, float)) and math.isfinite(speed) and speed > 0:
        parts.append(f'{speed:.0f} tok/s')
    parts += [f"V:{'on' if settings['vision'] else 'off'}",
              f"MTP:on/{settings['draft_tokens']}" if settings['prediction'] == 'mtp' else 'MTP:off',
              f"CPU:{settings['cpu_percent']}%"]
    return ' · '.join(parts)


def enrich_profile(profile, model, results, loads=None):
    available = model is not None
    model = model or {'name': 'Unavailable model', 'engines': ['auto']}
    key = settings_key(profile['settings'], model)
    matching, related = [], []
    for result in results:
        if result.get('settings', {}).get('model_id') != profile['settings']['model_id']:
            continue
        exact = key is not None and settings_key(result['settings'], model) == key
        summary = {k: result.get(k) for k in ('id', 'created', 'kind', 'state', 'median_tps', 'median_prompt_tps', 'benchmark', 'summary')}
        summary['exact_settings'] = exact
        (matching if exact else related).append(summary)
    def measurement(field):
        for result in sorted(matching, key=lambda r: r.get('created') or 0, reverse=True):
            value = result.get(field)
            if result['state'] in ('complete', 'timed_out') and type(value) in (int, float) and math.isfinite(value) and value > 0:
                return value, {k: result.get(k) for k in ('id', 'kind', 'benchmark', 'created', 'state')}
        return None, None
    speed, decode_source = measurement('median_tps')
    prefill, prefill_source = measurement('median_prompt_tps')
    automatic = compact_name(model, profile['settings'], speed) if available else profile['name']
    if available and profile.get('copy_number'):
        automatic += f" · copy {profile['copy_number']}"
    memory = (loads or {}).get(load_key(profile['settings'], model)) if available else None
    if memory and memory.get('ram_accounting') != RAM_ACCOUNTING:
        memory = {**memory, 'legacy_ram_bytes': memory.get('ram_bytes'), 'ram_bytes': None,
                  'ram_accounting': 'legacy_total_minus_available'}
    return {**profile, 'name': automatic if profile.get('auto_name') else profile['name'],
            'suggested_name': automatic, 'benchmark_results': matching[:6], 'related_results': related[:3],
            'performance': dict(decode_tps=speed, prefill_tps=prefill, decode_source=decode_source, prefill_source=prefill_source),
            'loaded_memory': memory}
