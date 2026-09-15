#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export LUMEN_PROJECT="${LUMEN_PROJECT:-$project_dir}"
exec python3 "$project_dir/launch.py" "$@"
