#!/usr/bin/env bash
# Install Lumen on a clean x86_64 Ubuntu machine without changing its GPU driver.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${LUMEN_RUNTIME:-${XDG_DATA_HOME:-$HOME/.local/share}/linux-llm-loader}"
model_dir="${LUMEN_MODEL_ROOT:-}"
mount_device="${LUMEN_MOUNT_DEVICE:-}"
install_desktop=1
preflight_only=0
install_benchmarks=0

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-ubuntu.sh --model-dir PATH [options]

Required:
  --model-dir PATH       Folder containing downloaded model folders/files

Optional:
  --runtime-dir PATH     Private Python and engine files (default: ~/.local/share/linux-llm-loader)
  --mount-device PATH    Removable model drive, such as /dev/disk/by-uuid/...
  --no-desktop           Configure Lumen without an application-menu shortcut
  --preflight-only       Check the machine and arguments without installing
  --with-benchmarks      Install Docker, enable its service, and grant this user Docker-group access
  -h, --help             Show this help
EOF
}

while (($#)); do
    case "$1" in
        --model-dir) model_dir="${2:?--model-dir needs a path}"; shift 2 ;;
        --runtime-dir) runtime_dir="${2:?--runtime-dir needs a path}"; shift 2 ;;
        --mount-device) mount_device="${2:?--mount-device needs a path}"; shift 2 ;;
        --no-desktop) install_desktop=0; shift ;;
        --preflight-only) preflight_only=1; shift ;;
        --with-benchmarks) install_benchmarks=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -n "$model_dir" ]] || { echo 'Missing --model-dir PATH.' >&2; usage >&2; exit 2; }
[[ -d "$model_dir" ]] || { echo "Model directory does not exist: $model_dir" >&2; exit 2; }
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
    echo 'This pinned runtime currently supports x86_64 Linux only.' >&2; exit 1;
}
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == ubuntu ]] || {
        echo "Automated setup supports Ubuntu. Detected: ${PRETTY_NAME:-unknown Linux}." >&2; exit 1;
    }
fi

missing_tools=()
command -v curl >/dev/null || missing_tools+=(curl)
command -v git >/dev/null || missing_tools+=(git)
command -v gcc >/dev/null || [[ -x "$runtime_dir/toolchain/usr/bin/x86_64-linux-gnu-gcc-15" ]] || missing_tools+=(build-essential)
command -v python3 >/dev/null || missing_tools+=(python3)

