#!/usr/bin/env python3
"""Create a checksum-compatible copy of the published handoff archive.

Only checksum manifests are normalized; every original member is verified
against the original HANDOFF-SHA256SUMS as it is copied. The source is never
modified. Split files and their checksums use LF on every operating system.
"""
import argparse
import copy
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

PREFIX = 'llm-handoff/'
MANIFEST = 'HANDOFF-SHA256SUMS'
NORMALIZE = {'downloads/SHA256SUMS', 'downloads/MONITORING-SHA256SUMS'}
MONITOR = 'downloads/monitoring-amd64-20261116.tar'
CHUNK = 4 * 1024 * 1024
PART_BYTES = 448 * 1024 * 1024
NAME = 'kylin-v10-gateway-reuse-amd64-20261116-lf.tar'


def parse_manifest(raw):
    result = {}
    for line in raw.splitlines():
        m = re.fullmatch(rb'([0-9a-f]{64})  ([^\x00-\x1f]+)', line)
        if not m:
            raise ValueError('Malformed checksum record')
        name = m.group(2).decode('utf-8')
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or name in result:
            raise ValueError('Unsafe or duplicate checksum path')
        result[name] = m.group(1).decode('ascii')
    return result


def manifest_bytes(items):
    return ''.join('{}  {}\n'.format(digest, name)
                   for name, digest in sorted(items.items())).encode('utf8')


def normalize_checksum_bytes(raw):
    fixed = raw.replace(b'\r\n', b'\n')
    if b'\r' in fixed:
        raise ValueError('Unexpected bare CR in checksum manifest')
    parse_manifest(fixed)
    if fixed and not fixed.endswith(b'\n'):
        fixed += b'\n'
    return fixed


class VerifyReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.source.read(size)
        self.digest.update(data)
        return data


