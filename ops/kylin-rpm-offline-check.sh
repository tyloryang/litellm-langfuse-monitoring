#!/usr/bin/env bash
# Inspect an explicitly selected RPM transaction without changing installed packages.
set -u
export LC_ALL=C
if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
    printf 'Usage: bash %s /path/to/selected-rpms\n' "$0" >&2; exit 2
fi
command -v rpm >/dev/null 2>&1 || { echo 'rpm is required' >&2; exit 2; }
dir=$(cd -- "$1" && pwd -P) || exit 2
shopt -s nullglob
files=("$dir"/*.rpm)
if [ "${#files[@]}" -eq 0 ]; then echo 'No RPMs in selected directory' >&2; exit 2; fi
if [ -f "$dir/SHA256SUMS" ]; then
    echo '### SHA256 manifest'
    (cd "$dir" && sha256sum -c SHA256SUMS) || exit 1
else echo 'WARNING: SHA256SUMS missing; integrity remains unverified'; fi
echo '### Selected packages'
rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' "${files[@]}" || exit 1
echo '### Package signature status: inspect NOKEY / unsigned / failures'
rpm --checksig "${files[@]}" || echo 'WARNING: signature check failed; do not treat as trusted'
echo '### Requirements (capabilities, including rpmlib)'
for f in "${files[@]}"; do
    printf '\n--- %s ---\n' "${f##*/}"
    rpm -qp --requires "$f" || exit 1
done
echo '### RPM transaction test against this machine; no repositories or installation'
echo 'This tests the ENTIRE selected directory and installed RPM database. Inspect conflicts and downgrades.'
rpm -Uvh --test "${files[@]}"
rc=$?
printf '\nTransaction test exit code: %s\n' "$rc"
echo 'This is not an installation, runtime test, signature trust decision, or proof for another RPM database.'
exit "$rc"
