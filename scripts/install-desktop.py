"""Write private machine paths to local config and install a desktop launcher."""
import argparse
import json
import os
from pathlib import Path
import shutil


parser = argparse.ArgumentParser()
parser.add_argument('--model-dir', required=True, help='Directory containing model folders/files')
parser.add_argument('--mount-device', help='Optional /dev/disk/by-uuid/... device for removable storage')
parser.add_argument('--no-desktop', action='store_true', help='Write configuration without an application-menu shortcut')
args = parser.parse_args()

project = Path(os.environ.get('LUMEN_PROJECT', Path(__file__).resolve().parents[1])).expanduser().resolve()
runtime = Path(os.environ.get('LUMEN_RUNTIME', Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'linux-llm-loader')).expanduser().resolve()
config_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'lumen'
applications = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
model_root = Path(args.model_dir).expanduser().resolve()
if not model_root.is_dir():
    raise SystemExit(f'Model directory does not exist: {model_root}')
runtime.mkdir(parents=True, exist_ok=True)
config_dir.mkdir(parents=True, exist_ok=True)
applications.mkdir(parents=True, exist_ok=True)
shutil.copy2(project / 'launch.py', runtime / 'launch.py')
shutil.copy2(project / 'assets/lumen.svg', runtime / 'lumen.svg')
config = {'project': str(project), 'runtime': str(runtime), 'model_root': str(model_root), 'port': 7860}
if args.mount_device:
    if not args.mount_device.startswith('/dev/'):
        raise SystemExit('--mount-device must be a device path below /dev/.')
    config['mount_device'] = args.mount_device
config_file = config_dir / 'config.json'
config_file.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
config_file.chmod(0o600)
print('Configured:', config_file)
if args.no_desktop:
    raise SystemExit(0)
desktop = applications / 'lumen.desktop'
desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=Lumen
Comment=Local LLM workbench with live hardware metrics
Exec=/usr/bin/python3 "{runtime / 'launch.py'}"
Icon={runtime / 'lumen.svg'}
Terminal=false
Categories=Development;Utility;
Keywords=LLM;AI;ExLlama;Qwen;DeepSeek;
StartupNotify=false
''')
desktop.chmod(0o755)
print('Installed:', desktop)
