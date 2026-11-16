#!/usr/bin/env python3
"""Create a sparse patch between equal-size, previously verified bundles."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from apply_offline_bundle_lf import CHUNK, validate_patch


def create_patch(source, target):
    if source.stat().st_size != target.stat().st_size:
        raise ValueError('Bundle sizes differ')
    oldhash, newhash = hashlib.sha256(), hashlib.sha256()
    changes = []
    offset = 0
    run_start = None
    before, after = bytearray(), bytearray()
    def finish():
        nonlocal run_start, before, after
        if run_start is not None:
            changes.append({'offset': run_start, 'before': base64.b64encode(before).decode('ascii'),
                            'after': base64.b64encode(after).decode('ascii')})
            run_start = None
            before, after = bytearray(), bytearray()
    with source.open('rb') as a, target.open('rb') as b:
        while True:
            old, new = a.read(CHUNK), b.read(CHUNK)
            if not old and not new:
                break
            if len(old) != len(new):
                raise ValueError('Bundles changed during reading')
            oldhash.update(old)
            newhash.update(new)
            if old == new:
                finish()
            else:
                for i, (x, y) in enumerate(zip(old, new)):
                    if x != y:
                        if run_start is None:
                            run_start = offset + i
                        before.append(x)
                        after.append(y)
                    else:
                        finish()
            offset += len(old)
    finish()
    info = {'schema': 1, 'source_bytes': offset, 'source_sha256': oldhash.hexdigest(),
            'target_sha256': newhash.hexdigest(), 'changes': changes}
    validate_patch(info)
    return info


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('patch', type=Path)
    args = parser.parse_args()
    info = create_patch(args.source, args.target)
    with args.patch.open('xb') as out:
        out.write((json.dumps(info, separators=(',', ':')) + '\n').encode('ascii'))
    print('Created sparse patch:', args.patch.name, 'changes:', len(info['changes']))
