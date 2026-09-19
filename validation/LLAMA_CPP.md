# llama.cpp and profile validation · 2026-09-19

The installed runtime is the official llama.cpp **b11050**, commit `60b06ab9a`, Linux x86_64 CUDA 12.8 release. Package checksums are recorded in `runtime-locks/llama-cpp.json`; the installer keeps its libraries private. Tests ran on an NVIDIA RTX 5090 with 32 GiB VRAM, driver 595.91.07, and approximately 183 GiB system RAM.

## Tested loading profiles

All three profiles allocate **32,768 tokens**, use **Q8 KV cache**, eight CPU threads, 512-token prompt chunks, temperature 0.7, thinking off, and a 4,096-token normal output limit. These settings are saved explicitly in the GUI.

| Model | CPU placement | Vision | MTP | Median decode | Median prompt processing |
|---|---|---|---|---:|---:|
| Qwen3.6 35B A3B HauhauCS Aggressive Q6_K_P | Experts from 10% of layers | On, local BF16 projector | Off | 183.1 tok/s | 523.1 tok/s |
| Qwen3.8 27B HauhauCS Aggressive Q4_K_P | Full GPU | Off | Embedded MTP, 2 drafts | 105.5 tok/s | 682.3 tok/s |
| Qwen3.8 27B Q6_K | Full GPU | Off | Embedded MTP, 2 drafts | 110.8 tok/s | 688.7 tok/s |

These are medians of the application's three distinct short-prompt speed tests, each with a 512-token output limit. Native llama.cpp timings determine both speeds. They describe this machine and workload, and do not establish performance at a filled 32K or 256K context. The full records are in the local benchmark archive and `validation/llama-cpp-benchmarks-20260919.json` (machine-specific, ignored by Git).

The existing 35B file has no MTP weights and was retained as requested. Both 27B files declare an embedded prediction layer. Readiness checks verify actual speculative decoding through `/slots`, and responses record accepted and rejected draft-token counts. This uses upstream embedded `draft-mtp`, not HauhauCS's separate FastMTP sidecar or patched runtime. Neither a replacement 35B nor new model weights were downloaded.

## Live qualification

`scripts/verify_llama.py` passed five configurations: both 27B files with MTP off and on, plus the 35B with vision on and MTP off. Checks covered:

- Correct ordinary answers and separate thinking output.
- Structured tool calls, validated arguments, and a tool-result round trip.
- Exact advance text token counts agreeing with engine usage.
- A conversation occupying 11,282 prompt tokens with correct retrieval.
- For the 35B, reading the fixture text `ORBIT 572` and identifying the colored shapes through the local vision projector.

The results are retained in `validation/llama-cpp-live-20260919.json` locally. Requested context capacity is checked against the running engine; automatic fitting and history shifting are disabled. Image token usage is available after generation; advance image token counting is not implemented. No matching projector was identified for either 27B file, so their profiles are text-only. Larger context capacities remain available but need their own memory and occupied-context qualification.

## Profile and loading UI

Browser checks verified collapsed rows, duplication, pointer drag reordering, ordering after a page reload, and the transition from the model-specific loading banner to the persistent ready notification. Profiles retain editing, custom naming, copyable configuration, and soft deletion/restoration. Benchmark matching requires the same effective settings; unrelated results cannot supply the compact name's speed.

Automated checks passed: 147 Python tests, one optional PowerShell test skipped, and 10 subtests. TypeScript checking and the production frontend build passed. Additional profile tests cover independent copies, invalid/stale order requests, trash restoration, and benchmark matching. The existing ExLlamaV3 path remains covered by the regression suite.
