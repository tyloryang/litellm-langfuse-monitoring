import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
import offline_bundle as bundle


class OfflineArtifacts(unittest.TestCase):
    """Artifact trust-boundary tests; fixtures are not deployable Docker archives."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "bundle"
        self.root.mkdir()
        for name in bundle.REQUIRED_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test artifact\n")
        wheels = self.root / "wheelhouse"
        wheels.mkdir()
        (wheels / "fixture-1.0-py3-none-any.whl").write_bytes(b"fixture only")
        self.images = [{"env_key": key, "offline_tag": "llm-offline/" + bundle.IMAGE_KEYS[key] + ":locked-amd64",
                        "image_id": "sha256:" + f"{i:064x}", "platform": "linux/amd64"}
                       for i, key in enumerate(sorted(bundle.REQUIRED_IMAGES), 1)]
        (self.root / "images.lock.json").write_text(json.dumps(self.images))
        (self.root / "images.env").write_text("".join(i["env_key"] + "=" + i["offline_tag"] + "\n" for i in self.images))

    def seal(self):
        return bundle.seal(self.root, "linux/amd64", "3.12")

    def test_round_trip_does_not_claim_deployment_acceptance(self):
        self.assertFalse(self.seal()["deployment_accepted"])
        result = bundle.verify(self.root)
        self.assertEqual(result["stage"], "ARTIFACTS_VERIFIED")
        self.assertEqual(result["images"], 9)
        self.assertFalse(result["deployment_accepted"])
        self.assertFalse(result["loaded_images_verified"])

    def test_changed_payload_fails_integrity(self):
        self.seal()
        (self.root / "source.tar").write_bytes(b"changed file!\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            bundle.verify(self.root)

    def test_unlisted_files_cannot_silently_enter_release(self):
        self.seal()
        (self.root / "surprise.whl").write_bytes(b"unsealed")
        with self.assertRaisesRegex(ValueError, "Missing or unexpected"):
            bundle.verify(self.root)

    def test_manifest_cannot_reference_outside_bundle(self):
        self.seal()
        path = self.root / "manifest.json"
        data = json.loads(path.read_text())
        data["files"]["../outside"] = {"bytes": 0, "sha256": "0" * 64}
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Unsafe artifact path"):
            bundle.verify(self.root)

    def test_runtime_credentials_are_kept_outside_bundle(self):
        (self.root / ".env").write_text("EXAMPLE_SECRET=fixture")
        with self.assertRaisesRegex(ValueError, "credentials/private keys"):
            self.seal()

    def test_symlink_cannot_hash_files_outside_bundle(self):
        target = self.root.parent / "external.txt"
        target.write_text("external")
        try:
            (self.root / "linked").symlink_to(target)
        except OSError:
            self.skipTest("Symlinks unavailable on this platform")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.seal()

    def test_missing_host_material_declaration_fails(self):
        (self.root / "host/README.md").unlink()
        with self.assertRaisesRegex(ValueError, "Missing required artifact"):
            self.seal()

    def test_loaded_image_must_match_full_id(self):
        self.seal()
        with patch.object(bundle, "inspect_image", return_value={"Id": "sha256:" + "f" * 64, "Os": "linux", "Architecture": "amd64"}):
            with self.assertRaisesRegex(ValueError, "Loaded image differs"):
                bundle.verify(self.root, loaded_images=True)

    def test_architecture_failure_happens_before_tag_or_save(self):
        arguments = argparse.Namespace(images_env=self.root / "images.env", bundle=self.root.parent / "export", platform="linux/amd64")
        with patch.object(bundle, "inspect_image", return_value={"Os": "linux", "Architecture": "arm64"}), patch.object(bundle, "run") as run:
            with self.assertRaisesRegex(ValueError, "platform mismatch"):
                bundle.export_images(arguments)
            run.assert_not_called()

    def test_image_source_file_cannot_contain_runtime_keys(self):
        path = self.root / "images.env"
        path.write_text(path.read_text() + "LANGFUSE_SECRET_KEY=fixture\n")
        with self.assertRaisesRegex(ValueError, "IMAGE assignments only"):
            bundle.image_env(path)


if __name__ == "__main__":
    unittest.main()
