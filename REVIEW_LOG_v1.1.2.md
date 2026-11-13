# Display Control+ v1.1.2 review log

Reviewed PR #3 (`cursor/fix-protection-and-startup-429d`) against the knight-account-api Worker contract in Knight-Logics/knight-account-api PR #1 (`worker/src/grace.ts`, `worker/src/index.ts`, `worker/src/ledger.ts` on `cursor/knight-account-worker-56cd`). No merge, tag, release, or deploy was performed.

## Contract the client now follows

- `POST /api/licenses/create-checkout-session`, `confirm-session`, `restore`, and `create-portal-session` stay the Display Control+ billing calls. `POST /api/knight-account` with `action=status` and `product_id=displaycontrol` is called on the same refresh so the client can cache `entitlement_token`, `in_grace`, `grace_until`, and `offline_grace_seconds`.
- Default base URL when `DISPLAY_CONTROL_LICENSE_SERVER` is unset: `https://knightlogics.com`. An empty value disables the calls.
- HTTP 503 is fail-open only when the JSON body sets `fail_open` to true (license store down). A 503 that means Stripe is not configured does not extend a license.
- Paid grace is 7 days (`604800` seconds), the same constant as `DEFAULT_OFFLINE_GRACE_SECONDS` in the Worker.
  - `active`, `trialing`, and `past_due` stay entitled through the period end, and for 7 days after the last successful sync once that period has ended.
  - `canceled`, `unpaid`, and `incomplete` stay entitled until `period_end` plus 7 days. The Worker rewrites that window to `past_due` plus a still-future period end on restore; the client also accepts the raw canceled status.
  - A 503 fail-open window is anchored at `last_synced_at` and is not pushed forward by later 503s. A successful sync clears it.
  - An expired local trial is not started or extended by a 503.
- `/api/knight-account` status is not recovery-code authenticated, so a failed restore is not replaced by a status payload.

## What was re-read

Every changed line in the PR, then the follow-up edits in:

- `license_meter.py`
- `payment_service.py`
- `overlay.py`
- `ensure_overlay_bg_task.py`
- `screen_grab.py`
- `monitor_activity.py`
- `installer/OLEDProtector.iss`
- `installer/build_installer.ps1`
- `updater.py`
- `requirements.txt`
- `CHANGELOG.txt`
- `.env.example`
- `.github/workflows/secret-scan.yml`
- `test_license_state.py`, `test_screen_grab.py`, `test_startup.py`, `test_payment_contract.py`, `test_billing_system.py`, `test_monitor_activity.py`

## CI log

Command:

```bash
gh run view 43398276811 --repo Knight-Logics/display-control-plus --log
```

That is the only workflow run on this branch before the review fixes. Job `gitleaks`, run `43398276811`, event `pull_request`, head `6a7f29c25726201314101ea07b539d95da01b5cf`, conclusion **success**.

Read line by line. Outcome:

- Checkout `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5` fetched the PR merge ref `4fa2a7691954aa87752980e719f8b443ac2b5b30`.
- Git printed its usual hint that a new repository uses `master` until Git 3.0. That is git's own advice during `git init`, not a failing step.
- Gitleaks `8.30.1` checksum matched `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`.
- Scan range `origin/main...HEAD`: `1 commits scanned`, `~40024 bytes`, `no leaks found`.
- One warning: `Node.js 20 is deprecated... actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5` is being forced onto Node.js 24.

Fix: pin checkout to `actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8` (`v5.0.0`). That commit's `action.yml` says `using: node24`. Confirmed with `gh api repos/actions/checkout/commits/08c6903cd8c0fde910a37f88322edcfb5dd907a8`.

Follow-up run after that pin, read the same way:

```bash
gh run view 43500882334 --repo Knight-Logics/display-control-plus --log
```

Job `gitleaks`, head merge `c4e74c2c43194ff0a8dc7d1004f07a5ad029f709` (includes `a10727e`), conclusion **success**. Checkout downloaded `08c6903`. Gitleaks checksum matched. `2 commits scanned`, `~80492 bytes`, `no leaks found`. There is no `##[warning]` and no Node.js 20 line. The only hint text is git's own `init.defaultBranch` note while Actions creates an empty repository. That is not a workflow failure.

There were no failing or flaky checks. The secret-scan workflow is the only check in this repository.

## Commands run on this machine

| Command | Outcome |
| --- | --- |
| `python3 -m unittest discover -s . -p 'test_*.py' -q` | **35 tests, OK** |
| `python3 -m ruff check --select F` on the PR modules and tests | **All checks passed** |
| `python3 -m mypy --ignore-missing-imports --follow-imports=silent license_meter.py payment_service.py screen_grab.py ensure_overlay_bg_task.py` | **Success: no issues found in 4 source files** |
| `python3 -m PyInstaller ... --name tray ... /workspace/tray.py` (PyInstaller 6.22.3, Python 3.12.3) | **Built `/tmp/dcp-dist/tray`, 22 MB** |

