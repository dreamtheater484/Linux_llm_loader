"""An installer failure must never damage the selected inference environment."""
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


def test_legacy_python_fallback_keeps_previous_installation_on_failure(monkeypatch,tmp_path):
    spec=importlib.util.spec_from_file_location('install_exllama',Path(__file__).resolve().parents[1]/'scripts/install-exllama.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    active=tmp_path/'exl3/bin/python';active.parent.mkdir(parents=True);active.write_text('existing environment')
    monkeypatch.setattr(installer.shutil,'which',lambda _:None)
    monkeypatch.setattr(installer.shutil,'disk_usage',lambda _:SimpleNamespace(free=20*2**30))
    commands=[]
    def run(args,**kwargs):
        commands.append(args)
        if 'pip' in args:
            raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(installer.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(tmp_path,tmp_path/'receipt.json')
    assert commands[0][1:3] == ['-m','venv']
    assert active.read_text() == 'existing environment'
    assert not list((tmp_path/'exl3-releases').iterdir())
    assert not (tmp_path/'receipt.json').exists()
