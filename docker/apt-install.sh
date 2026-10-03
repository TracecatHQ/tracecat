#!/bin/sh
# Install Debian packages, tolerating transient mirror faults.
#
# deb.debian.org is a CDN; individual edges intermittently serve truncated or
# stale files ("File has unexpected size", "Hash Sum mismatch"). Those errors
# persist for the rest of the build because the bad index stays in
# /var/lib/apt/lists, so apt's own Acquire::Retries cannot recover from them.
# Retrying with the lists purged re-resolves to (usually) a healthy edge.
#
# Usage: apt-install.sh [package...]
#        APT_UPGRADE=1 apt-install.sh [package...]   # also apt-get -y upgrade

set -eu

mkdir -p /etc/apt/apt.conf.d
cat >/etc/apt/apt.conf.d/99tracecat-retries <<'CONF'
Acquire::Retries "5";
Acquire::Retries::Delay::Maximum "10";
Acquire::http::Timeout "30";
Acquire::https::Timeout "30";
CONF

run_apt() {
    apt-get update || return 1
    if [ "$#" -gt 0 ]; then
        apt-get install -y --no-install-recommends "$@" || return 1
    fi
    if [ "${APT_UPGRADE:-0}" = "1" ]; then
        apt-get -y upgrade || return 1
    fi
    return 0
}

max_attempts=3
attempt=1
while :; do
    if run_apt "$@"; then
        break
    fi
    if [ "${attempt}" -ge "${max_attempts}" ]; then
        echo "apt-install: failed after ${max_attempts} attempts" >&2
        exit 1
    fi
    echo "apt-install: attempt ${attempt}/${max_attempts} failed; purging apt lists and retrying" >&2
    rm -rf /var/lib/apt/lists/*
    sleep $((attempt * 5))
    attempt=$((attempt + 1))
done

apt-get clean
rm -rf /var/lib/apt/lists/*