`ruff` with its full default rule set reports hundreds of pre-existing line-length and logging-style findings across the repo. Those were not rewritten. Pyflakes (`F`) on the modules this PR changes is clean.

PyInstaller warnings while building `tray.py` on Linux: `Namespace Gtk not available`, and missing typelibs for `AppIndicator3`, `AyatanaAppIndicator3`, and `Gtk 3.0`. Those are the Linux pystray backend. The Windows build uses the Win32 backend and does not need those typelibs. `warn-tray.txt` also lists expected cross-platform stubs (`_winapi`, `nt`, AppKit). No missing application module.

`python3 -m pip install pynput` failed here because `evdev` needs `Python.h` (`python3-dev` is not installed). Input listening imports `pynput` only when a listener starts, so unit tests import without it. The Windows build already installs `pynput` from `requirements.txt`.

Inno Setup, `DisplayControl.exe`, `overlay_bg.exe`, and `checkout_window.exe` were not built. `overlay.py` imports `tkinter` and the monitor code imports `pywin32`, and the installer script is Inno Setup. Those steps need a Windows machine.

## Errors found in review, and the fix

1. **Grace was 14 days.** The Worker constant is 7 days. `OFFLINE_GRACE_SECONDS` is now `7 * 24 * 60 * 60`.
2. **Any 64-character hex signature was treated as a valid license.** A local edit could set `lifetime` and keep a signature from another machine. v1 files still verify with the v1 prefix and the fingerprint variants. Anything else is quarantined to `license.json.tampered` and replaced with a normal trial, not an already-expired trial.
3. **`canceled` / `unpaid` / `incomplete` were cut off immediately.** They now stay entitled until `period_end` plus 7 days, matching `decideSubscription`.
4. **HTTP 503 was only stored as text.** `payment_service._request_json` now records `http_status` and `fail_open`. Only an explicit `fail_open: true` opens the paid window. The window is anchored at `last_synced_at` and does not slide on a later 503. A successful sync clears `fail_open_until` and `fail_open_paid`.
5. **A 503 could not be told apart from "Stripe is not configured."** Both are HTTP 503. Only the body flag `fail_open: true` fail-opens.
6. **The client never called `/api/knight-account`.** `sync_account` posts `/api/licenses/restore` and `/api/knight-account`. The status call adds the entitlement token when the restore email matches. A status payload is not used as a grant when restore fails.
7. **The default license server was blank**, so a packaged build with no env var never called the Worker. The default is `https://knightlogics.com`.
8. **`sync_remote_account` could set `lifetime` to true and never set it back to false.** An explicit `lifetime: false` from restore or confirm now clears it.
9. **Dashboard and background could read-modify-write `license.json` together, and two startup registrations could delete/create the scheduled task together.** Both paths take a reentrant file lock (`license.json.lock`, `startup.lock`).
10. **`unittest discover` executed `test_monitor_activity.py` at import and then failed on `pynput`.** That file is a manual demo. The loop is under `if __name__ == "__main__"`, and `pynput` is imported when input listeners start.
11. **Secret-scan Node.js 20 warning.** Checkout pin moved to v5.0.0 (`node24`), as listed above.
12. **Pyflakes in the touched modules:** unused `last_idle_snapshot`, unused `app_dir`, an unused license fetch, an f-string with no placeholder, and an unused `plan_key` in the billing fake. Removed. `payment_service` no longer assigns `None` over the dotenv function, which mypy rejected.

## Tests added for those paths

- v1 file migrates, unverified signature does not keep lifetime
- active subscription within 7 days of the last sync, and after that window
- canceled subscription inside and outside period_end plus 7 days
- 503 fail-open keeps a paid license, does not slide, does not revive an expired trial
- a later successful sync clears fail-open and a revoked lifetime
- both HTTP routes, status-alone does not grant, Stripe 503 is not fail-open
- negative virtual-desktop crop math
- Run-key startup when `schtasks` returns Access is denied

## Remaining risks (Windows hardware)

- `screen_grab._grab_region_win32` was not executed. BitBlt, DPI awareness, and a monitor left of or above the primary still need a real desktop. Pillow's `ImageGrab` path is no longer used for activity polling.
- HKCU Run and `schtasks /RL LIMITED /IT` were mocked. A standard-user install over an older elevated task still needs a Windows login.
- The Inno Setup script and the four Windows executables were not built here. The Linux `tray` binary is not a substitute for `tray.exe`.
- `entitlement_token` is stored but not HMAC-checked. The signing secret stays on the Worker. The token is only kept after a successful HTTPS response and is cleared as a grant source when a later sync says the license is gone.
- A real `license.json.tampered` from a missing signature is removed the next time the replacement `license.json` verifies. That cleans the old false positive. It also drops the quarantined copy after the following launch.
- `knightlogics.com/api/knight-account` still returns 404 until the site Worker forwards the path. A 404 is not fail-open. Local trial and an already stored paid license stay; a 404 does not create a license.
