# Live metrics audit — 19 September 2026

All times Europe/Amsterdam (CEST). Completed-request cutoff: 10:46:15.484. The active generation was not interrupted and no inference tests were submitted.

The high decode throughput is supported by installed engine accounting and independent request timestamps. The frontend uses `session.last_usage`, so displayed rates are from the last completed request even while the following request is active.

| Window | Completed requests | Output tokens | Weighted decode | Output / full elapsed time | Reported weighted prompt processing |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10:41:45.792–10:43:46.669 | 11 | 6,147 | 63.04 tok/s | 50.85 tok/s | 409.85 tok/s |
| Latest continuous run, 10:38:55.924–10:46:15.484 | 24 | 20,793 | 58.06 tok/s | 47.30 tok/s | 543.74 tok/s |

Historical weighted decode = sum(output tokens) / sum(output tokens / per-request decode rate). Historical log rates are rounded to 0.1 tok/s, so averages are approximate. Full elapsed time includes initial prompt processing and gaps between requests. The loader does not record OpenCode conversation IDs; the latest continuous run begins at a visible reset to a smaller context. Newer, unfinished requests are excluded.

## Direct cross-check

The screenshot corresponds to request #96: 614 tokens at 64.9 tok/s, 792 uncached input tokens in 1.18 seconds = 671.19 prompt tok/s. Log timestamps span 10.738 seconds; the engine reports 10.7 seconds. Its 375 accepted draft tokens are already included in 614 output tokens.

The longer request #102 completed while auditing: 8,054 completion tokens / 137.45 decode seconds = 58.596 tok/s. Engine total 147.78 s; independent Tabby log timestamps span 147.885 s; Inflect request wall time is 148.015856 s. MTP counts: 4,768 accepted, 1,804 rejected (72.55% acceptance). Reasoning effort is xhigh.

## Accounting checks

The actual loaded ExLlamaV3 generator increments completion count once per accepted sampled token before channel parsing. Reasoning, normal text and native tool-call syntax all contribute. Rejected speculative candidates do not contribute to completion count; accepted candidates are not added twice. Stop/control tokens can also be counted even if not displayed. There is no separate retained reasoning-token count to report a thinking/answer split.

The installed cumulative-requeue fix adds earlier completed output segments to the current count. Its file predates this model process. The actual installed method was exercised by the CPU-only requeue regression test; the reasoning tests verify preservation of usage across separate answer/reasoning channels. 13 targeted tests passed. No model/GPU workload was added.

## Caveats

- Rates are last-completed-response measurements, not instantaneous measurements or session averages.
- Prompt time accumulates prefill on internal requeues, while prompt-token numerator refers to the original uncached input. Request #102 reports 260 new prompt tokens / 10.28 accumulated prefill seconds = 25.29 tok/s, although Inflect observed first output after 1.14 s. This must not be interpreted as initial prompt performance alone. Tabby text-log “first token” is also misleading for this long case because it uses accumulated prefill; Inflect uses actual first streamed content/reasoning arrival. Tool-only responses can buffer until a full tool call is available.
- Decode excludes those prefill/requeue intervals and client tool execution. Full elapsed throughput is the better measure of overall task output speed.
- VRAM exactly matches nvidia-smi (30,496 MiB = 29.78125 GiB). RAM is system total minus available; CPU utilization is system-wide across 16 logical CPUs. They are not model-process-only measurements.
- CPU power reads amdgpu instantaneous PPT because RAPL package energy counters are not readable by this user. In 12 samples, reported power ranged 3.213–55.174 W while CPU utilization ranged 45.9–56.7%. The screenshot’s 2 W should not be treated as independently verified package power. Units match the kernel hwmon interface, but physical sensor accuracy is unverified: https://docs.kernel.org/gpu/amdgpu/thermal.html

See metrics-audit-20260919.json for request rows, formulas/results, raw latest usage and the short hardware sample window. These hardware samples do not reconstruct whole-session hardware averages.
