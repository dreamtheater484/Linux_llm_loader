"""Write private machine paths to local config and install a desktop launcher."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inflect_access import access_config
from inflect_platform import WINDOWS, config_home, data_home, default_runtime, hidden, venv_python


parser = argparse.ArgumentParser()
parser.add_argument('--model-dir', required=True, help='Directory containing model folders/files')
parser.add_argument('--mount-device', help='Optional /dev/disk/by-uuid/... device for removable storage (Linux)')
parser.add_argument('--no-desktop', action='store_true', help='Write configuration without an application-menu shortcut')
args = parser.parse_args()

project = Path(os.environ.get('INFLECT_PROJECT', Path(__file__).resolve().parents[1])).expanduser().resolve()
runtime = Path(os.environ.get('INFLECT_RUNTIME', default_runtime())).expanduser().resolve()
config_dir = config_home() / 'inflect'
applications = (config_home() / 'Microsoft/Windows/Start Menu/Programs') if WINDOWS else data_home() / 'applications'
model_root = Path(args.model_dir).expanduser().resolve()
if not model_root.is_dir():
    raise SystemExit(f'Model directory does not exist: {model_root}')
runtime.mkdir(parents=True, exist_ok=True)
config_dir.mkdir(parents=True, exist_ok=True)
applications.mkdir(parents=True, exist_ok=True)
for name in ('launch.py', 'inflect_access.py', 'inflect_platform.py'):
    shutil.copy2(project / name, runtime / name)
shutil.copy2(project / 'assets/inflect.svg', runtime / 'inflect.svg')
if WINDOWS:
    shutil.copy2(project / 'assets/inflect.ico', runtime / 'inflect.ico')
config_file = config_dir / 'config.json'
if not config_file.exists():
    for candidate in config_dir.parent.glob('*/config.json'):
        if candidate == config_file:
            continue
        try:
            old = json.loads(candidate.read_text(encoding='utf-8'))
            if old.get('project') != str(project) or 'model_root' not in old:
                continue
            candidate.replace(config_file)
            try:
                candidate.parent.rmdir()
            except OSError:
                pass
            break
        except (OSError, ValueError, TypeError):
            continue
try:
    existing = json.loads(config_file.read_text(encoding='utf-8'))
except (OSError, ValueError):
    existing = {}
config = {**existing, 'project': str(project), 'runtime': str(runtime), 'model_root': str(model_root), 'port': existing.get('port', 7860)}
config.update(access_config(config))
if args.mount_device:
    if WINDOWS:
        raise SystemExit('--mount-device applies to Linux only. Windows mounts drives by letter.')
    if not args.mount_device.startswith('/dev/'):
        raise SystemExit('--mount-device must be a device path below /dev/.')
    config['mount_device'] = args.mount_device
config_file.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
config_file.chmod(0o600)
print('Configured:', config_file)
if args.no_desktop:
    raise SystemExit(0)
if WINDOWS:
    # The manager's windowless interpreter runs the launcher: no console window,
    # and failures appear in a message box (see inflect_platform.notify_error).
    shortcut = applications / 'Inflect.lnk'
    script = ('$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:INFLECT_LNK);'
              '$s.TargetPath = $env:INFLECT_LNK_TARGET; $s.Arguments = $env:INFLECT_LNK_ARGS;'
              '$s.WorkingDirectory = $env:INFLECT_LNK_DIR; $s.IconLocation = $env:INFLECT_LNK_ICON;'
              "$s.Description = 'Local LLM workbench with live hardware metrics'; $s.Save()")
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], check=True, **hidden(),
                   env={**os.environ, 'INFLECT_LNK': str(shortcut),
                        'INFLECT_LNK_TARGET': str(venv_python(runtime / 'manager', windowed=True)),
                        'INFLECT_LNK_ARGS': f'"{runtime / "launch.py"}"', 'INFLECT_LNK_DIR': str(runtime),
                        'INFLECT_LNK_ICON': f'{runtime / "inflect.ico"},0'})
    print('Installed:', shortcut)
    raise SystemExit(0)
desktop = applications / 'inflect.desktop'
desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=Inflect
Comment=Local LLM workbench with live hardware metrics
Exec=/usr/bin/python3 "{runtime / 'launch.py'}"
Icon={runtime / 'inflect.svg'}
Terminal=false
Categories=Development;Utility;
Keywords=LLM;AI;ExLlama;Qwen;DeepSeek;
StartupNotify=false
''')
desktop.chmod(0o755)
# Remove superseded shortcuts for this exact launcher, never unrelated apps.
for candidate in applications.glob('*.desktop'):
    if candidate == desktop:
        continue
    content = candidate.read_text(encoding='utf-8', errors='replace')
    if f'Exec=/usr/bin/python3 "{runtime / "launch.py"}"' in content:
        for line in content.splitlines():
            if line.startswith('Icon='):
                old_icon = Path(line[5:])
                if old_icon.parent == runtime and old_icon != runtime / 'inflect.svg' and old_icon.suffix == '.svg':
                    old_icon.unlink(missing_ok=True)
        candidate.unlink()
print('Installed:', desktop)
