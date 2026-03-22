"""
Auto-update checker for Display Control+.

On startup, a background thread hits the GitHub Releases API.
If a newer version tag is found, a non-blocking Tk dialog is shown
offering to open the releases page.  The UI is never blocked.
"""

import threading
import logging
import webbrowser
import tkinter as tk
from tkinter import ttk

# ── Version ──────────────────────────────────────────────────────────────────
CURRENT_VERSION = "1.0.2"          # bump this string on every release
RELEASES_API    = "https://api.github.com/repos/Knight-Logics/display-control-plus/releases/latest"
RELEASES_PAGE   = "https://github.com/Knight-Logics/display-control-plus/releases/latest"

# ── Version comparison ────────────────────────────────────────────────────────
def _parse(tag: str):
    """Convert 'v1.2.3' or '1.2.3' to a tuple of ints for comparison."""
    return tuple(int(x) for x in tag.lstrip("v").split(".") if x.isdigit())


def _fetch_latest_tag() -> str | None:
    """Return the latest release tag string from GitHub, or None on any error."""
    try:
        import urllib.request
        import json
        req = urllib.request.Request(
            RELEASES_API,
            headers={"User-Agent": "DisplayControlPlus-updater/1.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        return data.get("tag_name", "").strip()
    except Exception as e:
        logging.debug(f"[updater] Could not fetch latest release: {e}")
        return None


# ── Update prompt dialog ──────────────────────────────────────────────────────
def _show_update_dialog(parent: tk.Tk, latest_tag: str):
    """Show a small, themed, non-modal update notification window."""
    dlg = tk.Toplevel(parent)
    dlg.title("Update Available")
    dlg.configure(bg="#0f1115")
    dlg.resizable(False, False)
    dlg.transient(parent)

    # Center over parent
    parent.update_idletasks()
    px, py = parent.winfo_x(), parent.winfo_y()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    w, h = 400, 180
    dlg.geometry(f"{w}x{h}+{px + (pw - w)//2}+{py + (ph - h)//2}")

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
        wraplength=356, justify="left"
    ).pack(anchor="w", padx=22, pady=0)

    tk.Label(
        dlg,
        text=f"You are running:  v{CURRENT_VERSION}",
        bg="#0f1115", fg="#4d5a72",
        font=("Segoe UI", 9)
    ).pack(anchor="w", padx=22, pady=(2, 14))

    btn_row = tk.Frame(dlg, bg="#0f1115")
    btn_row.pack(anchor="e", padx=22, pady=(0, 18))

    def open_release():
        webbrowser.open(RELEASES_PAGE)
        dlg.destroy()

    # Use plain tk.Button so we can fully control colors without ttk style inheritance
    tk.Button(
        btn_row,
        text="Download Update",
        command=open_release,
        bg="#30c18d", fg="#0a1310",
        activebackground="#3ed9a0", activeforeground="#08110e",
        relief=tk.FLAT, bd=0,
        padx=14, pady=6,
        font=("Segoe UI", 10, "bold"),
        cursor="hand2"
    ).pack(side=tk.LEFT, padx=(0, 8))

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
        tag = _fetch_latest_tag()
        if not tag:
            return
        try:
            if _parse(tag) > _parse(CURRENT_VERSION):
                parent.after(0, lambda: _show_update_dialog(parent, tag))
        except Exception as e:
            logging.debug(f"[updater] Version comparison failed: {e}")

    t = threading.Thread(target=_worker, daemon=True, name="update-checker")
    t.start()
