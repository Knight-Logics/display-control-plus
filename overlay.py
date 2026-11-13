import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import json
import logging
import multiprocessing
import os
import re
import sys
import ctypes
import threading
import time
import tempfile
import webbrowser
from PIL import Image, ImageTk
from billing_catalog import get_plan, plan_button_label
from monitor_activity import MonitorActivityDetector
from license_meter import get_status as get_license_status, consume_active_seconds, add_hours, activate_lifetime, activate_subscription, sync_remote_account, restore_from_recovery_code, apply_remote_sync_result
from payment_service import is_payment_available, create_checkout_session, confirm_session, create_portal_session, sync_account

# Debug logging setup
debug_window = None
debug_text = None
debug_queue = []

def log_debug(msg):
    """Log a message to the debug window and print to console."""
    timestamp = time.strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] {msg}"
    debug_queue.append(log_msg)
    logging.info(msg)
    if debug_text and debug_text.winfo_exists():
        debug_text.config(state=tk.NORMAL)
        debug_text.insert(tk.END, log_msg + "\n")
        debug_text.see(tk.END)
        debug_text.config(state=tk.DISABLED)

def open_debug_window():
    """Open a floating debug window."""
    global debug_window, debug_text
    if debug_window and debug_window.winfo_exists():
        debug_window.lift()
        return
    
    debug_window = tk.Toplevel()
    debug_window.title("Display Control+ Debug Log")
    debug_window.geometry("600x400")
    debug_window.configure(bg="#0f1115")
    
    ttk.Label(debug_window, text="Debug Log:", style="Body.TLabel").pack(anchor="w", padx=10, pady=(10, 5))
    
    debug_text = tk.Text(
        debug_window,
        bg="#161a22",
        fg="#d4dde9",
        font=("Courier New", 9),
        wrap=tk.WORD,
        state=tk.DISABLED
    )
    debug_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
    
    # Populate with existing logs
    for msg in debug_queue:
        debug_text.config(state=tk.NORMAL)
        debug_text.insert(tk.END, msg + "\n")
        debug_text.config(state=tk.DISABLED)
    debug_text.see(tk.END)

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
DEFAULT_RUNTIME_PROFILE = "DisplayControlPlus" if getattr(sys, "frozen", False) else "DisplayControlPlus-DevLocal"
DEFAULT_TASK_NAME = "DisplayControlBackground" if getattr(sys, "frozen", False) else "DisplayControlBackground-DevLocal"
RUNTIME_PROFILE = os.environ.get("DISPLAY_CONTROL_RUNTIME_PROFILE", DEFAULT_RUNTIME_PROFILE)
TASK_NAME = os.environ.get("DISPLAY_CONTROL_TASK_NAME", DEFAULT_TASK_NAME)
DEV_TEST_PURCHASES_ENABLED = (not getattr(sys, "frozen", False)) or os.environ.get("DISPLAY_CONTROL_ENABLE_TEST_PURCHASES", "").strip().lower() in ("1", "true", "yes", "on")
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", RUNTIME_PROFILE)
os.makedirs(RUNTIME_DIR, exist_ok=True)
USAGE_STATE_PATH = os.path.join(RUNTIME_DIR, "overlay_usage_state.json")

def runtime_path(name):
    return os.path.join(RUNTIME_DIR, name)


def _atomic_json_write(path, payload):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp_path, path)


def _config_file_signature():
    path = runtime_path("config.json")
    try:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logging.warning("Could not stat config file: %s", exc)
        return None


