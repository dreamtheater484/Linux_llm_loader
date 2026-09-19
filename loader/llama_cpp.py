"""llama.cpp runtime capabilities, prompt counting, and native timing adaptation."""
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import subprocess


@lru_cache(maxsize=8)
def _runtime_info(path, mtime_ns, size):
    try:
        help_result = subprocess.run([path, '--help'], capture_output=True, text=True, timeout=20, check=True)
        version = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=10, check=True)
        manifest = Path(path).resolve().parent.parent / 'manifest.json'
        receipt = json.loads(manifest.read_text()) if manifest.is_file() else None
        return dict(installed=True, version=(version.stdout or version.stderr).strip().splitlines()[0],
                    mtp='draft-mtp' in help_result.stdout and '--spec-draft-n-max' in help_result.stdout,
                    receipt=receipt)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        return dict(installed=False, version='Runtime unavailable', mtp=False, error=str(exc))


def runtime_info(binary):
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return dict(installed=False, version='Not installed', mtp=False)
    stat = binary.stat()
    return _runtime_info(str(binary), stat.st_mtime_ns, stat.st_size)


def has_media(messages):
    return any(isinstance(message.get('content'), list) and
               any(part.get('type') != 'text' for part in message['content']) for message in messages)


async def count_prompt(client, url, headers, messages, template_kwargs, tool_payload):
    if has_media(messages):
        # /tokenize counts text tokens only, not the projector's image patches.
        # The engine still enforces context capacity and returns full usage.
        return None
    response = await client.post(url + '/apply-template', headers=headers,
        json={'messages': messages, 'chat_template_kwargs': template_kwargs, **tool_payload})
    if response.is_error:
        raise ValueError('llama.cpp chat template failed: ' + response.text[:2000])
    prompt = response.json()['prompt']
    response = await client.post(url + '/tokenize', headers=headers,
        json={'content': prompt, 'add_special': True, 'parse_special': True})
    if response.is_error:
        raise ValueError('llama.cpp tokenization failed: ' + response.text[:2000])
    return len(response.json()['tokens'])


def normalize_usage(usage, timings):
    """Keep generated-token counts (including thinking); never count SSE chunks."""
    result = dict(usage)
    def numeric(key):
        value = timings.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None
    for source, target, divisor in (
        ('prompt_ms', 'prompt_time', 1000), ('predicted_ms', 'completion_time', 1000),
        ('prompt_per_second', 'prompt_tokens_per_sec', 1), ('predicted_per_second', 'completion_tokens_per_sec', 1)):
        value = numeric(source)
        if value is not None:
            result[target] = value / divisor
    result['llama_timings'] = timings
    drafted, accepted = numeric('draft_n'), numeric('draft_n_accepted')
    if drafted is not None and accepted is not None and accepted <= drafted:
        result['completion_tokens_details'] = {**result.get('completion_tokens_details', {}),
            'accepted_prediction_tokens': accepted, 'rejected_prediction_tokens': drafted - accepted}
    return result


async def effective_settings(client, url, headers, settings, requested):
    response = await client.get(url + '/props', headers=headers)
    response.raise_for_status()
    props = response.json()
    context = props.get('default_generation_settings', {}).get('n_ctx')
    if context != settings.context:
        raise ValueError(f'llama.cpp allocated {context} tokens instead of the requested {settings.context}.')
    if settings.vision and not props.get('modalities', {}).get('vision'):
        raise ValueError('llama.cpp did not load the requested vision projector.')
    response = await client.get(url + '/slots', headers=headers)
    response.raise_for_status()
    slots = response.json()
    mtp_active = bool(slots) and all(slot.get('speculative') is True for slot in slots)
    if settings.prediction == 'mtp' and not mtp_active:
        raise ValueError('MTP was requested but llama.cpp did not activate speculative decoding.')
    return {**requested, 'max_seq_len': context, 'use_vision': props.get('modalities', {}).get('vision', False),
            'mtp_active': mtp_active, 'build_info': props.get('build_info'),
            'chat_template_caps': props.get('chat_template_caps'),
            'sampling_defaults': {key: value for key, value in props.get('default_generation_settings', {}).get('params', {}).items()
                                  if key in ('top_k', 'top_p', 'min_p', 'repeat_penalty', 'presence_penalty', 'frequency_penalty')}}
