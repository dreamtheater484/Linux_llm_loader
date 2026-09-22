# Final dashboard semantics: model, available estimate, total

The earlier four-part dashboard described below is superseded. The final dashboard headlines model PSS, shows total capacity beside it, and shows conservative extra headroom while retaining the model. Headroom is max(0, min(total − model PSS, MemAvailable − model file PSS)). This avoids offering the model’s resident file-backed pages for reuse. Other applications/system allocations are accounted for by MemAvailable. No cache taxonomy or overlapping numbers remain in the dashboard or info panel. Five frontend accounting regression tests pass, including the 114.4 GiB model / 165.7 GiB OS availability case, unknown data, unload, anonymous memory and bounds.

# RAM accounting correction — 22 September 2026

The primary dashboard value, saved profile RAM value, RAM filter/sort and new benchmark RAM peaks now measure occupied physical system RAM (`total - free`), including resident model files in the page cache. Linux available memory remains the basis for load admission, where reclamation is relevant.

The dashboard retains its 110 px desktop metrics strip. A two-tone bar splits non-cache occupancy from file/buffer/reclaimable kernel cache (excluding shared memory). Model residency is a secondary metric, included in the system total, with an accessible details dialog. On phones, RAM spans two grid columns to avoid overflow. The model measurement sums engine/worker PSS, including file-backed pages without double-counting shared workers. It is sampled in a worker thread every five seconds; unreadable or exited processes are unknown, not zero.

Legacy profile RAM readings are retained in storage but excluded from corrected RAM comparisons; a new load replaces the measurement. Archived legacy measurements get explicit accounting metadata. New benchmark component peaks are independent maxima, not additive. JSON and Markdown ZIP reports use the annotated canonical result.

Validation:
- Production TypeScript/Vite build passes.
- Full Python suite: 178 passed, 1 skipped, 10 subtests passed, using the existing local downloader test dependencies.
- Covered the original 183 GiB / 170 GiB available / 47 GiB free case: primary RAM is 136 GiB, not 13 GiB. Tested shared-page PSS, worker exit, permission failure, unload during collection, model switching, legacy profiles and archive metadata.
- Browser review at 1280 px and 900 px desktop widths and 390 px phone width; fixed the phone overflow. Checked the details dialog, profile labels and production dashboard; no browser errors.
- Restarted only after the generation finished and restored the exact active settings (70% CPU experts, 128K/Q8, vision on, n-gram RAM). Did not reload the user's existing browser tab.
- Live saved measurement after activation: approximately 158.0 GiB occupied system RAM and 88.0 GiB model PSS. The model was ready, and corrected accounting was persisted. These values are a point-in-time snapshot, not a capacity estimate.

During final UI verification, subsequent user requests/reloads hit CUDA allocation failures. The logs identify `cudaMalloc` for a cuBLAS workspace and `cublasCreate_v2`, not a RAM telemetry exception. A separate ComfyUI process was holding 498 MiB VRAM; with the 70% CPU profile loaded, NVIDIA reported only 21 MiB free. The verification browser was closed. ComfyUI and the user's placement settings were left intact; additional CPU offload is needed for concurrent GPU workloads. The dashboard continues to report system RAM accurately after engine failures/unloads, including cached files retained by Linux.

## Chat recovery

After the user reported repeated incomplete-chunk errors, the supplied engine log confirmed CUDA OOM during lazy cuBLAS workspace allocation on the first prompt. Increased the active CPU expert share from 70% to 75%, preserving all other settings. Tested `/api/chat` and streamed `/v1/chat/completions` (the API used by Pi), including a longer prefill. Both delivered their expected text and complete stream termination. The engine remained ready. NVIDIA then reported approximately 3.1 GiB free VRAM (28.3 GiB used plus 0.5 GiB driver-reserved). ComfyUI remained running. The raw local verification result is `validation/chat-recovery-20260922.json`.
# Follow-up: explicit capacity, model, other, and available

The dashboard now headlines model resident RAM and shows system capacity, other occupied RAM, and Linux available RAM directly in the same card. Other occupancy is `occupied − model PSS`; it includes unrelated cache. Available memory overlaps with reclaimable pages in both categories, so it is shown separately from the physical occupancy bar. The details panel explains `model + other + completely free = capacity` and why available must not be added. Profile sorting/filtering still uses total physical occupancy.

Verified TypeScript and production builds, accounting examples (112 model + 70 other + 1 free = 183 total, with 166 available), missing/inconsistent/legacy samples, and unloaded state. Inspected the live dashboard at 1920, 1280, 900 and 390 pixels, including the scrollable mobile details dialog; no browser errors or warnings. Desktop hardware bar remains 110 pixels high. No backend restart or model reload was required. Existing user tabs were left untouched to preserve their in-memory chats; newly opened tabs receive the new build.
