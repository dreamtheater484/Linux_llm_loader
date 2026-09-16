"""Discover native reasoning controls from template text without executing it."""
import json
import re
from typing import Literal


ReasoningEffort = Literal['default', 'off', 'on', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
LEVELS = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max')


def inspect_reasoning(template):
    # Inspect Jinja statements, not comments or prose that merely mention a knob.
    statements = '\n'.join(re.findall(r'{%-?(.*?)-?%}', template, re.S))
    toggle = bool(re.search(r'\benable_thinking\b', statements))
    aliases = {'reasoning_effort'} | set(re.findall(r'\bset\s+(\w+)\s*=\s*reasoning_effort\b', statements))
    levels = set()
    for name in aliases:
        for group in re.findall(r'\b' + re.escape(name) + r'\s+(?:not\s+)?in\s*[\[(]([^\])]+)[\])]', statements):
            levels.update(re.findall(r"['\"](\w+)['\"]", group))
    levels = [level for level in LEVELS if level in levels]
    default = re.search(r"\breasoning_effort\s*\|\s*default\(\s*['\"](\w+)['\"]", statements)
    return {'toggle': toggle, 'levels': levels,
            'default_effort': default[1] if default and default[1] in levels else None,
            'options': ['default'] + (['off'] if toggle else []) + (levels or (['on'] if toggle else []))}


def native_template(path, exl3=False):
    # Match the installed engines' template preference. Unknown templates expose
    # only Model default, rather than advertising unsupported effort levels.
    names = (['tabby_template.jinja'] if exl3 else []) + ['chat_template.jinja', 'chat_template.json', 'tokenizer_config.json']
    for name in names:
        source = path / name
        if not source.is_file() or source.stat().st_size > 2_000_000:
            continue
        try:
            value = source.read_text(encoding='utf-8')
            if name.endswith('.json'):
                value = json.loads(value).get('chat_template')
                if isinstance(value, list):
                    chosen = value[0] if exl3 and value else next((t for t in value if t.get('name') == 'default'), {})
                    value = chosen.get('template')
                elif isinstance(value, dict):
                    value = value.get('default')
            if isinstance(value, str) and value:
                return value
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return ''


def native_reasoning(path, exl3=False):
    return inspect_reasoning(native_template(path, exl3))


def reasoning_kwargs(model, effort):
    """Use the same variables for token counting and generation in every engine."""
    capabilities = model.get('reasoning') or inspect_reasoning('')
    if effort == 'default':
        return {}
    if effort == 'on' and capabilities['toggle']:
        return {'enable_thinking': True}
    if effort not in capabilities['options']:
        raise ValueError('This checkpoint does not support that reasoning level. Choose Model default or one of its listed levels.')
    if effort == 'off':
        return {'enable_thinking': False}
    return {**({'enable_thinking': True} if capabilities['toggle'] else {}), 'reasoning_effort': effort}
