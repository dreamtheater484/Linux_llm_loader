#!/usr/bin/env python3
"""Switch Lumen between loopback-only and private-LAN access."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess


parser = argparse.ArgumentParser(description='Configure which computers can open Lumen.')
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument('--lan', action='store_true', help='Allow the directly connected private IPv4 subnet')
mode.add_argument('--local', action='store_true', help='Allow this computer only')
args = parser.parse_args()

config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
config_file = config_home / 'lumen/config.json'
try:
    config = json.loads(config_file.read_text(encoding='utf-8'))
except FileNotFoundError:
    raise SystemExit('Run Lumen setup before configuring network access.')
except (OSError, ValueError) as exc:
    raise SystemExit(f'Cannot read {config_file}: {exc}')
if not isinstance(config, dict):
    raise SystemExit(f'Invalid configuration in {config_file}.')

if args.local:
    config.update(listen_host='127.0.0.1', allowed_hosts=['127.0.0.1', 'localhost'],
                  public_url=f"http://127.0.0.1:{int(config.get('port', 7860))}")
    config.pop('lan_network', None)
    message = 'Lumen is now configured for this computer only.'
else:
    routes = json.loads(subprocess.check_output(['ip', '-json', 'route', 'show', 'default'], text=True))
    route = next((item for item in routes if item.get('prefsrc') and item.get('dev')), None)
    if route is None:
        raise SystemExit('No default IPv4 LAN route was found.')
    addresses = json.loads(subprocess.check_output(['ip', '-json', 'address', 'show', 'dev', route['dev']], text=True))
    info = next((entry for item in addresses for entry in item.get('addr_info', [])
                 if entry.get('family') == 'inet' and entry.get('local') == route['prefsrc']), None)
    if info is None:
        raise SystemExit('The default LAN address could not be matched to its interface.')
    interface = ipaddress.ip_interface(f"{info['local']}/{info['prefixlen']}")
    if not interface.ip.is_private or interface.ip.is_loopback or not interface.network.is_private:
        raise SystemExit('Refusing LAN mode because the default interface is not on a private IPv4 network.')
    port = int(config.get('port', 7860))
    config.update(listen_host=str(interface.ip), lan_network=str(interface.network),
                  allowed_hosts=[str(interface.ip)], public_url=f'http://{interface.ip}:{port}')
    message = f'Lumen will accept {interface.network} clients at http://{interface.ip}:{port}.'

temporary = config_file.with_suffix('.tmp')
temporary.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
temporary.chmod(0o600)
temporary.replace(config_file)
print(message)
print('Quit and reopen Lumen to apply the change.')
