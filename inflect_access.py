"""Shared, standard-library-only LAN policy for setup, launcher and server."""
import ipaddress
import json
import subprocess

PRIVATE_LANS = tuple(ipaddress.ip_network(value) for value in
                     ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def private_lan(host, subnet):
    try:
        address = ipaddress.ip_address(host)
        network = ipaddress.ip_network(subnet, strict=False)
        return (address.version == network.version == 4 and address in network
                and any(network.subnet_of(lan) for lan in PRIVATE_LANS)
                and address not in (network.network_address, network.broadcast_address))
    except (ValueError, TypeError):
        return False


def lan_interfaces():
    """Only directly connected IPv4 LANs on a default-route interface.

    Do not accidentally publish on Docker bridges, VPNs or wildcard addresses.
    Missing iproute2 or a public-only connection fails closed to loopback.
    """
    try:
        def read(*args):
            return json.loads(subprocess.check_output(['ip', '-json', *args], text=True, timeout=3))
        routes = sorted(read('-4', 'route', 'show', 'default'), key=lambda row: row.get('metric', 0))
        result = []
        for route in routes:
            device = route.get('dev', '')
            if not device or device.startswith(('lo', 'docker', 'br-', 'veth', 'tun', 'tap', 'wg', 'tailscale')):
                continue
            for interface in read('-4', 'address', 'show', 'dev', device):
                for item in interface.get('addr_info', []):
                    host = item.get('local', '')
                    if item.get('family') != 'inet' or item.get('scope') != 'global':
                        continue
                    network = str(ipaddress.ip_network(f"{host}/{item['prefixlen']}", strict=False))
                    if private_lan(host, network) and not any(row['host'] == host for row in result):
                        result.append(dict(name=device, host=host, subnet=network))
        return result
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return []


def access_config(config, interfaces=None):
    # Preserve explicit local-only choices made before the settings page existed.
    enabled = config.get('network_enabled', config.get('listen_host', '') not in ('127.0.0.1', '::1'))
    port = int(config.get('port', 7860))
    if not 1024 <= port <= 65535:
        raise ValueError('Inflect port must be between 1024 and 65535.')
    if 'network_enabled' not in config and config.get('listen_host'):
        host = config['listen_host']
        subnet = config.get('lan_network', '') if enabled else ''
        if enabled and not private_lan(host, subnet):
            raise ValueError('The configured address must be on a private IPv4 LAN.')
        return dict(network_enabled=bool(enabled), listen_host=host, lan_network=subnet,
                    allowed_hosts=[host, '127.0.0.1', 'localhost'], port=port,
                    public_url=f'http://{host}:{port}')
    interfaces = lan_interfaces() if interfaces is None else interfaces
    preferred = config.get('network_interface', config.get('listen_host'))
    selected = next((row for row in interfaces if row['host'] == preferred or row['name'] == preferred), None)
    if selected is None and not config.get('network_interface'):
        selected = next(iter(interfaces), None)
    host = selected['host'] if enabled and selected else '127.0.0.1'
    return dict(network_enabled=bool(enabled), listen_host=host,
                lan_network=selected['subnet'] if enabled and selected else '',
                allowed_hosts=[host, '127.0.0.1', 'localhost'], port=port,
                public_url=f'http://{host}:{port}')