install_system_tools() {
    ((${#missing_tools[@]} == 0)) && return
    command -v apt-get >/dev/null || {
        echo "Install these prerequisites first: ${missing_tools[*]}" >&2; exit 1;
    }
    local elevate=()
    if ((EUID != 0)); then
        command -v sudo >/dev/null || { echo "Install these prerequisites first: ${missing_tools[*]}" >&2; exit 1; }
        elevate=(sudo)
    fi
    "${elevate[@]}" apt-get update
    "${elevate[@]}" apt-get install -y ca-certificates "${missing_tools[@]}"
}

if ((preflight_only)) && ((${#missing_tools[@]})); then
    echo "Preflight found missing prerequisites: ${missing_tools[*]}" >&2
    exit 1
fi
((preflight_only)) || install_system_tools
command -v nvidia-smi >/dev/null || {
    echo 'nvidia-smi is unavailable. Install a current NVIDIA driver, reboot, then run setup again.' >&2; exit 1;
}
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
driver_version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1)"
driver_major="${driver_version%%.*}"
if [[ ! "$driver_major" =~ ^[0-9]+$ ]] || ((driver_major < 570)); then
    echo "The pinned CUDA 12.8 runtime requires an NVIDIA 570-series or newer driver; found $driver_version." >&2
    exit 1
fi

runtime_parent="$(dirname -- "$runtime_dir")"
mkdir -p "$runtime_parent"
available_kib="$(df -Pk "$runtime_parent" | awk 'NR==2 {print $4}')"
if [[ "$available_kib" =~ ^[0-9]+$ ]] && ((available_kib < 12 * 1024 * 1024)); then
    echo 'The runtime drive needs at least 12 GiB free.' >&2
    exit 1
fi
if ((preflight_only)); then
    echo 'Preflight passed. The GPU, model directory, build tools, and runtime disk are available.'
    exit 0
fi

if ((install_benchmarks)); then
    benchmark_user="${SUDO_USER:-$(id -un)}"
    if ((EUID == 0)); then
        /bin/bash "$project_dir/scripts/install-benchmark-docker.sh" --user "$benchmark_user"
    else
        sudo /bin/bash "$project_dir/scripts/install-benchmark-docker.sh" --user "$benchmark_user"
    fi
fi

mkdir -p "$runtime_dir/bin" "$runtime_dir/sources" "$project_dir/.runtime/wheels"
uv_bin="$(command -v uv || true)"
if [[ -z "$uv_bin" ]]; then
    uv_bin="$runtime_dir/bin/uv"
fi
if [[ ! -x "$uv_bin" ]]; then
    installer="$(mktemp)"
    trap 'rm -f "$installer"' EXIT
    curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "$installer"
    UV_INSTALL_DIR="$runtime_dir/bin" UV_NO_MODIFY_PATH=1 sh "$installer"
fi

export UV_CACHE_DIR="$runtime_dir/cache/uv"
export UV_PYTHON_INSTALL_DIR="$runtime_dir/python"
"$uv_bin" python install 3.12

manager_dir="$runtime_dir/manager"
if [[ ! -x "$manager_dir/bin/python" ]]; then
    "$uv_bin" venv --python 3.12 --seed "$manager_dir"
fi
"$uv_bin" pip install --python "$manager_dir/bin/python" -r "$project_dir/requirements-app.txt"

tabby_dir="$runtime_dir/sources/tabbyAPI"
tabby_revision='53da7919d4e45c63f4acbcbbc00cbe0f60a1ce65'
if [[ ! -d "$tabby_dir/.git" ]]; then
    git clone https://github.com/theroyallab/tabbyAPI.git "$tabby_dir"
fi
git -C "$tabby_dir" fetch --tags origin
git -C "$tabby_dir" checkout --detach "$tabby_revision"
for tabby_patch in tabby-exl3-model-card.patch tabby-qwen-tool-schema.patch; do
    if ! git -C "$tabby_dir" apply --reverse --check "$project_dir/patches/$tabby_patch" 2>/dev/null; then
        git -C "$tabby_dir" apply "$project_dir/patches/$tabby_patch"
    fi
done

exl_dir="$runtime_dir/exl3"
if [[ ! -x "$exl_dir/bin/python" ]]; then
    "$uv_bin" venv --python 3.12 --seed "$exl_dir"
fi
"$manager_dir/bin/python" "$project_dir/scripts/fetch_exl_wheel.py"
"$uv_bin" pip install --python "$exl_dir/bin/python" --extra-index-url https://pypi.nvidia.com \
    -r "$project_dir/runtime-locks/exl3-dependencies.txt" \
    'torch @ https://download.pytorch.org/whl/cu128/torch-2.9.0%2Bcu128-cp312-cp312-manylinux_2_28_x86_64.whl#sha256=87c62d3b95f1a2270bd116dbd47dc515c0b2035076fbb4a03b4365ea289e89c4' \
    "$project_dir/.runtime/wheels/exllamav3-1.5.0+cu128.torch2.9.0-cp312-cp312-linux_x86_64.whl" \
    "$tabby_dir"

# The pinned wheel drops earlier output-token counts after a second requeue.
# Patch the installed Python module (no CUDA rebuild or model changes).
exl_site=$("$exl_dir/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
if ! git -C "$exl_site" apply --reverse --check "$project_dir/patches/exl3-cumulative-output-tokens.patch" 2>/dev/null; then
    git -C "$exl_site" apply "$project_dir/patches/exl3-cumulative-output-tokens.patch"
fi

[[ -f "$project_dir/frontend/dist/index.html" ]] || {
    echo 'The compiled interface is missing from this checkout.' >&2; exit 1;
}
LUMEN_RUNTIME="$runtime_dir" "$exl_dir/bin/python" "$project_dir/scripts/probe_runtime.py"

desktop_args=(--model-dir "$model_dir")
[[ -n "$mount_device" ]] && desktop_args+=(--mount-device "$mount_device")
((install_desktop)) || desktop_args+=(--no-desktop)
LUMEN_PROJECT="$project_dir" LUMEN_RUNTIME="$runtime_dir" \
    "$manager_dir/bin/python" "$project_dir/scripts/install-desktop.py" "${desktop_args[@]}"

echo
echo 'Setup complete.'
echo "Models: $model_dir"
echo "Runtime: $runtime_dir"
if ((install_desktop)); then
    echo 'Open Lumen from the application menu, or run ./launch.sh.'
else
    echo 'Run ./launch.sh to start Lumen.'
fi
