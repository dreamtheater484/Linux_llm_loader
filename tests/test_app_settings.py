import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
import pytest

import inflect_access as access
from inflect_platform import WINDOWS, exllama_lock, owned, site_packages, venv_python
from loader import app_settings as preferences, server, engine_updates, engines
from loader.engines import Settings

LAN = [dict(name='eth-test', host='192.168.8.9', subnet='192.168.8.0/24')]
HEADERS = {'X-Inflect-Local': '1'}


class StubTree:
    """Mocked installer processes have no real PID; never build a job object around one."""
    def __init__(self, pid):
        self.pid = pid

    def terminate(self):
        pass

    kill = close = terminate


@pytest.fixture(autouse=True)
def no_process_control(monkeypatch):
    monkeypatch.setattr(engine_updates, 'ProcessTree', StubTree)


@pytest.fixture
def local_settings(monkeypatch, tmp_path):
    monkeypatch.setenv('INFLECT_CONFIG_FILE', str(tmp_path / 'config.json'))
    monkeypatch.delenv('INFLECT_COMFYUI_CONTAINER', raising=False)
    monkeypatch.delenv('INFLECT_COMFYUI_URL', raising=False)
    monkeypatch.setattr(preferences, 'lan_interfaces', lambda: LAN)
    monkeypatch.setattr(server, 'lan_interfaces', lambda: LAN)
    monkeypatch.setattr(server, 'MODEL_ROOT', tmp_path)
    monkeypatch.setattr(server, 'STATE', tmp_path)
    return TestClient(server.app)


def test_fresh_default_is_lan_with_fail_closed_fallback():
    assert access.access_config({}, LAN)['listen_host'] == '192.168.8.9'
    offline = access.access_config({}, [])
    assert offline['network_enabled'] and offline['listen_host'] == '127.0.0.1'
    assert access.access_config({'network_enabled': False}, LAN)['lan_network'] == ''
    assert access.access_config({'listen_host': '127.0.0.1'}, LAN)['network_enabled'] is False


@pytest.mark.parametrize('host,network', [('0.0.0.0','0.0.0.0/0'), ('203.0.113.2','203.0.113.0/24'),
    ('169.254.1.2','169.254.0.0/16'), ('127.0.0.1','127.0.0.0/8'), ('192.168.8.9','0.0.0.0/0'),
    ('192.168.8.255','192.168.8.0/24'), ('::1','::/0')])
def test_non_lan_addresses_rejected(host, network):
    assert not access.private_lan(host, network)


def test_discovery_without_prefsrc_and_with_docker_bridge(monkeypatch):
    def output(args, **kwargs):
        if 'route' in args:
            return json.dumps([{'dev':'docker0'}, {'dev':'eth-test'}])
        assert args[-1] == 'eth-test'
        return json.dumps([{'addr_info':[dict(family='inet',scope='global',local='192.168.8.9',prefixlen=24)]}])
    monkeypatch.setattr(access, 'WINDOWS', False)
    monkeypatch.setattr(access.subprocess, 'check_output', output)
    assert access.lan_interfaces() == LAN


def test_windows_discovery_uses_default_route_and_skips_virtual_switches():
    output = json.dumps({'routes': [
        dict(ifIndex=45, InterfaceAlias='vEthernet (WSL (Hyper-V firewall))', RouteMetric=0, InterfaceMetric=5),
        dict(ifIndex=10, InterfaceAlias='eth-test', RouteMetric=0, InterfaceMetric=30)],
        'addresses': [dict(ifIndex=45, IPAddress='172.28.64.1', PrefixLength=20, AddressState=4),
                      dict(ifIndex=10, IPAddress='169.254.1.9', PrefixLength=16, AddressState=1),
                      dict(ifIndex=10, IPAddress='192.168.8.9', PrefixLength=24, AddressState=4)]})
    assert access.windows_lan_interfaces(output) == LAN
    # PowerShell serialises a single default route as an object, not a list.
    single = json.dumps({'routes': dict(ifIndex=10, InterfaceAlias='eth-test', RouteMetric=0, InterfaceMetric=30),
                         'addresses': dict(ifIndex=10, IPAddress='192.168.8.9', PrefixLength=24, AddressState=4)})
    assert access.windows_lan_interfaces(single) == LAN
    assert access.windows_lan_interfaces('not json') == []


