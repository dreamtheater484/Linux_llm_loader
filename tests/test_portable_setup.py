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
    config_file = config_home / 'inflect/config.json'
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({
        'listen_host': '10.42.7.23',
        'lan_network': '10.42.7.0/24',
        'allowed_hosts': ['10.42.7.23'],
        'public_url': 'http://10.42.7.23:7860',
    }))
    environment = {
        **os.environ,
        'XDG_DATA_HOME': str(data_home),
        'XDG_CONFIG_HOME': str(config_home),
        'INFLECT_PROJECT': str(PROJECT),
        'INFLECT_RUNTIME': str(data_home / 'custom-runtime'),
    }
    subprocess.run([
        sys.executable, str(PROJECT / 'scripts/install-desktop.py'),
        '--model-dir', str(model_root), '--no-desktop',
    ], check=True, env=environment, capture_output=True, text=True)

    config = json.loads(config_file.read_text(encoding='utf-8'))
    assert config['project'] == str(PROJECT)
    assert config['model_root'] == str(model_root)
    assert config['runtime'] == str(data_home / 'custom-runtime')
    assert config['lan_network'] == '10.42.7.0/24'
    assert config['public_url'] == 'http://10.42.7.23:7860'
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600

    child_env = {k: v for k, v in environment.items() if k not in ('INFLECT_PROJECT', 'INFLECT_RUNTIME')}
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


def test_access_configurator_can_return_to_loopback(tmp_path):
    config_home = tmp_path / 'config'
    config_file = config_home / 'inflect/config.json'
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({'port': 7860, 'lan_network': '10.42.7.0/24'}))
    subprocess.run([
        sys.executable, str(PROJECT / 'scripts/configure-access.py'), '--local',
    ], check=True, env={**os.environ, 'XDG_CONFIG_HOME': str(config_home)}, capture_output=True, text=True)
    config = json.loads(config_file.read_text())
    assert config['listen_host'] == '127.0.0.1'
    assert config['allowed_hosts'] == ['127.0.0.1', 'localhost']
    assert 'lan_network' not in config
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def test_rename_discovers_existing_config_and_preserves_paths(tmp_path):
    config_home = tmp_path / 'config'
    old = config_home / 'previous-app/config.json'
    old.parent.mkdir(parents=True)
    config = dict(project=str(PROJECT), runtime=str(tmp_path/'runtime'), model_root=str(tmp_path/'models'), comfyui_container='existing-comfy', port=7860)
    old.write_text(json.dumps(config))
    result = subprocess.run([sys.executable, '-c', 'import launch; print(launch.CONFIG_FILE); print(launch.MODEL_ROOT)'],
        cwd=PROJECT, env={**os.environ,'XDG_CONFIG_HOME':str(config_home)}, check=True, capture_output=True,text=True)
    new = config_home / 'inflect/config.json'
    assert json.loads(new.read_text()) == config
    assert not old.exists()
    assert str(new) in result.stdout
