# Live profiles and benchmark output — 2026-09-19

- Saved profile summaries query current speed/coding archives and refresh every three seconds while visible. Coding engine metrics (including legacy records) and legacy speed-test prefill values are included. Latest valid measurements are selected separately for generation and prefill, with source/date. Matching uses normalized effective settings; deleted and aborted runs do not supply values.
- Cards prioritize full model variant and quant, generation/prefill medians, context/cache, then VRAM/RAM. Search, model selection, minimum speeds, exact context, memory ceilings and sorting work together. Unknown measurements are excluded by numeric filters. Dragging remains available in unfiltered saved order.
- Memory is recorded from fresh telemetry after readiness plus three seconds, when inference and benchmarks are idle. Persistent measurements represent total system usage with the setup loaded, not incremental model allocations. Response-only settings preserve the memory match; loading settings invalidate it.
- Coding and speed benchmarks share a bounded in-memory output tail. Model text, reasoning, progress, commands and command/grading subprocess output stream to a floating terminal. Polling uses incremental revisions and coalesces token blocks. Output is rendered as text. Fullscreen, minimize, copy, pause-follow and jump-to-latest controls are available across views.
- Completion opens a results popup with model, outcome/score, generation and prefill; full results remain accessible. Aborted runs discard the tail and do not show a completion score.
- SWE-bench Lite scoring is unchanged: resolved/unresolved per issue, no fabricated partial credit. UI explains one/two/three-issue presets and their possible percentages; incomplete grading withholds aggregate accuracy.

## Verification

- Full suite: **160 passed, 1 skipped; 10 subtests passed**. TypeScript and production Vite build passed.
- Browser: Full HD two columns and QHD three columns, no grid overflow; model/quant text search and speed filters; terminal token updates and literal HTML-like text, fullscreen, pause-follow, minimize; automatic completion popup while on Saved profiles; full-results navigation; no browser console errors.
- Terminal browser test used an isolated server, copied state and a clearly named synthetic model (`UI validation fixture`). No test results were inserted into the user's archive.
- Main server restarted only after it was idle. Latest model settings were saved and restored, including temperature 1.0. Qwen3.8 Flash Next 3.05 bpw, 256K/Q8, vision on, MTP 2, CPU 60% returned ready.
- First real loaded-memory record: 32,290,897,920 VRAM bytes / 83,035,426,816 RAM bytes (30.1 / 77.3 GiB system totals). The CPU 60% profile also resolves the existing SWE-bench medians of 60.825 generation / 277.505 prefill tok/s. Other unmeasured profiles remain unknown until loaded.
- Backup before deployment: `~/.local/share/linux-llm-loader/state/backups/before-live-profiles-20260919` (profiles and SQLite backup).