def test_preferences_save_preserves_private_fields_and_stale_edits(local_settings):
    preferences.write_config(dict(mount_device='/dev/test',comfyui_container='existing-comfy'))
    snapshot = local_settings.get('/api/settings').json()
    assert snapshot['values']['comfyui_enabled']
    assert snapshot['values']['comfyui_mode'] == 'docker'
    body = dict(revision=snapshot['revision'], values={**snapshot['values'],'comfyui_enabled':False,'network_enabled':False})
    response = local_settings.put('/api/settings', json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    saved = preferences.read_config()
    assert saved['mount_device'] == '/dev/test'
    assert saved['listen_host'] == '127.0.0.1' and saved['lan_network'] == ''
    assert saved['comfyui_enabled'] is False
    if not WINDOWS:  # Windows protects %APPDATA% with per-user ACLs instead of mode bits.
        assert os.stat(preferences.config_path()).st_mode & 0o777 == 0o600
    assert local_settings.put('/api/settings',json=body,headers=HEADERS).status_code == 409
    # A newly constructed view reads the persisted choices.
    assert local_settings.get('/api/settings').json()['values']['network_enabled'] is False


@pytest.mark.parametrize('changes', [dict(port=0),dict(network_enabled='yes'),dict(comfyui_url='http://192.168.1.1:8188'),
    dict(comfyui_url='http://localhost:8188/path'),dict(comfyui_container='x; docker stop all'),dict(gguf_server='relative'),
    dict(comfyui_enabled=True,comfyui_mode='docker',comfyui_container=''),dict(network_interface='public-interface'),
    dict(model_root='/missing-folder-for-inflect-test'),dict(listen_host='0.0.0.0')])
def test_invalid_preferences_leave_config_untouched(local_settings, changes):
    before = preferences.read_config()
    snapshot = local_settings.get('/api/settings').json()
    response = local_settings.put('/api/settings',headers=HEADERS,json=dict(revision=snapshot['revision'],values={**snapshot['values'],**changes}))
    assert response.status_code == 422, response.text
    assert preferences.read_config() == before


def test_configuration_mutations_require_same_origin_and_header(local_settings):
    assert local_settings.put('/api/settings',json={}).status_code == 403
    assert local_settings.put('/api/settings',json={},headers={**HEADERS,'Origin':'https://evil.example'}).status_code == 403
    monkey = TestClient(server.app,client=('203.0.113.5',1234))
    assert monkey.get('/api/settings',headers={'X-Forwarded-For':'127.0.0.1'}).status_code == 403


def test_disabled_comfy_never_probes_or_stops_it(local_settings, monkeypatch):
    preferences.write_config(dict(comfyui_enabled=False,comfyui_container='existing-comfy'))
    supervisor = server.Supervisor()
    supervisor.settings = Settings(model_id='test')
    supervisor.model = {'bytes': 2**30}
    supervisor._terminate = AsyncMock()
    cleanup = AsyncMock()
    monkeypatch.setattr(server, 'release_comfyui', cleanup)
    launch = Mock(side_effect=RuntimeError('engine reached'))
    monkeypatch.setattr(server, 'launch', launch)
    monkeypatch.setattr(server.psutil, 'virtual_memory', lambda: SimpleNamespace(available=200*2**30))
    asyncio.run(supervisor._load())
    cleanup.assert_not_awaited()
    launch.assert_called_once()
    assert supervisor.error == 'engine reached'


def test_api_mode_ignores_existing_container_environment(local_settings, monkeypatch):
    preferences.write_config(dict(comfyui_enabled=True,comfyui_mode='api',comfyui_container='existing-comfy'))
    supervisor = server.Supervisor()
    supervisor._terminate = AsyncMock()
    cleanup = AsyncMock(side_effect=RuntimeError('stop test before launching'))
    monkeypatch.setattr(server,'release_comfyui',cleanup)
    asyncio.run(supervisor._load())
    assert cleanup.call_args.kwargs['container'] == ''


def test_engine_update_requires_idle_and_managed_engine(local_settings, monkeypatch):
    monkeypatch.setattr(server.supervisor,'state','ready')
    assert local_settings.post('/api/settings/engines/gguf/update',json={},headers=HEADERS).status_code == 409
    monkeypatch.setattr(server.supervisor,'state','idle')
    monkeypatch.setattr(engine_updates,'engine_details',lambda: [dict(id='gguf',managed=False,supported=False)])
    assert local_settings.post('/api/settings/engines/gguf/update',json={},headers=HEADERS).status_code == 422


def test_failed_installer_does_not_activate_paths(local_settings, monkeypatch, tmp_path):
    before = preferences.read_config()
    process = SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(side_effect=[b'Failed to download\n', b''])),wait=AsyncMock(return_value=1),returncode=1,pid=-1)
    monkeypatch.setattr(engine_updates.asyncio,'create_subprocess_exec',AsyncMock(return_value=process))
    updater = engine_updates.EngineUpdater()
    asyncio.run(updater.run('exl3',tmp_path,server.active_preferences()))
    assert updater.state == 'error' and 'code 1' in updater.error
    assert preferences.read_config() == before


