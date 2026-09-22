#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export INFLECT_PROJECT="${INFLECT_PROJECT:-$project_dir}"
exec python3 "$project_dir/launch.py" "$@"