def repair_monitoring(source, expected_digest, output):
    digest = hashlib.sha256()
    while True:
        data = source.read(CHUNK)
        if not data:
            break
        digest.update(data)
    if digest.hexdigest() != expected_digest:
        raise ValueError('Original monitoring archive checksum mismatch')
    source.seek(0)
    prefix = 'monitoring-amd64-20261116/'
    checksum_name = prefix + 'SHA256SUMS'
    with tarfile.open(fileobj=source, mode='r:') as original:
        members = original.getmembers()
        files = [m for m in members if m.isfile()]
        names = [m.name for m in members]
        if len(names) != len(set(names)) or not all(
                (m.isfile() or m.isdir()) and m.name.startswith(prefix.rstrip('/')) for m in members):
            raise ValueError('Unexpected monitoring archive layout')
        checksum = original.extractfile(checksum_name).read()
        expected = parse_manifest(checksum)
        if {m.name for m in files} != {prefix + name for name in expected} | {checksum_name}:
            raise ValueError('Monitoring archive and checksums differ')
        with output.open('xb') as raw, tarfile.open(fileobj=raw, mode='w|', format=tarfile.PAX_FORMAT) as repaired:
            for member in members:
                header = copy.copy(member)
                if member.isdir():
                    repaired.addfile(header)
                elif member.name == checksum_name:
                    data = normalize_checksum_bytes(checksum)
                    header.size = len(data)
                    repaired.addfile(header, io.BytesIO(data))
                else:
                    name = member.name[len(prefix):]
                    with original.extractfile(member) as f:
                        reader = VerifyReader(f)
                        repaired.addfile(header, reader)
                        if reader.digest.hexdigest() != expected[name]:
                            raise ValueError('Monitoring checksum mismatch: ' + name)
    digest = hashlib.sha256()
    with output.open('rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def repair(source, output):
    source, output = source.resolve(), output.resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError('Source must be a regular archive')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / NAME
    partial = output / (NAME + '.incomplete')
    monitoring_partial = output / '.monitoring-lf.tar.incomplete'
    paths = [archive, partial, monitoring_partial, output / (NAME + '.sha256'),
             output / 'BUNDLE-LF-PARTS-SHA256SUMS', output / 'BUNDLE-LF-MANIFEST.json']
    if any(p.exists() for p in paths) or list(output.glob(NAME + '.part*')):
        raise FileExistsError('Output directory already contains release artifacts')
    with tarfile.open(source, 'r:') as original:
        members = original.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or not all(m.isfile() and m.name.startswith(PREFIX) for m in members):
            raise ValueError('Unexpected archive layout')
        expected = parse_manifest(original.extractfile(PREFIX + MANIFEST).read())
        if set(names) != {PREFIX + name for name in expected} | {PREFIX + MANIFEST}:
            raise ValueError('Archive and checksum manifest differ')
        if not (NORMALIZE | {MONITOR}) <= expected.keys():
            raise ValueError('Expected manifests not found')
        with original.extractfile(PREFIX + MONITOR) as f:
            monitor_digest = repair_monitoring(f, expected[MONITOR], monitoring_partial)
        normalized = {}
        for name in NORMALIZE:
            data = original.extractfile(PREFIX + name).read()
            if hashlib.sha256(data).hexdigest() != expected[name]:
                raise ValueError('Original checksum mismatch: ' + name)
            fixed = normalize_checksum_bytes(data)
            if name == 'downloads/MONITORING-SHA256SUMS':
                entries = parse_manifest(fixed)
                if entries != {'monitoring-amd64-20261116.tar': expected[MONITOR]}:
                    raise ValueError('Monitoring checksum list does not match outer archive')
                fixed = manifest_bytes({'monitoring-amd64-20261116.tar': monitor_digest})
            normalized[name] = fixed
        revised = dict(expected)
        revised.update({name: hashlib.sha256(data).hexdigest() for name, data in normalized.items()})
        revised[MONITOR] = monitor_digest
        new_manifest = manifest_bytes(revised)
        with partial.open('xb') as raw, tarfile.open(fileobj=raw, mode='w|', format=tarfile.PAX_FORMAT) as result:
            for member in members:
                name = member.name[len(PREFIX):]
                header = copy.copy(member)
                if name == MANIFEST or name in normalized:
                    data = new_manifest if name == MANIFEST else normalized[name]
                    header.size = len(data)
                    result.addfile(header, io.BytesIO(data))
                elif name == MONITOR:
                    header.size = monitoring_partial.stat().st_size
                    with monitoring_partial.open('rb') as f:
                        result.addfile(header, f)
                else:
                    with original.extractfile(member) as f:
                        reader = VerifyReader(f)
                        result.addfile(header, reader)
                        if reader.digest.hexdigest() != expected[name]:
                            raise ValueError('Original checksum mismatch: ' + name)
    partial.rename(archive)
    monitoring_partial.unlink()
    full = hashlib.sha256()
    parts = []
    with archive.open('rb') as inp:
        number = 1
        while True:
            first = inp.read(min(CHUNK, PART_BYTES))
            if not first:
                break
            part = output / (NAME + '.part{:02d}'.format(number))
            digest = hashlib.sha256()
            count = 0
            with part.open('xb') as out:
                block = first
                while block:
                    out.write(block)
                    digest.update(block)
                    full.update(block)
                    count += len(block)
                    if count == PART_BYTES:
                        break
                    block = inp.read(min(CHUNK, PART_BYTES - count))
            parts.append({'name': part.name, 'bytes': count, 'sha256': digest.hexdigest()})
            number += 1
    overall = full.hexdigest()
    (output / (NAME + '.sha256')).write_bytes(('{}  {}\n'.format(overall, NAME)).encode('ascii'))
    (output / 'BUNDLE-LF-PARTS-SHA256SUMS').write_bytes(manifest_bytes({p['name']: p['sha256'] for p in parts}))
    manifest = {'archive': NAME, 'archive_bytes': archive.stat().st_size,
                'archive_sha256': overall, 'parts': parts,
                'normalized_members': sorted(NORMALIZE | {MONITOR + ':SHA256SUMS'})}
    (output / 'BUNDLE-LF-MANIFEST.json').write_bytes((json.dumps(manifest, indent=2) + '\n').encode('utf8'))
    print('Created:', archive.name, 'bytes:', archive.stat().st_size, 'sha256:', overall, 'parts:', len(parts))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    repair(args.source, args.output)
