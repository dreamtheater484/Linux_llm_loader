# Inflect on Windows

Inflect runs natively on 64-bit Windows 10 and 11 with the same interface, profiles and engines as on Ubuntu:

| Engine | Windows runtime |
|---|---|
| ExLlamaV3 + TabbyAPI (EXL3) | **ExLlamaV3 1.5.4** (official `win_amd64` wheel) with the same **PyTorch 2.9.0 + CUDA 12.8**, TabbyAPI revision and Inflect patches as Linux. 1.5.0 disables the pinned expert arena on Windows and does not fit the 60% Qwen3.8 Flash Next profile in the VRAM Windows leaves free; 1.5.4 does, and already contains the cumulative token-count fix that Linux patches in. `triton-windows` replaces `triton`; every other Python package has the same version as the Linux lock ([exl3-dependencies-windows.txt](runtime-locks/exl3-dependencies-windows.txt)). |
| llama.cpp (GGUF, embedded MTP) | The same release, **b11050**, as the official **CUDA 13.4** Windows build. It contains Blackwell (RTX 50) kernels; drivers older than R580 or GPUs older than Turing get the official CUDA 12.4 build instead. |
| vLLM | Not available on Windows. Safetensors/NVFP4 checkpoints stay listed but cannot be loaded. |

Saved profiles keep their meaning: context, KV precision, vision, PLE n-gram residency, MTP and draft length, CPU expert share, threads and chunk size map to the same engine options. Engine settings that Inflect applies itself, such as the pinned MoE host arena and the 16 GiB host-memory reserve, are identical.

## Requirements

