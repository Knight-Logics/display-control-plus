# Display Control+

Windows utility for multi-monitor burn-in protection with tray controls, idle detection, and media overlays.

## Download

- Latest release: https://github.com/Knight-Logics/display-control-plus/releases/latest
- Portable release binaries should stay together in the same folder after download

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

`DisplayControl.exe` is intended for direct dashboard access, but the tray-first path is the recommended release workflow.

## Features

- Per-monitor protection profiles
- Idle detection (system-wide or per-monitor)
- Modes: blank, single image, slideshow, video playlist
- Background runtime with tray controls
- Settings persisted in AppData
- 14-day free trial for new installs
- Hosted billing with annual subscription and lifetime purchase options
- Recovery-code based account restore and billing management
- Auto-update prompt when newer GitHub release exists

## Billing And Account Model

- New installs start with a free trial.
- Paid access is sold as either an annual subscription or a lifetime license.
- Billing is handled by a hosted licensing service and Stripe checkout.
- The app stores your billing email and recovery code locally so you can restore access on another machine.
- "Manage Billing" opens the hosted customer billing portal for subscription changes.

## Persistent Settings

Settings are stored in:
- `%APPDATA%\KnightLogics\DisplayControlPlus\config.json`

Licensing/account state is stored in:
- `%APPDATA%\KnightLogics\DisplayControlPlus\license.json`

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
