import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
import time

from PIL import Image

try:
    import pystray
except ImportError:
    pystray = None

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", "DisplayControlPlus")
os.makedirs(RUNTIME_DIR, exist_ok=True)

LOG_PATH = os.path.join(RUNTIME_DIR, "overlay.log")
CONFIG_PATH = os.path.join(RUNTIME_DIR, "config.json")
BG_LOCK_PATH = os.path.join(RUNTIME_DIR, "overlay_bg.lock")
GUI_LOCK_PATH = os.path.join(RUNTIME_DIR, "display_control_gui.lock")

logging.basicConfig(filename=LOG_PATH, level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")


def app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))


def _runtime_candidates(*parts):
    base = app_base_dir()
    candidates = [os.path.join(base, *parts)]
    if parts and parts[0] != "dist":
        candidates.append(os.path.join(base, "dist", *parts))
    return candidates


def _first_existing_path(*parts):
    for candidate in _runtime_candidates(*parts):
        if os.path.exists(candidate):
            return candidate
    return ""


def _load_lock_pid(lock_path):
    try:
        with open(lock_path, "r", encoding="utf-8") as lock_file:
            raw = lock_file.read().strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        payload = json.loads(raw)
        pid = payload.get("pid")
        if isinstance(pid, str) and pid.isdigit():
            return int(pid)
        if isinstance(pid, int):
            return pid
    except Exception:
        return None
    return None


def is_dashboard_running():
    if not os.path.exists(GUI_LOCK_PATH):
        return False
    pid = _load_lock_pid(GUI_LOCK_PATH)
    if pid is None:
        try:
            os.remove(GUI_LOCK_PATH)
        except Exception:
            pass
        return False
    if _is_pid_alive(pid):
        return True
    try:
        os.remove(GUI_LOCK_PATH)
    except Exception:
        pass
    return False


def _is_pid_alive(pid):
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def is_background_running():
    if not os.path.exists(BG_LOCK_PATH):
        return False
    try:
        with open(BG_LOCK_PATH, "r", encoding="utf-8") as f:
            pid_str = f.read().strip()
        if not pid_str.isdigit():
            os.remove(BG_LOCK_PATH)
            return False
        pid = int(pid_str)
        if _is_pid_alive(pid):
            return True
        os.remove(BG_LOCK_PATH)
        return False
    except Exception:
        return False


def start_background_if_needed():
    if is_background_running():
        return
    bg_exe = _first_existing_path("overlay_bg.exe")
    bg_py = _first_existing_path("overlay_bg.py")
    try:
        if bg_exe:
            subprocess.Popen([bg_exe], start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW)
        elif bg_py:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            subprocess.Popen([pythonw, bg_py], start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        logging.error(f"Failed to start background process from tray: {e}")


def stop_background():
    if not os.path.exists(BG_LOCK_PATH):
        return
    try:
        with open(BG_LOCK_PATH, "r", encoding="utf-8") as f:
            pid_str = f.read().strip()
        if pid_str.isdigit():
            pid = int(pid_str)
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        logging.error(f"Failed to stop background process from tray: {e}")
    try:
        if os.path.exists(BG_LOCK_PATH):
            os.remove(BG_LOCK_PATH)
    except Exception:
        pass


def set_paused(value):
    cfg = _load_config()
    cfg["paused"] = bool(value)
    _save_config(cfg)


def is_paused():
    cfg = _load_config()
    return bool(cfg.get("paused", False))


def open_dashboard():
    if is_dashboard_running():
        logging.info("Dashboard is already running; tray open request ignored.")
        return

    app_exe_candidates = [
        _first_existing_path("DisplayControl.exe"),
        _first_existing_path("main.exe"),
    ]
    for candidate in app_exe_candidates:
        if candidate and os.path.exists(candidate):
            subprocess.Popen([candidate], start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return

    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    main_py = _first_existing_path("main.py")
    if main_py:
        subprocess.Popen([pythonw, main_py], start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW)


def _on_open(_icon, _item):
    open_dashboard()


def _on_pause_resume(_icon, _item):
    new_paused = not is_paused()
    set_paused(new_paused)
    if new_paused:
        stop_background()
    else:
        start_background_if_needed()


def _pause_label(_item):
    return "Resume Protection" if is_paused() else "Pause Protection"


def _on_exit(icon, _item):
    set_paused(True)
    stop_background()
    icon.stop()


def _status_label(_item):
    if is_paused():
        return "Status: Paused"
    if is_background_running():
        return "Status: Active"
    return "Status: Idle"


def _build_icon_image():
    primary_logo = _first_existing_path("Display Control+ Logo.png")
    fallback_logo = _first_existing_path("KnightLogicsLogo.png")
    chosen = primary_logo if os.path.exists(primary_logo) else fallback_logo

    if os.path.exists(chosen):
        with Image.open(chosen) as img:
            icon_img = img.convert("RGBA")
            icon_img.thumbnail((64, 64), Image.Resampling.LANCZOS)
            return icon_img

    img = Image.new("RGBA", (64, 64), (15, 17, 21, 255))
    return img


def keep_background_alive():
    while True:
        try:
            if not is_paused() and not is_background_running():
                start_background_if_needed()
        except Exception as e:
            logging.error(f"Tray heartbeat error: {e}")
        time.sleep(10)


def run_tray():
    if pystray is None:
        ctypes.windll.user32.MessageBoxW(0, "pystray is required for system tray mode. Install pystray to enable tray controls.", "Display Control+", 0x40)
        return

    # Default startup behavior for a professional package: protection active unless user paused it.
    if not is_paused():
        start_background_if_needed()

    image = _build_icon_image()
    menu = pystray.Menu(
        pystray.MenuItem(_status_label, None, enabled=False),
        pystray.MenuItem("Open Dashboard", _on_open),
        pystray.MenuItem(_pause_label, _on_pause_resume),
        pystray.MenuItem("Exit Completely", _on_exit)
    )

    icon = pystray.Icon("display_control_plus", image, "Display Control+", menu)
    icon.on_activate = _on_open  # Left-click opens dashboard
    threading.Thread(target=keep_background_alive, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    run_tray()
