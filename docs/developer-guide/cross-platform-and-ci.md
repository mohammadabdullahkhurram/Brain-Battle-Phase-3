# Cross-platform & CI

**What this page covers:** what differs between macOS and Windows in this
codebase, where each difference is handled, the automatic test run on both
systems, and what has still never been tried on a real Windows machine.

The lab uses both Mac and Windows computers. All development so far has
happened on macOS.

## Where the differences live

Nothing outside these places should ask which system it is running on.

| Difference | Handled in | How |
|---|---|---|
| Date formatting (`%-d` works on macOS, raises on Windows) | `support/platform_utils.format_date` and friends | Built from `d.day`, never a platform-only strftime code |
| The file manager's name and how to open it | `platform_utils.file_manager_name`, `reveal_in_file_manager`, `open_folder` | Finder / `open -R` on macOS; Explorer / `explorer /select,"path"` on Windows |
| Keyboard shortcut wording | `platform_utils.shortcut` | ⌘Z on macOS, Ctrl+Z elsewhere (the key handling itself accepts both) |
| Where settings are stored | `data/app_settings._config_dir` | `~/Library/Application Support/Brain Battle` · `%APPDATA%\Brain Battle` |
| The default recordings folder | `paths.documents_dir` | `Documents/Brain Battle Data` on both |
| Camera backend | `capture/usb_camera._preferred_backend` | AVFoundation · DirectShow |
| Video encoder | `data/export_module._detect_hw_encoder` | VideoToolbox on macOS; NVIDIA, AMD, then Intel on Windows; each must pass a real test encode, otherwise `libx264` |
| Keeping the computer awake | `capture/keep_awake` | `caffeinate` · `SetThreadExecutionState`; failures are swallowed |
| Replacing a file that is open elsewhere | `data/safe_io.replace_with_retry`, `read_json` | Windows refuses while another program has it open; retried with short waits for up to ~5 s |
| Appending a line to a shared log (`history.jsonl`) | `data/safe_io.append_line` | One `O_APPEND` write on macOS; on Windows an append-only handle (`FILE_APPEND_DATA`), because Python's append mode there only emulates appending and two writers can overwrite each other |
| The capture clock | `capture/clock.monotonic` | `time.perf_counter()` on both. `time.monotonic()` would tick only every 15.6 ms on Windows with Python ≤ 3.12 |
| Legal file and folder names | `session_storage.sanitize_name`, `labels_store.labeller_slug` | Windows' forbidden characters and reserved names are avoided on every system |
| Bluetooth | muselsl with the `bleak` backend | One code path; bleak talks to CoreBluetooth or WinRT |
| Fonts and the bell sound | bundled in `assets/`, loaded through Qt | Same files on both |
| Lab Streaming Layer | `pylsl` wheels bundle the library for both | Same code |

Rules of thumb when adding code:

- Build paths with `os.path.join` / `pathlib`; never write `/tmp` or a `/`
  separator into a path.
- Open text files with `encoding="utf-8"` — Windows' default is not UTF-8.
- Don't keep a file open while another part of the app replaces or renames it.
- No `strftime` code with a `-` or `#` flag. `tests/test_platform.py` fails if
  one appears anywhere in the source.
- No UI string may say "Finder"; use `show_in_file_manager_label()`. Also
  enforced by `tests/test_platform.py`.

## The automatic test run (GitHub Actions)

