"""
Auto-update checker for Display Control+.

On startup, a background thread checks GitHub Releases.
If a newer version exists, a topmost Tk dialog is shown that can download
the installer directly (no browser redirect) and launch it.
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import tkinter as tk

# ── Version ──────────────────────────────────────────────────────────────────
CURRENT_VERSION = "1.0.11"          # bump this string on every release
RELEASES_API    = "https://api.github.com/repos/Knight-Logics/display-control-plus/releases/latest"
RELEASES_PAGE   = "https://github.com/Knight-Logics/display-control-plus/releases/latest"
APPDATA_ROOT    = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_DIR     = os.path.join(APPDATA_ROOT, "KnightLogics", "DisplayControlPlus")
UPDATES_DIR     = os.path.join(RUNTIME_DIR, "updates")
PENDING_UPDATE_PATH = os.path.join(RUNTIME_DIR, "pending_update.json")

os.makedirs(UPDATES_DIR, exist_ok=True)

# ── Version comparison ────────────────────────────────────────────────────────
def _parse(tag: str):
    """Convert 'v1.2.3' or '1.2.3' to a tuple of ints for comparison."""
    return tuple(int(x) for x in tag.lstrip("v").split(".") if x.isdigit())


def _fetch_latest_release() -> dict | None:
    """Return latest release payload from GitHub, or None on any error."""
    try:
        import urllib.request

        req = urllib.request.Request(
            RELEASES_API,
            headers={"User-Agent": "DisplayControlPlus-updater/1.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logging.debug(f"[updater] Could not fetch latest release payload: {e}")
        return None


def _read_pending_update() -> dict:
    try:
        with open(PENDING_UPDATE_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _write_pending_update(tag: str):
    try:
        with open(PENDING_UPDATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"tag": tag, "ts": int(time.time())}, fh)
    except Exception as e:
        logging.debug(f"[updater] Could not write pending update state: {e}")


def _clear_pending_update():
    try:
        if os.path.exists(PENDING_UPDATE_PATH):
            os.remove(PENDING_UPDATE_PATH)
    except Exception as e:
        logging.debug(f"[updater] Could not clear pending update state: {e}")


def _should_suppress_prompt(latest_tag: str) -> bool:
    pending = _read_pending_update()
    pending_tag = str(pending.get("tag", "")).strip()
    pending_ts = int(pending.get("ts", 0) or 0)
    if not pending_tag:
        return False
    if _parse(CURRENT_VERSION) >= _parse(pending_tag):
        _clear_pending_update()
        return False
    if pending_tag == latest_tag and (time.time() - pending_ts) < 900:
        return True
    return False


def _select_installer_asset(release_payload: dict) -> tuple[str, str] | tuple[None, None]:
    """Pick the Windows installer asset URL/name from release assets."""
    assets = release_payload.get("assets", [])
    for asset in assets:
        name = str(asset.get("name", "")).strip()
        url = str(asset.get("browser_download_url", "")).strip()
        if name.lower().startswith("displaycontrolsetup_") and name.lower().endswith(".exe") and url:
            return url, name
    return None, None


def _download_installer(url: str, filename: str, status_cb=None) -> str:
    """Download installer to updates folder and return full path."""
    import urllib.request

    target_path = os.path.join(UPDATES_DIR, filename)
    tmp_path = f"{target_path}.part"

    if status_cb:
        status_cb("Downloading update...")

    req = urllib.request.Request(url, headers={"User-Agent": "DisplayControlPlus-updater/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp, open(tmp_path, "wb") as out:
        total = int(resp.headers.get("Content-Length", "0") or 0)
        downloaded = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if status_cb and total > 0:
                pct = int((downloaded / total) * 100)
                status_cb(f"Downloading update... {pct}%")

    os.replace(tmp_path, target_path)
    if status_cb:
        status_cb("Download complete.")
    return target_path


# ── Update prompt dialog ──────────────────────────────────────────────────────
def _show_update_dialog(parent: tk.Tk, latest_tag: str, asset_url: str | None, asset_name: str | None):
    """Show a topmost modal update dialog with direct installer download."""
    dlg = tk.Toplevel(parent)
    dlg.title("Update Available")
    dlg.configure(bg="#0f1115")
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.attributes("-topmost", True)
    dlg.lift()
    dlg.grab_set()

    # Center over parent
    parent.update_idletasks()
    px, py = parent.winfo_x(), parent.winfo_y()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    w, h = 470, 210
    dlg.geometry(f"{w}x{h}+{px + (pw - w)//2}+{py + (ph - h)//2}")

    status_var = tk.StringVar(value="Ready to download and install update.")
    in_progress = {"value": False}

    tk.Label(
        dlg,
        text="Update Available",
        bg="#0f1115", fg="#f2f7ff",
        font=("Segoe UI", 13, "bold")
    ).pack(anchor="w", padx=22, pady=(20, 4))

    tk.Label(
        dlg,
        text=f"A new version of Display Control+ is available:  {latest_tag}",
        bg="#0f1115", fg="#9aa7b7",
        font=("Segoe UI", 10),
        wraplength=426, justify="left"
    ).pack(anchor="w", padx=22, pady=0)

    tk.Label(
        dlg,
        text=f"You are running:  v{CURRENT_VERSION}",
        bg="#0f1115", fg="#4d5a72",
        font=("Segoe UI", 9)
    ).pack(anchor="w", padx=22, pady=(2, 6))

    tk.Label(
        dlg,
        textvariable=status_var,
        bg="#0f1115", fg="#73d9b5",
        font=("Segoe UI", 9),
        wraplength=426, justify="left"
    ).pack(anchor="w", padx=22, pady=(2, 14))

    btn_row = tk.Frame(dlg, bg="#0f1115")
    btn_row.pack(anchor="e", padx=22, pady=(0, 18))

    def _set_status(text: str):
        dlg.after(0, lambda: status_var.set(text))

    def _launch_installer(path: str):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # Kill sibling processes so the installer can replace their files cleanly.
        for proc in ("tray.exe", "overlay_bg.exe"):
            subprocess.run(
                ["taskkill", "/F", "/IM", proc],
                creationflags=flags,
                capture_output=True
            )
        subprocess.Popen(
            [path, "/SILENT", "/NORESTART"],
            creationflags=flags
        )

    def download_and_install():
        if in_progress["value"]:
            return
        if not asset_url or not asset_name:
            status_var.set("Installer asset not found on the latest release.")
            return

        resolved_url = asset_url
        resolved_name = asset_name

        in_progress["value"] = True
        download_btn.config(state=tk.DISABLED)

        def _worker():
            try:
                installer_path = _download_installer(resolved_url, resolved_name, status_cb=_set_status)
                _write_pending_update(latest_tag)
                _set_status("Installing update — app will reopen automatically...")
                dlg.after(0, lambda: _launch_installer(installer_path))
                # Close dashboard after launching installer so the installer can replace files.
                def _safe_destroy():
                    try:
                        parent.destroy()
                    except Exception:
                        pass
                dlg.after(800, _safe_destroy)
            except Exception as e:
                logging.debug(f"[updater] Download/install failed: {e}")
                _set_status(f"Update failed: {e}")
                dlg.after(0, lambda: download_btn.config(state=tk.NORMAL))
                in_progress["value"] = False

        threading.Thread(target=_worker, daemon=True, name="update-download").start()

    # Use plain tk.Button so we can fully control colors without ttk style inheritance
    download_btn = tk.Button(
        btn_row,
        text="Download and Install",
        command=download_and_install,
        bg="#30c18d", fg="#0a1310",
        activebackground="#3ed9a0", activeforeground="#08110e",
        relief=tk.FLAT, bd=0,
        padx=14, pady=6,
        font=("Segoe UI", 10, "bold"),
        cursor="hand2"
    )
    download_btn.pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(
        btn_row,
        text="Remind Me Later",
        command=dlg.destroy,
        bg="#252b37", fg="#d4dde9",
        activebackground="#2d3444", activeforeground="#f2f7ff",
        relief=tk.FLAT, bd=0,
        padx=14, pady=6,
        font=("Segoe UI", 10),
        cursor="hand2"
    ).pack(side=tk.LEFT)


# ── Public entry point ────────────────────────────────────────────────────────
def check_for_updates(parent: tk.Tk):
    """
    Call this once after the main window is displayed.
    Runs the network request in a background thread; if an update is found,
    schedules the dialog on the Tk main thread via after().
    """
    def _worker():
        payload = _fetch_latest_release()
        if not payload:
            return
        try:
            tag = str(payload.get("tag_name", "")).strip()
            if not tag:
                return
            if _should_suppress_prompt(tag):
                return
            if _parse(tag) > _parse(CURRENT_VERSION):
                asset_url, asset_name = _select_installer_asset(payload)
                parent.after(0, lambda: _show_update_dialog(parent, tag, asset_url, asset_name))
        except Exception as e:
            logging.debug(f"[updater] Version comparison failed: {e}")

    t = threading.Thread(target=_worker, daemon=True, name="update-checker")
    t.start()
