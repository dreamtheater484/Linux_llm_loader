"""Portable configuration checks that never touch the user's real home directory."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


PROJECT = Path(__file__).resolve().parents[1]


def test_private_config_drives_launcher_paths(tmp_path):
    model_root = tmp_path / 'models with spaces'
    model_root.mkdir()
    data_home = tmp_path / 'data'
    config_home = tmp_path / 'config'
    environment = {
        **os.environ,
        'XDG_DATA_HOME': str(data_home),
        'XDG_CONFIG_HOME': str(config_home),
        'LUMEN_PROJECT': str(PROJECT),
        'LUMEN_RUNTIME': str(data_home / 'custom-runtime'),
    }
    subprocess.run([
        sys.executable, str(PROJECT / 'scripts/install-desktop.py'),
        '--model-dir', str(model_root), '--no-desktop',
    ], check=True, env=environment, capture_output=True, text=True)

    config_file = config_home / 'lumen/config.json'
    config = json.loads(config_file.read_text(encoding='utf-8'))
    assert config['project'] == str(PROJECT)
    assert config['model_root'] == str(model_root)
    assert config['runtime'] == str(data_home / 'custom-runtime')
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600

    child_env = {k: v for k, v in environment.items() if k not in ('LUMEN_PROJECT', 'LUMEN_RUNTIME')}
    result = subprocess.run([
        sys.executable, '-c',
        'import launch; print(launch.PROJECT); print(launch.RUNTIME); print(launch.MODEL_ROOT)',
    ], check=True, cwd=PROJECT, env=child_env, capture_output=True, text=True)
    assert result.stdout.splitlines() == [str(PROJECT), str(data_home / 'custom-runtime'), str(model_root)]


def test_public_launcher_contains_no_machine_location():
    source = (PROJECT / 'launch.py').read_text(encoding='utf-8')
    assert '/run/media/' not in source
    assert '/home/' not in source
    assert '/dev/disk/by-uuid/' not in source


def test_setup_help_does_not_require_installation():
    result = subprocess.run(
        ['bash', str(PROJECT / 'scripts/setup-ubuntu.sh'), '--help'],
        check=True, capture_output=True, text=True,
    )
    assert '--model-dir PATH' in result.stdout
    assert '--preflight-only' in result.stdout
