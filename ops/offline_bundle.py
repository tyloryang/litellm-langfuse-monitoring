#!/usr/bin/env python3
"""Export local images and seal/verify offline artifacts using only the stdlib.

This tool never pulls images or installs packages. Artifact verification is
not a claim that a fresh deployment or its network isolation was accepted.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

IMAGE_KEYS = {
    "POSTGRES_IMAGE": "postgres", "CLICKHOUSE_IMAGE": "clickhouse",
    "REDIS_IMAGE": "redis", "MINIO_IMAGE": "minio", "MINIO_MC_IMAGE": "minio-mc",
    "LANGFUSE_WEB_IMAGE": "langfuse-web", "LANGFUSE_WORKER_IMAGE": "langfuse-worker",
    "LITELLM_IMAGE": "litellm", "NGINX_IMAGE": "nginx",
    "PROMETHEUS_IMAGE": "prometheus", "GRAFANA_IMAGE": "grafana", "DCGM_IMAGE": "dcgm",
}
REQUIRED_IMAGES = set(IMAGE_KEYS) - {"PROMETHEUS_IMAGE", "GRAFANA_IMAGE", "DCGM_IMAGE"}
REQUIRED_FILES = ("source.tar", "images/images.tar", "images.lock.json", "images.env",
                  "requirements.lock.txt", "host/README.md", "models/README.md", "config/README.md")
GENERATED = {"manifest.json", "SHA256SUMS"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or "\\" in value
            or ":" in value or path.as_posix() != value or any(ord(c) < 32 for c in value)):
        raise ValueError("Unsafe artifact path")
    return path


def files_under(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Bundle cannot contain symlinks")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            relative_path(name)
            if name not in GENERATED:
                if path.name == ".env" or path.suffix == ".key" or "secrets" in path.parts:
                    raise ValueError("Keep runtime credentials/private keys outside the artifact bundle")
                files[name] = path
    return files


def image_env(path):
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        value = value.strip().strip("\"'")
        if (not separator or key not in IMAGE_KEYS or key in result or not value
                or "REPLACE" in value or "$" in value or any(c.isspace() for c in value)):
            raise ValueError("Image file must contain unique IMAGE assignments only, without placeholders")
        result[key] = value
    if REQUIRED_IMAGES - result.keys():
        raise ValueError("Image file is missing required services: " + ", ".join(sorted(REQUIRED_IMAGES - result.keys())))
    return result


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(command[:4]))
    return result.stdout


def inspect_image(reference):
    return json.loads(run(["docker", "image", "inspect", reference]))[0]


def export_images(args):
    sources = image_env(args.images_env)
    bundle = args.bundle.resolve()
    archive = bundle / "images/images.tar"
    outputs = (archive, bundle / "images.lock.json", bundle / "images.env")
    if any(p.exists() for p in outputs) or (bundle / "manifest.json").exists():
        raise ValueError("Use a new bundle directory; existing image exports will not be overwritten")
    images = []
    # Resolve and validate the complete inventory before adding any local tags.
    for key, source in sources.items():
        data = inspect_image(source)
        actual = data["Os"] + "/" + data["Architecture"]
        if actual != args.platform:
            raise ValueError("Image platform mismatch for " + key + ": " + actual)
        alias = f"llm-offline/{IMAGE_KEYS[key]}:{data['Id'].split(':')[1][:16]}-{data['Architecture']}"
        images.append({"env_key": key, "source": source, "offline_tag": alias,
                       "image_id": data["Id"], "repo_digests": data.get("RepoDigests", []),
                       "platform": actual, "size_bytes": data["Size"]})
    archive.parent.mkdir(parents=True, exist_ok=True)
    for item in images:
        run(["docker", "image", "tag", item["image_id"], item["offline_tag"]])
    temporary = archive.with_suffix(".tar.partial")
    run(["docker", "image", "save", "-o", str(temporary), *[i["offline_tag"] for i in images]])
    temporary.rename(archive)
    (bundle / "images.lock.json").write_text(json.dumps(images, indent=2) + "\n", encoding="utf-8")
    (bundle / "images.env").write_text("".join(i["env_key"] + "=" + i["offline_tag"] + "\n" for i in images), encoding="utf-8")
    return {"stage": "IMAGES_EXPORTED", "images": len(images), "platform": args.platform,
            "archive": str(archive), "deployment_accepted": False}


def validate_inventory(root, platform):
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            raise ValueError("Missing required artifact: " + name)
    if not any((root / "wheelhouse").glob("*.whl")):
        raise ValueError("wheelhouse must contain the complete target-environment wheels")
    images = json.loads((root / "images.lock.json").read_text())
    env = image_env(root / "images.env")
    if len(images) != len(env) or {i["env_key"]: i["offline_tag"] for i in images} != env:
        raise ValueError("Image aliases do not match the inventory")
    if any(i["platform"] != platform or not re.fullmatch(r"sha256:[a-f0-9]{64}", i["image_id"]) for i in images):
        raise ValueError("Invalid image platform or image ID")
    return images


def seal(root, platform, python_version):
    root = root.resolve()
    files = files_under(root)
    images = validate_inventory(root, platform)
    manifest = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "platform": platform, "python_version": python_version,
                "stage": "ARTIFACTS_SEALED", "deployment_accepted": False,
                "images": images, "files": {name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
                                             for name, path in files.items()}}
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksums = {name: info["sha256"] for name, info in manifest["files"].items()}
    checksums["manifest.json"] = sha256(manifest_path)
    (root / "SHA256SUMS").write_bytes("".join(digest + "  " + name + "\n" for name, digest in sorted(checksums.items())).encode("utf-8"))
    return {"stage": "ARTIFACTS_SEALED", "files": len(files), "platform": platform,
            "checksum_file_sha256": sha256(root / "SHA256SUMS"), "deployment_accepted": False}


def verify(root, loaded_images=False):
    root = root.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported manifest schema")
    expected = manifest["files"]
    for name in expected:
        relative_path(name)
    actual = files_under(root)
    if actual.keys() != expected.keys():
        raise ValueError("Missing or unexpected bundle files")
    checksums = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        if not match:
            raise ValueError("Invalid SHA256SUMS entry")
        digest, name = match.groups()
        relative_path(name)
        if name in checksums:
            raise ValueError("Duplicate SHA256SUMS entry")
        checksums[name] = digest
    if checksums.keys() != (expected.keys() | {"manifest.json"}):
        raise ValueError("Incomplete SHA256SUMS inventory")
    if sha256(root / "manifest.json") != checksums["manifest.json"]:
        raise ValueError("Manifest checksum mismatch")
    for name, path in actual.items():
        digest = sha256(path)
        if path.stat().st_size != expected[name]["bytes"] or digest != expected[name]["sha256"] or digest != checksums[name]:
            raise ValueError("Artifact checksum mismatch: " + name)
    images = validate_inventory(root, manifest["platform"])
    if images != manifest["images"]:
        raise ValueError("Image inventory differs from sealed manifest")
    if loaded_images:
        for item in images:
            data = inspect_image(item["offline_tag"])
            if data["Id"] != item["image_id"] or data["Os"] + "/" + data["Architecture"] != item["platform"]:
                raise ValueError("Loaded image differs from lock: " + item["env_key"])
    return {"stage": "ARTIFACTS_VERIFIED", "files": len(actual), "images": len(images),
            "platform": manifest["platform"], "python_version": manifest["python_version"],
            "loaded_images_verified": loaded_images, "deployment_accepted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    images = commands.add_parser("images", help="Save already-local images; never pull")
    images.add_argument("--images-env", type=Path, required=True)
    images.add_argument("--bundle", type=Path, required=True)
    images.add_argument("--platform", choices=["linux/amd64", "linux/arm64"], required=True)
    sealing = commands.add_parser("seal", help="Hash the prepared bundle, not a deployment acceptance")
    sealing.add_argument("--bundle", type=Path, required=True)
    sealing.add_argument("--platform", choices=["linux/amd64", "linux/arm64"], required=True)
    sealing.add_argument("--python-version", default="3.12")
    verifying = commands.add_parser("verify", help="Verify file hashes and optionally locally loaded image IDs")
    verifying.add_argument("--bundle", type=Path, required=True)
    verifying.add_argument("--loaded-images", action="store_true")
    verifying.add_argument("--report", type=Path, help="Write evidence outside the sealed bundle")
    args = parser.parse_args()
    if args.command == "verify" and args.report and args.report.resolve().is_relative_to(args.bundle.resolve()):
        parser.error("Put the report outside the sealed bundle")
    try:
        if args.command == "images":
            result = export_images(args)
        elif args.command == "seal":
            result = seal(args.bundle, args.platform, args.python_version)
        else:
            result = verify(args.bundle, args.loaded_images)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        result = {"stage": "FAILED", "reason": str(error), "deployment_accepted": False}
        status = 1
    else:
        status = 0
    if getattr(args, "report", None):
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
