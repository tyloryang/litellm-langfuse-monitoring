import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Requires POSIX Bash")
class DDNPull(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = self.root / "calls.jsonl"
        self.source = "swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/fixture:1@sha256:" + "1" * 64
        self.target = "docker.io/library/fixture:1"
        self.mapping = self.root / "images.tsv"
        self.mapping.write_text("# platform=linux/amd64\n" + self.source + "\t" + self.target)
        self.script = Path(__file__).resolve().parents[1] / "ops/pull-from-ddn.sh"
        self.environment = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                                DOCKER_CALLS=str(self.calls), FIXTURE_PLATFORM="linux/amd64")
        fake_docker = self.root / "docker"
        fake_docker.write_text("#!" + sys.executable + "\n" + """import json, os, sys
from pathlib import Path
with Path(os.environ["DOCKER_CALLS"]).open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["image", "inspect"]:
    print(os.environ["FIXTURE_PLATFORM"] if sys.argv[-1] == "{{.Os}}/{{.Architecture}}" else "fixture metadata")
""")
        fake_docker.chmod(0o755)

    def run_pull(self, platform="linux/amd64"):
        result = subprocess.run(["bash", str(self.script), str(self.mapping), platform],
                                env=self.environment, capture_output=True, text=True)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result, calls

    def test_pulls_pinned_ddn_only_and_accepts_final_line_without_newline(self):
        result, calls = self.run_pull()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0], ["pull", "--platform", "linux/amd64", self.source])
        self.assertEqual([call for call in calls if call[0] == "tag"], [["tag", self.source, self.target]])
        self.assertEqual(len([call for call in calls if call[0] == "pull"]), 1)

    def test_mapping_platform_mismatch_fails_before_docker(self):
        result, calls = self.run_pull("linux/arm64")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_invalid_later_mapping_fails_before_any_pull(self):
        with self.mapping.open("a") as stream:
            stream.write("\nnginx:latest\tdocker.io/library/nginx:latest\n")
        result, calls = self.run_pull()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_pulled_platform_mismatch_never_tags_target(self):
        self.environment["FIXTURE_PLATFORM"] = "linux/arm64"
        result, calls = self.run_pull()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(call[0] == "pull" for call in calls))
        self.assertFalse(any(call[0] == "tag" for call in calls))


if __name__ == "__main__":
    unittest.main()
