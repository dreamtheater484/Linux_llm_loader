# Lumen validation

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
