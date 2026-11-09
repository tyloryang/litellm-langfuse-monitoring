#!/usr/bin/env bash
set -euo pipefail
# TSV format: <verified DDN pull reference><TAB><canonical Compose image reference>
# Copy image-map.example.tsv to image-map.tsv and replace mappings with the actual
# addresses returned by https://docker.aityp.com/ for each exact tag.
mapping_file="${1:-image-map.tsv}"
[[ -f "$mapping_file" ]] || { echo "Missing mapping file: $mapping_file" >&2; exit 1; }
while IFS=$'\t' read -r source target; do
  [[ -z "${source:-}" || "$source" == \#* ]] && continue
  [[ -n "${target:-}" ]] || { echo "Missing target for $source" >&2; exit 1; }
  echo "Pull: $source"
  docker pull "$source"
  docker tag "$source" "$target"
  docker image inspect "$target" --format '{{.Id}} {{json .RepoDigests}}'
done < "$mapping_file"
