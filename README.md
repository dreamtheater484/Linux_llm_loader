# Inflect · local model workbench

Inflect is a local Ubuntu interface for running large language models with a small set of understandable controls. It ships a qualified **ExLlamaV3 1.5.0 + TabbyAPI** path for EXL3 checkpoints and an optional pinned **llama.cpp b11050 / CUDA 12.8** runtime for GGUF models, including embedded Qwen MTP. The vLLM safetensors adapter still requires separate installation and qualification.

The interface includes streaming text and image chat, 256K context profiles, Q8/FP16/Q4 KV choices, vision and MTP controls, saved profiles, repeatable benchmarks, and live GPU power, CPU package power, VRAM, RAM, CPU load, prompt-processing speed, decode speed, and first-token time.


## Chat workspace

Inflect saves conversations, messages, reasoning, drafts and attachments on this computer in `state/conversations.sqlite3`. Open **Conversation history** (Ctrl+K) to search titles and message content, switch chats, or import a conversation. **New conversation** (Ctrl+Shift+O) starts a separate history. Changing models keeps the conversation open and records the model used for each answer.

- **Edit** and **Retry** create a new branch, preserving the original conversation. **Branch** continues from any message. Stop saves the partial answer. Replies are checkpointed once a second and on completion/disconnection; interrupted checkpoints are recovered after a manager restart.
- **Conversation options** contains rename (also click the title), chat instructions, JSON export/import, and permanent deletion. Inflect exports include attachment bytes; imports upload files individually so large histories do not depend on a single huge API request. Generic JSON with a `messages` list is also accepted for text conversations.
- Attach text, source code, HTML, SVG, Markdown, PDFs, PNG/JPEG/WebP/GIF images, and MP3/WAV audio. Files are limited to 12 MB each, 20 per message and 200 per conversation. PDFs support up to 250 pages and 2 million extracted characters. Text and PDF text use either inference engine. GIF images are converted to PNG for vision input. SVG source is sent as text. Images require a loaded vision model; audio playback is supported, but model input requires a transcript because neither installed engine has a qualified audio path. Scanned PDFs need page images or external OCR.
- Code blocks have copy, download and preview controls. The resizable preview panel supports HTML with inline CSS/JavaScript, SVG, Markdown, source/text, images, paged PDFs and audio. HTML and SVG run in a sandbox with no parent-page access and no external connections. Self-contained files work offline; remote scripts, fonts and images are intentionally blocked. Source and rendered views are available together with expand, restart and download controls.
- **Maximize chat** expands the workspace across the window. A compact top strip retains GPU/CPU power, VRAM, model RAM, CPU load, prompt speed, decode speed and first-token latency. Model settings open as a drawer; **Restore dashboard** returns to the regular layout.

**Permanent deletion:** there is no chat trash or undo. Deleting removes the conversation, draft, attachments and generated previews from the app. SQLite secure deletion scrubs freed database cells and the database uses delete-mode journals, not a retained WAL. If the conversation contains an assistant reply and an engine is loaded, Inflect unloads its owned engine process to release prompt/KV caches. The dialog explains this before deletion. Other active requests must finish first. Open app windows receive a deletion notification. Independent branches, downloaded exports, external backups and SSD-level forensic remnants are outside this guarantee.

Chats do not use browser local storage or IndexedDB for their content. Only the selected conversation ID is remembered in the browser. Revision checks prevent one window from silently overwriting another. The same qualified inference pipeline serves the chat UI and the compatible API for ExLlamaV3/TabbyAPI and llama.cpp.

Validation and instructions for repeating the isolated UI checks are in [the chat workspace report](validation/INFLECT_CHAT_WORKSPACE_20260922.md).


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

### 2. Clone Inflect

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
8. Adds **Inflect** to the application menu.

No model is downloaded or moved.

### 5. Start Inflect

Open **Inflect** from Ubuntu's application menu, or run:

```bash
./launch.sh
```

The interface opens at `http://127.0.0.1:7860`. Inflect starts in computer-only mode. Its inference engine always remains bound to loopback.

## Optional private-LAN access

To let phones, tablets, or other computers on the directly connected private network open Inflect:

