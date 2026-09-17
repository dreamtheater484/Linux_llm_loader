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

The interface opens at `http://127.0.0.1:7860`. Lumen starts in computer-only mode. Its inference engine always remains bound to loopback.

## Optional private-LAN access

To let phones, tablets, or other computers on the directly connected private network open Lumen:

```bash
python3 scripts/configure-access.py --lan
```

Quit and reopen Lumen. The command prints the private URL to use on other devices. LAN mode binds the GUI to the detected private IPv4 address and accepts clients only from that exact subnet. Requests from public internet addresses are rejected even if a router is accidentally configured to forward the port. No inference-engine port or temporary engine key is exposed.

Every trusted device on that subnet can use the GUI; Lumen does not provide individual user accounts. Avoid LAN mode on guest, hotel, university, or other untrusted shared networks.

Return to computer-only access with:

```bash
python3 scripts/configure-access.py --local
```

Quit and reopen Lumen again to apply the change. A changing DHCP address may also require rerunning `--lan`.

## External model drive

### Download Qwen3.8 Flash Next 4.05 bpw on Windows

Use the updated `download-models.ps1` from this checkout. It reuses the resumable HTTP downloader, with Xet disabled. In PowerShell, from the checkout folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\download-models.ps1 -Models Qwen4 -ModelRoot "D:\Documents\AI\models"
```

Replace the drive/path with your existing model folder. Requires Windows PowerShell 5.1+ and Python 3.10+. It selects only [turboderp's `4.05bpw_h6_ng6` revision](https://huggingface.co/turboderp/Qwen3.8-Flash-Next-exl3/tree/4.05bpw_h6_ng6), including its vision tower, PLE/n-gram table, MTP weights, tokenizer and chat template. The model gets its own `Qwen3.8-Flash-Next-EXL3-4.05bpw` folder; existing 3-bit files stay separate.

At verification the complete repository is 107.46 GB (100.08 GiB). Allow about **120 GB free** for the download and the script's headroom check. Add `-List` to preview current sizes without downloading weights, or `-VerifyOnly` to recheck an existing download. If interrupted, rerun the same command: completed files are reused and saved partial files resume. Final checksum verification reads all files and can take several minutes.

The downloader pins the first resolved commit, validates HTTP byte ranges, retries expired links/timeouts/rate limits, checks remaining disk space, prevents simultaneous writers, logs progress, and reports success only after checksum verification. It does not execute model code. The default selection remains the original three 3-bit models; `Qwen4` is opt-in, and explicit `All` selects all four variants.

### Storage

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
6. Use **Benchmarks** for timed coding evaluations, an archive with comparison and report exports, or the original prompt-processing and decode speed test.
7. Click **Save profile** to name the current setup. Saved profiles can be loaded, edited, renamed, deleted, restored, or saved as a copy. Each card also shows its complete reusable JSON configuration with a one-click **Copy config** button.

The conversation follows new output automatically. Scrolling away pauses tail following; **Jump to latest** resumes it. Closing the browser tab leaves the local server running. **Unload model** frees model RAM and VRAM. **Quit Lumen** stops the server.

## Coding benchmarks in 30 minutes or less

Open **Benchmarks → Run a benchmark**, choose a test, and prepare its environments once. Load the model/profile you want to measure, check the displayed configuration, choose a 10-, 20-, or 30-minute maximum, and start. Preparation downloads are outside the timer; timed runs use cached data and containers. **Stop & archive** retains completed results and available partial answers.

| Mode | What it measures | Fixed local subset |
|---|---|---|
| Quick coding | Writing correct Python functions, checked with the original and extended [EvalPlus](https://github.com/evalplus/evalplus) tests | 20 HumanEval+ problems; up to 90 seconds per problem |
| Repository coding | Inspecting and fixing real codebases with Lumen's bash agent, then grading in a fresh checkout with the official [SWE-bench](https://github.com/SWE-bench/SWE-bench) harness | 10 minutes: Pylint; 20 minutes: Pylint + Flask; 30 minutes: Pylint + Flask + pytest; one fixed SWE-bench Lite issue per repository |
| Speed test | Prompt-processing and decode throughput, without grading answers | The existing three prompts |

The short coding modes are practical alternatives to a lengthy DeepSWE run. They are **not DeepSWE or LiveBench implementations** and their scores are not full-suite or official leaderboard results. The repository runner is Lumen's own JSON/bash agent, with one attempt and at most 40 turns. It works without requiring a checkpoint-specific native tool parser. Each preset selects only the repositories it has time for. It reserves time within the remaining run budget to capture and grade the latest patch, gives the agent remaining-time/action feedback, and saves a patch checkpoint after every command. The archive and copied report show when the agent ran out of actions or produced no patch. Preset task IDs and subset fingerprints are recorded; compare scores only across the same subset and conditions. Vision remains in the recorded configuration, but these are text-only coding tests, not a vision evaluation.

**Reading results:** passed tasks satisfy the upstream tests; failed tests are distinct from runner errors, time limits, and unattempted tasks. Aggregate accuracy appears only when every selected task has a grading result. Partial runs show passed/selected and grading coverage. Time limits measure speed and quality together; a small subset is a quick comparison, not a precise ranking. Generation uses the loaded maximum output, temperature, and reasoning choice. Output limits include reasoning. Sampling defaults other than temperature remain the installed engine's defaults, and results can vary across repeated runs.

**Archive and sharing:** every run records the model name, path, quantization, model ID, file metadata and available download receipt; all settings including context, KV precision, MTP and draft length, vision, reasoning, output cap, temperature, CPU placement/threads, and chunk size; effective and requested engine configuration; runtime packages/source fingerprints; hardware; dataset revision, immutable Docker image IDs, task IDs, prompts, limits, timings, and available generation metrics. File metadata fingerprints are not full weight checksums; small model configuration/template files are hashed. Saved-profile names are captured when their configuration matches exactly.

Use **Archive** to search by model or quantization, inspect individual tasks, or compare two runs. **Copy full report** produces Markdown suitable for pasting elsewhere; Markdown, JSON, and ZIP downloads are also available. ZIPs include available answers, reasoning, trajectories, code/patches, and grader output. Reports contain local model paths and configuration details. Their contents can be inspected before sharing. Archives survive restarts and remain available if a model/profile is later renamed or removed.

Results live in `~/.local/share/linux-llm-loader/state/results.sqlite3` and `state/evaluations/<run-id>/`; cached manifests live under `state/evaluations/assets/`. Copy both the database and evaluation directory when backing up. Docker stores images in its own data directory. Nothing is uploaded by the benchmark runner. A previously interrupted run is retained with an **Interrupted** status.

### Install the benchmark execution environment

Docker runs generated code and repository tests; model inference stays in the existing host engine. The containers receive no model files, user checkout, credentials, or Docker socket. Task execution has no network access, drops Linux capabilities, and is limited to 2 CPU cores, 4 GiB RAM, and 256 processes. The repository grader uses a second fresh container. First preparation checks that each reference fix passes and that its unfixed baseline fails before making that subset runnable.

Install Docker on an existing Ubuntu machine:

```bash
sudo /bin/bash scripts/install-benchmark-docker.sh --user "$(id -un)"
```

The installer enables Docker at startup and adds the named user to the Docker group, which grants root-equivalent control. Lumen uses `sg` to activate that already-granted membership in an older login session; Ubuntu 26.04's missing login utility is installed when necessary. New installations can use `./scripts/setup-ubuntu.sh --model-dir "/path/to/models" --with-benchmarks`.

In Lumen, click **Prepare benchmark** for each coding mode. The first build/pulls can take several minutes and several GB of disk space. Preparation is cancellable, and completed Docker layers are reused on retry. A failed environment check is shown as a setup error, never a model failure. EvalPlus 0.3.1 and SWE-bench 3.0.15 are isolated in the evaluator image, and its actual installed dependencies and image ID are archived. Tasks are selected deterministically using `lumen-coding-v1`; the repository subset is versioned as `lumen-swe-offline-v2`, replacing Requests (whose historical tests require public HTTP services) with Pylint while preserving the Flask and pytest selections. All upstream required and regression tests remain enforced. Setup failures include a concise explanation and a downloadable full log. The resolved dataset revision is pinned in the cached manifest. Avoid clearing Docker images needed by a prepared subset; if an image is removed, rerun preparation.

The **Thinking** selector above the message box applies to the next message, even after loading the model. **Model default** leaves reasoning to the checkpoint's chat template; **Off** and native effort levels appear only when identified in that template. The current Qwen3.8 Flash Next template offers Low, Medium and Extra high (default); both DeepSeek V4 Flash templates offer Low (default), High and Max. Other checkpoints may offer different choices. Effort levels are model instructions, not fixed token budgets.

There is no separate 1K thinking cap. **Maximum output** limits thinking and the final answer together, so increase it for longer reasoning. An output-limit notice explains when generation stops at that limit. Saved profiles include the selected effort; older profiles retain Off if thinking was disabled, while previously enabled thinking becomes Model default. Changing effort does not change GPU placement or reload weights, although a changed prompt prefix may reduce prompt-cache reuse.

**Decode speed includes thinking tokens**, answer tokens and tool-call output. The engine counts generated token IDs before splitting the response into channels; prompt processing is separate. The pinned ExLlamaV3 1.5.0 wheel has a cumulative-counter bug after multiple output requeues: earlier tokens are omitted while elapsed generation time is retained. Setup applies `patches/exl3-cumulative-output-tokens.patch` to the installed Python module, correcting counts and reported speed for long responses without changing inference settings or token generation. See [the accounting investigation](validation/GENERATION_ACCOUNTING.md).

API clients can send `reasoning_effort` with `/api/chat`, `/api/token-count`, or `/v1/chat/completions`. Use `default`, `off` (or OpenAI's `none` alias on the `/v1` route), or a native level listed by the model's `reasoning.options` in `/api/library`. Omitting it uses the loaded profile's saved choice. Unsupported levels are rejected; the same template variables are used for token counting and generation. vLLM and llama.cpp integrations remain unqualified until tested with their installed engine/checkpoint versions. Their request template controls are documented by [vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) and [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

## Model and engine support

| Format | Engine | Status |
|---|---|---|
| EXL3 | ExLlamaV3 1.5.0 through pinned TabbyAPI | Installed and qualified by the setup script |
| Native safetensors | vLLM | Adapter present; runtime and model-specific combinations are not installed or qualified |
| GGUF | llama.cpp server | Adapter present; runtime and model-specific combinations are not installed or qualified |

The current 3-bit model set uses EXL3 3.04–3.05 bpw checkpoints. EXL3 weights cannot be loaded by vLLM by changing the engine selector. Vision also requires a checkpoint with a compatible embedded vision component or projector.

## OpenAI-compatible tools

Clients connect to Lumen's `/v1` base URL and must send `X-Lumen-Local: 1` on POST requests. No client API key is needed. Query `/v1/models` for the loaded model ID. The internal engine remains authenticated and bound to loopback.

For ExLlamaV3 Qwen3.8 Flash Next, Lumen selects TabbyAPI's `qwen3_coder` parser only when the checkpoint architecture and selected chat template match its syntax. `/api/status` reports the active `tool_calling` capability. Other model/engine combinations retain ordinary chat; requests offering tools are explicitly rejected until a compatible parser is configured.

The pinned Tabby runtime includes `patches/tabby-qwen-tool-schema.patch`. It passes tool schemas to the native Qwen parser, preserves explicitly declared string parameters verbatim, and accepts `True`/`False` only for explicitly boolean parameters. Duplicate parameters are rejected and output-limit termination is preserved, so truncated calls cannot be reported as completed calls. Final arguments must still pass Lumen's JSON Schema validation.

`tools` reach the native chat template and token counter. Structured calls retain IDs, names and JSON arguments in both response modes. Streaming calls include indexes; calls are emitted after the engine finishes parsing and Lumen validates them, so arguments may arrive as one complete fragment. Lumen does not execute tools. The client supplies assistant `tool_calls` followed by `role: "tool"` results with matching `tool_call_id` values; multiple results may arrive in a different order.

| Tool setting | Behavior |
|---|---|
| Omitted or `auto`, with tools | Model chooses whether to call the offered functions |
| `none`, or no tools | Definitions omitted and native tool parsing disabled for that request |
| `required` or a named function choice | HTTP 422: installed TabbyAPI does not enforce these modes |
| `parallel_tool_calls: false` with active tools | HTTP 422: a single-call constraint is not supported |
| `strict: true` | HTTP 422: constrained tool generation is not supported; ordinary tool arguments are still schema-validated after generation |

Malformed, truncated, unknown or schema-invalid engine calls fail instead of becoming executable calls. Lumen never parses tool-looking prose itself. Invalid tool history and unsupported reasoning/tool options are rejected before opening a response stream.

Qwen3.8 supports `reasoning_effort` values `default`, `off`, `low`, `medium`, and `xhigh`. `default` uses the checkpoint default, currently `xhigh`; the API also accepts `none` as an alias for `off`, and `on` enables the checkpoint's default thinking level. `minimal`, `high`, and `max` are rejected for this checkpoint even though other models may support them. Omitting the field uses the loaded profile's choice. Reasoning is returned separately in `reasoning_content`.

After updating an existing installation, rerun the setup script with your existing model/runtime locations; it installs manager dependencies and applies the idempotent Tabby patches. Wait for active generation to finish before restarting Lumen and reloading the model to activate its native parser. Profiles need no migration. To exercise the complete API without executing real tools:

```bash
python3 scripts/verify_tool_calls.py --url http://YOUR_PRIVATE_LAN_IP:7860 --output validation/tool-calling-live.json
```

This checks schema visibility, streaming and non-streaming calls, reasoning off/low, explicit mode rejection, and synthetic tool-result round trips. It is API verification, not an end-to-end OpenCode tool or subagent test. Keep the generated report private.

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

The launcher also accepts these environment overrides: `LUMEN_PROJECT`, `LUMEN_RUNTIME`, `LUMEN_MODEL_ROOT`, `LUMEN_PORT`, `LUMEN_LISTEN_HOST`, `LUMEN_LAN_NETWORK`, `LUMEN_PUBLIC_URL`, `LUMEN_MOUNT_DEVICE`, `LUMEN_STATE`, `LUMEN_TABBY`, and `LUMEN_GGUF_SERVER`.

## Troubleshooting

- **`nvidia-smi` fails:** repair the host NVIDIA driver and reboot. The installer intentionally does not alter drivers.
- **No models appear:** rerun setup with the directory immediately above the model folders, then use the library refresh button.
- **Model loading fails:** open **Engine log**. Common causes are an incomplete checkpoint, incompatible settings, insufficient RAM, or insufficient VRAM.
- **First response is slow:** GPU kernels can compile on first use. Compare sustained performance over several requests.
- **External drive moved:** rerun setup with the new `--model-dir` and optional `--mount-device` values.
- **Port 7860 is occupied:** stop the other service or set a different `LUMEN_PORT` before launching.
- **LAN URL stopped working:** the private DHCP address probably changed; rerun `python3 scripts/configure-access.py --lan` and restart Lumen.
- **Full disk:** the private runtime uses about 10 GiB before caches. Models are stored separately and are not deleted by Lumen.

## Validation and development

The repository keeps test procedures rather than machine-generated reports. See [VALIDATION.md](VALIDATION.md). Run the application tests with the inference environment:

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
