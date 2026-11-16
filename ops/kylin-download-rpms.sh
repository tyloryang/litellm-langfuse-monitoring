#!/usr/bin/env bash
# Run only on a matching, connected Kylin preparation clone with authorized repos.
set -euo pipefail
export LC_ALL=C
if [ "$#" -lt 4 ]; then
    echo "Usage: bash $0 dnf|yum EMPTY_DIR TOP_LEVEL_FILE REPO_ID [REPO_ID ...]" >&2; exit 2
fi
mode=$1; dest=$2; top=$3; shift 3
case "$mode" in dnf|yum) ;; *) echo 'Mode must be dnf or yum' >&2; exit 2;; esac
[ -f "$top" ] || { echo 'Top-level package list missing' >&2; exit 2; }
mkdir -p -- "$dest"
dest=$(cd -- "$dest" && pwd -P)
[ -z "$(ls -A -- "$dest")" ] || { echo 'Destination must be empty' >&2; exit 2; }
mapfile -t packages < <(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$top")
[ "${#packages[@]}" -gt 0 ] || { echo 'Top-level package list is empty' >&2; exit 2; }
for p in "${packages[@]}"; do
    [[ "$p" =~ ^[A-Za-z0-9_+.:~-][A-Za-z0-9_+.:~-]*$ ]] || { echo "Invalid package spec: $p" >&2; exit 2; }
done
repoargs=( '--disablerepo=*' )
for id in "$@"; do
    [[ "$id" =~ ^[A-Za-z0-9_.:-]+$ ]] || { echo "Invalid repo ID: $id" >&2; exit 2; }
    repoargs+=( "--enablerepo=$id" )
done
echo 'Only explicit repositories are enabled. Verify OS/build, source provenance and signatures independently.'
if [ "$mode" = dnf ]; then
    dnf "${repoargs[@]}" download --resolve --alldeps --destdir "$dest" "${packages[@]}"
else
    yumdownloader "${repoargs[@]}" --resolve --alldeps --destdir "$dest" "${packages[@]}"
fi
shopt -s nullglob
files=("$dest"/*.rpm)
[ "${#files[@]}" -gt 0 ] || { echo 'No RPMs downloaded' >&2; exit 1; }
rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' "${files[@]}" | sort > "$dest/RPM-NEVRA.txt"
(cd "$dest" && sha256sum -- *.rpm > SHA256SUMS)
echo 'Downloaded RPMs, RPM-NEVRA.txt and SHA256SUMS. These do not prove a complete closure or signature trust.'
