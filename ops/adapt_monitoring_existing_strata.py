#!/usr/bin/env python3
"""Adapt a fresh cloud-monitoring bundle to an existing host Strata service.

The Strata key is requested later by prepare.py and never passed as an argument.
This script handles only the exact monitoring bundle shipped on 2026-11-16.
"""
import argparse
import ipaddress
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Fresh source/deploy/cloud-monitoring directory')
    parser.add_argument('--host-ip', required=True, help='Host IPv4 reachable from containers')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    try:
        address = ipaddress.IPv4Address(args.host_ip)
    except ValueError:
        parser.error('--host-ip must be an IPv4 address')
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        parser.error('--host-ip must be a unicast, non-loopback address reachable from containers')
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    root = args.directory.resolve(strict=True)
    if (root / '.env').exists() or (root / 'secrets').exists():
        raise SystemExit('Private monitoring configuration already exists; preserve it and adapt manually')
    target = '{}:{}'.format(address, args.port)
    files = {
        'prometheus.yml': ('targets: ["strata:8080"]', 'targets: ["{}"]'.format(target)),
        'docker-compose.yml': ('STRATA_METRICS_URL: http://strata:8080/metrics',
                               'STRATA_METRICS_URL: http://{}/metrics'.format(target)),
        'prepare.py': ("    strata_token = docker('exec', 'llm-a100-strata-1', 'printenv', 'API_KEY')",
                       "    import getpass\n"
                       "    strata_token = getpass.getpass('Strata API key: ')\n"
                       "    if strata_token != getpass.getpass('Confirm Strata API key: '):\n"
                       "        raise SystemExit('Strata API keys do not match')"),
    }
    planned = []
    for name, (before, after) in files.items():
        path = root / name
        backup = root / (name + '.before-host-strata')
        if backup.exists():
            raise SystemExit('Backup exists for {}; inspect before proceeding'.format(name))
        content = path.read_text(encoding='utf-8')
        if content.count(before) != 1:
            raise SystemExit('{} is not the expected fresh monitoring bundle'.format(name))
        planned.append((path, backup, content, content.replace(before, after, 1)))
    # Backups are made before any replacement. If a write is interrupted, retain
    # them and inspect the directory instead of automatically overwriting it.
    for path, backup, content, _ in planned:
        with backup.open('x', encoding='utf-8', newline='') as handle:
            handle.write(content)
    for path, _, _, updated in planned:
        with path.open('w', encoding='utf-8', newline='') as handle:
            handle.write(updated)
    print('Prepared host Strata monitoring for {}. Original files have .before-host-strata backups.'.format(target))
    print('Run prepare.py interactively; verify the key and container-to-host access before starting monitoring.')


if __name__ == '__main__':
    main()
