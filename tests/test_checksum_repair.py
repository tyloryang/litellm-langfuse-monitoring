import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
import repair_offline_archive_lf as repair
import repair_offline_checksum_files as standalone
import create_offline_bundle_lf_patch as patch_create
import apply_offline_bundle_lf as patch_apply
import build_full_chain_update as supplement


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_tar(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


class ChecksumRepairTest(unittest.TestCase):
    def fixture(self, directory, corrupt=False):
        payload = b'original-monitoring-image'
        monsum = ('{}  images.tar.gz\r\n'.format(sha(payload))).encode()
        monitor = make_tar({'monitoring-amd64-20261116/images.tar.gz': payload,
                            'monitoring-amd64-20261116/SHA256SUMS': monsum})
        gateway = b'original-gateway-image'
        checksums = ('{}  gateway.tar.gz\r\n'.format(sha(gateway))).encode()
        monlist = ('{}  monitoring-amd64-20261116.tar\r\n'.format(sha(monitor))).encode()
        files = {'downloads/SHA256SUMS': checksums,
                 'downloads/MONITORING-SHA256SUMS': monlist,
                 repair.MONITOR: monitor, 'downloads/gateway.tar.gz': gateway}
        handoff = repair.manifest_bytes({name: sha(data) for name, data in files.items()})
        if corrupt:
            files['downloads/gateway.tar.gz'] = b'corrupted image'
        data = make_tar({**{'llm-handoff/' + k: v for k, v in files.items()},
                         'llm-handoff/HANDOFF-SHA256SUMS': handoff})
        source = directory / 'original.tar'
        source.write_bytes(data)
        return source, gateway, monitor

    def test_repaired_archive_updates_all_nested_checksum_levels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, gateway, old_monitor = self.fixture(root)
            with patch.object(repair, 'PART_BYTES', 4096), patch.object(repair, 'CHUNK', 1024):
                repair.repair(source, root / 'release')
            out = root / 'release'
            manifest = json.loads((out / 'BUNDLE-LF-MANIFEST.json').read_text())
            data = b''.join((out / p['name']).read_bytes() for p in manifest['parts'])
            self.assertEqual(sha(data), manifest['archive_sha256'])
            for path in out.iterdir():
                if path.name.endswith('.sha256') or path.name.endswith('SHA256SUMS'):
                    self.assertNotIn(b'\r', path.read_bytes())
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                handoff = repair.parse_manifest(archive.extractfile('llm-handoff/HANDOFF-SHA256SUMS').read())
                for name, digest in handoff.items():
                    self.assertEqual(sha(archive.extractfile('llm-handoff/' + name).read()), digest)
                self.assertEqual(archive.extractfile('llm-handoff/downloads/gateway.tar.gz').read(), gateway)
                monbytes = archive.extractfile('llm-handoff/' + repair.MONITOR).read()
                self.assertNotEqual(sha(monbytes), sha(old_monitor))
                monlist = repair.parse_manifest(archive.extractfile('llm-handoff/downloads/MONITORING-SHA256SUMS').read())
                self.assertEqual(monlist['monitoring-amd64-20261116.tar'], sha(monbytes))
                with tarfile.open(fileobj=io.BytesIO(monbytes)) as monitor:
                    checksums = monitor.extractfile('monitoring-amd64-20261116/SHA256SUMS').read()
                    self.assertNotIn(b'\r', checksums)
                    self.assertEqual(repair.parse_manifest(checksums)['images.tar.gz'], sha(b'original-monitoring-image'))

    def test_corrupt_input_rejected_and_standalone_output_preserves_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, _, _ = self.fixture(root, corrupt=True)
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                repair.repair(source, root / 'broken')
            original = root / 'sample.sha256'
            original.write_bytes((sha(b'a') + '  sample\r\n').encode())
            standalone.repair_files([original], root / 'fixed')
            fixed = (root / 'fixed/sample.sha256').read_bytes()
            self.assertNotIn(b'\r', fixed)
            self.assertEqual(repair.parse_manifest(original.read_bytes()), repair.parse_manifest(fixed))
            with self.assertRaises(FileExistsError):
                standalone.repair_files([original], root / 'fixed')

    def test_sparse_patch_validates_source_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / 'old.tar'
            new = root / 'new.tar'
            old.write_bytes(b'abcdefghijklmno')
            new.write_bytes(b'abXXefghijYlmno')
            with patch.object(patch_create, 'CHUNK', 3), patch.object(patch_apply, 'CHUNK', 2):
                info = patch_create.create_patch(old, new)
                result = root / 'result.tar'
                patch_apply.apply_patch(old, result, info)
                self.assertEqual(result.read_bytes(), new.read_bytes())
                with self.assertRaises(FileExistsError):
                    patch_apply.apply_patch(old, result, info)
                damaged = root / 'damaged.tar'
                damaged.write_bytes(b'abcdefghijklmnp')
                missing = root / 'must-not-exist.tar'
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    patch_apply.apply_patch(damaged, missing, info)
                self.assertFalse(missing.exists())
                self.assertFalse((root / 'must-not-exist.tar.incomplete').exists())

    def test_supplement_builder_writes_lf_checksums_on_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'docs/kylin-v10-offline-assets').mkdir(parents=True)
            for name in supplement.DOCS:
                (root / 'docs' / name).write_bytes(('fixture ' + name).encode())
            for name in supplement.FILES:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(('fixture ' + name).encode())
            with patch.object(supplement, 'ROOT', root), patch('sys.stdout', new_callable=io.StringIO):
                supplement.main()
            base = root / '.runtime/full-chain-release'
            checksum = (base / (supplement.NAME + '.sha256')).read_bytes()
            self.assertNotIn(b'\r', checksum)
            self.assertEqual(repair.parse_manifest(checksum)[supplement.NAME], sha((base / supplement.NAME).read_bytes()))
            with tarfile.open(base / supplement.NAME, 'r:gz') as tar:
                inner = tar.extractfile(supplement.PREFIX + '/MANUAL-UPDATE-SHA256SUMS').read()
                self.assertNotIn(b'\r', inner)
                for name, digest in repair.parse_manifest(inner).items():
                    self.assertEqual(sha(tar.extractfile(supplement.PREFIX + '/' + name).read()), digest)

if __name__ == '__main__':
    unittest.main()