| Requirement | What is needed |
|---|---|
| GPU and driver | An NVIDIA CUDA GPU with an R570 or newer driver; `nvidia-smi` must work. Setup never changes the driver. llama.cpp's CUDA 13 build needs R580 or newer. |
| Git | [Git for Windows](https://git-scm.com/download/win), for example `winget install --exact --id Git.Git`. |
| Runtime storage | 12 GiB free (16 GiB with llama.cpp) on the drive holding `%LOCALAPPDATA%`. |
| Administrator rights | Not needed. No CUDA toolkit, Visual Studio or system Python is required: setup installs a private Python 3.12 with `uv`. |

Memory and model-size guidance is the same as in the [README](README.md#requirements).

## Install

Open **Windows PowerShell** or **Windows Terminal** (not a terminal embedded in a Microsoft Store/MSIX app, see Troubleshooting), then:

```powershell
git clone https://github.com/dreamtheater484/Linux_llm_loader.git
cd Linux_llm_loader
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -ModelDir 'D:\Documents\AI\models' -PreflightOnly
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -ModelDir 'D:\Documents\AI\models' -WithLlama
```

Replace the model folder with yours. The checkout can share the model drive with an Ubuntu installation; Windows and Linux use separate private runtimes. Setup:

1. Checks Windows, Git, the NVIDIA driver and free space.
2. Installs `uv` (unless present) and a private Python 3.12 under `%LOCALAPPDATA%\Inflect`.
3. Creates the manager environment, clones the pinned TabbyAPI revision and applies Inflect's patches.
4. Downloads the pinned ExLlamaV3 wheel, verifies its release SHA-256, installs it with the pinned PyTorch, and patches the cumulative-token counter.
5. Runs a short CUDA smoke test (skip with `-SkipGpuCheck` and run setup again later to verify).
6. With `-WithLlama`, installs the checksum-pinned llama.cpp build and verifies CUDA and MTP support.
7. Writes `%APPDATA%\inflect\config.json` and adds **Inflect** to the Start menu.

Start Inflect from the Start menu, or run `launch.cmd` in the checkout. The browser opens automatically. `powershell -File .\scripts\setup-windows.ps1 -Help` lists all options.

## Bring your Linux profiles and history

Profiles, library choices, benchmark history and chats live in Ubuntu's `~/.local/share/linux-llm-loader/state`, which Windows cannot read. In Ubuntu, copy them to the shared model drive:

```bash
mkdir -p "/run/media/$USER/New Volume/Inflect-linux-state"
cp -a ~/.local/share/linux-llm-loader/state/. ~/.config/inflect/config.json "/run/media/$USER/New Volume/Inflect-linux-state/"
```

Then in Windows, with Inflect closed:

```powershell
& "$env:LOCALAPPDATA\Inflect\manager\Scripts\python.exe" .\scripts\import-linux-state.py --from 'D:\Inflect-linux-state'
```

or pass `-ImportLinuxState 'D:\Inflect-linux-state'` to setup. Models inside the model folder keep identical IDs on both systems, so every profile finds its checkpoint. Models in *added* library locations are matched by translating the location: `/run/media/<user>/<label>/…` maps to the Windows drive with the same volume label, and `--map /linux/path=D:\windows\path` adds explicit translations. Existing Windows profiles and conversations are never overwritten; importing twice adds nothing. Port and network-access preferences are imported; ComfyUI integration must be set up again because containers and URLs differ between systems.

## Where things live

| Data | Windows location |
|---|---|
| Manager, Python, ExLlamaV3, PyTorch, TabbyAPI, llama.cpp, caches | `%LOCALAPPDATA%\Inflect\` |
| Triton kernel cache | `%LOCALAPPDATA%\Inflect\cache\triton\` |
| Private machine configuration and Hugging Face token | `%APPDATA%\inflect\` |
| Profiles, benchmark history, chats, logs | `%LOCALAPPDATA%\Inflect\state\` |
| Start menu shortcut | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Inflect.lnk` |

These folders are private to your Windows account.

## Performance on Windows

Measured on one machine (RTX 5090, Ryzen 7 7800X3D, 192 GB DDR5) with the same profiles as on Linux, using Inflect's three-prompt speed test:

| Profile | Windows | Linux, same machine |
|---|---|---|
| Qwen3.8 Flash Next EXL3 3.05 bpw · 256K/Q8 · vision · MTP 2 · CPU 60% | Loads in 50–110 s (warm/cold file cache), 29.5 GiB VRAM; **24–29 tok/s** decode, 80–145 tok/s prefill | ≈60 tok/s decode, ≈280 tok/s prefill (recorded coding-benchmark medians) |
| Qwen3.8 Flash Next GGUF Q4_K_M · 128K/Q8 · vision · PLE in RAM · all experts on CPU | Loads in 25 s, 8.6 GiB VRAM; **22 tok/s** decode | not recorded |

Everything works on Windows, but **CPU-offloaded mixture-of-experts decoding is about half as fast as on Linux** with ExLlamaV3. ExLlamaV3's handoff profiler (`EXL3_MOE_HANDOFF_PROF=1`) shows where the time goes: each forward pass waits on 48 CPU expert jobs of ≈1.2 ms compute plus ≈0.5 ms GPU↔CPU handoff (87 ms per pass versus 42 ms on Linux). The compute already runs near the ≈50 GB/s memory bandwidth Windows achieves here; the handoff latency comes from the Windows display-driver model. More CPU threads make it worse (SMT siblings compete); 7 or 8 threads are best. Fully GPU-resident models are not affected. For the fastest CPU-offloaded MoE inference on the same computer, use Linux.

### Faster CPU offload: allow large pages

With Memory Integrity (Virtualization-Based Security) on, as it is by default on Windows 11, Windows itself runs under the hypervisor, and memory-streaming code on regular 4 KB pages pays for every TLB miss twice. ExLlamaV3 can place its CPU expert arena on 2 MB large pages instead, but Windows only allows that for accounts holding the **Lock pages in memory** right:

1. Press Win+R, run `secpol.msc` (Windows Pro/Enterprise), open **Local Policies → User Rights Assignment → Lock pages in memory**, choose **Add User or Group…**, enter your user name, and confirm.
2. Sign out and back in (rights apply to new sign-ins only).

On an **administrator** account, User Account Control removes this right from programs that run normally (non-elevated tokens keep only five basic privileges), so Inflect only gets large pages when it is started from an elevated ("Run as administrator") terminal, or from a standard user account. Weigh that before running a network-reachable server elevated; with Network off in Settings it listens on this computer only.

Inflect detects the right and then uses ExLlamaV3's private expert arena on large pages (the pinned arena, which speeds up prefill, cannot use them on Windows). Large pages stay in RAM and are never paged out while a model is loaded; they are released when it unloads. If memory is too fragmented after a long uptime, ExLlamaV3 falls back to regular pages and says so in the engine log. Set `EXL3_MOE_PINNED_ARENA=1` before starting Inflect to keep the pinned arena regardless.

Windows also keeps about 1 GiB more of the GPU's memory for itself than Linux does (CUDA reports ≈30.3 GiB free on a 32 GiB card with an idle desktop). Profiles that use the card's last gigabyte on Linux may need one more CPU-expert step on Windows; the bundled ExLlamaV3 build fits the 60% Flash Next profile.

## What is different on Windows

- **Engine shutdown.** Each engine runs in a kill-on-close job object. Unloading, quitting, restarting, or a crash of Inflect itself always ends the engine and every process it started, so GPU memory is released.
- **Memory readings.** System RAM uses Windows' page lists with the same accounting as Linux: in use = total − free, of which standby and modified pages are cache. Model RAM is the engine's working set (Linux reports proportional set size). Its shareable part, mostly memory-mapped model files, is shown as file-backed memory.
- **CPU power** comes from the RAPL package counter that Windows publishes as *Energy Meter* (AMD and Intel). If your system lacks it, Inflect shows the sensor as unavailable rather than estimating.
- **First load** compiles Triton kernels once per model architecture and settings. Later loads use the cache.
- **Network access** is on by default, as on Linux. The first time Inflect listens on your LAN address, Windows Firewall asks whether to allow Python. Allow **private networks** only, or switch Network off in Settings.
- **Coding benchmarks** use [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL 2 (`winget install --exact --id Docker.DockerDesktop`). Start Docker Desktop before preparing or running a benchmark.
- **Engine updates** from Settings → Engines install the pinned Windows builds side by side, exactly like on Linux.

## Troubleshooting

- **Setup finished, but the Start menu entry reports missing files:** setup ran inside a packaged (Microsoft Store/MSIX) app's terminal, such as an IDE or assistant app from the Store. Windows redirects such an app's `%LOCALAPPDATA%` writes into its private container. Run setup again from Windows PowerShell or Windows Terminal.
- **`nvidia-smi` is not recognized:** install or repair the NVIDIA driver, reboot, and rerun setup.
- **A model does not fit:** another GPU application may be holding VRAM. Inflect's header shows VRAM in use before loading; `nvidia-smi` lists the processes.
- **The LAN address does not open from another device:** allow Python through Windows Firewall for private networks, or check Settings → Network.
- **`git apply` reports a patch conflict:** a TabbyAPI checkout was modified by hand. Delete `%LOCALAPPDATA%\Inflect\sources\tabbyAPI` and rerun setup.
- Startup errors appear in a message box; details are in `%LOCALAPPDATA%\Inflect\state\manager.log`.

## Tests

```powershell
$env:PYTHONPATH = "$env:TEMP\inflect-test-deps"
& "$env:LOCALAPPDATA\Inflect\manager\Scripts\python.exe" -m pip install --target $env:PYTHONPATH filelock==3.32.6 huggingface-hub==1.31.0
& "$env:LOCALAPPDATA\Inflect\manager\Scripts\python.exe" -m pytest -q
```

Linux-only fixtures (sysfs power sensors, the Bash installer) are skipped on Windows, and Windows-only checks (job objects, junctions, the PowerShell installer) are skipped on Linux.