```bash
python3 scripts/configure-access.py --lan
```

Quit and reopen Inflect. The command prints the private URL to use on other devices. LAN mode binds the GUI to the detected private IPv4 address and accepts clients only from that exact subnet. Requests from public internet addresses are rejected even if a router is accidentally configured to forward the port. No inference-engine port or temporary engine key is exposed.

Every trusted device on that subnet can use the GUI; Inflect does not provide individual user accounts. Avoid LAN mode on guest, hotel, university, or other untrusted shared networks.

Return to computer-only access with:

```bash
python3 scripts/configure-access.py --local
```

Quit and reopen Inflect again to apply the change. A changing DHCP address may also require rerunning `--lan`.

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

The device path is written only to `~/.config/inflect/config.json`; it is never added to Git.

## Using the workbench

1. Select a model in the library.
2. Start with **256K** for the qualified EXL3 profiles or **32K** for GGUF, **Q8 KV**, and **Vision** where a compatible component is identified. Increasing context also increases memory use.
3. Choose an MTP length and CPU expert share from a saved or recommended profile.
4. Click **Load model** and wait for the ready state.
5. Type a message, drag in an image, choose an image file, or paste a screenshot with **Ctrl+V**.
6. Use **Benchmarks** for coding evaluations, an archive with comparison and report exports, or the original prompt-processing and decode speed test.
7. Click **Save profile** to keep the current setup. Profiles start collapsed, with compact automatic names showing the model, weight quant, context/cache precision, measured decode speed when available, vision, MTP, and CPU share. Drag the grip to reorder them; keyboard arrows and expanded move buttons work too. **Duplicate** creates an independent copy. Expand a row to edit, rename, view matching benchmark results, delete, or copy its full JSON configuration. Deleted profiles can be restored. Automatic names follow settings and benchmark changes; custom names remain available.
8. Loading displays a prominent banner with the requested model, elapsed time, engine logs, and cancellation. When the engine verifies readiness, a persistent **Ready to chat** message names the loaded model and its context, vision, and MTP settings.

Profile cards show the full model variant and quant, generation and prefill speeds, context/KV precision, and recorded VRAM/RAM totals. Search by model/quant/profile; filter minimum speeds, exact context, and maximum memory; sort by model, either speed, context, or memory. Reset filters and choose saved order to drag again. Profiles poll the benchmark archive every three seconds, including coding evaluations and legacy speed timings. Each speed uses the newest valid measurement from a completed or time-limited benchmark with matching settings, with its source/date shown. Missing measurements remain blank.

Memory is sampled once the loaded engine has been ready for at least three seconds and inference/benchmarks are idle. It records **total system VRAM/RAM usage**, including other applications, rather than claiming a model-only allocation. The timestamp is available on hover. Values persist in the results database and are shared by copies with identical loading settings. Changing context, KV, placement, or other loading settings requires a new measurement; sampling-only changes retain it.

The RAM dashboard shows three figures: **model RAM** as the headline, **total usable RAM**, and **available RAM (estimated) while keeping the loaded model resident**. The model measurement includes the engine and workers, counting shared pages proportionally (PSS). Dashboard headroom conservatively subtracts resident file-backed model PSS from the system available-memory estimate, clamped to zero and to capacity minus model residency. Anonymous/shared allocations are already excluded by system availability and are not subtracted twice. Locked/shared file pages and kernel reserves can make this estimate conservative; it is not a guarantee that another model will fit. Missing or inconsistent measurements show a dash. With no model loaded, the estimate uses system availability. The dashboard and its info panel use plain language; raw cache categories are not displayed. Profile RAM sorting/filtering remains based on total occupied physical RAM including cache (`MemTotal − MemFree`).

Backend load admission still uses Linux's `MemAvailable` estimate: it includes reclaimable cache and is not completely free memory. Saved readings from before this correction cannot be reconstructed; their RAM value is excluded from comparisons and marked for remeasurement on the next load. New response/benchmark peaks include total RAM, cache, non-cache and model residency with explicit accounting metadata; component peaks are independent maxima and must not be added together. Historical benchmark exports retain their original numbers, labelled `legacy_total_minus_available`; new system measurements use `physical_including_cache_v1`.

