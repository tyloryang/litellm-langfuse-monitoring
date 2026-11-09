#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mapping_file="${1:-$script_dir/../offline/ddn-image-map.amd64.tsv}"
platform="${2:-${TARGET_PLATFORM:-linux/amd64}}"
[[ -f "$mapping_file" ]] || { echo "Missing mapping file: $mapping_file" >&2; exit 1; }
[[ "$platform" == linux/amd64 || "$platform" == linux/arm64 ]] || { echo "Unsupported platform: $platform" >&2; exit 1; }
IFS= read -r declaration < "$mapping_file"
[[ "$declaration" == "# platform=$platform" ]] || { echo "Mapping platform must match $platform" >&2; exit 1; }
sources=()
targets=()
while IFS=$'\t' read -r source target extra || [[ -n "${source:-}" ]]; do
  [[ -z "${source:-}" || "$source" == \#* ]] && continue
  [[ "$source" =~ ^swr\.cn-north-4\.myhuaweicloud\.com/ddn-k8s/[a-zA-Z0-9_./:-]+@sha256:[a-f0-9]{64}$ && "${target:-}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_./:-]+$ && -z "${extra:-}" ]] || { echo "Invalid DDN mapping" >&2; exit 1; }
  sources+=("$source")
  targets+=("$target")
done < "$mapping_file"
[[ "${#sources[@]}" -gt 0 ]] || { echo "Empty DDN mapping" >&2; exit 1; }
for index in "${!sources[@]}"; do
  source="${sources[$index]}"
  target="${targets[$index]}"
  echo "Pull: $source"
  docker pull --platform "$platform" "$source"
  actual_platform="$(docker image inspect "$source" --format '{{.Os}}/{{.Architecture}}')"
  [[ "$actual_platform" == "$platform" ]] || { echo "Pulled image platform mismatch: $actual_platform" >&2; exit 1; }
  docker tag "$source" "$target"
  docker image inspect "$target" --format '{{.Id}} {{json .RepoDigests}}'
done