`.github/workflows/tests.yml` runs every test script on **macOS and Windows**
for each push to `develop` or `main`, each pull request, and on demand (the
*Run workflow* button on the repository's Actions page). It needs no accounts,
tokens or secrets.

What it does on each system: installs Python 3.12, installs ffmpeg (Homebrew on
macOS, Chocolatey on Windows), installs the app (`pip install -e .`), and runs
`python tools/run_tests.py --jobs 2` with Qt's offscreen platform, since the
runners have no display. If anything fails, each script's full log is kept as a
downloadable artifact named `test-logs-<system>`.

Things to know:

- The devices are simulated (`BRAIN_BATTLE_SIMULATE`), exactly as on a
  developer's machine. CI proves the app's own code runs on Windows; it says
  nothing about real cameras, real headbands or real Bluetooth there.
- A few scripts measure real time. The runner already re-runs a failed script
  on its own before counting it; a shared CI machine can still be slow enough
  to fail one. Read the log before believing a timing failure.
- Every test script imports `tests/_env.py` first, which points the app at a
  throwaway settings file — a test run never touches a real user's settings.

## Fixed on 2026-11-17: CI green on both systems

Read from the Windows and macOS logs of the runs on `be4202f` and `339f941`,
then fixed at the cause. No test was skipped or loosened to get there.

| Script | What happened | Cause | Fix |
|---|---|---|---|
| `test_xdf` (Windows) | "our LSL time matches `pylsl.local_clock()`" measured 3.3–3.6 ms (limit 1 ms) | **A real bug.** The capture clock was `time.monotonic()`, which on Windows with Python ≤ 3.12 (CI uses 3.12) ticks every ~15.6 ms. Every frame, marker and phase time on Windows was rounded to 15.6 ms, and the one-off offset to the LSL clock was off by up to a tick | The capture clock is now `time.perf_counter()` (QueryPerformanceCounter on Windows, the same clock as before on macOS), and every stamp goes through `clock.monotonic()` ([ADR-0017](../decisions/adr-0017-one-capture-clock.md)). The strict 1 ms limit stays, on CI too |
| `test_xdf` (both, under load) | the drift part of the same check failed once at 713–867 µs while other scripts ran | The test read the two clocks one after the other; a thread switch between the reads counted as drift | It reads them as the tightest of 200 bracketed reads, as `capture/clock.py` does. Passes with ten CPU-hogging processes running |
| `test_shared_folder` (Windows) | Stalled until the 15-minute stop in one run; "the shared history has every line, none torn" failed (83 events / 106 lines) in the next | **Two causes.** (1) Hang: when Windows refused one of 200 file replacements for longer than the retries lasted, the exception skipped the line that stops the test's reader thread, which then spun for ever. (2) Torn lines — **a real bug**: Python's append mode on Windows moves to the end and then writes, so two processes appending to `history.jsonl` at once overwrote each other | (1) The reader is always stopped (`try/finally`, daemon thread), a stuck helper process is killed, an error in one part is reported and the rest still runs; replacements and reads are retried with short waits for ~5 s instead of 6 tries. (2) `safe_io.append_line` writes each line as one append-only write (`FILE_APPEND_DATA` on Windows, `O_APPEND` elsewhere); a new test has 6 processes append 150 long lines each at full speed |
| `test_lsl_streams`, `test_record_flow` (both) | Occasionally could not find or subscribe to a stream ("open_stream timeout") | Every process published the same stream names *and* the same LSL source id, so a test could subscribe to another script's (or a running app's) stream; and it waited with fixed sleeps | Each test script sets its own `BRAIN_BATTLE_LSL_TAG`, which `lsl_outlets.source_id` adds to every stream's source id; `tests/_env.py`'s `lsl_resolve` / `lsl_inlet` find a stream by name **and** source id, retry until a deadline, and wait for the outlet to see the subscriber. Three copies of `test_lsl_streams` now pass side by side. The stream names (and `recording.xdf`) are unchanged |
| `test_lsl_streams` (Windows) | "…and use no more open files": 318 → 319 after five publishers | On Windows the count is the process's kernel **handle** count (there is no descriptor table), which also includes thread and event handles the runtime opens and closes by itself | A warm-up cycle first, then on Windows the check is "fewer new handles than publishers": a leak in the outlets adds at least one per publisher (liblsl 1.17 kept 3). macOS / Linux keep the exact "no more open files" check |
| `test_record_flow` (Windows, under load) | "XDF markers: first is BASELINE_START, right after the session start" measured 1.85 s (bound 1.5 s) | The start-up work before the baseline begins (writing the manifest and history, starting four encoders and two EEG writers) took longer on a busy runner; the marker honestly records when the baseline (and its bell) started | The check is now exact instead of a wall-clock bound: the XDF marker must equal its own `phase_markers.csv` time within 5 ms — which is what proves both are on one clock |
| `test_eeg_streaming` (both, under load) | "subprocess actually streamed samples: sent=0" | The helper rewrote its sample count by truncating the file and writing again; a kill between the two left it empty | One appended line per chunk; the test reads the last complete line |
| `test_descriptors` (both) | `psutil` missing; later a one-off +4 threads in one snapshot on Windows | `psutil` was not installed on the runners; Windows' own thread pool starts and retires worker threads | `psutil` is in `requirements.txt` (the CI log prints its version). A leak grows every visit, so the check now asks that the typical later visit is back at the first visit's count and no single visit is far above it |

`pylsl` is now 1.18.7 (liblsl 1.18), which no longer leaks descriptors —
[ADR-0022](../decisions/adr-0022-lsl-outlets-open-once.md).

### Still open: two first-attempt failures on Windows

Runs `43997541652` and `44000265457` (2026-11-17) both ended green on macOS and
Windows, 35 of 35. In the second, two Windows scripts failed while other
scripts were running and passed when the runner re-ran them alone:

| Script | What happened | What is known |
|---|---|---|
| `test_shared_folder` | "200 replacements succeed while another reader keeps opening the file": `PermissionError(13, 'Access is denied')` even after ~5 s of retries, and one of 160 locked read-change-write edits was lost | Windows refuses to replace a file while another program has it open, and Python opens files without delete-sharing. A reader that re-opens the file in a tight loop (much harder than real use: the app re-reads other labellers every 5 s) can keep the replace refused. The app shows such a failed save and retries it, but the root fix — reading shared files with delete-sharing on Windows — is not done yet |
| `test_smoke` | All checks passed ("GREEN: 0 failure(s)"), yet the script exited with an error | The process failed *after* its last check, i.e. while Qt and Python were shutting down. The exit code is not in the log; needs a Windows machine (or a CI run that prints the code) to look into |

## Never tried on a real Windows machine

Verified only by reading the code and by the Windows CI run, where there is
one:

1. Cameras through DirectShow, and four of them at once.
2. Headbands over Windows Bluetooth (WinRT via bleak), two at once.
3. Hardware video encoders on real NVIDIA / AMD / Intel graphics.
4. `explorer /select,` with unusual folder names.
5. Sleep prevention during a long recording.
6. A real network drive (SMB) shared between a Mac and a PC.
7. The Windows firewall prompt for Lab Streaming Layer.
8. How the interface looks at Windows display scaling of 125% and 150%.

The first session on a Windows computer should go through
[New recording](../user-guide/new-recording.md) end to end and note anything
that differs from this documentation.
