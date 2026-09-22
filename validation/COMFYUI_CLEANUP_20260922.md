# Follow-up: confirmed ComfyUI backend lifecycle

The original allocator-zero implementation below is superseded on this installation.

## Reproduced failure

ComfyUI's queue was empty after `/free`, but it retained 64 MiB in its CUDA allocator (32 MiB reported free within that allocator). NVIDIA reported 764 MiB for the backend process. Inflect incorrectly treated this small residual allocation as an unfinished unload, waited 120 seconds, and blocked the IQ3_M model. The earlier tiny-VAE test happened to return an allocator reading of zero and did not exercise this state.

## Correction

The user explicitly authorized stopping/restarting ComfyUI. The local launcher config now identifies `pi-ubuntu-comfy` as the managed container. Inflect waits for an idle queue, pins the inspected container ID, stops it, and confirms `State.Running == false` before starting the LLM. Completion does not depend on allocator counters. ComfyUI is restarted and its API checked after successful loading, failure, or cancellation. Already-stopped services remain stopped. Cancelling an in-flight Docker stop waits for it to finish and restores the service. Cancelling an LLM load terminates the partial engine before restarting ComfyUI. Restart errors remain visible in the dashboard as well as logs. Commands and readiness waits have bounded timeouts.

## Validation

- Full suite after lifecycle implementation: 205 passed, 1 skipped, 10 subtests passed.
- After final cancellation/error safeguards: 43 relevant tests passed, including 28 ComfyUI tests. Coverage includes queue busy, confirmed stop, already stopped, stop failure, cancellation while Docker stop is in flight, restart with nonzero allocator baseline, engine failure/cancellation restoration order, and visible restart errors.
- Live original failure state: ComfyUI stopped in about 0.6 seconds; NVIDIA's compute-process list was empty before the IQ3_M engine began loading. First cold load reached ready with restored ComfyUI in about 48 seconds (roughly 33 seconds for model startup and 15 for ComfyUI startup).
- Live model/cache exercise: a 64×64 constant image was encoded/decoded with the installed VAE. Reload subsequently stopped that backend and restored it successfully.
- Live cancellation: cancelled during LLM weight loading after ComfyUI had stopped. ComfyUI's API returned and Inflect became idle. Retrying the same IQ3_M settings succeeded.
- Final deployed code: IQ3_M loaded with the original settings; both `/api/chat` and `/v1/chat/completions` streamed through completion. ComfyUI API was available afterward and Inflect remained ready.
- No queue cancellation, container deletion, workflow deletion, or backend image changes were used.

API-only mode remains available for unconfigured installations, but cannot conclusively confirm cleanup with persistent allocator reservations. Its failure message now recommends explicit managed-container configuration instead of incorrectly blaming queued jobs. Abrupt host/process termination outside graceful cancellation is not covered by the restoration guarantee.

---

# ComfyUI cleanup before LLM loading — 2026-09-22

Implemented the requested unload-models/cache policy while keeping ComfyUI running. Every Supervisor model start waits for ComfyUI's running and pending jobs, posts `/free` with both flags, and observes two idle/zero CUDA allocator samples before launching the inference process. The loading banner shows the current stage. Unreachable-by-connection-refusal ComfyUI is skipped; other failures and timeouts prevent the new engine from starting. No queue interruption or backend shutdown is performed.

## Validation

- Full Python suite: **197 passed, 1 skipped, 10 subtests passed**. Includes 19 cleanup tests covering busy queues, work arriving during cleanup, delayed memory release, refusal, malformed responses, HTTP failures, timeout, cancellation, and the supervisor launch gate.
- TypeScript check and production Vite build passed; `git diff --check` passed.
- Live ComfyUI workflow: encoded and decoded a 64×64 constant image using the existing `ae.safetensors` VAE, with a temporary preview output. Workflow completed successfully and left actual model/cache allocations behind.
- Restarted the idle Inflect manager to activate the code, then loaded the existing Qwen3.8 Flash Next Q4_K_M profile with its exact settings preserved (128K context, Q8 KV, vision enabled, PLE in RAM, CPU expert share 100%).
- Observed cleanup before weight loading. ComfyUI reported CUDA allocator memory decreasing from 33,554,432 bytes to zero; NVIDIA process memory decreased from 880 MiB to 688 MiB. The same ComfyUI backend PID remained running. Its remaining context/library allocations are intentionally retained and are not counted as unloadable model weights.
- Inflect reached `ready`; `/api/chat` streamed a complete response, and `/v1/chat/completions` streamed a stop event and `[DONE]`. No incomplete chunked-read error occurred.
- Dashboard inspected at 1280×720: layout intact, total occupied RAM remains the primary metric, cache breakdown and resident model memory visible; no browser errors or warnings.
- Final state: original LLM settings restored, model ready, ComfyUI running with idle queue and zero reported allocator memory.

## Limits

ComfyUI's `/free` API is asynchronous. The allocator/queue observations are a completion signal, not proof that all GPU memory belongs to that allocator. CUDA context, libraries, and allocations owned by extensions may remain. A new ComfyUI job submitted after cleanup can allocate GPU memory again. Busy/failure scenarios were tested with controlled mocks; the successful release/load/chat path was exercised against the real services.