def _read_usage_state():
    try:
        with open(USAGE_STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not read overlay usage state: %s", exc)
    return {
        "active": False,
        "active_session_seconds": 0,
        "remaining_seconds": 0,
        "lifetime": False,
        "updated_at": int(time.time()),
    }


def _write_usage_state(**updates):
    state = _read_usage_state()
    state.update(updates)
    state["updated_at"] = int(time.time())
    try:
        with open(USAGE_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError as exc:
        logging.warning("Could not write overlay usage state: %s", exc)


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
    return _runtime_candidates(*parts)[0]


def _current_process_names():
    names = {
        os.path.basename(sys.executable).lower(),
        "python.exe",
        "pythonw.exe",
        "displaycontrol.exe",
        "displaycontrol+.exe",
        "displaycontrolplus.exe",
        "main.exe",
    }
    return {name for name in names if name}


def _load_lock_payload(lock_path):
    with open(lock_path, "r") as lock_file:
        raw = lock_file.read().strip()
    if not raw:
        return None, ""
    if raw.isdigit():
        return int(raw), ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logging.warning("Could not parse lock file %s: %s", lock_path, exc)
        return None, ""
    pid = payload.get("pid")
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    if not isinstance(pid, int):
        return None, ""
    process_name = str(payload.get("process_name", "")).strip().lower()
    return pid, process_name


def _start_startup_registration():
    def _run():
        try:
            from ensure_overlay_bg_task import ensure_overlay_bg_task

            result = ensure_overlay_bg_task()
            if not result or not result.get("ok"):
                detail = (result or {}).get("task_error") or "no startup target"
                logging.error("Per-user startup registration failed: %s", detail)
        except (OSError, subprocess.SubprocessError) as exc:
            logging.error("Failed to register per-user startup: %s", exc)

    threading.Thread(target=_run, daemon=True, name="display-control-startup-registration").start()

# --- Logging Setup ---
logging.basicConfig(filename=runtime_path("overlay.log"), level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

# --- Overlay Functions ---
def show_black_overlay(geometry, demo=False):
    logging.debug(f"show_black_overlay called with geometry={geometry}, demo={demo}")
    left, top, right, bottom = geometry
    width = right - left
    height = bottom - top
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{width}x{height}+{left}+{top}")
    root.configure(bg='black')
    root.attributes('-topmost', True)
    root.config(cursor="none")
    if demo:
        root.after(3000, root.destroy)
    root.mainloop()

# --- Idle Detection Top-Level Function ---
# This function provides system-wide idle time for background overlay logic
_idle_detector = None
def get_idle_duration():
    global _idle_detector
    if _idle_detector is None:
        try:
            from monitor_control import get_monitors
            monitors = get_monitors()
            _idle_detector = MonitorActivityDetector(monitors)
            _idle_detector.start()
            time.sleep(1)  # Let detector initialize
        except Exception as e:
            logging.error(f"[DIAG] Failed to initialize MonitorActivityDetector: {e}")
            return 0
    idle_times = _idle_detector.get_idle_times()
    return idle_times.get("system", 0)

def show_image_overlay(geometry, img_path, demo=False):
    # Display the selected image over the screen
    logging.info(f"show_image_overlay called: geometry={geometry}, img_path={img_path}, demo={demo}")
    left, top, right, bottom = geometry
    width = right - left
    height = bottom - top
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{width}x{height}+{left}+{top}")
    root.configure(bg='black')
    root.attributes('-topmost', True)
    root.config(cursor="none")
    try:
        logging.info(f"Attempting to open image: {img_path}")
        img = Image.open(img_path)
        logging.info(f"Image opened: {img.size}, mode={img.mode}")
        # Use modern Pillow resampling constant
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS  # fallback for older Pillow
        img = img.resize((width, height), resample)
        photo = ImageTk.PhotoImage(img)
        label = tk.Label(root, image=photo, bg='black')
        label.image = photo  # Keep reference
        label.pack(fill=tk.BOTH, expand=True)
        logging.info("Image displayed on overlay window.")
    except Exception as e:
        logging.error(f"Failed to display image overlay: {e}")
        label = tk.Label(root, text=f"Error displaying image:\n{e}", fg="red", bg="black", font=("Segoe UI", 20))
        label.pack(fill=tk.BOTH, expand=True)
    if demo:
        root.after(3000, root.destroy)
    root.mainloop()

def show_slideshow_overlay(geometry, img_paths, interval=30, demo=False):
    import itertools
    logging.info(f"[SLIDESHOW] Starting slideshow overlay: geometry={geometry}, interval={interval}, demo={demo}, img_paths={img_paths}")
    left, top, right, bottom = geometry
    width = right - left
    height = bottom - top
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{width}x{height}+{left}+{top}")
    root.configure(bg='black')
    root.attributes('-topmost', True)
    root.config(cursor="none")

    label = tk.Label(root, bg='black')
    label.pack(fill=tk.BOTH, expand=True)

    def update_image(img_path):
        logging.info(f"[SLIDESHOW] Attempting to load image: {img_path}")
        try:
            img = Image.open(img_path)
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS
            img = img.resize((width, height), resample)
            photo = ImageTk.PhotoImage(img)
            label.config(image=photo)
            label.image = photo
            logging.info(f"[SLIDESHOW] Displayed image: {img_path}")
        except Exception as e:
            logging.error(f"[SLIDESHOW] Slideshow image load error: {e}")
            label.config(text=f"Error loading: {img_path}", fg="red", bg="black")

    def slideshow_loop():
        paths = itertools.cycle(img_paths)
        def advance():
            next_img = next(paths)
            logging.info(f"[SLIDESHOW] Advancing to next image: {next_img}")
            update_image(next_img)
            root.after(interval * 1000, advance)
        advance()

    slideshow_loop()

    if demo:
        logging.info("[SLIDESHOW] Demo mode: overlay will close after 3 seconds.")
        root.after(3000, root.destroy)
    root.mainloop()

def show_video_overlay(geometry, video_paths, demo=False):
    logging.info(f"show_video_overlay called: geometry={geometry}, video_paths={video_paths}, demo={demo}")
    left, top, right, bottom = geometry
    width = right - left
    height = bottom - top
    try:
        import cv2
    except ImportError:
        logging.error("opencv-python not installed. Falling back to black overlay for video mode.")
        show_black_overlay(geometry, demo)
        return
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{width}x{height}+{left}+{top}")
    root.configure(bg='black')
    root.attributes('-topmost', True)
    root.config(cursor="none")
    label = tk.Label(root, bg='black')
    label.pack(fill=tk.BOTH, expand=True)
    playlist = [p for p in (video_paths or []) if isinstance(p, str) and os.path.exists(p)]
    if not playlist:
        logging.error("No valid video files for video overlay.")
        root.destroy()
        return

    state = {
        "index": 0,
        "cap": None,
        "delay": 33,
        "photo": None
    }

    def open_current_video():
        if state["cap"] is not None:
            state["cap"].release()
            state["cap"] = None
        path = playlist[state["index"]]
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            logging.error(f"Could not open video: {path}")
            return False
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        state["delay"] = max(1, int(1000 / fps))
        state["cap"] = cap
        return True

    if not open_current_video():
        root.destroy()
        return

    def play_frame():
        cap = state["cap"]
        if cap is None:
            return

        ret, frame = cap.read()
        if not ret:
            state["index"] = (state["index"] + 1) % len(playlist)
            if not open_current_video():
                root.after(500, play_frame)
                return
            cap = state["cap"]
            ret, frame = cap.read()

        if ret:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_resized = cv2.resize(frame_rgb, (width, height))
            img = Image.fromarray(frame_resized)
            photo = ImageTk.PhotoImage(img)
            state["photo"] = photo
            label.config(image=photo)
        root.after(state["delay"], play_frame)

    if demo:
        root.after(3000, root.destroy)
    play_frame()
    root.mainloop()
    if state["cap"] is not None:
        state["cap"].release()


def _normalize_geometry(geometry):
    if not isinstance(geometry, (list, tuple)) or len(geometry) != 4:
        return None
    values = []
    for value in geometry:
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            return None
    return tuple(values)


def _monitor_identity(monitors, idx):
    if not isinstance(idx, int) or idx < 0 or idx >= len(monitors):
        return None
    geometry = _normalize_geometry(monitors[idx].get("geometry"))
    if geometry is None:
        return None
    return {
        "index": idx,
        "geometry": list(geometry),
        "device": str(monitors[idx].get("device", "")).strip(),
    }


def _resolve_monitor_indices(monitors, saved_indices=None, saved_geometries=None, saved_devices=None):
    saved_indices = [idx for idx in (saved_indices or []) if isinstance(idx, int) and idx >= 0]
    saved_geometries = [_normalize_geometry(geometry) for geometry in (saved_geometries or [])]
    saved_devices = [str(device or "").strip() for device in (saved_devices or [])]

    resolved = []
    used = set()

    def add_idx(idx):
        if isinstance(idx, int) and 0 <= idx < len(monitors) and idx not in used:
            resolved.append(idx)
            used.add(idx)

    ordered_count = max(len(saved_indices), len(saved_geometries), len(saved_devices))
    for pos in range(ordered_count):
        saved_device = saved_devices[pos].lower() if pos < len(saved_devices) else ""
        saved_geometry = saved_geometries[pos] if pos < len(saved_geometries) else None
        matched_idx = None

        if saved_device:
            for idx, mon in enumerate(monitors):
                if idx in used:
                    continue
                device_name = str(mon.get("device", "")).strip().lower()
                if device_name and device_name == saved_device:
                    matched_idx = idx
                    break

        if matched_idx is None and saved_geometry is not None:
            for idx, mon in enumerate(monitors):
                if idx in used:
                    continue
                if _normalize_geometry(mon.get("geometry")) == saved_geometry:
                    matched_idx = idx
                    break

        if matched_idx is None and pos < len(saved_indices):
            candidate = saved_indices[pos]
            if 0 <= candidate < len(monitors) and candidate not in used:
                matched_idx = candidate

        add_idx(matched_idx)

    if not resolved:
        for idx in saved_indices:
            add_idx(idx)

    return resolved


def _hydrate_setting_group(monitors, group):
    hydrated = dict(group or {})
    original_indices = [idx for idx in hydrated.get("monitor_indices", []) if isinstance(idx, int)]
    resolved_indices = _resolve_monitor_indices(
        monitors,
        original_indices,
        hydrated.get("monitor_geometries", []),
        hydrated.get("monitor_devices", []),
    )
    if original_indices and resolved_indices and resolved_indices != original_indices:
        logging.info(
            f"Resolved saved monitor indices {original_indices} to current indices {resolved_indices} "
            f"for group '{hydrated.get('name', 'Setting')}'."
        )

    hydrated["monitor_indices"] = resolved_indices
    hydrated["monitor_geometries"] = []
    hydrated["monitor_devices"] = []
    for idx in resolved_indices:
        identity = _monitor_identity(monitors, idx)
        if identity is None:
            continue
        hydrated["monitor_geometries"].append(identity["geometry"])
        hydrated["monitor_devices"].append(identity["device"])
    return hydrated


def _collect_overlay_targets(groups, monitors, idle_snapshot):
    overlay_targets = {}
    for raw_group in groups:
        if not isinstance(raw_group, dict):
            continue
        group = _hydrate_setting_group(monitors, raw_group)
        indices = group.get("monitor_indices", [])
        if not indices:
            continue

        timeout_min = group.get("timeout", 5)
        timeout_sec = max(1, int(float(timeout_min) * 60))
        scope = group.get("scope", "system")
        detection_mode = group.get("detection_mode", "input")
        if detection_mode not in ("input", "activity", "both"):
            detection_mode = "input"

        mode_idle = idle_snapshot.get(detection_mode, {})
        if scope == "system":
            if mode_idle.get("system", 0) >= timeout_sec:
                for idx in indices:
                    overlay_targets[idx] = group
            continue

        for idx in indices:
            geometry = _normalize_geometry(monitors[idx].get("geometry"))
            if geometry is not None and mode_idle.get(geometry, 0) >= timeout_sec:
                overlay_targets[idx] = group

    return overlay_targets


def _collect_wake_targets(groups, monitors, idle_snapshot, previous_input_idle=None):
    wake_targets = set()
    input_idle = idle_snapshot.get("input", {})
    previous_input_idle = previous_input_idle or {}

    def _saw_fresh_input(current_idle, previous_idle):
        if previous_idle is None:
            return False
        try:
            current_idle = float(current_idle)
            previous_idle = float(previous_idle)
        except (TypeError, ValueError):
            return False
        # A drop in idle time means new input arrived after overlays were shown.
        return current_idle + 0.25 < previous_idle

    for raw_group in groups:
        if not isinstance(raw_group, dict):
            continue
        group = _hydrate_setting_group(monitors, raw_group)
        indices = group.get("monitor_indices", [])
        if not indices:
            continue

        scope = group.get("scope", "system")
        if scope == "system":
            if _saw_fresh_input(input_idle.get("system", 999999), previous_input_idle.get("system")):
                wake_targets.update(indices)
            continue

        for idx in indices:
            geometry = _normalize_geometry(monitors[idx].get("geometry"))
            if geometry is not None and _saw_fresh_input(input_idle.get(geometry, 999999), previous_input_idle.get(geometry)):
                wake_targets.add(idx)

    return wake_targets

# --- Config Functions ---
def save_config(monitors, selected, mode, file_paths, timeout, interval, enabled, scope, detection_mode,
    setting_groups=None, paused=None):
    existing = load_config() or {}
    groups = [_hydrate_setting_group(monitors, group) for group in (setting_groups or []) if isinstance(group, dict)]
    active = groups[0] if groups else _hydrate_setting_group(monitors, {
        "name": "Setting 1",
        "monitor_indices": list(selected),
        "mode": mode,
        "file_paths": list(file_paths),
        "timeout": timeout,
        "interval": interval,
        "enabled": enabled,
        "scope": scope,
        "detection_mode": detection_mode
    })
    active_indices = list(active.get("monitor_indices", selected))
    config = {
        # Save both geometries and indices for clarity and backward compatibility
        "monitors": list(active.get("monitor_geometries", [])),
        "monitor_devices": list(active.get("monitor_devices", [])),
        "monitor_indices": active_indices,
        "mode": active.get("mode", mode),
        "file_paths": list(active.get("file_paths", file_paths)),
        "timeout": active.get("timeout", timeout),
        "interval": active.get("interval", interval),
        "enabled": bool(active.get("enabled", enabled)),
        "scope": active.get("scope", scope) if active.get("scope", scope) in ("system", "per-monitor") else "system",
        "detection_mode": active.get("detection_mode", detection_mode) if active.get("detection_mode", detection_mode) in ("input", "activity", "both") else "input",
        "paused": bool(existing.get("paused", False)) if paused is None else bool(paused),
        "setting_groups": groups
    }
    _atomic_json_write(runtime_path("config.json"), config)
    logging.info(f"Config saved: {config}")

def load_config():
    path = runtime_path("config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not read config %s: %s", path, exc)
        return None


def set_config_paused(paused, reason=""):
    config = load_config() or {}
    config["paused"] = bool(paused)
    if reason:
        config["pause_reason"] = reason
    else:
        config.pop("pause_reason", None)
    _atomic_json_write(runtime_path("config.json"), config)

# --- Single Instance Enforcement ---
def is_background_running():
    lock_path = runtime_path("overlay_bg.lock")
    if not os.path.exists(lock_path):
        return False
    try:
        with open(lock_path, "r") as f:
            pid_str = f.read().strip()
            if not pid_str.isdigit():
                os.remove(lock_path)
                return False
            pid = int(pid_str)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        else:
            os.remove(lock_path)
            return False
    except (OSError, ValueError, AttributeError) as exc:
        logging.warning("Background lock check failed: %s", exc)
        try:
            os.remove(lock_path)
        except OSError as remove_exc:
            logging.debug("Could not remove background lock: %s", remove_exc)
        return False

def set_background_lock(state):
    lock_path = runtime_path("overlay_bg.lock")
    if state:
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))
    else:
        try:
            os.remove(lock_path)
        except OSError as exc:
            logging.debug("Could not remove background lock: %s", exc)

def _is_pid_alive(pid):
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except (OSError, AttributeError) as exc:
        logging.debug("Could not query pid %s: %s", pid, exc)
        return False

def _get_process_image_name(pid):
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line = (result.stdout or "").strip()
        if not line or line.startswith("INFO:"):
            return ""
        if line.startswith('"'):
            return line.split('","', 1)[0].strip('"').lower()
        return line.split(",", 1)[0].strip().lower()
    except (OSError, subprocess.SubprocessError) as exc:
        logging.debug("Could not resolve process name for pid %s: %s", pid, exc)
        return ""

def is_gui_running():
    lock_path = runtime_path("display_control_gui.lock")
    if not os.path.exists(lock_path):
        return False
    try:
        pid, locked_process_name = _load_lock_payload(lock_path)
        if pid is None:
            os.remove(lock_path)
            return False
        process_name = _get_process_image_name(pid)
        valid_names = _current_process_names()
        if locked_process_name:
            valid_names.add(locked_process_name)
        if _is_pid_alive(pid) and (not process_name or process_name in valid_names):
            return True
        os.remove(lock_path)
        return False
    except (OSError, ValueError, AttributeError) as exc:
        logging.warning("Dashboard lock check failed: %s", exc)
        try:
            os.remove(lock_path)
        except OSError as remove_exc:
            logging.debug("Could not remove dashboard lock: %s", remove_exc)
        return False

def set_gui_lock(state):
    lock_path = runtime_path("display_control_gui.lock")
    if state:
        payload = {
            "pid": os.getpid(),
            "process_name": os.path.basename(sys.executable).lower(),
        }
        with open(lock_path, "w") as f:
            json.dump(payload, f)
    else:
        try:
            os.remove(lock_path)
        except OSError as exc:
            logging.debug("Could not remove dashboard lock: %s", exc)

# --- Startup registration ---
def register_task_scheduler():
    """Kept for older callers. Startup itself is per-user and does not need elevation."""
    _start_startup_registration()

def unregister_task_scheduler():
    from ensure_overlay_bg_task import RUN_VALUE_NAME

    try:
        subprocess.run(
            ["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", "/v", RUN_VALUE_NAME, "/f"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            logging.info("Scheduled task delete skipped: %s", detail or result.returncode)
    except (OSError, subprocess.SubprocessError) as exc:
        logging.error("Startup unregister failed: %s", exc)

# --- Background Overlay Logic ---
def run_background_overlay():
    if is_background_running():
        logging.info('Background overlay already running. Exiting.')
        return
    set_background_lock(True)
    _write_usage_state(active=False, active_session_seconds=0)
    try:
        last_config_dump = ""
        last_config_sig = None
        session_overlay_seconds = 0
        last_block_reason = None
        while True:
            license_status = get_license_status()
            if not license_status.get("can_protect", False):
                _write_usage_state(
                    active=False,
                    active_session_seconds=session_overlay_seconds,
                    remaining_seconds=license_status.get("remaining_seconds", 0),
                    lifetime=bool(license_status.get("lifetime", False)),
                )
                reason = str(license_status.get("block_reason") or "no active trial or license")
                if reason != last_block_reason:
                    logging.warning("Protection is idle: %s", reason)
                    last_block_reason = reason
                time.sleep(60)
                continue
            last_block_reason = None

            config = load_config() or {}
            if config.get("pause_reason") == "license_exhausted":
                logging.info("Clearing stale license_exhausted pause from the retired credit gate.")
                set_config_paused(False, "")
                config["paused"] = False
            if config.get("paused", False):
                logging.info("Protection paused. Sleeping 5s.")
                time.sleep(5)
                continue
            raw_groups = config.get("setting_groups", [])
            groups = [g for g in raw_groups if isinstance(g, dict) and g.get("enabled", True)]
            if not groups:
                logging.info("No enabled setting groups found. Sleeping 30s.")
                time.sleep(30)
                continue

            config_dump = json.dumps(groups, sort_keys=True)
            last_config_sig = _config_file_signature()
            if config_dump != last_config_dump:
                logging.info(f"Background overlay loaded setting groups: {json.dumps(groups, indent=2)}")
                last_config_dump = config_dump

            from monitor_control import get_monitors
            all_monitors = get_monitors()
            if not all_monitors:
                logging.warning("No monitors detected. Sleeping 30s.")
                time.sleep(30)
                continue

            groups = [_hydrate_setting_group(all_monitors, group) for group in groups]
            groups = [group for group in groups if group.get("monitor_indices")]
            if not groups:
                logging.warning("No saved monitor mappings matched current monitors. Sleeping 30s.")
                time.sleep(30)
                continue

            # Use the most comprehensive detection mode required by any active group
            # so the detector tracks whichever signal(s) the groups actually need.
            _group_modes = {g.get("detection_mode", "input") for g in groups}
            _detector_mode = "both" if ("both" in _group_modes or "activity" in _group_modes) else "input"
            detector = MonitorActivityDetector(all_monitors, mode=_detector_mode, scope="per-monitor")
            detector.start()

            overlay_targets = {}
            try:
                while True:
                    idle_snapshot = detector.get_idle_snapshot()
                    overlay_targets = _collect_overlay_targets(groups, all_monitors, idle_snapshot)

                    if overlay_targets:
                        break

                    time.sleep(1)
                    new_sig = _config_file_signature()
                    if new_sig != last_config_sig:
                        new_config = load_config() or {}
                        new_groups = [g for g in new_config.get("setting_groups", []) if isinstance(g, dict) and g.get("enabled", True)]
                        new_dump = json.dumps(new_groups, sort_keys=True)
                        if new_dump != config_dump:
                            groups = [_hydrate_setting_group(all_monitors, group) for group in new_groups]
                            groups = [group for group in groups if group.get("monitor_indices")]
                            config_dump = new_dump
                            last_config_sig = new_sig
                            break
                        last_config_sig = new_sig
            finally:
                detector.stop()

            if not overlay_targets:
                continue

            overlay_procs = {}
            for idx, group in overlay_targets.items():
                geometry = all_monitors[idx]["geometry"]
                active_mode = group.get("mode", "blank")
                active_files = list(group.get("file_paths", []))
                active_interval = int(group.get("interval", 30))

                if active_mode == "single" and active_files:
                    proc = multiprocessing.Process(target=show_image_overlay, args=(geometry, active_files[0], False))
                elif active_mode == "slideshow" and active_files:
                    proc = multiprocessing.Process(target=show_slideshow_overlay, args=(geometry, active_files, active_interval, False))
                elif active_mode == "video" and active_files:
                    proc = multiprocessing.Process(target=show_video_overlay, args=(geometry, active_files, False))
                elif active_mode == "blank":
                    proc = multiprocessing.Process(target=show_black_overlay, args=(geometry, False))
                else:
                    logging.warning(f"Skipping invalid group mode/files for monitor {idx + 1}: {active_mode}")
                    continue
                overlay_procs[idx] = proc

            for proc in overlay_procs.values():
                proc.start()

            logging.info("Overlay(s) started. Waiting for activity to close overlays.")
            last_meter_tick = time.time()
            meter_accumulator = 0.0
            close_detector = MonitorActivityDetector(all_monitors, mode="input", scope="per-monitor")
            close_detector.start()
            previous_input_idle = None
            try:
                while True:
                    overlay_procs = {idx: proc for idx, proc in overlay_procs.items() if proc.is_alive()}
                    if not overlay_procs:
                        break

                    now = time.time()
                    delta = max(0.0, now - last_meter_tick)
                    last_meter_tick = now
                    meter_accumulator += delta
                    consumed_whole_seconds = int(meter_accumulator)
                    if consumed_whole_seconds > 0:
                        status_after = consume_active_seconds(consumed_whole_seconds)
                        meter_accumulator -= consumed_whole_seconds
                        session_overlay_seconds += consumed_whole_seconds
                        _write_usage_state(
                            active=True,
                            active_session_seconds=session_overlay_seconds,
                            remaining_seconds=status_after.get("remaining_seconds", 0),
                            lifetime=bool(status_after.get("lifetime", False)),
                        )
                        if not status_after.get("can_protect", False):
                            logging.warning(
                                "Protection entitlement ended while an overlay was active: %s",
                                status_after.get("block_reason") or "no active trial or license",
                            )
                            break
                    else:
                        current_status = get_license_status()
                        _write_usage_state(
                            active=True,
                            active_session_seconds=session_overlay_seconds,
                            remaining_seconds=current_status.get("remaining_seconds", 0),
                            lifetime=bool(current_status.get("lifetime", False)),
                        )

                    close_idle_snapshot = close_detector.get_idle_snapshot()
                    wake_targets = _collect_wake_targets(groups, all_monitors, close_idle_snapshot, previous_input_idle)
                    previous_input_idle = dict(close_idle_snapshot.get("input", {}))
                    for idx, proc in list(overlay_procs.items()):
                        if idx in wake_targets:
                            logging.info(f"Input detected for monitor {idx + 1}. Closing overlay.")
                            proc.terminate()
                            overlay_procs.pop(idx, None)

                    if not overlay_procs:
                        break

                    time.sleep(0.5)
            finally:
                close_detector.stop()

            _write_usage_state(active=False, active_session_seconds=session_overlay_seconds)
            for proc in overlay_procs.values():
                if proc.is_alive():
                    proc.terminate()
    finally:
        _write_usage_state(active=False)
        set_background_lock(False)

# --- GUI Setup ---
def show_monitor_indicator(geometry, monitor_num):
    # Show a small monitor badge on the selected physical display for quick identification.
    left, top, right, bottom = geometry
    indicator_size = 72
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{indicator_size}x{indicator_size}+{left}+{top}")
    root.configure(bg="#0f1115")
    root.attributes("-topmost", True)
    canvas_ind = tk.Canvas(root, width=indicator_size, height=indicator_size, bg="#0f1115", highlightthickness=0)
    canvas_ind.pack()
    canvas_ind.create_oval(8, 8, indicator_size - 8, indicator_size - 8, fill="#30c18d", outline="#30c18d")
    canvas_ind.create_text(
        indicator_size // 2,
        indicator_size // 2,
        text=str(monitor_num + 1),
        fill="#08110e",
        font=("Segoe UI", 22, "bold")
    )
    root.after(1200, root.destroy)
    root.mainloop()

def launch_gui():
    if is_gui_running():
        ctypes.windll.user32.MessageBoxW(0, "Display Control+ is already open.", "Display Control+", 0x40)
        return
    set_gui_lock(True)
    _start_startup_registration()

    try:
        from updater import CURRENT_VERSION as APP_VERSION
    except (ImportError, AttributeError) as exc:
        logging.warning("Could not read app version: %s", exc)
        APP_VERSION = "unknown"

    from monitor_control import get_monitors

    # Set an explicit AppUserModelID so Windows groups the app consistently on the taskbar.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KnightLogics.DisplayControlPlus")
    except (AttributeError, OSError) as exc:
        logging.debug("Could not set AppUserModelID: %s", exc)

    win = tk.Tk()
    win.title("Display Control+")
    screen_w = max(1024, win.winfo_screenwidth())
    screen_h = max(768, win.winfo_screenheight())
    target_w = min(1120, screen_w - 120)
    target_h = min(860, screen_h - 120)
    win.geometry(f"{target_w}x{target_h}")
    win.minsize(980, 720)
    win.configure(bg="#0f1115")

    logo_path = _first_existing_path("Display Control+ Logo.png")
    logo_ico_path = _first_existing_path("Display Control+ Logo.ico")
    brand_logo_path = _first_existing_path("KnightLogicsLogo.png")
    header_logo = None
    brand_logo = None
    window_icon = None
    _tmp_ico_path = None

    try:
        if os.path.exists(logo_ico_path):
            # Use packaged ICO for native Windows taskbar/process icons.
            win.iconbitmap(default=logo_ico_path)
            with Image.open(logo_ico_path) as ico_logo:
                icon_img = ico_logo.copy().convert("RGBA")
                icon_img.thumbnail((128, 128), Image.Resampling.LANCZOS)
                window_icon = ImageTk.PhotoImage(icon_img)
                win.iconphoto(True, window_icon)
        elif os.path.exists(logo_path):
            with Image.open(logo_path) as base_logo:
                icon_img = base_logo.copy().convert("RGBA")
                icon_img.thumbnail((128, 128), Image.Resampling.LANCZOS)
                window_icon = ImageTk.PhotoImage(icon_img)
                win.iconphoto(True, window_icon)
    except Exception as e:
        logging.warning(f"Could not load app icon: {e}")

    if os.path.exists(logo_path):
        try:
            with Image.open(logo_path) as base_logo:
                header_img = base_logo.copy().convert("RGBA")
                header_img.thumbnail((40, 40), Image.Resampling.LANCZOS)
                header_logo = ImageTk.PhotoImage(header_img)
        except Exception as e:
            logging.warning(f"Could not load header logo: {e}")

    if os.path.exists(brand_logo_path):
        try:
            with Image.open(brand_logo_path) as base_brand_logo:
                brand_img = base_brand_logo.copy().convert("RGBA")
                brand_img.thumbnail((18, 18), Image.Resampling.LANCZOS)
                brand_logo = ImageTk.PhotoImage(brand_img)
        except Exception as e:
            logging.warning(f"Could not load Knight Logics logo: {e}")

    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure(".", background="#0f1115", foreground="#e7edf4", fieldbackground="#161a22")
    style.configure("Card.TLabelframe", background="#161a22", bordercolor="#252b37", borderwidth=1, relief="solid")
    style.configure("Card.TLabelframe.Label", background="#161a22", foreground="#9fe1c8", font=("Segoe UI", 11, "bold"))
    style.configure("Header.TLabel", background="#0f1115", foreground="#f2f7ff", font=("Segoe UI", 22, "bold"))
    style.configure("Subhead.TLabel", background="#0f1115", foreground="#9aa7b7", font=("Segoe UI", 10))
    style.configure("Body.TLabel", background="#161a22", foreground="#d4dde9", font=("Segoe UI", 10))
    style.configure("TCheckbutton", background="#161a22", foreground="#d4dde9", font=("Segoe UI", 10))
    style.configure("TRadiobutton", background="#161a22", foreground="#d4dde9", font=("Segoe UI", 10))
    style.configure("TButton", padding=(12, 7), font=("Segoe UI", 10, "bold"),
        background="#252b37", foreground="#d4dde9")
    style.map("TButton",
        background=[("active", "#2d3444"), ("pressed", "#202634")],
        foreground=[("active", "#f2f7ff"), ("pressed", "#f2f7ff")])
    style.configure("Accent.TButton", background="#30c18d", foreground="#0a1310")
    style.map("Accent.TButton",
        background=[("active", "#3ed9a0"), ("pressed", "#2ab783")],
        foreground=[("active", "#08110e"), ("pressed", "#08110e")])
    style.configure("TCombobox", fieldbackground="#252b37", foreground="#d4dde9",
        background="#252b37", selectbackground="#252b37", selectforeground="#d4dde9",
        arrowcolor="#9fe1c8")
    style.map("TCombobox",
        fieldbackground=[("readonly", "#252b37"), ("!readonly", "#252b37")],
        background=[("active", "#2d3444"), ("!active", "#252b37")],
        foreground=[("readonly", "#d4dde9"), ("active", "#d4dde9"), ("!active", "#d4dde9")],
        selectbackground=[("readonly", "#252b37")],
        selectforeground=[("readonly", "#d4dde9")])

    style.map("TCheckbutton",
        background=[("active", "#161a22"), ("pressed", "#161a22")],
        foreground=[("active", "#f2f7ff"), ("pressed", "#f2f7ff")])
    style.configure("Dark.TRadiobutton", background="#161a22", foreground="#d4dde9", font=("Segoe UI", 10))
    style.map("Dark.TRadiobutton",
        background=[("active", "#161a22"), ("!active", "#161a22")],
        foreground=[("active", "#f2f7ff"), ("selected", "#e7edf4"), ("!selected", "#d4dde9")],
        indicatorcolor=[("selected", "#30c18d"), ("!selected", "#161a22")])
    win.option_add("*TCombobox*Listbox.background", "#252b37")
    win.option_add("*TCombobox*Listbox.foreground", "#d4dde9")
    win.option_add("*TCombobox*Listbox.selectBackground", "#30c18d")
    win.option_add("*TCombobox*Listbox.selectForeground", "#0a1310")

    root_frame = ttk.Frame(win, padding=(16, 12, 16, 10))
    root_frame.pack(fill=tk.BOTH, expand=True)

    license_frame = tk.Frame(root_frame, bg="#111a24", bd=1, relief=tk.SOLID, highlightthickness=0)
    license_frame.pack(fill=tk.X, pady=(0, 10))

    license_title = tk.Label(
        license_frame,
        text="Licensing",
        bg="#111a24",
        fg="#9fe1c8",
        font=("Segoe UI", 10, "bold")
    )
    license_title.pack(side=tk.LEFT, padx=(10, 8), pady=8)

    license_status_label = tk.Label(
        license_frame,
        text="",
        bg="#111a24",
        fg="#d4dde9",
        font=("Segoe UI", 10)
    )
    license_status_label.pack(side=tk.LEFT, padx=(0, 14), pady=8)

    recovery_status_label = tk.Label(
        license_frame,
        text="",
        bg="#111a24",
        fg="#7f91a7",
        font=("Segoe UI", 9)
    )
    recovery_status_label.pack(side=tk.LEFT, padx=(0, 14), pady=8)

    usage_live_label = tk.Label(
        license_frame,
        text="",
        bg="#111a24",
        fg="#9aa7b7",
        font=("Segoe UI", 9)
    )
    usage_live_label.pack(side=tk.LEFT, padx=(0, 10), pady=8)

    recovery_frame = tk.Frame(root_frame, bg="#111a24", bd=1, relief=tk.SOLID, highlightthickness=0)
    recovery_frame.pack(fill=tk.X, pady=(0, 10))

    recovery_email_label = tk.Label(
        recovery_frame,
        text="Account Email: Not set",
        bg="#111a24",
        fg="#9aa7b7",
        font=("Segoe UI", 9)
    )
    recovery_email_label.pack(side=tk.LEFT, padx=(10, 8), pady=8)

    recovery_code_var = tk.StringVar(value="No recovery code yet")
    recovery_code_entry = tk.Entry(
        recovery_frame,
        textvariable=recovery_code_var,
        state="readonly",
        readonlybackground="#161a22",
        fg="#d4dde9",
        bd=0,
        relief=tk.FLAT,
        font=("Consolas", 9),
        width=54
    )
    recovery_code_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=8)

    def copy_recovery_code():
        full_code = getattr(copy_recovery_code, "full_code", "")
        if not full_code:
            messagebox.showinfo("Display Control+", "No recovery code is available yet.")
            return
        win.clipboard_clear()
        win.clipboard_append(full_code)
        win.update()
        messagebox.showinfo("Display Control+", "Recovery code copied to clipboard.")

    copy_recovery_button = tk.Button(
        recovery_frame,
        text="Copy",
        bg="#1f2f3f",
        fg="#d4dde9",
        activebackground="#27384b",
        activeforeground="#f2f7ff",
        relief=tk.FLAT,
        cursor="hand2",
        padx=10,
        pady=4,
        command=copy_recovery_code
    )
    manage_billing_button = tk.Button(
        recovery_frame,
        text="Manage Billing",
        bg="#1f2f3f",
        fg="#d4dde9",
        activebackground="#27384b",
        activeforeground="#f2f7ff",
        relief=tk.FLAT,
        cursor="arrow",
        padx=10,
        pady=4,
        state=tk.DISABLED,
        command=lambda: None
    )
    copy_recovery_button.pack(side=tk.RIGHT, padx=(0, 10), pady=6)
    manage_billing_button.pack(side=tk.RIGHT, padx=(0, 8), pady=6)

    buy_annual_button = tk.Button(
        license_frame,
        text=plan_button_label("annual"),
        bg="#1f2f3f",
        fg="#d4dde9",
        activebackground="#27384b",
        activeforeground="#f2f7ff",
        relief=tk.FLAT,
        cursor="hand2",
        padx=10,
        pady=4
    )

    buy_lifetime_button = tk.Button(
        license_frame,
        text=plan_button_label("lifetime"),
        bg="#2d8f68",
        fg="#08110e",
        activebackground="#37a877",
        activeforeground="#08110e",
        relief=tk.FLAT,
        cursor="hand2",
        padx=10,
        pady=4
    )

    def refresh_license_label():
        def _truncate_code(text, head=10, tail=10):
            text = str(text or "").strip()
            if len(text) <= head + tail + 3:
                return text
            return f"{text[:head]}...{text[-tail:]}"

        def _fmt_hms(total_seconds):
            try:
                total_seconds = max(0, int(total_seconds))
            except (TypeError, ValueError):
                total_seconds = 0
            h, rem = divmod(total_seconds, 3600)
            m, s = divmod(rem, 60)
            return f"{h:02d}:{m:02d}:{s:02d}"

        status = get_license_status()
        billing_kind = status.get("billing_kind", "none")

        if status.get("lifetime", False):
            license_status_label.config(text="Lifetime license active", fg="#73d9b5")
            buy_annual_button.config(state=tk.DISABLED, cursor="arrow")
            buy_lifetime_button.config(state=tk.DISABLED, cursor="arrow")
        elif status.get("subscription_active", False):
            renewal_text = status.get("subscription_current_period_end_display", "")
            if status.get("subscription_cancel_at_period_end", False) and renewal_text:
                descriptor = f"Annual subscription active | Ends {renewal_text}"
            elif renewal_text:
                descriptor = f"Annual subscription active | Renews {renewal_text}"
            else:
                descriptor = "Annual subscription active"
            license_status_label.config(text=descriptor, fg="#73d9b5")
            buy_annual_button.config(state=tk.DISABLED, cursor="arrow")
            buy_lifetime_button.config(state=tk.DISABLED, cursor="arrow")
        elif status.get("trial_active", False):
            days_left = int(status.get("trial_days_remaining", 0) or 0)
            license_status_label.config(text=f"Free trial active | {days_left} day(s) remaining", fg="#d4dde9")
            buy_annual_button.config(state=tk.NORMAL, cursor="hand2")
            buy_lifetime_button.config(state=tk.NORMAL, cursor="hand2")
        elif billing_kind == "legacy_credits":
            license_status_label.config(text=f"Legacy credits remaining: {status.get('legacy_remaining_display', '00:00:00')}", fg="#d4dde9")
            buy_annual_button.config(state=tk.NORMAL, cursor="hand2")
            buy_lifetime_button.config(state=tk.NORMAL, cursor="hand2")
        else:
            license_status_label.config(text="No active plan. Start annual or lifetime to enable protection.", fg="#ff8b94")
            buy_annual_button.config(state=tk.NORMAL, cursor="hand2")
            buy_lifetime_button.config(state=tk.NORMAL, cursor="hand2")

        recovery_code = status.get("recovery_code", "")
        customer_email = status.get("customer_email", "")
        if customer_email:
            recovery_email_label.config(text=f"Account Email: {customer_email}")
        else:
            recovery_email_label.config(text="Account Email: Not set")

        if recovery_code:
            if status.get("subscription_active", False):
                recovery_status_label.config(text=f"Billing status: {status.get('subscription_status', 'active')}", fg="#7f91a7")
            elif status.get("trial_active", False):
                recovery_status_label.config(text="Recovery code ready for cross-device restore", fg="#7f91a7")
            elif billing_kind == "legacy_credits":
                recovery_status_label.config(text="Legacy entitlement detected", fg="#7f91a7")
            else:
                recovery_status_label.config(text="Recovery code ready", fg="#7f91a7")
            recovery_code_var.set(_truncate_code(recovery_code))
            copy_recovery_code.full_code = recovery_code
            copy_recovery_button.config(state=tk.NORMAL, cursor="hand2")
        else:
            recovery_status_label.config(text="", fg="#7f91a7")
            recovery_code_var.set("No recovery code yet")
            copy_recovery_code.full_code = ""
            copy_recovery_button.config(state=tk.DISABLED, cursor="arrow")

        if is_payment_available() and customer_email and recovery_code:
            manage_billing_button.config(state=tk.NORMAL, cursor="hand2")
        else:
            manage_billing_button.config(state=tk.DISABLED, cursor="arrow")

        usage_state = _read_usage_state()
        usage_text = _fmt_hms(usage_state.get("active_session_seconds", 0))
        if usage_state.get("active", False):
            usage_live_label.config(text=f"Overlay active | Session usage: {usage_text}", fg="#73d9b5")
        else:
            usage_live_label.config(text=f"Overlay idle | Session usage: {usage_text}", fg="#9aa7b7")

    def _resume_protection_after_purchase():
        set_config_paused(False, "")

    def _prompt_purchase_email():
        """Prompt for email with validation."""
        while True:
            email = simpledialog.askstring(
                "Display Control+",
                "Enter your billing email.\n\nThis email is used for receipts, restores, subscription sync, and billing management.",
                parent=win,
            )
            if email is None:
                return None
            email = email.strip()
            if email and "@" in email and "." in email.split("@")[-1]:
                return email
            messagebox.showwarning("Display Control+", "Enter a valid email address to continue.")

    def _start_checkout_webview(checkout_url, on_success, on_error=None):
        """Launch checkout_window.py as a subprocess.

        Opens the Stripe checkout URL inside a native EdgeChromium window
        (no OS browser).  Calls on_success() once the success redirect is
        detected, or on_error() if the window is closed without payment.
        Both callbacks are scheduled on the tkinter main thread via after().
        """
        import subprocess as _sp

        helper_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkout_window.py")
        helper_exe = _first_existing_path("checkout_window.exe") if getattr(sys, "frozen", False) else ""

        def _run():
            result_path = None
            try:
                fd, result_path = tempfile.mkstemp(prefix="dcp_checkout_", suffix=".json")
                os.close(fd)

                if helper_exe and os.path.exists(helper_exe):
                    cmd = [helper_exe, checkout_url, result_path]
                else:
                    cmd = [sys.executable, helper_script, checkout_url, result_path]

                proc = _sp.run(cmd, capture_output=True, text=True, timeout=660)
                data = None
                if result_path and os.path.exists(result_path):
                    try:
                        with open(result_path, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                    except Exception as read_exc:
                        log_debug(f"Checkout helper result file read error: {read_exc}")

                if data is None:
                    lines = [l.strip() for l in proc.stdout.strip().splitlines() if l.strip()]
                    data = json.loads(lines[-1]) if lines else {"ok": False, "reason": "no_output"}

                log_debug(f"Checkout window result: {data}")
                if data.get("ok"):
                    win.after(0, on_success)
                elif on_error:
                    win.after(0, on_error)
            except Exception as exc:
                log_debug(f"Checkout webview subprocess error: {exc}")
                if on_error:
                    win.after(0, on_error)
            finally:
                if result_path and os.path.exists(result_path):
                    try:
                        os.remove(result_path)
                    except OSError as remove_exc:
                        logging.debug("Could not remove checkout result file: %s", remove_exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    def _sync_current_account_state(show_feedback=False):
        current_status = get_license_status()
        email = str(current_status.get("customer_email", "")).strip()
        recovery_code = str(current_status.get("recovery_code", "")).strip()
        if not is_payment_available():
            if show_feedback:
                messagebox.showwarning("Display Control+", "Hosted billing is not configured for this build.")
            return False
        if not email or not recovery_code:
            if show_feedback:
                messagebox.showwarning("Display Control+", "A billing email and recovery code are required before account sync is available.")
            return False

        log_debug(f"Syncing account state for {email}")
        result = sync_account(email, recovery_code)
        if not result.get("ok", False):
            error = result.get("error", "unknown error")
            log_debug(f"Account sync failed: {error}")
            try:
                apply_remote_sync_result(result, recovery_code)
            except OSError as exc:
                logging.warning("Could not record license server failure: %s", exc)
            if show_feedback and result.get("fail_open"):
                messagebox.showwarning(
                    "Display Control+",
                    "The license server is temporarily unavailable. Protection already on this PC stays as it is.",
                )
            elif show_feedback:
                messagebox.showerror("Display Control+", result.get("error", "Could not sync account state."))
            refresh_license_label()
            return False

        apply_remote_sync_result(result, recovery_code)
        refresh_license_label()
        if show_feedback:
            messagebox.showinfo("Display Control+", "Account state refreshed from the billing server.")
        return True

    def _schedule_initial_account_sync():
        current_status = get_license_status()
        email = str(current_status.get("customer_email", "")).strip()
        recovery_code = str(current_status.get("recovery_code", "")).strip()
        if not (is_payment_available() and email and recovery_code):
            return

        def _worker():
            result = sync_account(email, recovery_code)
            if not result.get("ok", False):
                error = result.get("error", "unknown error")
                log_debug(f"Initial account sync skipped: {error}")
                try:
                    apply_remote_sync_result(result, recovery_code)
                except OSError as exc:
                    logging.warning("Could not record license server failure: %s", exc)
                return
            def _apply_sync():
                apply_remote_sync_result(result, recovery_code)
                refresh_license_label()
            win.after(0, _apply_sync)

        threading.Thread(target=_worker, daemon=True).start()

    def _process_checkout(plan_key):
        """Process a live hosted checkout for annual or lifetime licensing."""
        plan = get_plan(plan_key) or {}
        log_debug(f"Starting payment flow for plan={plan_key}")

        current_status = get_license_status()
        existing_email = str(current_status.get("customer_email", "")).strip()
        preauth_email = existing_email if existing_email else _prompt_purchase_email()
        if preauth_email is None:
            return False

        session_result = create_checkout_session(plan_key, preauth_email)
        if not session_result.get("ok", False):
            error_msg = session_result.get("error", "Unknown checkout creation error")
            log_debug(f"ERROR creating session: {error_msg}")
            messagebox.showerror(
                "Payment Error",
                f"Could not create checkout session: {error_msg}"
            )
            return False

        session_id = session_result.get("session_id")
        checkout_url = session_result.get("url")
        log_debug(f"Session created: {session_id}")
        log_debug(f"Checkout URL: {checkout_url}")

        def _finalize_successful_payment(session_conf):
            account_payload = session_conf.get("account", {}) if isinstance(session_conf, dict) else {}
            if not account_payload:
                messagebox.showerror("License Update Error", "Payment completed, but no account entitlement payload was returned.")
                return

            status = sync_remote_account(account_payload, account_payload.get("recovery_code", ""))
            _resume_protection_after_purchase()
            refresh_license_label()

            if status.get("lifetime", False):
                plan_line = "Lifetime access enabled."
            elif status.get("subscription_active", False):
                renewal_text = status.get("subscription_current_period_end_display", "")
                if renewal_text:
                    if status.get("subscription_cancel_at_period_end", False):
                        plan_line = f"Annual subscription active. Access continues through {renewal_text}."
                    else:
                        plan_line = f"Annual subscription active. Next renewal: {renewal_text}."
                else:
                    plan_line = "Annual subscription active."
            else:
                plan_line = f"Entitlement synced: {plan.get('short_label', plan_key.title())}."

            if session_conf.get("email_sent", False):
                email_line = "Recovery email sent successfully."
            else:
                email_error = session_conf.get("email_error", "billing server did not confirm email delivery")
                email_line = f"Recovery email not confirmed: {email_error}"

            messagebox.showinfo(
                "Display Control+",
                f"Payment successful!\n\n"
                f"{plan_line}\n\n"
                f"Email: {status.get('customer_email', '')}\n"
                f"Recovery Code: {status.get('recovery_code', '')}\n\n"
                f"{email_line}\n\n"
                f"Use Restore Account on another machine to sync your entitlements, or Manage Billing to update your subscription."
            )

        def _confirm_and_finalize(retries_left=4):
            try:
                session_confirmation = confirm_session(session_id)
                if session_confirmation.get("ok", False):
                    _finalize_successful_payment(session_confirmation)
                    return

                msg = session_confirmation.get("error", "Payment not yet confirmed.")
                log_debug(f"Checkout success callback not paid yet: {msg}")
            except Exception as e:
                msg = str(e)
                log_debug(f"Checkout success callback confirm exception: {msg}")

            if retries_left > 0:
                win.after(1500, lambda: _confirm_and_finalize(retries_left - 1))
            else:
                messagebox.showinfo(
                    "Payment Pending",
                    "Checkout finished, but confirmation is still pending.\n\n"
                    "If you were charged, your time should appear shortly after confirmation."
                )

        # Launch the inline checkout window (no browser needed).
        # Success notification appears only after Stripe confirms payment.
        _start_checkout_webview(
            checkout_url,
            on_success=_confirm_and_finalize,
            on_error=lambda: log_debug("Checkout window closed without payment"),
        )
        return True

    def _confirm_demo_purchase(label, action):
        ok = messagebox.askyesno(
            "Display Control+",
            f"{label}\n\nDemo mode: local purchase simulation (no live payment).\nApply this purchase now?"
        )
        if not ok:
            return
        email = _prompt_purchase_email()
        if email is None:
            return
        status = action(email)
        _resume_protection_after_purchase()
        refresh_license_label()
        messagebox.showinfo(
            "Display Control+",
            f"Demo entitlement applied to this device.\n\nEmail: {status.get('customer_email', email)}\nRecovery code: {status.get('recovery_code', '')}"
        )

    def open_test_purchase_window():
        test_win = tk.Toplevel(win)
        test_win.title("Display Control+ Test Purchases")
        test_win.geometry("420x230")
        test_win.configure(bg="#0f1115")
        test_win.resizable(False, False)

        tk.Label(
            test_win,
            text="Test Purchase Tools",
            bg="#0f1115",
            fg="#73d9b5",
            font=("Segoe UI", 12, "bold")
        ).pack(pady=(16, 8))

        tk.Label(
            test_win,
            text="Local-only simulation. No live charge will be created.\nUse this to test annual/lifetime entitlement behavior and restore flows.",
            bg="#0f1115",
            fg="#d4dde9",
            font=("Segoe UI", 9),
            justify="center",
            wraplength=360
        ).pack(pady=(0, 14), padx=20)

        tk.Button(
            test_win,
            text="Simulate Annual",
            command=lambda: _confirm_demo_purchase(
                "Start annual subscription demo",
                lambda email: activate_subscription(email, plan_key="annual", current_period_end=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 31536000))),
            ),
            bg="#1f2f3f",
            fg="#d4dde9",
            activebackground="#27384b",
            activeforeground="#f2f7ff",
            relief=tk.FLAT,
            cursor="hand2",
            padx=12,
            pady=6
        ).pack(pady=4)

        tk.Button(
            test_win,
            text="Simulate Lifetime",
            command=lambda: _confirm_demo_purchase("Grant lifetime demo entitlement", lambda email: activate_lifetime(email)),
            bg="#1f2f3f",
            fg="#d4dde9",
            activebackground="#27384b",
            activeforeground="#f2f7ff",
            relief=tk.FLAT,
            cursor="hand2",
            padx=12,
            pady=6
        ).pack(pady=4)

        tk.Button(
            test_win,
            text="Add Legacy Credits",
            command=lambda: _confirm_demo_purchase("Add legacy credit balance", lambda email: add_hours(1200, email)),
            bg="#2d8f68",
            fg="#08110e",
            activebackground="#37a877",
            activeforeground="#08110e",
            relief=tk.FLAT,
            cursor="hand2",
            padx=12,
            pady=6
        ).pack(pady=4)

    def _extract_recovery_code(raw_text):
        text = str(raw_text or "")
        text = text.replace("\ufeff", "").replace("\u200b", "")
        compact = "".join(text.split())
        if not compact:
            return ""

        match = re.search(r"DCPA-[A-F0-9]{16}", compact, flags=re.IGNORECASE)
        if match:
            return match.group(0).upper()

        match = re.search(r"DCP2\.[A-Za-z0-9_-]+\.[A-Fa-f0-9]{64}", compact, flags=re.IGNORECASE)
        if match:
            return match.group(0)
        if compact.upper().startswith("DCP2.") and compact.count(".") >= 2:
            return compact
        return ""

    def _prompt_recovery_code_dialog():
        dlg = tk.Toplevel(win)
        dlg.title("Restore Account")
        dlg.configure(bg="#0f1115")
        dlg.resizable(False, False)
        dlg.transient(win)
        dlg.grab_set()

        tk.Label(
            dlg,
            text="Paste your recovery code or full email text:",
            bg="#0f1115",
            fg="#d4dde9",
            font=("Segoe UI", 10),
            justify="left",
            anchor="w"
        ).pack(fill=tk.X, padx=14, pady=(12, 8))

        txt = tk.Text(
            dlg,
            width=72,
            height=6,
            bg="#111a24",
            fg="#d4dde9",
            insertbackground="#d4dde9",
            relief=tk.FLAT,
            padx=8,
            pady=6,
            wrap="word",
            font=("Consolas", 9)
        )
        txt.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        txt.focus_set()

        result = {"code": None}

        def _submit():
            raw = txt.get("1.0", "end-1c")
            code = _extract_recovery_code(raw)
            if not code:
                messagebox.showwarning(
                    "Display Control+",
                    "Could not find a valid recovery code.\n\n"
                    "Tip: paste the recovery code directly or the full email text that contains it."
                )
                return
            result["code"] = code
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        btns = tk.Frame(dlg, bg="#0f1115")
        btns.pack(fill=tk.X, padx=14, pady=(0, 12))

        tk.Button(
            btns,
            text="Cancel",
            command=_cancel,
            bg="#3a1519",
            fg="#ff9098",
            relief=tk.FLAT,
            cursor="hand2",
            padx=10,
            pady=5
        ).pack(side=tk.RIGHT)

        tk.Button(
            btns,
            text="Restore",
            command=_submit,
            bg="#2d8f68",
            fg="#08110e",
            activebackground="#37a877",
            activeforeground="#08110e",
            relief=tk.FLAT,
            cursor="hand2",
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold")
        ).pack(side=tk.RIGHT, padx=(0, 8))

        win.wait_window(dlg)
        return result["code"]

    def restore_account_from_code():
        recovery_code = _prompt_recovery_code_dialog()
        if not recovery_code:
            return

        remembered_email = str(get_license_status().get("customer_email", "")).strip()
        email = simpledialog.askstring(
            "Display Control+",
            "Enter your purchase email:",
            parent=win,
            initialvalue=remembered_email,
        )
        email = (email or "").strip()
        if not email:
            messagebox.showwarning("Display Control+", "Your purchase email is required to restore this account.")
            return

        result = sync_account(email, recovery_code)
        if result.get("fail_open"):
            apply_remote_sync_result(result, recovery_code)
            refresh_license_label()
            messagebox.showwarning(
                "Display Control+",
                "The license server is temporarily unavailable. Protection already on this PC stays as it is.",
            )
            return
        if result.get("ok", False):
            apply_remote_sync_result(result, recovery_code)
        else:
            apply_remote_sync_result(result, recovery_code)
            result = restore_from_recovery_code(recovery_code, email)
        if not result.get("ok", False):
            messagebox.showerror("Display Control+", result.get("error", "Could not restore account."))
            return

        _resume_protection_after_purchase()
        refresh_license_label()
        if result.get("already_restored", False):
            messagebox.showinfo("Display Control+", "This code was already redeemed on this installation.")
        else:
            messagebox.showinfo("Display Control+", "Account restored successfully.")

    def manage_billing():
        if not is_payment_available():
            messagebox.showwarning("Display Control+", "Hosted billing is not configured for this build.")
            return

        current_status = get_license_status()
        email = str(current_status.get("customer_email", "")).strip()
        recovery_code = str(current_status.get("recovery_code", "")).strip()
        if not email:
            email = simpledialog.askstring("Display Control+", "Enter your billing email:", parent=win) or ""
            email = email.strip()
        if not recovery_code:
            recovery_code = _prompt_recovery_code_dialog() or ""
        if not email or not recovery_code:
            return

        result = create_portal_session(email, recovery_code)
        if not result.get("ok", False):
            messagebox.showerror("Display Control+", result.get("error", "Could not open billing portal."))
            return

        if not webbrowser.open(result.get("url", "")):
            messagebox.showerror("Display Control+", "Could not open the billing portal in your browser.")

    manage_billing_button.config(command=manage_billing)

    if is_payment_available():
        buy_annual_button.config(command=lambda: _process_checkout("annual"))
        buy_lifetime_button.config(command=lambda: _process_checkout("lifetime"))
        log_debug("Hosted billing available - using license server")
    else:
        log_debug("Payment NOT available - using demo mode")
        buy_annual_button.config(
            command=lambda: _confirm_demo_purchase(
                "Start annual subscription demo",
                lambda email: activate_subscription(email, plan_key="annual", current_period_end=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 31536000))),
            )
        )
        buy_lifetime_button.config(command=lambda: _confirm_demo_purchase("Grant lifetime demo entitlement", lambda email: activate_lifetime(email)))

    buy_lifetime_button.pack(side=tk.RIGHT, padx=(0, 8), pady=6)
    buy_annual_button.pack(side=tk.RIGHT, padx=(0, 8), pady=6)

    refresh_license_label()
    _schedule_initial_account_sync()

    header_row = ttk.Frame(root_frame)
    header_row.pack(fill=tk.X, anchor="w")

    if header_logo is not None:
        logo_label = tk.Label(header_row, image=header_logo, bg="#0f1115", bd=0, highlightthickness=0)
        logo_label.image = header_logo
        logo_label.pack(side=tk.LEFT, padx=(0, 10), pady=(0, 2))

    header_text_col = ttk.Frame(header_row)
    header_text_col.pack(side=tk.LEFT, anchor="w")

    ttk.Label(header_text_col, text="Display Control+", style="Header.TLabel").pack(anchor="w")
    ttk.Label(
        header_text_col,
        text="Build and save multiple protection setups for your displays, then switch between them instantly.",
        style="Subhead.TLabel"
    ).pack(anchor="w", pady=(2, 8))

    brand_block = tk.Frame(header_row, bg="#0f1115")
    brand_block.pack(side=tk.RIGHT, anchor="ne", pady=(6, 0))

    brand_top = tk.Frame(brand_block, bg="#0f1115")
    brand_top.pack(side=tk.TOP, anchor="e")

    tk.Label(
        brand_top,
        text="Built by",
        bg="#0f1115",
        fg="#9aa7b7",
        font=("Segoe UI", 9)
    ).pack(side=tk.LEFT, padx=(0, 6))

    if brand_logo is not None:
        brand_logo_label = tk.Label(brand_top, image=brand_logo, bg="#0f1115", bd=0, highlightthickness=0)
        brand_logo_label.image = brand_logo
        brand_logo_label.pack(side=tk.LEFT, padx=(0, 6))

    tk.Label(
        brand_top,
        text="Knight Logics |",
        bg="#0f1115",
        fg="#c4d0de",
        font=("Segoe UI", 9, "bold")
    ).pack(side=tk.LEFT, padx=(0, 4))

    brand_top_link = tk.Label(
        brand_top,
        text="KnightLogics.com",
        bg="#0f1115",
        fg="#73d9b5",
        cursor="hand2",
        font=("Segoe UI", 9, "underline")
    )
    brand_top_link.pack(side=tk.LEFT)
    brand_top_link.bind("<Button-1>", lambda _e: webbrowser.open("https://knightlogics.com"))

    tk.Label(
        brand_block,
        text=f"Version v{APP_VERSION}",
        bg="#0f1115",
        fg="#6f7f92",
        font=("Segoe UI", 8)
    ).pack(side=tk.TOP, anchor="e", pady=(2, 0))

    # Keep a small gap between the header and main content without pushing actions off-screen.
    content_spacer = tk.Frame(root_frame, height=4, bg="#0f1115")
    content_spacer.pack(fill=tk.X)

    content = ttk.Frame(root_frame)
    content.pack(fill=tk.BOTH, expand=True)
    content.columnconfigure(0, weight=2)
    content.columnconfigure(1, weight=3)
    content.rowconfigure(0, weight=1)
    content.rowconfigure(1, weight=0)

    monitors = get_monitors()
    cfg = load_config() or {}
    raw_groups = cfg.get("setting_groups", [])
    setting_groups = [_hydrate_setting_group(monitors, g) for g in raw_groups if isinstance(g, dict)]
    initial_group = setting_groups[0] if setting_groups else {}
    selected_indices = set(initial_group.get("monitor_indices", []))
    if not selected_indices:
        selected_indices = set(
            _resolve_monitor_indices(
                monitors,
                cfg.get("monitor_indices", []),
                cfg.get("monitors", []),
                cfg.get("monitor_devices", []),
            )
        )
    if not selected_indices and monitors:
        selected_indices = set(range(len(monitors)))
    monitor_selected = [idx in selected_indices for idx in range(len(monitors))]

    timeout_options = [
        (0.1667, "10 sec"),
        (1, "1 min"),
        (3, "3 min"),
        (5, "5 min"),
        (10, "10 min"),
        (15, "15 min"),
        (30, "30 min"),
        (45, "45 min"),
        (60, "60 min")
    ]
    interval_options = [(30, "30 sec"), (60, "1 min"), (300, "5 min")]

    timeout_var = tk.DoubleVar(value=initial_group.get("timeout", cfg.get("timeout", 5)))
    mode_var = tk.StringVar(value=initial_group.get("mode", cfg.get("mode", "blank")))
    interval_var = tk.IntVar(value=initial_group.get("interval", cfg.get("interval", 30)))
    scope_var = tk.StringVar(value=initial_group.get("scope", cfg.get("scope", "system")))
    detection_mode_var = tk.StringVar(value=initial_group.get("detection_mode", cfg.get("detection_mode", "input")))
    enabled_var = tk.BooleanVar(value=initial_group.get("enabled", cfg.get("enabled", True)))
    file_paths = list(initial_group.get("file_paths", cfg.get("file_paths", [])))

    timeout_label_to_value = {label: value for value, label in timeout_options}
    timeout_value_to_label = {value: label for value, label in timeout_options}
    timeout_choice = tk.StringVar(value=timeout_value_to_label.get(timeout_var.get(), "5 min"))

    interval_value_to_label = {value: label for value, label in interval_options}
    interval_label_to_value = {label: value for value, label in interval_options}
    interval_choice = tk.StringVar(value=interval_value_to_label.get(interval_var.get(), "30 sec"))

    left_card = ttk.Labelframe(content, text="Display Layout", style="Card.TLabelframe", padding=(12, 10, 12, 8))
    left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
    left_card.columnconfigure(0, weight=1)

    ttk.Label(left_card, text="Click monitors to include/exclude them from protection.", style="Body.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

    canvas_width = 420
    canvas_height = 250
    canvas = tk.Canvas(left_card, width=canvas_width, height=canvas_height, bg="#0c0f14", highlightthickness=1, highlightbackground="#2a3240")
    canvas.grid(row=1, column=0, sticky="nsew")

    monitor_shapes = []
    monitor_rect_ids = []
    monitor_label_ids = []

    def draw_monitor_map():
        canvas.delete("all")
        monitor_shapes.clear()
        monitor_rect_ids.clear()
        monitor_label_ids.clear()

        if not monitors:
            canvas.create_text(
                canvas_width // 2,
                canvas_height // 2,
                text="No displays detected",
                fill="#ff6f76",
                font=("Segoe UI", 12, "bold")
            )
            return

        margin = 18
        min_left = min(m["geometry"][0] for m in monitors)
        min_top = min(m["geometry"][1] for m in monitors)
        max_right = max(m["geometry"][2] for m in monitors)
        max_bottom = max(m["geometry"][3] for m in monitors)
        total_w = max(1, max_right - min_left)
        total_h = max(1, max_bottom - min_top)

        scale = min((canvas_width - margin * 2) / total_w, (canvas_height - margin * 2) / total_h)
        offset_x = (canvas_width - (total_w * scale)) / 2
        offset_y = (canvas_height - (total_h * scale)) / 2

        for idx, mon in enumerate(monitors):
            left, top, right, bottom = mon["geometry"]
            x1 = int((left - min_left) * scale + offset_x)
            y1 = int((top - min_top) * scale + offset_y)
            x2 = int((right - min_left) * scale + offset_x)
            y2 = int((bottom - min_top) * scale + offset_y)

            selected = monitor_selected[idx]
            fill_color = "#163526" if selected else "#1a1f2a"
            outline_color = "#30c18d" if selected else "#4d5a72"
            width = 3 if selected else 2

            rect = canvas.create_rectangle(x1, y1, x2, y2, fill=fill_color, outline=outline_color, width=width)
            label = canvas.create_text(
                (x1 + x2) // 2,
                (y1 + y2) // 2,
                text=f"Display {idx + 1}",
                fill="#e9f2ff",
                font=("Segoe UI", 10, "bold")
            )

            monitor_shapes.append((x1, y1, x2, y2))
            monitor_rect_ids.append(rect)
            monitor_label_ids.append(label)

    def on_canvas_click(event):
        for idx, (x1, y1, x2, y2) in enumerate(monitor_shapes):
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                monitor_selected[idx] = not monitor_selected[idx]
                draw_monitor_map()
                if monitor_selected[idx]:
                    multiprocessing.Process(target=show_monitor_indicator, args=(monitors[idx]["geometry"], idx)).start()
                break

    canvas.bind("<Button-1>", on_canvas_click)
    draw_monitor_map()

    right_card = ttk.Labelframe(content, text="Protection Settings", style="Card.TLabelframe", padding=(14, 10, 14, 8))
    right_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
    right_card.columnconfigure(1, weight=1)

    ttk.Label(right_card, text="Idle timeout", style="Body.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6), padx=(0, 10))
    timeout_combo = ttk.Combobox(right_card, state="readonly", textvariable=timeout_choice, values=list(timeout_label_to_value.keys()), width=16)
    timeout_combo.grid(row=0, column=1, sticky="w", pady=(0, 6))

    ttk.Label(right_card, text="Overlay mode", style="Body.TLabel").grid(row=1, column=0, sticky="nw", pady=(4, 6), padx=(0, 10))
    mode_frame = tk.Frame(right_card, bg="#161a22")
    mode_frame.grid(row=1, column=1, sticky="w", pady=(4, 6))
    mode_choices = [("Blank", "blank"), ("Single Image", "single"), ("Slideshow", "slideshow"), ("Video", "video")]
    for text, value in mode_choices:
        ttk.Radiobutton(mode_frame, text=text, variable=mode_var, value=value, style="Dark.TRadiobutton").pack(side=tk.LEFT, padx=(0, 10))

    interval_label = ttk.Label(right_card, text="Slideshow interval", style="Body.TLabel")
    interval_label.grid(row=2, column=0, sticky="w", pady=(2, 6), padx=(0, 10))
    interval_combo = ttk.Combobox(right_card, state="readonly", textvariable=interval_choice, values=list(interval_label_to_value.keys()), width=16)
    interval_combo.grid(row=2, column=1, sticky="w", pady=(2, 6))

    media_card = ttk.Labelframe(right_card, text="Media Attachments", style="Card.TLabelframe", padding=(12, 8, 12, 8))
    media_card.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    media_card.columnconfigure(1, weight=1)

    upload_button = ttk.Button(media_card, text="Choose Files")
    upload_button.grid(row=0, column=0, sticky="w")

    upload_label = ttk.Label(media_card, text="No media selected", style="Body.TLabel")
    upload_label.grid(row=0, column=1, sticky="w", padx=(10, 0))

    thumb_row = tk.Frame(media_card, bg="#161a22")
    thumb_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    thumbnail_imgs = []

    ttk.Label(right_card, text="Detection scope", style="Body.TLabel").grid(row=4, column=0, sticky="nw", pady=(2, 6), padx=(0, 10))
    scope_frame = tk.Frame(right_card, bg="#161a22")
    scope_frame.grid(row=4, column=1, sticky="w", pady=(2, 6))
    ttk.Radiobutton(scope_frame, text="System-wide", variable=scope_var, value="system", style="Dark.TRadiobutton").pack(side=tk.LEFT, padx=(0, 10))
    ttk.Radiobutton(scope_frame, text="Per-monitor", variable=scope_var, value="per-monitor", style="Dark.TRadiobutton").pack(side=tk.LEFT)

    ttk.Label(right_card, text="Detection mode", style="Body.TLabel").grid(row=5, column=0, sticky="nw", pady=(2, 8), padx=(0, 10))
    detect_frame = tk.Frame(right_card, bg="#161a22")
    detect_frame.grid(row=5, column=1, sticky="w", pady=(2, 8))
    ttk.Radiobutton(detect_frame, text="Input", variable=detection_mode_var, value="input", style="Dark.TRadiobutton").pack(side=tk.LEFT, padx=(0, 10))
    ttk.Radiobutton(detect_frame, text="Activity", variable=detection_mode_var, value="activity", style="Dark.TRadiobutton").pack(side=tk.LEFT, padx=(0, 10))
    ttk.Radiobutton(detect_frame, text="Both", variable=detection_mode_var, value="both", style="Dark.TRadiobutton").pack(side=tk.LEFT)

    apply_row = ttk.Frame(right_card)
    apply_row.grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 6))

    enabled_check = ttk.Checkbutton(right_card, text="Enable background protection", variable=enabled_var)
    enabled_check.grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 4))

    help_card = ttk.Labelframe(right_card, text="How It Works", style="Card.TLabelframe", padding=(10, 6, 10, 4))
    help_card.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    help_card.columnconfigure(1, weight=1)

    _help_rows = [
        ("Idle Timeout",      "How long a display must be inactive before protection kicks in."),
        ("Overlay Mode",      "Blank turns the screen off; Image/Slideshow/Video shows media."),
        ("Slideshow Interval","Time between each image when using Slideshow mode."),
        ("Media Files",       "Required for Image, Slideshow, and Video modes."),
        ("Detection Scope",   "System-wide: whole PC idle. Per-monitor: each screen tracked separately."),
        ("Detection Mode",    "Input: activates when keyboard/mouse are idle. Activity: stays off while video/media is playing â€” screen pixel changes are detected, so pausing also resumes the idle timer. Both: only activates when both input AND screen are fully idle."),
        ("Apply",             "Saves the current settings as a new named protection profile."),
    ]
    for _r, (_label, _desc) in enumerate(_help_rows):
        tk.Label(
            help_card,
            text=_label,
            bg="#161a22",
            fg="#9fe1c8",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            width=18,
            justify="left"
        ).grid(row=_r, column=0, sticky="nw", pady=(0, 3), padx=(0, 10))
        tk.Label(
            help_card,
            text=_desc,
            bg="#161a22",
            fg="#9aa7b7",
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=300
        ).grid(row=_r, column=1, sticky="nw", pady=(0, 3))

    settings_list_card = ttk.Labelframe(left_card, text="Applied Settings", style="Card.TLabelframe", padding=(10, 6, 10, 6))
    settings_list_card.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    settings_list_card.columnconfigure(0, weight=1)

    settings_list_body = ttk.Frame(settings_list_card)
    settings_list_body.grid(row=0, column=0, sticky="ew")

    def get_current_payload(setting_name=None):
        selected = [idx for idx, sel in enumerate(monitor_selected) if sel]
        scope_value = scope_var.get()
        if scope_value not in ("system", "per-monitor"):
            scope_value = "system"
        detection_value = detection_mode_var.get()
        if detection_value not in ("input", "activity", "both"):
            detection_value = "input"
        monitor_geometries = []
        monitor_devices = []
        for idx in selected:
            identity = _monitor_identity(monitors, idx)
            if identity is None:
                continue
            monitor_geometries.append(identity["geometry"])
            monitor_devices.append(identity["device"])
        return {
            "name": setting_name or "Setting 1",
            "monitor_indices": selected,
            "monitor_geometries": monitor_geometries,
            "monitor_devices": monitor_devices,
            "mode": mode_var.get(),
            "file_paths": list(file_paths),
            "timeout": timeout_label_to_value.get(timeout_choice.get(), timeout_var.get()),
            "interval": interval_label_to_value.get(interval_choice.get(), interval_var.get()),
            "enabled": bool(enabled_var.get()),
            "scope": scope_value,
            "detection_mode": detection_value
        }

    def next_setting_name():
        existing = {str(g.get("name", "")).strip().lower() for g in setting_groups}
        idx = 1
        while True:
            candidate = f"Setting {idx}"
            if candidate.lower() not in existing:
                return candidate
            idx += 1

    last_applied_form_snapshot = {"value": None}

    def _build_form_state_snapshot():
        payload = get_current_payload(setting_name="")
        payload["name"] = ""
        return json.dumps(payload, sort_keys=True)

    def _mark_form_applied():
        last_applied_form_snapshot["value"] = _build_form_state_snapshot()

    def _form_has_unapplied_changes():
        return _build_form_state_snapshot() != last_applied_form_snapshot.get("value")

    _mark_form_applied()

    def remove_setting_group(group_name):
        keep = [g for g in setting_groups if g.get("name") != group_name]
        if len(keep) == len(setting_groups):
            return
        setting_groups[:] = keep
        render_setting_groups()
        selected_now = [idx for idx, sel in enumerate(monitor_selected) if sel]
        save_config(
            monitors,
            selected_now,
            mode_var.get(),
            file_paths,
            timeout_label_to_value.get(timeout_choice.get(), timeout_var.get()),
            interval_label_to_value.get(interval_choice.get(), interval_var.get()),
            enabled_var.get(),
            scope_var.get(),
            detection_mode_var.get(),
            setting_groups
        )

    def render_setting_groups():
        def short_name(path):
            name = os.path.basename(path or "")
            if len(name) <= 24:
                return name
            return f"{name[:10]}...{name[-10:]}"

        def format_timeout(minutes):
            try:
                value = float(minutes)
            except (TypeError, ValueError):
                return str(minutes)
            if value < 1:
                return f"{int(round(value * 60))}s"
            if int(value) == value:
                return f"{int(value)}m"
            return f"{value}m"

        def media_summary(group):
            mode_name = group.get("mode", "blank")
            files = list(group.get("file_paths", []))
            if mode_name == "blank":
                return "none"
            if mode_name == "single":
                return short_name(files[0]) if files else "missing"
            if mode_name == "slideshow":
                count = len(files)
                interval = int(group.get("interval", 30))
                return f"{count} img @ {interval}s" if count else "missing"
            if mode_name == "video":
                if not files:
                    return "missing"
                if len(files) == 1:
                    return short_name(files[0])
                return f"{len(files)} videos"
            return "unknown"

        for child in settings_list_body.winfo_children():
            child.destroy()
        if not setting_groups:
            ttk.Label(settings_list_body, text="No settings applied yet.", style="Body.TLabel").pack(anchor="w")
            return
        for group in setting_groups:
            display_nums = [str(i + 1) for i in group.get("monitor_indices", [])]
            mode_name = group.get("mode", "blank")
            timeout_text = format_timeout(group.get("timeout", 5))
            scope_text = group.get("scope", "system")
            detect_text = group.get("detection_mode", "input")
            media_text = media_summary(group)
            line = (
                f"{group.get('name', 'Setting')} | D:{','.join(display_nums)} | Mode:{mode_name} | "
                f"Timeout:{timeout_text} | Media:{media_text} | Scope:{scope_text} | Detect:{detect_text}"
            )
            row = ttk.Frame(settings_list_body)
            row.pack(fill=tk.X, anchor="w", pady=(0, 2))
            tk.Button(
                row,
                text="X",
                command=lambda n=group.get("name", ""): remove_setting_group(n),
                bg="#3a1519",
                fg="#ff9098",
                activebackground="#552126",
                activeforeground="#ffd3d7",
                relief=tk.FLAT,
                bd=0,
                padx=6,
                pady=1,
                cursor="hand2"
            ).pack(side=tk.LEFT, padx=(0, 6))
            ttk.Label(row, text=line, style="Body.TLabel").pack(side=tk.LEFT, anchor="w")

    render_setting_groups()

    def update_thumbnails():
        for widget in thumb_row.winfo_children():
            widget.destroy()
        thumbnail_imgs.clear()
        if not file_paths:
            return
        if mode_var.get() == "video":
            text = os.path.basename(file_paths[0]) if file_paths else "No video selected"
            tk.Label(thumb_row, text=f"Video: {text}", fg="#d4dde9", bg="#161a22", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
            return

        max_thumb_size = 68
        for path in file_paths[:8]:
            try:
                img = Image.open(path)
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS
                img.thumbnail((max_thumb_size, max_thumb_size), resample)
                photo = ImageTk.PhotoImage(img)
                thumbnail_imgs.append(photo)
                lbl = tk.Label(thumb_row, image=photo, bg="#161a22", bd=1, relief=tk.SOLID, highlightthickness=0)
                lbl.pack(side=tk.LEFT, padx=(0, 6))
            except Exception as e:
                logging.error(f"Thumbnail error for {path}: {e}")

    def refresh_upload_summary():
        mode = mode_var.get()
        if mode == "blank":
            upload_label.config(text="No media required in Blank mode")
        elif not file_paths:
            upload_label.config(text="No media selected")
        elif mode == "single":
            upload_label.config(text=f"Selected: {os.path.basename(file_paths[0])}")
        elif mode == "video":
            if len(file_paths) == 1:
                upload_label.config(text=f"Selected: {os.path.basename(file_paths[0])}")
            else:
                upload_label.config(text=f"Selected: {len(file_paths)} videos")
        else:
            upload_label.config(text=f"Selected: {len(file_paths)} images")
        update_thumbnails()

    def upload_files():
        mode = mode_var.get()
        if mode == "blank":
            return
        if mode == "single":
            path = filedialog.askopenfilename(
                title="Select Image",
                filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp"), ("All Files", "*.*")]
            )
            if path:
                file_paths.clear()
                file_paths.append(path)
        elif mode == "slideshow":
            paths = filedialog.askopenfilenames(
                title="Select Slideshow Images",
                filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp"), ("All Files", "*.*")]
            )
            if paths:
                file_paths.clear()
                file_paths.extend(paths)
        elif mode == "video":
            paths = filedialog.askopenfilenames(
                title="Select Video(s)",
                filetypes=[("Video Files", "*.mp4;*.mov;*.avi;*.mkv;*.wmv;*.webm;*.flv;*.m4v"), ("All Files", "*.*")]
            )
            if paths:
                file_paths.clear()
                file_paths.extend(paths)
        refresh_upload_summary()

    upload_button.configure(command=upload_files)

    def update_media_controls(*_args):
        mode = mode_var.get()
        is_slide = mode == "slideshow"
        needs_media = mode in ("single", "slideshow", "video")
        if is_slide:
            interval_label.grid()
            interval_combo.grid()
        else:
            interval_label.grid_remove()
            interval_combo.grid_remove()
        if needs_media:
            media_card.grid()
        else:
            media_card.grid_remove()
        refresh_upload_summary()

    def apply_settings(show_success=True):
        current_license = get_license_status()
        if not current_license.get("can_protect", False):
            messagebox.showwarning(
                "Display Control+",
                current_license.get("block_reason") or "A trial, annual subscription, or lifetime license is required before protection can run."
            )
            refresh_license_label()
            return False

        selected = [idx for idx, sel in enumerate(monitor_selected) if sel]
        if not selected:
            messagebox.showwarning("Display Control+", "Select at least one display to protect.")
            return False

        mode = mode_var.get()
        if mode in ("single", "video") and not file_paths:
            messagebox.showwarning("Display Control+", "Choose a media file for the selected mode.")
            return False
        if mode == "slideshow" and not file_paths:
            messagebox.showwarning("Display Control+", "Choose one or more images for slideshow mode.")
            return False

        timeout_val = timeout_label_to_value.get(timeout_choice.get(), 5)
        interval_val = interval_label_to_value.get(interval_choice.get(), 30)
        timeout_var.set(timeout_val)
        interval_var.set(interval_val)

        conflicting = []
        for group in setting_groups:
            overlap = sorted(set(group.get("monitor_indices", [])) & set(selected))
            if overlap:
                conflicting.append((group, overlap))
        if conflicting:
            conflict_displays = sorted({idx for _, ov in conflicting for idx in ov})
            display_text = ", ".join(str(i + 1) for i in conflict_displays)
            replace_ok = messagebox.askyesno(
                "Display Control+",
                f"Settings already exist for display(s): {display_text}. Override them?"
            )
            if not replace_ok:
                return False
            survivors = []
            for group in setting_groups:
                remaining = [i for i in group.get("monitor_indices", []) if i not in selected]
                if remaining:
                    group["monitor_indices"] = remaining
                    survivors.append(group)
            setting_groups[:] = survivors

        name = next_setting_name()
        setting_groups.append(get_current_payload(setting_name=name))
        render_setting_groups()

        save_config(
            monitors,
            selected,
            mode,
            file_paths,
            timeout_var.get(),
            interval_var.get(),
            enabled_var.get(),
            scope_var.get(),
            detection_mode_var.get(),
            setting_groups,
            paused=False
        )
        _mark_form_applied()
        logging.info("Settings applied from GUI.")

        bg_exe_path = _first_existing_path("overlay_bg.exe")
        bg_py_path = _first_existing_path("overlay_bg.py")
        pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw_path):
            pythonw_path = sys.executable

        # In source/dev runs, always use overlay_bg.py so metering logic stays in sync.
        use_bg_exe = bool(getattr(sys, "frozen", False) and os.path.exists(bg_exe_path))
        if use_bg_exe:
            tr_cmd = f'"{bg_exe_path}"'
            spawn_cmd = [bg_exe_path]
        else:
            tr_cmd = f'"{pythonw_path}" "{bg_py_path}"'
            spawn_cmd = [pythonw_path, bg_py_path]

        try:
            from ensure_overlay_bg_task import register_per_user_startup

            startup = register_per_user_startup(tr_cmd)
            if not startup.get("ok"):
                logging.error("[TASK] Per-user startup failed: %s", startup.get("task_error") or "Run key was not written")
            elif not startup.get("scheduled_task"):
                logging.info("[TASK] Scheduled task was not created (%s). Per-user Run startup is enabled.", startup.get("task_error") or "access denied")
        except (OSError, subprocess.SubprocessError) as exc:
            logging.error("[TASK] Per-user startup failed: %s", exc)

        # Also spawn background process immediately (don't wait for task scheduler)
        if not is_background_running():
            try:
                subprocess.Popen(spawn_cmd, start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW)
                logging.info("Background process spawned immediately.")
            except (OSError, subprocess.SubprocessError) as exc:
                logging.warning("Could not spawn background process immediately: %s", exc)


        if show_success:
            messagebox.showinfo("Display Control+", f"{name} applied successfully.")
        return True

    def preview_overlay():
        current_license = get_license_status()
        if not current_license.get("can_protect", False):
            messagebox.showwarning("Display Control+", current_license.get("block_reason") or "Protection preview is unavailable until a trial or license is active.")
            refresh_license_label()
            return

        selected = [idx for idx, sel in enumerate(monitor_selected) if sel]
        if not selected:
            messagebox.showwarning("Display Control+", "Select at least one display for preview.")
            return

        mode = mode_var.get()
        interval_val = interval_label_to_value.get(interval_choice.get(), 30)
        for idx in selected:
            geometry = monitors[idx]["geometry"]
            try:
                if mode == "single" and file_paths:
                    multiprocessing.Process(target=show_image_overlay, args=(geometry, file_paths[0], True)).start()
                elif mode == "slideshow" and file_paths:
                    multiprocessing.Process(target=show_slideshow_overlay, args=(geometry, file_paths, interval_val, True)).start()
                elif mode == "video" and file_paths:
                    multiprocessing.Process(target=show_video_overlay, args=(geometry, file_paths, True)).start()
                else:
                    multiprocessing.Process(target=show_black_overlay, args=(geometry, True)).start()
            except Exception as e:
                logging.error(f"Preview launch failed for display {idx + 1}: {e}")

    def on_close():
        set_gui_lock(False)
        win.destroy()

    ttk.Button(apply_row, text="Apply", style="Accent.TButton", command=apply_settings).pack(side=tk.LEFT)

    actions = ttk.Frame(root_frame)
    actions.pack(fill=tk.X, pady=(6, 0))

    ttk.Button(actions, text="Preview", command=preview_overlay).pack(side=tk.LEFT)
    
    refresh_link = tk.Label(
        actions,
        text="Refresh",
        bg="#0f1115",
        fg="#73d9b5",
        cursor="hand2",
        font=("Segoe UI", 9, "underline")
    )
    refresh_link.pack(side=tk.LEFT, padx=(6, 0))
    def on_refresh_click():
        log_debug("Refresh button clicked")
        refresh_license_label()
    refresh_link.bind("<Button-1>", lambda _e: on_refresh_click())

    def apply_and_close():
        if _form_has_unapplied_changes() and not apply_settings(show_success=False):
            return
        win.destroy()

    ttk.Button(actions, text="Save & Close", command=apply_and_close).pack(side=tk.RIGHT)
    ttk.Button(actions, text="Close", command=on_close).pack(side=tk.RIGHT, padx=(8, 0))

    restore_link = tk.Label(
        actions,
        text="Restore Account",
        bg="#0f1115",
        fg="#73d9b5",
        cursor="hand2",
        font=("Segoe UI", 9, "underline")
    )
    restore_link.place(relx=0.5, rely=0.5, anchor="center")
    restore_link.bind("<Button-1>", lambda _e: restore_account_from_code())

    mode_var.trace_add("write", update_media_controls)
    update_media_controls()
    refresh_upload_summary()
    refresh_license_label()

    def schedule_license_refresh():
        if win.winfo_exists():
            refresh_license_label()
            win.after(1000, schedule_license_refresh)

    schedule_license_refresh()

    # Check for a newer GitHub release 2 s after the window is ready
    try:
        from updater import check_for_updates
        win.after(2000, lambda: check_for_updates(win))
    except Exception as e:
        logging.debug(f"[updater] Could not schedule update check: {e}")

    win.protocol("WM_DELETE_WINDOW", on_close)
    try:
        win.mainloop()
    finally:
        set_gui_lock(False)

# --- Entry Point ---
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if "--background" in sys.argv:
        run_background_overlay()
    else:
        launch_gui()

