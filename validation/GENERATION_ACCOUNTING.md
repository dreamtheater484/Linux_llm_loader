# Generation-speed accounting investigation

## What is counted

Inflect's `loader/server.py::Supervisor.stream` uses the engine's `completion_tokens_per_sec` and `completion_tokens`. It does not count only visible answer text. Its client-time fallback also uses the full completion count and observes both reasoning and content events.

In the pinned Tabby implementation, `backends/exllamav3/model.py::handle_finish_chunk` computes generated tokens / generation time from ExLlamaV3's `new_tokens` and `time_generate`. `endpoints/OAI/utils/common_.py::get_usage_stats` forwards these fields into the OpenAI response. Reasoning/content/tool separation happens after sampling and does not subtract reasoning tokens.

## Confirmed defect

The installed ExLlamaV3 1.5.0 wheel's `generator/job.py::prepare_for_requeue` saved only the immediately completed segment:

```python
"rq_new_tokens": self.new_tokens
```

The final event reports `rq_new_tokens + new_tokens`, whereas elapsed generation time and speculative-decoding counters remain cumulative. After a second requeue, this drops older segments from the numerator and understates both output usage and tok/s. Long thinking responses are more likely to cross several requeue boundaries; the defect is not a deliberate exclusion of reasoning.

The correction is:

```python
"rq_new_tokens": self.rq_new_tokens + self.new_tokens
```

`patches/exl3-cumulative-output-tokens.patch` contains the one-line fix. Setup applies it idempotently to the installed engine Python module after installing the pinned wheel. No model, CUDA kernel, sampling, cache, output limit, profile, or scheduling settings change.

## Verification and limits

- `tests/test_generation_accounting.py` executes the actual installed requeue method with lightweight CPU-only sequence stand-ins. Three completed 4,096-token segments and a final 2,048-token segment previously reported **6,144** instead of **14,336**. The patch reports **14,336**, preserving generation time, draft counters and the remaining output limit.
- The test reverses/reapplies the real patch in a temporary directory, so it demonstrates both the original failure and the fix without loading a model.
- Existing reasoning tests verify that Inflect preserves the engine's full completion count and speed across effort levels, even when answer and reasoning use separate response channels.
- An affected long run was found in retained local engine metrics. Its reported accepted draft count exceeded its reported completion count, consistent with this cumulative-count defect. Full historical output was not retained, so the exact corrected speed cannot be reconstructed confidently.
- The runtime fix was installed while Inflect had no loaded model. It takes effect on the next model load; no running generation was interrupted. A fresh long GPU generation has not been benchmarked as part of this accounting correction.
- Full regression suite after installation: **116 passed, 1 Windows-only test skipped, 10 subtests passed**. Frontend type checking and production build passed; the speed/count tooltips now explicitly include thinking.

The accompanying `Qwen4` downloader update passed the existing HTTP failure/resume suite and a real Hugging Face metadata-only preview. The verified branch resolved to `55a732e0c4c3d4614bc42b68493bb930d9b02c0a`, with 27 files totaling 107,463,600,896 bytes, including `vision_k6.safetensors` and `ngram_embedding.safetensors`. No model weights were downloaded here. The Windows PowerShell entry point itself was not executed on Linux.

Actual throughput can still vary with context length, prompt/content, speculative-token acceptance and system load. Correcting this metric does not make the underlying generation execute faster.
