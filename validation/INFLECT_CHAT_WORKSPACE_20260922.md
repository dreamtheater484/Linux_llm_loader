# Inflect chat workspace

Implemented persistent local conversations and an engine-independent chat interface in the existing workbench. Existing working-tree changes to metrics, models, profiles, benchmarks and ComfyUI handling were preserved.

## Delivered

- Local SQLite conversation library with title/content search, automatic titles, renaming, draft persistence, system instructions, JSON export/import, and revision checks across windows.
- Streaming answers and reasoning, stored model attribution and performance, stop, partial-response recovery, immutable editing/retry branches, and branching at any message.
- Attachments stored with their conversation. HTML/SVG/Markdown/code/text, images, paged PDF and audio previews; downloadable originals and generated code. PDF text extraction and GIF conversion for compatible model input.
- HTML previews support inline JavaScript in an opaque-origin sandbox, with network connections and parent-page access blocked. The preview pane resizes and expands independently.
- Chat expansion keeps live hardware/performance readings in a compact strip. Settings remain available in a drawer. Regular workbench, benchmarks and saved profiles remain available.
- Inflect naming across source, documentation, environment variables, request headers, package identity, generated assets and installed launcher. Matching vector logo in the sidebar, message avatars, empty state, compact strip, browser icon and desktop launcher. Configuration migration preserves machine paths and existing settings.

## Verification

- Production frontend builds successfully with TypeScript checks. Vite reports a non-blocking bundle-size advisory for the combined Markdown/math/highlighting renderer.
- Python suite: **220 passed, 1 skipped**, plus **10 passing subtests**. The skip is an existing runtime-dependent parser test. Test-only downloader dependencies were supplied from the existing private test directory.
- Memory-accounting frontend tests: **5 passed**. Expanded metrics use the same memory calculation as the dashboard.
- Automated Chromium checks against an isolated fixture: HTML interaction and parent isolation; editing, retry, branching; reload/draft persistence; search; import/export; Markdown, PDF canvas, SVG, image and audio previews; unsupported audio input preserving the draft; stopped and disconnected replies; narrow-screen overflow; permanent deletion and notification across two windows. No page errors.
- Live hardware checks through the new conversation API: ExLlamaV3/TabbyAPI with Qwen3.8 Flash Next and llama.cpp with the existing Qwen3.8 27B Q4_K_P profile both returned `Inflect is ready.` with a normal stop. Each reply was persisted, reopened, exported and permanently deleted; deletion unloaded its engine to release prompt caches. Disposable test conversations were removed.
- The original ExLlama model was restored with its exact prior settings after testing. The Inflect manager and desktop shortcut are installed locally.
- No old product-name references remain in project text/assets, excluding Git history, private runtime dependencies and generated caches.

## Deletion and capability boundaries

Chat deletion has no trash or undo. The database uses secure deletion, cascading attachment deletion, delete-mode journals and no persistent search index. A deleted record cannot be recreated by a late stream update. Conversation content is not cached in browser local storage or IndexedDB. Only the active conversation ID is remembered there. Deleting a conversation with replies unloads an active owned inference engine and clears the manager's live engine-log buffer; unrelated in-flight requests prevent deletion.

Independent branches, user-downloaded exports, external backups and storage-device forensic remnants are not erased. Audio files can be played but require transcripts for model input. Scanned PDFs require page images or OCR. Remote resources in HTML previews are blocked; use self-contained files. Images require a vision-capable loaded model/profile.

## Repeating browser checks

Start `tests/serve_chat_fixture.py` with the manager Python. It uses a fresh temporary state directory, binds to loopback port 7861 and substitutes deterministic inference; it does not load actual models. Run `frontend/tests/chat-browser.cjs` with Node and Playwright available. `PLAYWRIGHT_MODULE` can point to a bundled Playwright module; `INFLECT_TEST_OUTPUT` selects the screenshot/export directory. Stop the fixture server afterward. Do not point this destructive test suite at the production server.
