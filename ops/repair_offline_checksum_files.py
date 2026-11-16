#!/usr/bin/env python3
"""Write LF copies of SHA-256 lists, preserving every digest and path."""
import argparse
from pathlib import Path

from repair_offline_archive_lf import normalize_checksum_bytes, parse_manifest


def repair_files(sources, destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    planned = []
    seen = set()
    for source in sources:
        if source.is_symlink() or not source.is_file():
            raise ValueError('Expected a regular input file: ' + str(source))
        output = destination / source.name
        if source.name in seen or output.exists() or output == source.resolve():
            raise FileExistsError('Output exists or overlaps input: ' + str(output))
        seen.add(source.name)
        original = source.read_bytes()
        fixed = normalize_checksum_bytes(original)
        if parse_manifest(original) != parse_manifest(fixed):
            raise ValueError('Checksum records changed')
        planned.append((output, fixed))
    for output, data in planned:
        with output.open('xb') as f:
            f.write(data)
        print(output.name, 'written with LF')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('files', type=Path, nargs='+')
    args = parser.parse_args()
    repair_files(args.files, args.destination)
