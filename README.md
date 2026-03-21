# Display Control+

Windows utility for multi-monitor burn-in protection with tray controls, idle detection, and media overlays.

## Download

- Latest release: https://github.com/Knight-Logics/display-control-plus/releases/latest
- Direct v1.0.0 page: https://github.com/Knight-Logics/display-control-plus/releases/tag/v1.0.0

## What Users Should Run

Most users should run:
- `tray.exe`

Optional executables:
- `DisplayControl.exe` (opens dashboard directly)
- `overlay_bg.exe` (background fallback process; usually managed by tray)

## Quick Start (End Users)

1. Download `tray.exe` from Releases.
2. Double-click `tray.exe`.
3. Right-click tray icon and choose **Open Dashboard**.
4. Select displays, choose timeout/mode, and click **Apply**.

## Features

- Per-monitor protection profiles
- Idle detection (system-wide or per-monitor)
- Modes: blank, single image, slideshow, video playlist
- Background runtime with tray controls
- Settings persisted in AppData
- Auto-update prompt when newer GitHub release exists

## Persistent Settings

Settings are stored in:
- `%APPDATA%\KnightLogics\DisplayControlPlus\config.json`

Media attachments persist across restarts as saved file paths. If files are moved or deleted, the setting remains but media cannot load until paths are corrected.

## Legal

- License: `LICENSE.txt`
- Terms: `TERMS_OF_USE.md`
- Privacy: `PRIVACY_POLICY.md`

## Developer Notes

- Entry points:
  - `main.py`
  - `tray.py`
  - `overlay_bg.py`
- Build outputs are in `dist/`.
- Release binaries are attached to GitHub Releases.
