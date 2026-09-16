# Qwen tool-calling repair

## Root cause

Three independent omissions broke Lumen → TabbyAPI → ExLlamaV3 tool calling:

1. `loader/server.py::openai_chat` constructed a `ChatRequest` without `tools`, `tool_choice`, or `parallel_tool_calls`. `Supervisor.stream` consequently sent no function definitions to Tabby's native chat template. This explains unchanged prompt-token counts with and without tools.
2. `loader/engines.py::launch` did not select Tabby's `tool_format`. Its default disables tool parsing, so generated Qwen pseudo-XML was returned as ordinary text.
3. `Supervisor.stream` and `openai_chat` collected only text/reasoning. Even structured upstream calls would have been discarded.

Live verification exposed a fourth issue: the pinned Tabby `qwen3_coder` parser guessed parameter types with `json.loads` without seeing the schemas. For example, raw `True` became a string rather than a boolean, while a string parameter containing `123` became a number.

## Implemented changes

- `loader/tool_calls.py::ToolRequest` validates supported request options and schemas. `validate_tool_history` checks assistant call IDs against subsequent tool results. `ToolCallAccumulator` assembles and validates structured engine calls before releasing them to a client.
- `loader/server.py::Supervisor.stream` forwards definitions to both generation and token counting; retains structured calls, IDs, names, arguments, and finish reasons; and separates reasoning from answer text and calls.
- `loader/server.py::openai_chat` returns OpenAI-compatible non-streaming messages and indexed streaming deltas. Streaming calls can arrive as one complete argument fragment because the native engine buffers them until parsing finishes.
- `loader/engines.py::launch` enables `qwen3_coder` only for the matching Qwen architecture and selected template. Tool parsing is disabled for requests without active tools, and reasoning cannot produce executable calls.
- `patches/tabby-qwen-tool-schema.patch` passes schemas through Tabby's collector/dispatcher to its native Qwen parser. Explicit string parameters remain strings; explicit boolean parameters accept `True`/`False`. It rejects duplicate parameters and preserves output-limit termination instead of relabeling truncated output as completed tool calls. Setup applies the patch idempotently.
- Invalid or unoffered calls produce an API error; Lumen never executes functions or parses arbitrary prose into calls.

The patch targets TabbyAPI commit `53da7919d4e45c63f4acbcbbc00cbe0f60a1ce65`. ExLlamaV3 weights, quantization, offload, context, vision, MTP, and saved profiles are unchanged.

## Supported controls

| Control | Behavior |
|---|---|
| `tool_choice: auto`, or omitted with tools | Model decides whether to call a function |
| `tool_choice: none`, or no tools | No definitions or tool parsing for the request |
| `required` / named function choice | Explicit HTTP 422; this Tabby implementation does not enforce them |
| `parallel_tool_calls: false` | Explicit HTTP 422 with active tools; multiple calls otherwise supported |
| `strict: true` | Explicit HTTP 422; post-generation schema validation remains enabled |
| Qwen reasoning | `default`, `off`, `low`, `medium`, `xhigh` |
| Reasoning aliases | `none` → `off`; `on` → native thinking default (`xhigh`) |
| Unsupported Qwen reasoning | `minimal`, `high`, `max` → HTTP 422 |

Omitted reasoning uses the loaded profile setting. `default` defers to the checkpoint template. Unsupported options fail before an SSE response begins.

## Verification scope

Verified on the deployed Qwen/ExLlamaV3 installation on 2026-09-16:

- Regression suite: **114 passed, 1 skipped, 10 subtests passed**. The skipped test requires Windows PowerShell.
- Live API verifier: **20 checks passed**. Identical user messages used 64 prompt tokens without tools and 421 with tools; the model copied the description-only marker into correctly typed arguments.
- Non-streaming and streaming: valid structured calls with IDs, names, JSON arguments, streaming indexes, and `finish_reason: "tool_calls"`.
- Complete call → synthetic result → final answer exchanges passed for reasoning `off` and `low` in both response modes. Reasoning remained separate.
- Two calls in one response retained distinct IDs and indexes; reversed tool-result arrival was correctly matched by ID.
- `none` worked in both modes; `required`, named choices, and unsupported Qwen reasoning levels returned HTTP 422.
- Final model state: ready and idle; loaded settings and saved profiles unchanged. The LAN header requirement and authenticated loopback-only engine listener were rechecked after deployment.

Lumen was restarted and Qwen reloaded after coordination. No further server restart is needed for this deployment.

Regression tests cover request forwarding, fragmented and multiple calls, ID matching, typed arguments, invalid/truncated calls, unsupported options, reasoning separation, and the existing LAN/header checks. The optional native-parser test runs the actual installed Tabby parser against strings, booleans, arrays, objects, duplicate parameters, and non-evaluated command-looking text.

`scripts/verify_tool_calls.py` uses unique synthetic function names and description markers. It checks both response modes, supported/unsupported tool choices, reasoning off/low, complete tool-result exchanges, multiple calls, and reversed result arrival order. It never executes model-generated commands. Machine-specific reports are ignored by Git.

These are API and parser tests. OpenCode is on a separate machine and was stopped for deployment; no end-to-end OpenCode native tool or subagent execution is claimed.

## Boundaries

The native parser uses direct parameter `type` declarations for its type corrections. Complex unions/references retain native coercion and must pass final schema validation. A model can still generate an invalid name, arguments, or truncated response; these fail explicitly instead of being executed. Automatic tool execution and forced tool selection are not added.

Lumen remains on the configured private LAN address. Tabby remains authenticated on loopback. Internal credentials and machine-specific settings are not included in this report.
