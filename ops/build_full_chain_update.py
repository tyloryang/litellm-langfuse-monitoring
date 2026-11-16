#!/usr/bin/env python3
"""Build the additive offline documentation and trace-validation supplement."""
import gzip
import hashlib
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'llm-offline-full-chain-update-20261116'
NAME = 'offline-full-chain-update-20261116.tar.gz'
DOCS = (
    'offline-deployment-README.md', 'kylin-v10-reuse-offline-bundle-20261116.md',
    'kylin-v10-full-offline-deployment-20261116.md', 'kylin-v10-offline-operations-step-by-step.md',
    'offline-release-20261116.md', 'offline-monitoring-addon-20261116.md',
    'cloud-strata-262k-tuning-20261116.md', 'cloud-strata-cpa-access-20261116.md',
    'cloud-real-inference-20261116.md', 'cloud-strata-json-log-audit-20261116.md',
    'cloud-strata-memlock-deployment-20261116.md', 'grafana-formulas.md',
    'offline-acceptance-template.md', 'kylin-v10-package-preparation.md',
)
FILES = (
    'nginx/nginx.conf', 'litellm/config.yaml', 'litellm/token_router_config.yaml',
    'deploy/cloud-monitoring/strata-proxy.conf', 'ops/adapt_monitoring_existing_strata.py',
    'deploy/strata/docker-compose.example.yml', 'examples/strata-iq3_xxs-cloud-4slot.example.json',
    'examples/offline_tool_trace_check.py', 'ops/kylin-target-inventory.sh',
    'ops/kylin-download-rpms.sh', 'ops/kylin-rpm-offline-check.sh',
    'ops/kylin-verify-nvidia-metadata.py',
)


def main():
    output_dir = ROOT / '.runtime' / 'full-chain-release'
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / NAME
    checksum = output_dir / (NAME + '.sha256')
    if archive.exists() or checksum.exists():
        raise FileExistsError('Output already exists; preserve published artifacts')
    sources = {'README.md': ROOT / 'docs/offline-deployment-README.md'}
    sources.update(('manual/docs/' + name, ROOT / 'docs' / name) for name in DOCS)
    sources.update(('manual/' + name, ROOT / name) for name in FILES)
    for path in sorted((ROOT / 'docs/kylin-v10-offline-assets').iterdir()):
        if path.is_file() and path.suffix.lower() in ('.jpg', '.png'):
            sources['manual/docs/kylin-v10-offline-assets/' + path.name] = path
    data = {}
    for name, path in sources.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or symlinked input: ' + str(path))
        data[name] = path.read_bytes()
    manifest = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n'
                       for name, value in sorted(data.items())).encode('utf-8')
    data['MANUAL-UPDATE-SHA256SUMS'] = manifest
    with archive.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as tar:
                for name, content in sorted(data.items()):
                    info = tarfile.TarInfo(PREFIX + '/' + name)
                    info.size = len(content)
                    info.mode = 0o644
                    info.mtime = 0
                    tar.addfile(info, io.BytesIO(content))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(sha + '  ' + NAME + '\n', encoding='ascii')
    print('archive:', archive, 'bytes:', archive.stat().st_size, 'files:', len(data), 'sha256:', sha)


if __name__ == '__main__':
    main()
