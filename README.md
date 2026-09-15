# Lumen · local model workbench

Lumen is a local Ubuntu interface for running large language models with a small set of understandable controls. It currently ships a qualified **ExLlamaV3 1.5.0 + TabbyAPI** path for EXL3 checkpoints. Adapters for vLLM safetensors and llama.cpp GGUF models are visible only when those engines are installed separately.

The interface includes streaming text and image chat, 256K context profiles, Q8/FP16/Q4 KV choices, vision and MTP controls, saved profiles, repeatable benchmarks, and live GPU power, CPU package power, VRAM, RAM, CPU load, prompt-processing speed, decode speed, and first-token time.

## Requirements

The automated installer targets a fresh **x86_64 Ubuntu 24.04 or newer** desktop. Other Linux distributions can run the application, but their system-package installation is not automated yet.

| Requirement | What is needed |
|---|---|
| GPU | An NVIDIA CUDA GPU supported by CUDA 12.8. The NVIDIA driver must already be installed and `nvidia-smi` must work. The installer never replaces the driver. |
| GPU memory | 32 GiB is recommended for the supplied large-model profiles. Less may work with additional CPU offload and a smaller cache, but is not qualified. |
| System memory | 128 GiB minimum for the large 3-bit mixture-of-experts models; 192 GiB recommended for comfortable headroom at 256K. Smaller models need much less. |
| CPU | A modern x86-64 CPU with at least 8 physical cores is recommended. CPU and memory bandwidth materially affect offloaded-model speed. |
| Ubuntu packages | Git, curl, CA certificates, Python 3 for the launcher, and a C/C++ build toolchain. Setup installs missing packages through `apt` after asking `sudo` when necessary. |
| Runtime storage | At least 12 GiB free on the Linux filesystem used for the private runtime. Keep roughly 20 GiB free for caches and upgrades. |
| Model storage | Enough space for the selected checkpoints. Models may live on a different internal or external drive and are never copied by setup. |
| Network | Required during first setup to obtain the pinned Python, PyTorch/CUDA libraries, ExLlamaV3 wheel, TabbyAPI revision, and Python packages. |

A system-wide CUDA toolkit is **not** required. The private engine environment supplies its own CUDA 12.8 runtime libraries. The NVIDIA kernel driver remains system-managed.

## Fresh Ubuntu installation

### 1. Prepare the NVIDIA driver

Install the recommended proprietary or open NVIDIA driver for the GPU using Ubuntu's driver tool, reboot, and check:

```bash
nvidia-smi
```

Do not continue until that command displays the GPU without an error.

### 2. Clone Lumen

On a minimal installation, install Git first:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/dreamtheater484/Linux_llm_loader.git
cd Linux_llm_loader
```

### 3. Run the preflight check

Replace `/path/to/models` with the directory containing the downloaded model folders:

```bash
./scripts/setup-ubuntu.sh --model-dir "/path/to/models" --preflight-only
```

This checks the operating system, CPU architecture, NVIDIA access, model directory, compiler tools, and free runtime space without downloading or changing the installation.

### 4. Install

```bash
./scripts/setup-ubuntu.sh --model-dir "/path/to/models"
```

Setup performs these steps:

1. Installs any missing basic Ubuntu packages.
2. Installs an isolated Python 3.12 runtime with `uv` under the user's data directory.
3. Creates separate manager and ExLlamaV3 environments.
4. Downloads and verifies the pinned ExLlamaV3 wheel and PyTorch build.
5. Checks out the pinned TabbyAPI revision and applies the included status-reporting patch.
6. Runs a real CUDA calculation.
7. Stores this machine's project and model paths in a private local configuration file.
8. Adds **Lumen** to the application menu.

No model is downloaded or moved.

### 5. Start Lumen

Open **Lumen** from Ubuntu's application menu, or run:

```bash
./launch.sh
```

The interface opens at `http://127.0.0.1:7860`. Lumen and its inference engines bind only to the local computer.

## External model drive

An external drive is fine. Drive speed affects installation, model loading, and cold startup; steady generation primarily uses RAM and VRAM. A Linux filesystem is preferable for the application runtime, while model files can remain on NTFS or another mounted filesystem.

If the project or models live on a removable drive and you want the launcher to mount it automatically, provide its stable device path during setup:

```bash
./scripts/setup-ubuntu.sh \
  --model-dir "/mount/path/models" \
  --mount-device "/dev/disk/by-uuid/YOUR-UUID"
```

