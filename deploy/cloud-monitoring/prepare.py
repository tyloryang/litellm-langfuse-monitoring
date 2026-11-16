#!/usr/bin/env python3
"""Prepare private monitoring settings for the existing cloud Compose deployment."""
import argparse
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()

def private_value(value):
    if not value or any(c in value for c in "'\r\n"):
        raise ValueError('A required value is empty or cannot be represented safely in the Compose env file')
    return "'" + value + "'"

def write_new(path, data, mode):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(data)
    os.chmod(path, mode)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grafana-root-url', required=True, help='Externally visible HTTPS root URL')
    parser.add_argument('--certs-dir', required=True, help='Existing Nginx TLS certificate directory')
    parser.add_argument('--network', default='llm-a100_backend')
    parser.add_argument('--host-port', type=int, default=8802)
    args = parser.parse_args()
    env_path = ROOT / '.env'
    secret_dir = ROOT / 'secrets'
    if env_path.exists() or secret_dir.exists():
        raise SystemExit('Existing private configuration found; preserve and inspect it before rerunning')
    certs = Path(args.certs_dir).resolve()
    if not (certs / 'server.crt').is_file() or not (certs / 'server.key').is_file():
        raise SystemExit('TLS certificate or key not found in the specified directory')
    if not args.grafana_root_url.startswith('https://') or not args.grafana_root_url.endswith('/'):
        raise SystemExit('The external Grafana URL must be an HTTPS URL ending in /')
    if not 1 <= args.host_port <= 65535:
        raise SystemExit('Invalid host port')
    docker('network', 'inspect', args.network)
    litellm_token = docker('exec', 'llm-a100-litellm-1', 'printenv', 'LITELLM_MASTER_KEY')
    strata_token = docker('exec', 'llm-a100-strata-1', 'printenv', 'API_KEY')
    images = {
        'LITELLM_IMAGE': docker('inspect', '--format', '{{.Config.Image}}', 'llm-a100-litellm-1'),
        'NGINX_IMAGE': docker('inspect', '--format', '{{.Config.Image}}', 'llm-a100-nginx-1'),
    }
    values = {
        **images, 'STRATA_API_KEY': strata_token,
        'GRAFANA_ADMIN_USER': 'admin', 'GRAFANA_ADMIN_PASSWORD': secrets.token_urlsafe(24),
        'GRAFANA_ROOT_URL': args.grafana_root_url, 'GRAFANA_HOST_PORT': str(args.host_port),
        'NGINX_CERTS_DIR': str(certs), 'CLOUD_BACKEND_NETWORK': args.network,
    }
    env_text = ''.join(k + '=' + private_value(v) + '\n' for k, v in values.items())
    secret_dir.mkdir(mode=0o700)
    try:
        for filename, token in [('litellm_metrics_token', litellm_token), ('strata_api_key', strata_token)]:
            path = secret_dir / filename
            write_new(path, token + '\n', 0o400)
            # The Prometheus image runs as nobody (65534); the bind-mounted file must be readable.
            os.chown(path, 65534, 65534)
        write_new(env_path, env_text, 0o600)
    except Exception:
        raise SystemExit('Preparation stopped; inspect the private configuration directory before retrying')
    print('Private monitoring settings prepared; credentials were not printed.')

if __name__ == '__main__':
    main()
