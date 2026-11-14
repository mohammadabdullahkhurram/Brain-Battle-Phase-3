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
| Replacing a file that is open elsewhere | `data/safe_io.replace_with_retry` | Windows refuses; retried briefly |
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
