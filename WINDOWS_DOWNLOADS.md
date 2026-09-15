# Download the models in Windows

Use **Windows PowerShell 5.1 or PowerShell 7**, with **Python 3.10 or newer** installed. No administrator rights or CUDA installation are needed. If Python is missing, install it from [Python for Windows](https://www.python.org/downloads/windows/) with the Python launcher included, then reopen PowerShell.

## Run

Open PowerShell **in this project folder on Windows** (`Documents\Codex\Linux_llm_loader`) and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\download-models.ps1
```

The script finds `Documents\AI\models` relative to its own location, so changing drive letters between Windows and Ubuntu does not matter. The execution-policy option applies to this new PowerShell process only.

If you saved the script somewhere else, specify the actual Windows path to the model directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\download-models.ps1 -ModelRoot 'D:\Documents\AI\models'
```

**Replace `D:` with the correct drive letter.** An explicitly supplied destination can be created if it does not exist.

## Included by default

| Selector | Download | Approximate GB |
|---|---|---:|
| `Qwen3` | Qwen3.8 Flash Next EXL3 `3.05bpw_h5_ng5` | 85 |
| `DeepSeek3` | DeepSeek V4 Flash 0731 EXL3 `3.04bpw` | 118 |
| `DeepSeekVision3` | DeepSeek V4 Flash Vision Exp EXL3 `3.04bpw` | 118 |
| **Total new downloads** | **Three separate model folders** | **~321 GB / 299 GiB** |

These are the **3-bit trials you selected first**. The 4-bit versions can be considered after testing and are not included in this script. Your existing Qwen NVFP4 and GGUF models are reused later; GLM remains excluded. The script downloads **each complete selected revision**, including all tokenizer, quantization, prediction, vision and patch files that the publisher includes. It does not filter down to weight shards or combine different quants in one directory. Completeness of the published files does not establish that a vision package works in the inference engine; those tests remain for Ubuntu.

Source repositories: [Qwen EXL3](https://huggingface.co/turboderp/Qwen3.8-Flash-Next-exl3), [DeepSeek 3.04 EXL3](https://huggingface.co/turboderp/DeepSeek-V4-Flash-0731-exl3), [DeepSeek Vision EXL3](https://huggingface.co/turboderp/DeepSeek-V4-Flash-Vision-Exp-exl3).

## Useful options

Preview current sizes, revisions and free space without downloading model files:

```powershell
powershell -ExecutionPolicy Bypass -File .\download-models.ps1 -List
```

Download just Qwen in the current PowerShell session:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\download-models.ps1 -Models Qwen3
```

Verify already downloaded files without downloading weights:

```powershell
powershell -ExecutionPolicy Bypass -File .\download-models.ps1 -VerifyOnly
```

Downloads now use **resumable HTTP v2 by default**. `-UseHttp` is accepted for compatibility and selects the same transport. The startup message must say `TRANSPORT: resumable HTTP v2; Xet is not used`. The previous library displayed “Reconstructing” even in HTTP mode; this version uses explicit file names, byte progress and retry messages.

Before switching from the old script, press **Ctrl+C in its PowerShell window and wait for it to exit**. The launcher refuses to start while another downloader is using the same model folder. Run the command above again; completed files are reused. Unfinished files from the old downloader cannot reliably be recovered, and old Xet temporary files are never treated as sequential HTTP data.

If Hugging Face asks for access/authentication, visit the repository page to obtain any required access, then rerun with `-Login`. Login uses Hugging Face's normal authentication flow. Do not put a token in the script. To reduce concurrent transfers, add `-Workers 1` (default: 2; maximum: 8).

## Resuming and completion

- Keep the PC awake and the drive connected during downloads.
- To resume after interruption, **run the same command again**. Keep each model's `.cache` directory and the root `.linux-llm-downloads` directory. New HTTP partials live under `.linux-llm-downloads/http/<model>/<commit>/` and are retained after errors and Ctrl+C. Completed downloads are reused; checksum verification reads them again.
- The script checks metadata for all selected repositories before transferring weights. It checks space for missing bytes plus 10 GiB headroom, subtracting saved HTTP partial bytes. Other programs can still consume disk space during a long run.
- Each branch is resolved to a fixed commit and recorded before downloading. Reruns use that commit, preventing an upstream branch update from changing a download halfway through.
- Each file uses sequential 64 MiB HTTP ranges. Every request starts at the pinned Hugging Face URL with a fresh query, allowing a new signed CDN link. A retry resumes at the saved byte offset. Range boundaries and total size are checked before appending; a server that ignores a nonzero resume range causes a clear failure while preserving the partial file.
- Connections time out after 15 seconds; reads time out after 30 seconds of network inactivity. Connection errors, HTTP 401/403, 408, 429 and server errors are retried with increasing delays (2–60 seconds; `Retry-After` can extend this to 5 minutes). After 12 consecutive failures without completing a range request, that file fails with its partial data retained. Other files and queued models continue; the script exits unsuccessfully if any failed. Persistent access errors still require resolving repository access.
- Progress is printed about every 10 seconds while bytes arrive, including file name, amount saved, percentage and speed. Timeouts and retries are explicit. The append-only diagnostic log is `.linux-llm-downloads/download.log`; signed URLs and bearer tokens are redacted. Final checksum command output appears in the terminal. Receipts record download, verification, interrupted or failed state and sanitized failure details.
- Ctrl+C asks active workers to stop; allow pending network reads to reach their timeout. A second run against the same model folder is blocked. The lock releases when the process exits, so a leftover lock file does not need deleting.
- Files are checked against repository sizes and shard indexes, then verified with Hugging Face's checksum command, including `--fail-on-missing-files`. **“All selected repositories downloaded and checksum-verified”** is the completion message. Hash verification can take several minutes for this much data. [Hugging Face verification documentation](https://huggingface.co/docs/huggingface_hub/guides/cli#hf-cache-verify)
- A checksum mismatch needs a targeted repair; a routine resume may reuse a same-size corrupted file. Use the filename and repository reported by `hf` with the isolated CLI's `download --force-download` option, the receipt's commit, and the same `--local-dir`, then rerun verification. Avoid forcing a download of the entire repository unnecessarily.

The script uses a dedicated environment under `%LOCALAPPDATA%\LinuxLlmLoader\downloader`. It installs dependencies only if the compatibility check fails; it does not automatically upgrade them on each run. Hugging Face supplies metadata, authentication and final checksum verification; the script owns HTTP transfers and partial-file retention. Model files and transfer data go to the selected drive. It does not install inference engines, execute downloaded model code, or require Windows symlink privileges.

When finished, perform a **full Windows shutdown** before booting Ubuntu so the shared NTFS volume is not left hibernated. For example, after closing your work, hold **Shift** while choosing **Shut down**. Leave the model folders where they are; Ubuntu can read the same files.

## Validation performed

The tests cover model selection, safe paths, pinned revisions, space estimates, preflight failures, missing shards, checksum failure handling and continuing the queue after failure. Local HTTP-server tests cover dropped connections, read timeouts, expired links, rate limiting, server errors, restarting a partial download, cancellation, incorrect or ignored ranges, bounded authorization failures, completed-file reuse, concurrent-run locking and credential redaction.

Windows PowerShell 5.1 preview and launcher guard checks were exercised. Live small-file downloads from all three pinned repositories were checked against Hub hashes and resumed from saved offsets. Actual safetensors shard samples from each repository were interrupted after 1 MiB and resumed to 2 MiB using the new transfer code. These checks establish the recovery behavior; they do not substitute for completing and checksum-verifying the full 321 GB download.

To rerun the offline checks from the project directory with Python:

```text
python -B -m unittest discover -s tests -v
```
