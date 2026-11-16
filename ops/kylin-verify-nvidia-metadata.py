#!/usr/bin/env python3
"""Verify candidate RPM hashes through a separately signature-verified repomd.xml."""
import gzip
import hashlib
import pathlib
import sys
import xml.etree.ElementTree as ET

NS = {"r": "http://linux.duke.edu/metadata/repo", "c": "http://linux.duke.edu/metadata/common"}
NAMES = {"nvidia-container-toolkit", "nvidia-container-toolkit-base", "libnvidia-container-tools", "libnvidia-container1"}

def digest(data, algorithm):
    return hashlib.new(algorithm, data).hexdigest()

def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 kylin-verify-nvidia-metadata.py /path/to/nvidia")
    root = pathlib.Path(sys.argv[1])
    data = ET.parse(root / "repodata/repomd.xml").find("r:data[@type='primary']", NS)
    checksum = data.find("r:checksum", NS)
    compressed = (root / "repodata/primary.xml.gz").read_bytes()
    if digest(compressed, checksum.get("type")) != checksum.text:
        sys.exit("FAIL: primary metadata checksum")
    packages = ET.fromstring(gzip.decompress(compressed))
    found = set()
    for pkg in packages.findall("c:package", NS):
        name = pkg.findtext("c:name", namespaces=NS)
        version = pkg.find("c:version", NS)
        if (name not in NAMES or version.get("ver") != "1.17.8"
                or version.get("rel") != "1" or pkg.findtext("c:arch", namespaces=NS) != "x86_64"):
            continue
        if name in found:
            sys.exit("FAIL: duplicate package in metadata: " + name)
        location = pkg.find("c:location", NS).get("href")
        path = root / pathlib.PurePosixPath(location).name
        checksum = pkg.find("c:checksum", NS)
        if digest(path.read_bytes(), checksum.get("type")) != checksum.text:
            sys.exit("FAIL: package checksum: " + str(path))
        found.add(name)
        print("OK", path.name)
    if found != NAMES:
        sys.exit("FAIL: missing package metadata: " + ", ".join(sorted(NAMES - found)))
    print("PASS: metadata checksum chain only; verify repomd.xml signature and key trust separately")

if __name__ == "__main__":
    main()
