# Benchmark archive and speed display validation · 2026-09-19

- Desktop archive layouts: two columns at 1920 × 1080 and three at 2560 × 1440. No horizontal card overflow observed. Full model names are available on hover.
- Browser checks used an isolated copy of the real SQLite archive. Bulk delete of all four retained coding runs, Recently deleted, and bulk restore succeeded. The AJAX model picker narrowed the archive to the 4.05 bpw model. No production result was deleted during UI testing.
- Latest existing HumanEval+ result: 20 measured responses, median decode 43.57 tok/s, median prefill 175.82 tok/s, median first-token latency 1.232 seconds. These values were recovered from saved engine metrics and visibly verified in the results dialog.
- Coding cards, details, per-task metrics, comparisons, JSON exports, and Markdown reports expose recorded speeds. No grading-wall-time or stream-chunk estimate is substituted for engine throughput. Missing measurements stay unavailable.
- Aborted coding runs are removed from SQLite and their per-run artifact directories; startup clears legacy cancelled/interrupted runs and abandoned running checkpoints. Completed and time-limited runs remain. Aborted speed tests are not inserted into the archive.
- Manual deletion is reversible through Recently deleted. Bulk validation is atomic, and running tests cannot be deleted or restored. Deleted results are excluded from saved-profile benchmark matching.
- Automated coverage includes cancellation before/after task startup, restart cleanup, speed-test cancellation, library model lookup, filters/sorting, bulk deletion/restoration, authorization, and speed aggregation with missing/invalid/non-engine samples.