def test_successful_update_activates_only_validated_paths(local_settings, monkeypatch, tmp_path):
    python=venv_python(tmp_path/'new env');python.parent.mkdir(parents=True);python.touch();python.chmod(0o700)
    tabby=tmp_path/'new tabby';tabby.mkdir();(tabby/'main.py').touch()
    process=SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(return_value=b'')),wait=AsyncMock(return_value=0),returncode=0,pid=-1)
    async def start(*args,**kwargs):
        receipt=Path(args[args.index('--receipt')+1])
        receipt.write_text(json.dumps(dict(exl_python=str(python),tabby_dir=str(tabby))))
        assert all(kwargs.get(key) == value for key, value in owned().items())
        assert 'install-exllama.py' in args[1]
        return process
    monkeypatch.setattr(engine_updates.asyncio,'create_subprocess_exec',start)
    updater=engine_updates.EngineUpdater()
    asyncio.run(updater.run('exl3',tmp_path,server.active_preferences()))
    assert updater.state == 'complete', updater.error
    assert preferences.read_config()['exl_python'] == str(python)
    assert local_settings.get('/api/settings').json()['restart_required']


def test_successful_install_does_not_overwrite_concurrent_configuration(local_settings,monkeypatch,tmp_path):
    process=SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(return_value=b'')),wait=AsyncMock(return_value=0),returncode=0,pid=-1)
    async def start(*args,**kwargs):
        receipt=Path(args[args.index('--receipt')+1])
        receipt.write_text(json.dumps(dict(exl_python=str(tmp_path/'other/env/bin/python'),tabby_dir=str(tmp_path/'other/tabby'))))
        preferences.write_config(dict(port=8000))
        return process
    monkeypatch.setattr(engine_updates.asyncio,'create_subprocess_exec',start)
    updater=engine_updates.EngineUpdater()
    asyncio.run(updater.run('exl3',tmp_path,server.active_preferences()))
    assert updater.state == 'error' and 'another window' in updater.error
    assert preferences.read_config() == dict(port=8000)


def test_installed_exllama_version_comes_from_environment(tmp_path,monkeypatch):
    python=venv_python(tmp_path/'env');python.parent.mkdir(parents=True);python.touch()
    site=tmp_path/'env'/('Lib/site-packages' if WINDOWS else 'lib/python3.12/site-packages')
    info=site/'exllamav3-2.0.0.dist-info';info.mkdir(parents=True)
    assert site_packages(tmp_path/'env') == [site]
    (info/'METADATA').write_text('Name: exllamav3\nVersion: 2.0.0\n')
    tabby=tmp_path/'tabby';tabby.mkdir();(tabby/'main.py').touch()
    monkeypatch.setattr(engines.subprocess,'check_output',lambda *args,**kwargs:'1234abcd\n')
    version=engines.exl_version(str(python),str(tabby),())
    assert version['installed'] and version['version'] == '2.0.0 / TabbyAPI 1234abcd'


PINNED = exllama_lock(engines.PROJECT)


@pytest.mark.parametrize('installed,package,torch,current', [(True,PINNED['package_version'],'2.9.0+cu128',True),
    (False,PINNED['package_version'],'2.9.0+cu128',False), (True,PINNED['version'],'2.9.0+cu128',False),
    (True,PINNED['package_version'],'2.8.0',False)])
def test_supported_exllama_requires_matching_gpu_dependencies(monkeypatch,installed,package,torch,current):
    monkeypatch.setattr(engines,'engine_inventory',lambda:[dict(id='exl3',installed=installed,
        package_version=package,torch_version=torch,tabby_revision='53da7919')])
    assert engine_updates.engine_details()[0]['supported'] is current


@pytest.mark.parametrize('windows', [False, True])
def test_platform_lock_overrides_only_its_own_engine_pins(monkeypatch, windows):
    import inflect_platform
    monkeypatch.setattr(inflect_platform, 'WINDOWS', windows)
    lock = exllama_lock(engines.PROJECT)
    assert lock['tabby_revision'] == '53da7919d4e45c63f4acbcbbc00cbe0f60a1ce65' and lock['torch'] == '2.9.0+cu128'
    assert lock['wheel'].startswith(f"exllamav3-{lock['package_version']}-cp312-cp312-")
    assert lock['wheel'].endswith('win_amd64.whl' if windows else 'linux_x86_64.whl')
    # Linux keeps its qualified 1.5.0 build and token-count patch; the Windows build already contains the fix.
    assert (lock['version'], lock['site_patches']) == (('1.5.4', []) if windows else ('1.5.0', ['patches/exl3-cumulative-output-tokens.patch']))


def test_restart_blocked_during_busy_work(local_settings,monkeypatch):
    monkeypatch.setattr(server.supervisor,'state','loading')
    spawn=Mock();monkeypatch.setattr(server.subprocess,'Popen',spawn)
    assert local_settings.post('/api/settings/restart',json={},headers=HEADERS).status_code == 409
    spawn.assert_not_called()