Profile speed uses matching effective engine and generation settings. Results for the same model with different settings appear separately inside the expanded profile. Speed tests use three short prompts and 512-token output limits; their numbers are not long-context throughput guarantees.

The conversation follows new output automatically. Scrolling away pauses tail following; **Jump to latest** resumes it. Closing the browser tab leaves the local server running. **Unload model** frees model RAM and VRAM. **Quit Inflect** stops the server.

Before every model load or reload, Inflect releases the previous LLM and prepares ComfyUI. For a Docker deployment, set `comfyui_container` in `~/.config/inflect/config.json` (or `INFLECT_COMFYUI_CONTAINER`) to the explicit ComfyUI backend container name. Inflect waits for its queue to become idle, stops that container, confirms it has stopped, then launches the LLM. Once loading finishes, fails, or is cancelled, Inflect restarts ComfyUI if it was running before and waits for its API to return. An already stopped ComfyUI stays stopped. Docker commands use the inspected container ID, have bounded timeouts, and cancellation waits for an in-flight stop before restoring the service. No workflow files or saved outputs are deleted. A busy queue is preserved; it times out with a specific message instead of being cancelled. Avoid submitting new ComfyUI work during a model switch.

The local installation uses `pi-ubuntu-comfy`. This managed lifecycle replaces the allocator-zero check: ComfyUI can retain small CUDA allocations after a successful `/free`, so zero bytes is not a reliable completion requirement. The load banner shows stop, model load, and restart stages. ComfyUI's empty backend may allocate its CUDA context again after restart; a new image workflow can also use VRAM again.

The ComfyUI API address defaults to `http://127.0.0.1:8188`; override it with `INFLECT_COMFYUI_URL` using a loopback address. Without an explicitly configured container, the portable API-only mode still requests `/free` and checks the queue/allocator. It cannot conclusively confirm release when the allocator retains memory; its error advises managed container cleanup rather than incorrectly blaming queued jobs. A refused connection in API-only mode is skipped. Managed mode requires Docker CLI access for the Inflect user and never guesses which container to stop.

## Coding benchmarks

Open **Benchmarks → Run a benchmark**, choose a test, and prepare its environments once. Load the model/profile you want to measure and check the displayed configuration. HumanEval+ uses Short for 20 problems, Medium for 40, and Long for 80. For repository coding, the labels select one, two, or three repositories. Neither mode imposes a normal generation, task, or whole-run timer. Preparation downloads are outside the run. **Stop & discard** cancels the test and removes that run and its temporary artifacts. Running checkpoints are kept only for live progress; cancelled and interrupted runs are excluded from the archive and removed, including after a restart.

