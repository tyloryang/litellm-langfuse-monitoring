#!/usr/bin/env python3
"""Build the amd64 gateway/monitoring handoff for an existing Strata service.

The archive intentionally excludes model weights, Strata, site secrets, data,
and target-specific OS dependencies. Run from the repository root.
"""
import argparse
import hashlib
import io
import os
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAME = 'kylin-v10-gateway-reuse-amd64-20261116.tar'
MAIN = 'nginx-litellm-langfuse-amd64-centos7-candidate-20261116-v3.tar.gz'
MON = 'monitoring-amd64-20261116.tar'
VALIDATED = 'source-and-docs-20261116-validated.tar.gz'
PINNED = {
    MAIN: '18d0c72ba47b918951927e5aae37b59f1a6aa39e31121c7ca6a1c5dd4ddaaf3d',
    MON: 'ee5707541d089e19840cd23297089e1d659f2f612eb606de6eb179183537cb12',
    VALIDATED: 'ca92bcd443f237929abe784d743952542d5d4fccdc6ba6ce2721231a48cb234b',
}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def add_bytes(tar, name, data):
    info = tarfile.TarInfo('llm-handoff/' + name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    tar.addfile(info, io.BytesIO(data))


class HashWriter:
    def __init__(self, file):
        self.file = file
        self.hash = hashlib.sha256()

    def write(self, data):
        n = self.file.write(data)
        if n != len(data):
            raise OSError('Short write while building archive')
        self.hash.update(data)
        return n

    def flush(self):
        self.file.flush()


def build_archive(output, files, generated):
    """Create exclusively, checking source stability and every input digest."""
    if output.exists() or output.with_suffix(output.suffix + '.incomplete').exists():
        raise FileExistsError('Output or incomplete archive already exists')
    records = []
    snapshots = {}
    for archive_name, path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Expected regular source file: {}'.format(path))
        before = path.stat()
        sha = digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Source changed during hashing: {}'.format(path))
        if path.name in PINNED and sha != PINNED[path.name]:
            raise ValueError('Pinned artifact checksum mismatch: {}'.format(path.name))
        snapshots[archive_name] = (path, before.st_size, before.st_mtime_ns)
        records.append((archive_name, sha))
    for archive_name, data in generated:
        records.append((archive_name, hashlib.sha256(data).hexdigest()))
    names = [n for n, _ in records]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate archive entry')
    manifest = ''.join('{}  {}\n'.format(sha, name) for name, sha in sorted(records)).encode('utf8')
    temp = output.with_suffix(output.suffix + '.incomplete')
    with temp.open('xb') as file:
        writer = HashWriter(file)
        with tarfile.open(fileobj=writer, mode='w|', format=tarfile.PAX_FORMAT) as tar:
            for archive_name, path in files:
                _, size, modified = snapshots[archive_name]
                before = path.stat()
                if (before.st_size, before.st_mtime_ns) != (size, modified):
                    raise ValueError('Source changed before packaging: {}'.format(path))
                info = tarfile.TarInfo('llm-handoff/' + archive_name)
                info.size = size
                info.mode = 0o644
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                with path.open('rb') as source:
                    tar.addfile(info, source)
                after = path.stat()
                if (after.st_size, after.st_mtime_ns) != (size, modified):
                    raise ValueError('Source changed during packaging: {}'.format(path))
                print('Packed:', archive_name, flush=True)
            for archive_name, data in generated:
                add_bytes(tar, archive_name, data)
            add_bytes(tar, 'HANDOFF-SHA256SUMS', manifest)
        file.flush()
        os.fsync(file.fileno())
        sha = writer.hash.hexdigest()
    if output.exists():
        raise FileExistsError('Output appeared while building; preserving incomplete archive')
    os.rename(temp, output)
    return sha, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / NAME
    checksums = output.with_suffix(output.suffix + '.sha256')
    if checksums.exists():
        raise SystemExit('Checksum file already exists; refusing overwrite')
    release = ROOT / 'offline-bundle/release-20261116-compressed'
    downloads = {
        MAIN: ROOT / 'offline-bundle' / MAIN,
        MON: ROOT / 'offline-bundle' / MON,
        VALIDATED: release / VALIDATED,
        'SHA256SUMS': release / 'SHA256SUMS',
        'VALIDATED-SHA256SUMS': release / 'VALIDATED-SHA256SUMS',
        'MONITORING-SHA256SUMS': ROOT / 'offline-bundle/MONITORING-SHA256SUMS',
    }
    files = [('downloads/' + name, path) for name, path in downloads.items()]
    docs = [
        'kylin-v10-reuse-offline-bundle-20261116.md',
        'kylin-v10-full-offline-deployment-20261116.md',
        'offline-release-20261116.md', 'offline-monitoring-addon-20261116.md',
        'cloud-strata-262k-tuning-20261116.md', 'cloud-strata-cpa-access-20261116.md',
        'cloud-real-inference-20261116.md', 'cloud-strata-json-log-audit-20261116.md',
        'cloud-strata-memlock-deployment-20261116.md',
        'grafana-formulas.md', 'offline-acceptance-template.md',
    ]
    package_doc = ROOT / 'docs/kylin-v10-package-preparation.md'
    if package_doc.is_file():
        docs.append(package_doc.name)
    files.extend(('manual/docs/' + name, ROOT / 'docs' / name) for name in docs)
    image_dir = ROOT / 'docs/kylin-v10-offline-assets'
    for image in sorted(image_dir.iterdir()):
        if image.suffix.lower() in ('.png', '.jpg') and image.is_file():
            files.append(('manual/docs/kylin-v10-offline-assets/' + image.name, image))
    for rel in ('nginx/nginx.conf', 'litellm/config.yaml', 'litellm/token_router_config.yaml',
                'deploy/cloud-monitoring/strata-proxy.conf', 'ops/adapt_monitoring_existing_strata.py',
                'deploy/strata/docker-compose.example.yml',
                'examples/strata-iq3_xxs-cloud-4slot.example.json',
                'ops/kylin-target-inventory.sh', 'ops/kylin-download-rpms.sh',
                'ops/kylin-rpm-offline-check.sh', 'ops/kylin-verify-nvidia-metadata.py'):
        files.append(('manual/' + rel, ROOT / rel))
    candidates = ROOT / 'offline-bundle/kylin-v10-system-rpm-candidates-20261116'
    if digest(candidates / 'SHA256SUMS') != 'fa813af2d36c88f4ad8b594a6a81e976e2242b47472a3ca1da45e85984269d2f':
        raise ValueError('Candidate RPM manifest checksum does not match the reviewed copy')
    approved = set()
    for row in (candidates / 'SHA256SUMS').read_text(encoding='utf8').splitlines():
        expected, name = row.split('  ', 1)
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or name in approved or digest(candidates / rel) != expected:
            raise ValueError('Candidate RPM file checksum mismatch: {}'.format(name))
        approved.add(name)
    found = {path.relative_to(candidates).as_posix() for path in candidates.rglob('*') if path.is_file()}
    if found != approved | {'SHA256SUMS'}:
        raise ValueError('Candidate RPM directory has missing or unreviewed files')
    for name in sorted(found):
        files.append(('system/kylin-v10-system-rpm-candidates-20261116/' + name, candidates / name))
    dashboard = ROOT / 'grafana/dashboards/litellm-gpu-overview.json'
    formulas = ROOT / 'docs/grafana-formulas.md'
    files.extend([('patches/litellm-gpu-overview.json', dashboard), ('patches/grafana-formulas.md', formulas)])
    patch_sums = ('{}  litellm-gpu-overview.json\n{}  grafana-formulas.md\n'.format(
        digest(dashboard), digest(formulas))).encode('ascii')
    manual_sums = ''.join('{}  {}\n'.format(digest(path), name[len('manual/'):])
                          for name, path in files if name.startswith('manual/')).encode('utf8')
    readme = ('离线交付入口：manual/docs/kylin-v10-reuse-offline-bundle-20261116.md\n'
              '先在此目录执行 sha256sum -c HANDOFF-SHA256SUMS。\n'
              '本包复用既有 Strata、模型和容器环境；不含它们或真实凭据。\n'
              'system/ 仅为宿主工具候选，不是已验证的麒麟 RPM 依赖闭包，不应覆盖现有环境。\n'
              '目标麒麟的断网部署、真实推理、监控和备份恢复尚需现场验收。\n').encode('utf8')
    generated = [('README.txt', readme), ('patches/PATCH-SHA256SUMS', patch_sums),
                 ('manual/MANUAL-SHA256SUMS', manual_sums)]
    sha, records = build_archive(output, files, generated)
    with checksums.open('x', encoding='ascii', newline='') as file:
        file.write('{}  {}\n'.format(sha, output.name))
    print('Archive:', output, 'bytes:', output.stat().st_size, 'files:', len(records) + 1)
    print('SHA256:', sha)
    if output.stat().st_size >= 2 * 1024**3:
        print('NOTICE: archive exceeds the GitHub Release single-asset size limit; split before upload.')


if __name__ == '__main__':
    main()
