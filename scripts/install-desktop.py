"""Write private machine paths to local config and install a desktop launcher."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inflect_access import access_config


parser = argparse.ArgumentParser()
parser.add_argument('--model-dir', required=True, help='Directory containing model folders/files')
parser.add_argument('--mount-device', help='Optional /dev/disk/by-uuid/... device for removable storage')
parser.add_argument('--no-desktop', action='store_true', help='Write configuration without an application-menu shortcut')
args = parser.parse_args()

project = Path(os.environ.get('INFLECT_PROJECT', Path(__file__).resolve().parents[1])).expanduser().resolve()
runtime = Path(os.environ.get('INFLECT_RUNTIME', Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'linux-llm-loader')).expanduser().resolve()
config_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'inflect'
applications = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
model_root = Path(args.model_dir).expanduser().resolve()
if not model_root.is_dir():
    raise SystemExit(f'Model directory does not exist: {model_root}')
runtime.mkdir(parents=True, exist_ok=True)
config_dir.mkdir(parents=True, exist_ok=True)
applications.mkdir(parents=True, exist_ok=True)
shutil.copy2(project / 'launch.py', runtime / 'launch.py')
shutil.copy2(project / 'inflect_access.py', runtime / 'inflect_access.py')
shutil.copy2(project / 'assets/inflect.svg', runtime / 'inflect.svg')
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
    if not args.mount_device.startswith('/dev/'):
        raise SystemExit('--mount-device must be a device path below /dev/.')
    config['mount_device'] = args.mount_device
config_file.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
config_file.chmod(0o600)
print('Configured:', config_file)
if args.no_desktop:
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
