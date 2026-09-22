#!/usr/bin/env bash
# Install Ubuntu's Docker Engine package; never change model/CUDA dependencies.
set -euo pipefail
if [[ ${1:-} != --user || $# != 2 ]]; then
    echo 'Usage: install-benchmark-docker.sh --user USERNAME' >&2
    exit 2
fi
benchmark_user=$2
[[ $benchmark_user =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || { echo 'Invalid username.' >&2; exit 2; }
id "$benchmark_user" >/dev/null
[[ $EUID == 0 ]] || { echo 'Administrator authentication is required. Run this script using pkexec or sudo.' >&2; exit 1; }
source /etc/os-release
[[ $ID == ubuntu ]] || { echo 'Automatic Docker installation supports Ubuntu only.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
    apt-get update
    apt-get install -y docker.io
fi
# Ubuntu 26.04 moved sg/newgrp out of the base login package. This lets
# an existing Inflect session use the membership granted below immediately.
if ! command -v sg >/dev/null; then
    apt-get install -y util-linux-extra
fi
systemctl enable --now docker
usermod -aG docker "$benchmark_user"
runuser -u "$benchmark_user" -- docker info --format 'Docker Engine {{.ServerVersion}} is ready.'
echo 'Docker installed. Inflect can activate the Docker group without a new login.'
