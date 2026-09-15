#!/usr/bin/env python3
"""Launch Lumen using per-user configuration written during setup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
DATA_HOME = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
CONFIG_FILE = CONFIG_HOME / 'lumen/config.json'


def read_config():
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


CONFIG = read_config()
SOURCE_PROJECT = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get('LUMEN_PROJECT', CONFIG.get('project', SOURCE_PROJECT))).expanduser()
RUNTIME = Path(os.environ.get('LUMEN_RUNTIME', CONFIG.get('runtime', DATA_HOME / 'linux-llm-loader'))).expanduser()
MODEL_ROOT = Path(os.environ.get('LUMEN_MODEL_ROOT', CONFIG.get('model_root', Path.home() / 'models'))).expanduser()
PORT = int(os.environ.get('LUMEN_PORT', CONFIG.get('port', 7860)))
URL = f'http://127.0.0.1:{PORT}'


def ready():
    try:
        with urllib.request.urlopen(URL + '/api/status', timeout=1) as response:
            return 'hardware' in json.load(response)
    except Exception:
        return False


def main():
    if not PROJECT.is_dir():
        mount_device = os.environ.get('LUMEN_MOUNT_DEVICE', CONFIG.get('mount_device'))
        if mount_device:
            subprocess.run(['udisksctl', 'mount', '-b', mount_device], check=True)
    if not PROJECT.is_dir():
        raise RuntimeError(f'Lumen project is unavailable: {PROJECT}. Re-run setup after moving it.')
    python = RUNTIME / 'manager/bin/python'
    if not python.is_file():
        python = PROJECT / '.runtime/manager/bin/python'
    if not python.is_file() or not (PROJECT / 'frontend/dist/index.html').is_file():
        raise RuntimeError('Lumen setup is incomplete. See the project README.')
    if not ready():
        state = RUNTIME / 'state'
        state.mkdir(parents=True, exist_ok=True)
        with (state / 'manager.log').open('a') as log:
            environment = os.environ.copy()
            environment.update(LUMEN_PROJECT=str(PROJECT), LUMEN_RUNTIME=str(RUNTIME),
                               LUMEN_MODEL_ROOT=str(MODEL_ROOT), LUMEN_PORT=str(PORT))
            process = subprocess.Popen([str(python), '-m', 'uvicorn', 'loader.server:app', '--host', '127.0.0.1', '--port', str(PORT)],
                cwd=PROJECT, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(120):
            if ready():
                break
            if process.poll() is not None:
                raise RuntimeError(f'Lumen could not start. Read {state / "manager.log"}.')
            time.sleep(1)
        else:
            process.terminate()
            raise RuntimeError(f'Lumen startup timed out. Read {state / "manager.log"}.')
    if '--no-browser' not in sys.argv:
        webbrowser.open(URL)
    print(URL)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        if os.environ.get('DISPLAY'):
            subprocess.run(['notify-send', 'Lumen could not start', str(exc)], check=False)
        sys.exit(1)
