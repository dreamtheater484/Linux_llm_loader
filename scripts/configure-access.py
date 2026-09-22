#!/usr/bin/env python3
"""Switch Inflect between loopback-only and private-LAN access."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inflect_access import access_config, lan_interfaces


parser = argparse.ArgumentParser(description='Configure which computers can open Inflect.')
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument('--lan', action='store_true', help='Allow the directly connected private IPv4 subnet')
mode.add_argument('--local', action='store_true', help='Allow this computer only')
args = parser.parse_args()

config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
config_file = config_home / 'inflect/config.json'
try:
    config = json.loads(config_file.read_text(encoding='utf-8'))
except FileNotFoundError:
    raise SystemExit('Run Inflect setup before configuring network access.')
except (OSError, ValueError) as exc:
    raise SystemExit(f'Cannot read {config_file}: {exc}')
if not isinstance(config, dict):
    raise SystemExit(f'Invalid configuration in {config_file}.')

if args.local:
    config.update(network_enabled=False, listen_host='127.0.0.1', allowed_hosts=['127.0.0.1', 'localhost'],
                  public_url=f"http://127.0.0.1:{int(config.get('port', 7860))}")
    config.pop('lan_network', None)
    message = 'Inflect is now configured for this computer only.'
else:
    interfaces = lan_interfaces()
    config.update(network_enabled=True, network_interface='')
    config.update(access_config(config, interfaces))
    message = (f"Inflect will accept {config['lan_network']} clients at {config['public_url']}."
               if config['lan_network'] else 'No private LAN detected. Inflect will stay local until one is available.')

temporary = config_file.with_suffix('.tmp')
temporary.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
temporary.chmod(0o600)
temporary.replace(config_file)
print(message)
print('Quit and reopen Inflect to apply the change.')