The device path is written only to `~/.config/lumen/config.json`; it is never added to Git.

## Using the workbench

1. Select a model in the library.
2. Keep **256K**, **Q8 KV**, and **Vision** where the checkpoint supports them.
3. Choose an MTP length and CPU expert share from a saved or recommended profile.
4. Click **Load model** and wait for the ready state.
5. Type a message, drag in an image, choose an image file, or paste a screenshot with **Ctrl+V**.
6. Use **Benchmarks** to compare prompt-processing and decode speed with the exact settings recorded.
7. Click **Save profile** to name the current setup. Saved profiles can be loaded, edited, renamed, deleted, restored, or saved as a copy.

The conversation follows new output automatically. Scrolling away pauses tail following; **Jump to latest** resumes it. Closing the browser tab leaves the local server running. **Unload model** frees model RAM and VRAM. **Quit Lumen** stops the server.

## Model and engine support

| Format | Engine | Status |
|---|---|---|
| EXL3 | ExLlamaV3 1.5.0 through pinned TabbyAPI | Installed and qualified by the setup script |
| Native safetensors | vLLM | Adapter present; runtime and model-specific combinations are not installed or qualified |
| GGUF | llama.cpp server | Adapter present; runtime and model-specific combinations are not installed or qualified |

The current 3-bit model set uses EXL3 3.04–3.05 bpw checkpoints. EXL3 weights cannot be loaded by vLLM by changing the engine selector. Vision also requires a checkpoint with a compatible embedded vision component or projector.

## Files and privacy

| Data | Default location |
|---|---|
| Source checkout and compiled interface | Wherever this repository is cloned |
| Manager, Python, ExLlamaV3, PyTorch, TabbyAPI, caches | `~/.local/share/linux-llm-loader/` |
| Private machine configuration | `~/.config/lumen/config.json` |
| Profiles, benchmark history, logs, temporary engine keys | `~/.local/share/linux-llm-loader/state/` |
| Desktop shortcut | `~/.local/share/applications/lumen.desktop` |
| Model files | The directory supplied with `--model-dir` |

Machine configuration, logs, profiles, benchmark results, CUDA reports, model files, credentials, `.env` files, and private keys are excluded from Git. Internal engine keys are generated for each run, stored with owner-only permissions, and removed from displayed logs. Chat content remains in browser memory and is cleared when the page reloads.

## Configuration and moving files

Run setup again whenever the source checkout, runtime, or model directory moves. Existing model files and saved profiles are reused.

Useful options:

```bash
./scripts/setup-ubuntu.sh --help
./scripts/setup-ubuntu.sh --model-dir "/path/to/models" --runtime-dir "$HOME/.local/share/linux-llm-loader"
./scripts/setup-ubuntu.sh --model-dir "/path/to/models" --no-desktop
```

The launcher also accepts these environment overrides: `LUMEN_PROJECT`, `LUMEN_RUNTIME`, `LUMEN_MODEL_ROOT`, `LUMEN_PORT`, `LUMEN_MOUNT_DEVICE`, `LUMEN_STATE`, `LUMEN_TABBY`, and `LUMEN_GGUF_SERVER`.

## Troubleshooting

- **`nvidia-smi` fails:** repair the host NVIDIA driver and reboot. The installer intentionally does not alter drivers.
- **No models appear:** rerun setup with the directory immediately above the model folders, then use the library refresh button.
- **Model loading fails:** open **Engine log**. Common causes are an incomplete checkpoint, incompatible settings, insufficient RAM, or insufficient VRAM.
- **First response is slow:** GPU kernels can compile on first use. Compare sustained performance over several requests.
- **External drive moved:** rerun setup with the new `--model-dir` and optional `--mount-device` values.
- **Port 7860 is occupied:** stop the other service or set a different `LUMEN_PORT` before launching.
- **Full disk:** the private runtime uses about 10 GiB before caches. Models are stored separately and are not deleted by Lumen.

## Validation and development

The repository keeps test procedures rather than machine-generated reports. See [VALIDATION.md](VALIDATION.md). Run the application tests with the manager environment:

```bash
~/.local/share/linux-llm-loader/exl3/bin/python -m pytest -q
```

The production frontend is committed, so Node is unnecessary for normal installation. To change the interface, use Node.js 22.20+ or 24.12+ and pnpm 11:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm exec tsc -b
pnpm exec vite build --config vite.config.js
```

After backend changes, quit and reopen Lumen. After frontend-only changes, rebuild and refresh the browser.
