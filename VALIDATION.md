# Inflect validation

This repository deliberately does not publish machine-generated benchmark reports, local paths, hardware serial-like identifiers, profile names, prompts, or chat output. Validation artifacts are written locally and ignored by Git.

## Automated checks

After setup, run:

```bash
~/.local/share/linux-llm-loader/exl3/bin/python -m pytest -q
```

The suite covers:

- model-library scanning and incomplete checkpoints;
- context, KV, vision, MTP, and engine compatibility checks;
- paths containing spaces and protection against path escape;
- process cancellation and local request controls;
- telemetry parsing and unavailable sensors;
- profile creation, editing, deletion, and restoration;
- template-specific reasoning choices, legacy profile migration, matching token-count/generation controls, and changing effort per request without changing loaded settings;
- Windows model-download planning, resumption, integrity checks, and bounded failures.

The Windows-only PowerShell integration test is skipped on Linux. Local HTTP transfer tests use a temporary loopback server and do not download models.

The manager environment also runs the app tests. To run the entire suite there, install the development-only `pytest`, `filelock`, and `huggingface-hub` dependencies in a separate test environment or target directory; they are used by the downloader tests, not by benchmark inference.

## Settings and LAN acceptance

`tests/test_app_settings.py` covers private-LAN defaults and fail-closed discovery, opt-out persistence, configuration validation and file permissions, concurrent edits, ComfyUI skipping, engine version detection, update admission, and successful/failed installer activation with synthetic subprocesses. `tests/test_comfyui.py` also checks cancellation and restoration around model loads.

Build the frontend, run `tests/serve_chat_fixture.py` using the manager Python, and run `node frontend/tests/settings-browser.cjs`. Set `PLAYWRIGHT_MODULE` to your Playwright package if it is not installed in the frontend. This fixture has its own configuration and chat data. The browser test checks settings navigation, reload persistence, invalid input, model controls, engine version cards, and a narrow-screen layout. `frontend/tests/chat-browser.cjs` covers the separate chat regressions. Never point these destructive fixture tests at a real chat database.

For live acceptance, with no request or benchmark active:

1. Save Network off, restart from Settings, and verify the previous LAN socket is closed and localhost works.
2. Save Network on, restart, and verify the displayed LAN address and exact allowed subnet. Cross-origin requests must fail; a forged forwarding header must not change the source address the launcher trusts.
3. Confirm the previous ComfyUI preference is retained. Load a model with integration enabled; verify the named container is restored after startup.
4. Check that the loaded model's settings match the original profile after restoring it.
5. Engine update tests do not replace clean-machine installation qualification. Test new supported runtime locks on a spare installation before publishing them; custom engine paths must never be overwritten by the managed updater.

## Coding benchmark verification

`tests/test_benchmarks.py` covers frozen configurations, secret removal, all settings in Markdown, ZIP contents, database pagination/search, cancellation before and during work, partial-answer preservation, hard run deadlines, restart recovery, changed-asset rejection, concurrent-run protection, export routes, and subprocess timeout/output limits. These tests use synthetic results and do not exercise Docker.

Verify the real upstream graders separately, without loading or querying a model:

```bash
~/.local/share/linux-llm-loader/manager/bin/python scripts/verify_benchmarks.py \
  --prepare --suite all --output validation/coding-graders.json
```

Use the manager Python for your installation (older installations may use `.runtime/manager/bin/python`). `--prepare` builds/downloads task environments and checks each repository's reference fix and unfixed baseline. Omit it for an offline recheck of already prepared assets. The verifier runs all 20 HumanEval+ canonical solutions through EvalPlus, rejects a deliberately incorrect answer, stops infinite generated code with the external deadline, checks repository baselines, and checks container cleanup. It does not create fake model-performance archive entries. Do not run the verifier against the same state directory as an active coding benchmark.

Interface acceptance for the coding modes:

- Prepare both modes, start with the loaded configuration, change tabs, and verify live progress survives navigation.
- Stop during inference and during test execution. Completed results and available partial output must remain archived.
- Search archived runs beyond the first page; inspect a finished and an incomplete result. No aggregate accuracy should appear for an incompletely graded run.
- Copy a report and compare it to its JSON download. All model, quantization, KV, MTP, vision, reasoning, sampling, and placement settings must agree.
- Compare two runs; changed subset/protocol/time limits must be flagged. Inspect the ZIP's per-task answers, available patches/trajectories, and grader output.
- Verify keyboard/Escape behavior, clipboard fallback, download links, and narrow-screen layouts. UI screenshots with synthetic fixtures are visual checks, not model-performance evidence.

## Fresh-install qualification

Use a clean supported Ubuntu installation and run:

```bash
./scripts/setup-ubuntu.sh --model-dir "/path/to/models" --preflight-only
./scripts/setup-ubuntu.sh --model-dir "/path/to/models"
./launch.sh
```

Verify that:

1. Setup obtains Python 3.12 without relying on a pre-existing development or Codex environment.
2. The CUDA smoke test identifies the expected GPU and completes a real matrix operation.
3. The model library shows the configured directory and finds complete checkpoints.
4. The desktop shortcut and `launch.sh` open the same local application.
5. A model loads, generates a response, unloads, and leaves no managed engine process behind.

## Model acceptance checks

For every engine/checkpoint combination presented as qualified:

- Configure 262,144 total tokens and confirm the engine reports that cache capacity.
- Run a real near-capacity request while reserving room for the answer.
- Confirm Q8 KV cache or clearly label the engine's equivalent representation.
- For vision checkpoints, attach or paste a real image and confirm the engine receives it.
- For MTP, confirm that the prediction component loads and report accepted draft tokens.
- Measure prompt processing, decode, first-token time, RAM, VRAM, GPU power, and CPU power/load.
- Repeat short benchmarks with uncached prompts and record the median and range.
- Stop generation mid-stream and ensure the partial answer remains usable.

Results are stored under the private runtime state directory. Reports created by validation scripts under `validation/` are ignored by Git and should be shared only after manual redaction.

## Interface acceptance checks

- Select → configure → load → chat → benchmark → save profile → switch/unload works without editing a script.
- The chat follows streamed output until the user scrolls away, then resumes through **Jump to latest**.
- Text paste remains text; screenshot paste creates a removable image preview for vision models.
- Profiles can be named, loaded, edited, copied, deleted, undone, and restored.
- Change Thinking between Off, Model default and native levels while a model is loaded. The next request uses the selected level without requiring a reload; profiles and copied configuration retain it. Thinking and the final answer share Maximum output.
- Errors remain readable and the interface stays usable after a failed model load.
- Desktop and narrow layouts have no horizontal overflow and retain keyboard-visible focus.
- Hardware and performance fields show unavailable data as unavailable rather than estimating it.

## Performance interpretation

Keep these cases separate:

1. Cold application/model start, including disk reads and first kernel compilation.
2. Empty conversation and KV cache after kernel warm-up.
3. Long occupied context close to the configured limit.

Decode speed from a short prompt must not be presented as filled-context performance. Cached input tokens must not be counted as fresh prompt processing. Engine version, checkpoint revision, context, KV precision, vision state, MTP length, CPU placement, and output length belong with every retained result.
