#!/usr/bin/env bash
# Read-only inventory; output is intended for local review before sharing.
set -u
export LC_ALL=C
section() { printf '\n### %s\n' "$1"; }
run() { printf '\n$'; printf ' %q' "$@"; printf '\n'; "$@" 2>&1 || printf '[unavailable or failed: %s]\n' "$?"; }
section 'OS and platform'
for f in /etc/os-release /etc/kylin-release /etc/system-release; do
    if [ -r "$f" ]; then printf '\n--- %s ---\n' "$f"; cat "$f"; fi
done
run uname -a
run getconf GNU_LIBC_VERSION
run rpm --version
run stat -fc %T /sys/fs/cgroup
section 'Required host tools'
for tool in bash python3 openssl tar sha256sum docker; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf '%s: %s\n' "$tool" "$(command -v "$tool")"
    else printf '%s: MISSING\n' "$tool"; fi
done
run bash --version
run python3 --version
run python3 -c 'import hashlib,json,ssl,tarfile; print("python3 standard library: OK")'
run openssl version
run tar --version
run sha256sum --version
section 'Installed RPM inventory (review before sharing)'
if command -v rpm >/dev/null 2>&1; then
    rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' 2>&1 | sort
else printf '[rpm unavailable]\n'; fi
section 'Container and GPU software'
run systemctl is-active docker
run docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
run docker info --format 'storage={{.Driver}} cgroupDriver={{.CgroupDriver}} cgroupVersion={{.CgroupVersion}} runtimes={{json .Runtimes}} defaultRuntime={{.DefaultRuntime}}'
run docker compose version
run containerd --version
run runc --version
run nvidia-ctk --version
run nvidia-container-cli --version
run nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
section 'Manual follow-up'
printf '%s\n' 'Review output for site-sensitive information before sharing.' 'A working GPU container test requires a separately supplied local image and explicit operator execution.'
