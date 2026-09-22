#!/usr/bin/env python3
"""Launch Inflect using per-user configuration written during setup."""
import json
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
DATA_HOME = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
CONFIG_FILE = CONFIG_HOME / 'inflect/config.json'


def read_config():
    # Discover an earlier installation by its recorded project/runtime, so an
    # app rename preserves machine paths without retaining old public branding.
    if not CONFIG_FILE.exists() and CONFIG_HOME.exists():
        location = Path(__file__).resolve().parent
        for candidate in CONFIG_HOME.glob('*/config.json'):
            if candidate == CONFIG_FILE:
                continue
            try:
                previous = json.loads(candidate.read_text(encoding='utf-8'))
                if not isinstance(previous, dict) or not all(k in previous for k in ('project', 'runtime', 'model_root')):
                    continue
                if location not in (Path(previous['project']).resolve(), Path(previous['runtime']).resolve()):
                    continue
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                candidate.replace(CONFIG_FILE)
                CONFIG_FILE.chmod(0o600)
                try:
                    candidate.parent.rmdir()
                except OSError:
                    pass
                break
            except (OSError, ValueError, TypeError):
                continue
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


CONFIG = read_config()
SOURCE_PROJECT = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get('INFLECT_PROJECT', CONFIG.get('project', SOURCE_PROJECT))).expanduser()
RUNTIME = Path(os.environ.get('INFLECT_RUNTIME', CONFIG.get('runtime', DATA_HOME / 'linux-llm-loader'))).expanduser()
MODEL_ROOT = Path(os.environ.get('INFLECT_MODEL_ROOT', CONFIG.get('model_root', Path.home() / 'models'))).expanduser()
PORT = int(os.environ.get('INFLECT_PORT', CONFIG.get('port', 7860)))
LISTEN_HOST = os.environ.get('INFLECT_LISTEN_HOST', CONFIG.get('listen_host', '127.0.0.1'))
LAN_NETWORK = os.environ.get('INFLECT_LAN_NETWORK', CONFIG.get('lan_network', ''))
PUBLIC_URL = os.environ.get('INFLECT_PUBLIC_URL', CONFIG.get('public_url', f'http://{LISTEN_HOST}:{PORT}'))
PROBE_URL = f'http://{LISTEN_HOST}:{PORT}'


def ready():
    try:
        with urllib.request.urlopen(PROBE_URL + '/api/status', timeout=1) as response:
            return 'hardware' in json.load(response)
    except Exception:
        return False


def main():
    listen_ip = ipaddress.ip_address(LISTEN_HOST)
    if not (listen_ip.is_loopback or listen_ip.is_private):
        raise RuntimeError('Inflect refuses to listen on a public IP address.')
    if not listen_ip.is_loopback:
        network = ipaddress.ip_network(LAN_NETWORK, strict=False) if LAN_NETWORK else None
        if network is None or listen_ip not in network or not network.is_private:
            raise RuntimeError('LAN mode needs a private INFLECT_LAN_NETWORK containing the listen address.')
    if not PROJECT.is_dir():
        mount_device = os.environ.get('INFLECT_MOUNT_DEVICE', CONFIG.get('mount_device'))
        if mount_device:
            subprocess.run(['udisksctl', 'mount', '-b', mount_device], check=True)
    if not PROJECT.is_dir():
        raise RuntimeError(f'Inflect project is unavailable: {PROJECT}. Re-run setup after moving it.')
    python = RUNTIME / 'manager/bin/python'
    if not python.is_file():
        python = PROJECT / '.runtime/manager/bin/python'
    if not python.is_file() or not (PROJECT / 'frontend/dist/index.html').is_file():
        raise RuntimeError('Inflect setup is incomplete. See the project README.')
    if not ready():
        state = RUNTIME / 'state'
        state.mkdir(parents=True, exist_ok=True)
        with (state / 'manager.log').open('a') as log:
            environment = os.environ.copy()
            environment.update(INFLECT_PROJECT=str(PROJECT), INFLECT_RUNTIME=str(RUNTIME),
                               INFLECT_MODEL_ROOT=str(MODEL_ROOT), INFLECT_PORT=str(PORT),
                               INFLECT_LISTEN_HOST=LISTEN_HOST, INFLECT_LAN_NETWORK=LAN_NETWORK,
                               INFLECT_ALLOWED_HOSTS=','.join(CONFIG.get('allowed_hosts', [])))
            if CONFIG.get('comfyui_container') and 'INFLECT_COMFYUI_CONTAINER' not in environment:
                environment['INFLECT_COMFYUI_CONTAINER'] = CONFIG['comfyui_container']
            process = subprocess.Popen([str(python), '-m', 'uvicorn', 'loader.server:app', '--host', LISTEN_HOST, '--port', str(PORT)],
                cwd=PROJECT, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(120):
            if ready():
                break
            if process.poll() is not None:
                raise RuntimeError(f'Inflect could not start. Read {state / "manager.log"}.')
            time.sleep(1)
        else:
            process.terminate()
            raise RuntimeError(f'Inflect startup timed out. Read {state / "manager.log"}.')
    if '--no-browser' not in sys.argv:
        webbrowser.open(PUBLIC_URL)
    print(PUBLIC_URL)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        if os.environ.get('DISPLAY'):
            subprocess.run(['notify-send', 'Inflect could not start', str(exc)], check=False)
        sys.exit(1)
