#!/usr/bin/env python3
"""Apply a verified sparse LF repair patch to a copy of an offline bundle."""
import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

CHUNK = 4 * 1024 * 1024
MAX_PATCH_BYTES = 16 * 1024 * 1024


def validate_patch(info):
    if not isinstance(info, dict) or info.get('schema') != 1:
        raise ValueError('Unsupported patch schema')
    size = info.get('source_bytes')
    if type(size) is not int or size < 0:
        raise ValueError('Invalid source size')
    for key in ('source_sha256', 'target_sha256'):
        if not isinstance(info.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', info[key]):
            raise ValueError('Invalid archive checksum')
    entries = info.get('changes')
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValueError('Invalid patch entries')
    result = []
    end = 0
    total = 0
    for item in entries:
        if not isinstance(item, dict) or type(item.get('offset')) is not int:
            raise ValueError('Invalid patch offset')
        try:
            before = base64.b64decode(item['before'], validate=True)
            after = base64.b64decode(item['after'], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Invalid patch data') from exc
        offset = item['offset']
        if offset < end or not before or len(before) != len(after) or offset + len(before) > size:
            raise ValueError('Overlapping or invalid patch range')
        total += len(before) + len(after)
        if total > MAX_PATCH_BYTES:
            raise ValueError('Patch is too large')
        result.append((offset, before, after))
        end = offset + len(before)
    return result


def apply_patch(source, destination, info):
    changes = validate_patch(info)
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError('Source archive must be a regular file')
    if source.stat().st_size != info['source_bytes']:
        raise ValueError('Source archive size mismatch')
    partial = destination.with_name(destination.name + '.incomplete')
    if destination.exists() or partial.exists() or destination.resolve() == source.resolve():
        raise FileExistsError('Output already exists or overlaps source')
    oldhash, newhash = hashlib.sha256(), hashlib.sha256()
    created = False
    try:
        with source.open('rb') as inp, partial.open('xb') as out:
            created = True
            pos = 0
            def copy_count(count):
                while count:
                    data = inp.read(min(CHUNK, count))
                    if not data:
                        raise ValueError('Unexpected end of source')
                    out.write(data)
                    oldhash.update(data)
                    newhash.update(data)
                    count -= len(data)
            for offset, before, after in changes:
                copy_count(offset - pos)
                actual = inp.read(len(before))
                if actual != before:
                    raise ValueError('Patch does not match source at offset {}'.format(offset))
                oldhash.update(actual)
                out.write(after)
                newhash.update(after)
                pos = offset + len(before)
            copy_count(info['source_bytes'] - pos)
            if inp.read(1):
                raise ValueError('Source grew during patching')
            if oldhash.hexdigest() != info['source_sha256']:
                raise ValueError('Source archive checksum mismatch')
            if newhash.hexdigest() != info['target_sha256']:
                raise ValueError('Repaired archive checksum mismatch')
        partial.rename(destination)
    except BaseException:
        if created and partial.exists():
            partial.unlink()
        raise
    print('Repaired archive verified:', destination.name, newhash.hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.patch.stat().st_size > MAX_PATCH_BYTES:
        raise ValueError('Patch file is too large')
    apply_patch(args.source, args.output, json.loads(args.patch.read_text(encoding='utf8')))
