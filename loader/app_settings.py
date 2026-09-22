"""Private application settings, with atomic writes and stale-edit protection."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from inflect_access import access_config, lan_interfaces
from .comfyui import local_url


def config_path():
    return Path(os.environ.get('INFLECT_CONFIG_FILE',
                str(Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'inflect/config.json')))


def read_config():
    try:
        data = json.loads(config_path().read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Inflect configuration must be an object.')
        return data
    except FileNotFoundError:
        return {}


def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def comfy_settings(config=None):
    config = read_config() if config is None else config
    container = config.get('comfyui_container', os.environ.get('INFLECT_COMFYUI_CONTAINER', ''))
    url = config.get('comfyui_url', os.environ.get('INFLECT_COMFYUI_URL', 'http://127.0.0.1:8188'))
    return dict(enabled=config.get('comfyui_enabled', bool(container or os.environ.get('INFLECT_COMFYUI_URL'))),
                mode=config.get('comfyui_mode', 'docker' if container else 'api'), url=url, container=container)


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    network_enabled: bool = True
    network_interface: str = Field('', max_length=64)
    port: int = Field(7860, ge=1024, le=65535)
    model_root: str = Field(min_length=1, max_length=4096)
    gguf_server: str = Field(min_length=1, max_length=4096)
    exl_python: str = Field(min_length=1, max_length=4096)
    tabby_dir: str = Field(min_length=1, max_length=4096)
    comfyui_enabled: bool = False
    comfyui_mode: Literal['api', 'docker'] = 'api'
    comfyui_url: str = 'http://127.0.0.1:8188'
    comfyui_container: str = Field('', max_length=128)

    @field_validator('comfyui_url')
    @classmethod
    def local_endpoint(cls, value):
        return local_url(value)

    @field_validator('comfyui_container')
    @classmethod
    def container_name(cls, value):
        if value and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', value):
            raise ValueError('Enter a Docker container name or ID.')
        return value

    @field_validator('model_root', 'gguf_server', 'exl_python', 'tabby_dir')
    @classmethod
    def absolute_path(cls, value):
        path = Path(value).expanduser()
        if not path.is_absolute() or any(ord(char) < 32 for char in value):
            raise ValueError('Enter an absolute path on the Inflect computer.')
        return str(path)


class SavePreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    values: Preferences


def values(config, active):
    integration = comfy_settings(config)
    return {**{key: config.get(key, active[key]) for key in
               ('model_root', 'gguf_server', 'exl_python', 'tabby_dir', 'port')},
            'network_enabled': config.get('network_enabled', config.get('listen_host', '') not in ('127.0.0.1', '::1')),
            'network_interface': config.get('network_interface', ''),
            **{f'comfyui_{key}': value for key, value in integration.items()}}


def save_preferences(body, active):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        current = read_config()
        if revision(current) != body.revision:
            from fastapi import HTTPException
            raise HTTPException(409, 'Settings changed in another window. Reload settings before saving.')
        updated = body.values.model_dump()
        previous = values(current, active)
        for key in ('model_root', 'gguf_server', 'exl_python', 'tabby_dir'):
            if updated[key] == previous[key]:
                continue
            target = Path(updated[key])
            if key in ('model_root', 'tabby_dir'):
                valid = target.is_dir() and os.access(target, os.R_OK | os.X_OK)
                if key == 'tabby_dir':
                    valid = valid and (target / 'main.py').is_file()
            else:
                valid = target.is_file() and os.access(target, os.X_OK)
            if not valid:
                raise ValueError(f'{key.replace("_", " ").capitalize()}: the path is unavailable or has the wrong type.')
        if updated['comfyui_enabled'] and updated['comfyui_mode'] == 'docker' and not updated['comfyui_container']:
            raise ValueError('Choose the exact ComfyUI Docker container before enabling container control.')
        interfaces = lan_interfaces()
        if updated['network_interface'] and not any(row['name'] == updated['network_interface'] for row in interfaces):
            # Keep an existing preference while temporarily disconnected.
            if updated['network_interface'] != previous['network_interface']:
                raise ValueError('That LAN interface is no longer available. Reload settings.')
        updated = {**current, **updated}
        updated.update(access_config(updated, interfaces))
        write_config(updated)
        return updated


def write_config(config):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(config, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)  # NamedTemporaryFile creates mode 0600.
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