| Mode | What it measures | Fixed local subset |
|---|---|---|
| Quick coding | Writing correct Python functions, checked with the original and extended [EvalPlus](https://github.com/evalplus/evalplus) tests | Short: 20; Medium: 40; Long: 80 HumanEval+ problems |
| Repository coding | Inspecting and fixing real codebases with Inflect's bash agent, then grading in a fresh checkout with the official [SWE-bench](https://github.com/SWE-bench/SWE-bench) harness | Short: Pylint; Medium: Pylint + Flask; Long: Pylint + Flask + pytest |
| Speed test | Prompt-processing and decode throughput, without grading answers | The existing three prompts |

The coding modes are practical alternatives to a lengthy full-suite run. They are **not DeepSWE or LiveBench implementations** and their scores are not full-suite or official leaderboard results. HumanEval+ uses deterministic nested prefixes: Medium contains all 20 Short problems plus 20 more, and Long contains all 40 Medium problems plus 40 more. Repository coding also uses deterministic prefixes: Short runs Pylint, Medium adds Flask, and Long adds pytest. Both modes have no model-generation, per-task, or whole-run wall-clock cutoff; each answer may finish naturally. HumanEval+'s isolated grader retains a 120-second safety ceiling for generated code that hangs. Repository shell commands retain a 45-second ceiling and its isolated grader retains a 150-second ceiling. The repository runner is Inflect's own JSON/bash agent, with one attempt and at most 40 turns. The archive and copied report show when the agent ran out of actions or produced no patch. Preset task IDs and subset fingerprints are recorded; compare scores only across the same subset and conditions. Vision remains in the recorded configuration, but these are text-only coding tests, not a vision evaluation.

**Live output:** coding and speed benchmarks open a floating terminal with streamed answers, thinking, agent commands, command/test output, and progress. Scroll up or disable **Follow tail** to pause scrolling; **Jump to latest** resumes it. Expand to full screen or minimize it while working elsewhere. The tail retains up to 1,048,576 characters in memory and is discarded for aborted runs. Completed coding runs keep full artifacts in their archive. A completion popup names the model, shows scores when available and both speeds, and opens the full results.

SWE-bench issues are individually resolved or unresolved, without partial credit. A fully graded Short run has one issue (0% or 100%); Medium has two (0%, 50%, or 100%); Long has three (0%, 33.3%, 66.7%, or 100%).

**Reading results:** passed tasks satisfy the upstream tests; failed tests are distinct from runner errors, grader safety timeouts, and unattempted tasks. Aggregate accuracy appears only when every selected task has a grading result. Partial runs show passed/selected and grading coverage. A small subset is a quick comparison, not a precise ranking. Generation uses the loaded maximum output and temperature, plus the **Thinking for this benchmark** choice. That choice is frozen for the run and recorded in its results. Output limits include reasoning. Sampling defaults other than temperature remain the installed engine's defaults, and results can vary across repeated runs.

**Archive and sharing:** every run records the model name, path, quantization, model ID, file metadata and available download receipt; all settings including context, KV precision, MTP and draft length, vision, reasoning, output cap, temperature, CPU placement/threads, and chunk size; effective and requested engine configuration; runtime packages/source fingerprints; hardware; dataset revision, immutable Docker image IDs, task IDs, prompts, limits, timings, and available generation metrics. File metadata fingerprints are not full weight checksums; small model configuration/template files are hashed. Saved-profile names are captured when their configuration matches exactly.

Use **Archive** to search by model or quantization, inspect individual tasks, or compare two runs. **Copy full report** produces Markdown suitable for pasting elsewhere; Markdown, JSON, and ZIP downloads are also available. ZIPs include available answers, reasoning, trajectories, code/patches, and grader output. Reports contain local model paths and configuration details. Their contents can be inspected before sharing. Archives survive restarts and remain available if a model/profile is later renamed or removed.

The archive uses two columns on Full HD and three on QHD, with full model names on hover. Search current library models through the live model picker, filter by suite or outcome, and sort by date or score. Speed tests have the same model filter, sorting, and deletion controls. Select individual runs or all visible results for bulk deletion/restoration. Exports and two-run comparison remain available.

Coding result cards and the results dialog show **decode tok/s** and **prefill tok/s** from the saved engine timings. The dialog also shows first-token latency and per-task response speeds. Values are medians across measured responses, exclude test execution, include thinking in decoding, and reflect prompt-cache reuse for prefill. Older records are calculated from their existing response metrics; missing timings show a dash. Markdown reports and comparisons include these speeds too.

Results live in `~/.local/share/linux-llm-loader/state/results.sqlite3` and `state/evaluations/<run-id>/`; cached manifests live under `state/evaluations/assets/`. Copy both the database and evaluation directory when backing up. Docker stores images in its own data directory. Nothing is uploaded by the benchmark runner. Interrupted runs are discarded on startup. Completed runs can be moved to **Recently deleted** and restored there; deleting a result also removes it from saved-profile benchmark matching.

### Install the benchmark execution environment

Docker runs generated code and repository tests; model inference stays in the existing host engine. The containers receive no model files, user checkout, credentials, or Docker socket. Task execution has no network access, drops Linux capabilities, and is limited to 2 CPU cores, 4 GiB RAM, and 256 processes. The repository grader uses a second fresh container. First preparation checks that each reference fix passes and that its unfixed baseline fails before making that subset runnable.

Install Docker on an existing Ubuntu machine:

```bash
sudo /bin/bash scripts/install-benchmark-docker.sh --user "$(id -un)"
```

The installer enables Docker at startup and adds the named user to the Docker group, which grants root-equivalent control. Inflect uses `sg` to activate that already-granted membership in an older login session; Ubuntu 26.04's missing login utility is installed when necessary. New installations can use `./scripts/setup-ubuntu.sh --model-dir "/path/to/models" --with-benchmarks`.

In Inflect, click **Prepare benchmark** for each coding mode. The first build/pulls can take several minutes and several GB of disk space. Preparation is cancellable, and completed Docker layers are reused on retry. A failed environment check is shown as a setup error, never a model failure. EvalPlus 0.3.1 and SWE-bench 3.0.15 are isolated in the evaluator image, and its actual installed dependencies and image ID are archived. Tasks are selected deterministically using `inflect-coding-v1`; the repository subset is versioned as `inflect-swe-offline-v2`, replacing Requests (whose historical tests require public HTTP services) with Pylint while preserving the Flask and pytest selections. All upstream required and regression tests remain enforced. Setup failures include a concise explanation and a downloadable full log. The resolved dataset revision is pinned in the cached manifest. Avoid clearing Docker images needed by a prepared subset; if an image is removed, rerun preparation.

The **Thinking** selector above the message box applies to the next message, even after loading the model. **Model default** leaves reasoning to the checkpoint's chat template; **Off** and native effort levels appear only when identified in that template. The current Qwen3.8 Flash Next template offers Low, Medium and Extra high (default); both DeepSeek V4 Flash templates offer Low (default), High and Max. Other checkpoints may offer different choices. Effort levels are model instructions, not fixed token budgets.

There is no separate 1K thinking cap. **Maximum output** limits thinking and the final answer together, so increase it for longer reasoning. An output-limit notice explains when generation stops at that limit. **Profile thinking preference** saves the initial choice for chat and benchmarks; their own selectors apply independently without changing the saved profile. Loading keeps all native modes available and does not freeze a thinking level into the engine. New profiles use Model default, with the template's default level displayed when detectable. Older profiles retain Off if thinking was disabled, while previously enabled thinking becomes Model default. Changing effort does not change GPU placement or reload weights, although a changed prompt prefix may reduce prompt-cache reuse. Models with only an on/off switch expose that switch; models without detected controls expose only Model default.

**Decode speed includes thinking tokens**, answer tokens and tool-call output. The engine counts generated token IDs before splitting the response into channels; prompt processing is separate. The pinned ExLlamaV3 1.5.0 wheel has a cumulative-counter bug after multiple output requeues: earlier tokens are omitted while elapsed generation time is retained. Setup applies `patches/exl3-cumulative-output-tokens.patch` to the installed Python module, correcting counts and reported speed for long responses without changing inference settings or token generation. See [the accounting investigation](validation/GENERATION_ACCOUNTING.md).

API clients can send `reasoning_effort` with `/api/chat`, `/api/token-count`, `/api/evaluations`, `/api/benchmark`, or `/v1/chat/completions`. Use `default`, `off` (or OpenAI's `none` alias on the `/v1` route), or a native level listed by the model's `reasoning.options` in `/api/library`. Omitting it uses the loaded profile's saved choice. Unsupported levels are rejected; the same template variables are used for token counting and generation. llama.cpp supports exact advance counting for text and tool conversations; image token usage is reported after generation. vLLM remains unqualified. Engine template controls are documented by [vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) and [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/b11050/tools/server/README.md).

## Model and engine support

| Format | Engine | Status |
|---|---|---|
| EXL3 | ExLlamaV3 1.5.0 through pinned TabbyAPI | Installed and qualified by the setup script |
| Native safetensors | vLLM | Adapter present; runtime and model-specific combinations are not installed or qualified |
| GGUF | llama.cpp b11050, CUDA 12.8 | Optional pinned installer; Qwen3.6 35B and Qwen3.8 27B tested, including embedded 27B MTP |

The current 3-bit model set uses EXL3 3.04–3.05 bpw checkpoints. EXL3 weights cannot be loaded by vLLM by changing the engine selector. Vision also requires a checkpoint with a compatible embedded vision component or projector.

### llama.cpp and GGUF models

On an existing installation, run:

```bash
python3 scripts/install-llama.py
```

Use `--runtime-dir PATH` for a custom runtime directory, or pass `--with-llama` to `scripts/setup-ubuntu.sh` on a fresh installation. The installer uses SHA-256-pinned official Linux x86_64 CUDA 12.8 packages and private CUDA libraries; it needs no system CUDA toolkit or driver changes. Allow 4 GiB free for installation. Restart Inflect after upgrading its application code. A custom executable can be selected with `INFLECT_GGUF_SERVER`.

Choose **more local variants** in the library or search for a model to see GGUF files. Auto selects llama.cpp for them. Saved profiles retain context, cache precision, prediction mode and draft length, CPU placement, threads, prompt chunk size, sampling temperature, and thinking choice. The default GGUF context is 32K; larger contexts require separate memory and occupied-context testing. The engine must allocate exactly the requested capacity; automatic context shrinking and history shifting are disabled.

For MoE models, **Fine-tune → CPU placement → Experts** keeps attention on the GPU and moves expert weights from the selected percentage of layers into RAM. **Whole layers** also moves attention and other layer work. Zero CPU share requests full GPU offload. For the 35B Q6_K_P model on a 32 GiB card, the initial setting uses 10% expert-layer offload to leave room for cache and vision; dense 27B profiles start with full GPU offload.

Qwen Flash Next checkpoints can include a large PLE n-gram embedding table. Inflect keeps that table in system RAM by default to avoid per-token storage reads; this can consume tens of GiB. **Fine-tune → PLE n-gram table** can instead stream lookup rows from model storage. ExLlamaV3 maps this choice to `ngram_ram`; llama.cpp maps it to `--lazy-mode off` for RAM residency or `--lazy-mode on` for storage streaming. In llama.cpp, lazy mode applies to any other eligible lazy tensors as well.

**Prediction acceleration** enables llama.cpp's native `draft-mtp` when a supported GGUF declares embedded MTP weights. Both Qwen3.8 27B Q4_K_P and Q6_K checkpoints include them. Draft length defaults to two; the server verifies speculative decoding is active before reporting readiness, and records accepted/rejected draft counts with each response. GGUF metadata, rather than the filename, determines MTP availability. The existing HauhauCS Qwen3.6 35B Aggressive Q6_K_P has no MTP weights and runs with prediction off. No checkpoint is replaced or downloaded by engine setup. HauhauCS's separate FastMTP sidecar and patched runtime are not part of this embedded-MTP integration.

Qwen thinking modes and structured tool calls use the checkpoint's native Jinja template. Matching vision projectors are discovered in the model directory or library root; ambiguous matches remain disabled. Without a matching projector, text and MTP still work. Prompt-processing speed and decode speed come from llama.cpp's native timings, with thinking included in generated-token counts. See [GGUF validation](validation/LLAMA_CPP.md) for the exact tested scope.

## OpenAI-compatible tools

Clients connect to Inflect's `/v1` base URL and must send `X-Inflect-Local: 1` on POST requests. No client API key is needed. Query `/v1/models` for the loaded model ID. The internal engine remains authenticated and bound to loopback.

For ExLlamaV3 Qwen3.8 Flash Next, Inflect selects TabbyAPI's `qwen3_coder` parser only when the checkpoint architecture and selected chat template match its syntax. For supported Qwen GGUF models, llama.cpp handles parsing through the embedded Jinja template. `/api/status` reports the active `tool_calling` capability. Other model/engine combinations retain ordinary chat; requests offering tools are explicitly rejected until a compatible parser is configured.

The pinned Tabby runtime includes `patches/tabby-qwen-tool-schema.patch`. It passes tool schemas to the native Qwen parser, preserves explicitly declared string parameters verbatim, and accepts `True`/`False` only for explicitly boolean parameters. Duplicate parameters are rejected and output-limit termination is preserved, so truncated calls cannot be reported as completed calls. Final arguments must still pass Inflect's JSON Schema validation.

`tools` reach the native chat template and token counter. Structured calls retain IDs, names and JSON arguments in both response modes. Streaming calls include indexes; calls are emitted after the engine finishes parsing and Inflect validates them, so arguments may arrive as one complete fragment. Inflect does not execute tools. The client supplies assistant `tool_calls` followed by `role: "tool"` results with matching `tool_call_id` values; multiple results may arrive in a different order.

| Tool setting | Behavior |
|---|---|
| Omitted or `auto`, with tools | Model chooses whether to call the offered functions |
| `none`, or no tools | Definitions omitted and native tool parsing disabled for that request |
| `required` or a named function choice | HTTP 422: installed TabbyAPI does not enforce these modes |
| `parallel_tool_calls: false` with active tools | HTTP 422: a single-call constraint is not supported |
| `strict: true` | HTTP 422: constrained tool generation is not supported; ordinary tool arguments are still schema-validated after generation |

Malformed, truncated, unknown or schema-invalid engine calls fail instead of becoming executable calls. Inflect never parses tool-looking prose itself. Invalid tool history and unsupported reasoning/tool options are rejected before opening a response stream.

Qwen3.8 supports `reasoning_effort` values `default`, `off`, `low`, `medium`, and `xhigh`. `default` uses the checkpoint default, currently `xhigh`; the API also accepts `none` as an alias for `off`, and `on` enables the checkpoint's default thinking level. `minimal`, `high`, and `max` are rejected for this checkpoint even though other models may support them. Omitting the field uses the loaded profile's choice. Reasoning is returned separately in `reasoning_content`.

After updating an existing installation, rerun the setup script with your existing model/runtime locations; it installs manager dependencies and applies the idempotent Tabby patches. Wait for active generation to finish before restarting Inflect and reloading the model to activate its native parser. Profiles need no migration. To exercise the complete API without executing real tools:

```bash
python3 scripts/verify_tool_calls.py --url http://YOUR_PRIVATE_LAN_IP:7860 --output validation/tool-calling-live.json
```

This checks schema visibility, streaming and non-streaming calls, reasoning off/low, explicit mode rejection, and synthetic tool-result round trips. It is API verification, not an end-to-end OpenCode tool or subagent test. Keep the generated report private.

## Files and privacy

| Data | Default location |
|---|---|
| Source checkout and compiled interface | Wherever this repository is cloned |
| Manager, Python, ExLlamaV3, PyTorch, TabbyAPI, caches | `~/.local/share/linux-llm-loader/` |
| Private machine configuration | `~/.config/inflect/config.json` |
| Profiles, benchmark history, logs, temporary engine keys | `~/.local/share/linux-llm-loader/state/` |
| Desktop shortcut | `~/.local/share/applications/inflect.desktop` |
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

The launcher also accepts these environment overrides: `INFLECT_PROJECT`, `INFLECT_RUNTIME`, `INFLECT_MODEL_ROOT`, `INFLECT_PORT`, `INFLECT_LISTEN_HOST`, `INFLECT_LAN_NETWORK`, `INFLECT_PUBLIC_URL`, `INFLECT_MOUNT_DEVICE`, `INFLECT_STATE`, `INFLECT_TABBY`, and `INFLECT_GGUF_SERVER`.

## Troubleshooting

- **`nvidia-smi` fails:** repair the host NVIDIA driver and reboot. The installer intentionally does not alter drivers.
- **No models appear:** rerun setup with the directory immediately above the model folders, then use the library refresh button.
- **Model loading fails:** open **Engine log**. Common causes are an incomplete checkpoint, incompatible settings, insufficient RAM, or insufficient VRAM.
- **First response is slow:** GPU kernels can compile on first use. Compare sustained performance over several requests.
- **External drive moved:** rerun setup with the new `--model-dir` and optional `--mount-device` values.
- **Port 7860 is occupied:** stop the other service or set a different `INFLECT_PORT` before launching.
- **LAN URL stopped working:** the private DHCP address probably changed; rerun `python3 scripts/configure-access.py --lan` and restart Inflect.
- **Full disk:** the private runtime uses about 10 GiB before caches. Models are stored separately and are not deleted by Inflect.

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

After backend changes, quit and reopen Inflect. After frontend-only changes, rebuild and refresh the browser.
