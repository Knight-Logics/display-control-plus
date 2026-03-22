import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json
import logging
import multiprocessing
import os
import sys
import ctypes
import threading
import time
import webbrowser
from PIL import Image, ImageTk
from monitor_activity import MonitorActivityDetector

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", "DisplayControlPlus")
os.makedirs(RUNTIME_DIR, exist_ok=True)

def runtime_path(name):
    return os.path.join(RUNTIME_DIR, name)


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
    except Exception:
        return None, ""
    pid = payload.get("pid")
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    if not isinstance(pid, int):
        return None, ""
    process_name = str(payload.get("process_name", "")).strip().lower()
    return pid, process_name


def _start_startup_registration():
    try:
        task_name = "DisplayControlBackground"
        check = subprocess.run(
            ["SchTasks", "/Query", "/TN", task_name],
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        access_denied = "Access is denied" in ((check.stderr or "") + (check.stdout or ""))
        if access_denied:
            return
        if getattr(sys, "frozen", False):
            from ensure_overlay_bg_task import ensure_overlay_bg_task

            threading.Thread(
                target=ensure_overlay_bg_task,
                daemon=True,
                name="display-control-startup-registration",
            ).start()
            return
        script_path = os.path.join(app_base_dir(), "ensure_overlay_bg_task.py")
        if os.path.exists(script_path):
            subprocess.Popen([sys.executable, script_path], cwd=app_base_dir(), creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        logging.error(f"Failed to ensure background task from overlay.py: {e}")

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
        logging.info(f"Image displayed on overlay window.")
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

# --- Config Functions ---
def save_config(monitors, selected, mode, file_paths, timeout, interval, enabled, scope, detection_mode,
    setting_groups=None, paused=None):
    existing = load_config() or {}
    groups = setting_groups or []
    active = groups[0] if groups else {
        "name": "Setting 1",
        "monitor_indices": list(selected),
        "mode": mode,
        "file_paths": list(file_paths),
        "timeout": timeout,
        "interval": interval,
        "enabled": enabled,
        "scope": scope,
        "detection_mode": detection_mode
    }
    active_indices = list(active.get("monitor_indices", selected))
    config = {
        # Save both geometries and indices for clarity and backward compatibility
        "monitors": [monitors[i]['geometry'] for i in active_indices if i < len(monitors)],
        "monitor_indices": active_indices,
        "mode": active.get("mode", mode),
        "file_paths": list(active.get("file_paths", file_paths)),
        "timeout": active.get("timeout", timeout),
        "interval": active.get("interval", interval),
        "enabled": bool(active.get("enabled", enabled)),
        "scope": active.get("scope", scope),
        "detection_mode": active.get("detection_mode", detection_mode),
        "paused": bool(existing.get("paused", False)) if paused is None else bool(paused),
        "setting_groups": groups
    }
    with open(runtime_path("config.json"), "w") as f:
        json.dump(config, f, indent=2)
    logging.info(f"Config saved: {config}")

def load_config():
    try:
        with open(runtime_path("config.json"), "r") as f:
            return json.load(f)
    except Exception:
        return None

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
    except Exception:
        try:
            os.remove(lock_path)
        except Exception:
            pass
        return False

def set_background_lock(state):
    lock_path = runtime_path("overlay_bg.lock")
    if state:
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))
    else:
        try:
            os.remove(lock_path)
        except Exception:
            pass

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

def _get_process_image_name(pid):
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        line = (result.stdout or "").strip()
        if not line or line.startswith("INFO:"):
            return ""
        if line.startswith('"'):
            return line.split('","', 1)[0].strip('"').lower()
        return line.split(",", 1)[0].strip().lower()
    except Exception:
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
    except Exception:
        try:
            os.remove(lock_path)
        except Exception:
            pass
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
        except Exception:
            pass

# --- Task Scheduler Integration ---
def register_task_scheduler():
    exe = sys.executable
    script = os.path.abspath(__file__)
    task_name = "Display Control+"
    cmd = f'SchTasks /Create /F /TN "{task_name}" /TR "{exe} {script} --background" /SC ONLOGON /RL HIGHEST'
    try:
        subprocess.run(cmd, shell=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        run_cmd = f'SchTasks /Run /TN "{task_name}"'
        subprocess.run(run_cmd, shell=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        logging.error(f"Task Scheduler registration or start failed: {e}")

def unregister_task_scheduler():
    task_name = "Display Control+"
    cmd = f'SchTasks /Delete /F /TN "{task_name}"'
    try:
        subprocess.run(cmd, shell=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        logging.error(f"Task Scheduler unregister failed: {e}")

# --- Background Overlay Logic ---
def run_background_overlay():
    if is_background_running():
        logging.info('Background overlay already running. Exiting.')
        return
    set_background_lock(True)
    try:
        last_config_dump = ""
        while True:
            config = load_config() or {}
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
            if config_dump != last_config_dump:
                logging.info(f"Background overlay loaded setting groups: {json.dumps(groups, indent=2)}")
                last_config_dump = config_dump

            from monitor_control import get_monitors
            all_monitors = get_monitors()
            if not all_monitors:
                logging.warning("No monitors detected. Sleeping 30s.")
                time.sleep(30)
                continue

            # Use the most comprehensive detection mode required by any active group
            # so the detector tracks whichever signal(s) the groups actually need.
            _group_modes = {g.get("detection_mode", "input") for g in groups}
            _detector_mode = "both" if ("both" in _group_modes or "activity" in _group_modes) else "input"
            detector = MonitorActivityDetector(all_monitors, mode=_detector_mode, scope="per-monitor")
            detector.start()

            overlay_targets = {}
            while True:
                idle_times = detector.get_idle_times()
                system_idle = get_idle_duration()
                overlay_targets.clear()

                for group in groups:
                    indices = [i for i in group.get("monitor_indices", []) if isinstance(i, int) and i < len(all_monitors)]
                    if not indices:
                        continue
                    timeout_min = group.get("timeout", 5)
                    timeout_sec = int(float(timeout_min) * 60)
                    scope = group.get("scope", "system")

                    if scope == "system":
                        detection_mode = group.get("detection_mode", "input")
                        if detection_mode == "activity":
                            effective_idle = idle_times.get("system", 0)
                        elif detection_mode == "both":
                            effective_idle = min(system_idle, idle_times.get("system", 0))
                        else:
                            effective_idle = system_idle
                        if effective_idle >= timeout_sec:
                            for idx in indices:
                                overlay_targets[idx] = group
                    else:
                        for idx in indices:
                            geom = tuple(all_monitors[idx]["geometry"])
                            if idle_times.get(geom, 0) >= timeout_sec:
                                overlay_targets[idx] = group

                if overlay_targets:
                    break

                time.sleep(1)
                new_config = load_config() or {}
                new_groups = [g for g in new_config.get("setting_groups", []) if isinstance(g, dict) and g.get("enabled", True)]
                if json.dumps(new_groups, sort_keys=True) != config_dump:
                    groups = new_groups
                    config_dump = json.dumps(groups, sort_keys=True)
                    break

            if not overlay_targets:
                continue

            overlay_procs = []
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
                overlay_procs.append(proc)

            for proc in overlay_procs:
                proc.start()

            logging.info("Overlay(s) started. Waiting for activity to close overlays.")
            while True:
                if get_idle_duration() < 1:
                    break
                time.sleep(0.5)

            for proc in overlay_procs:
                if proc.is_alive():
                    proc.terminate()
    finally:
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

    from monitor_control import get_monitors

    win = tk.Tk()
    win.title("Display Control+")
    win.geometry("1120x780")
    win.minsize(980, 700)
    win.configure(bg="#0f1115")

    app_dir = app_base_dir()
    logo_path = _first_existing_path("Display Control+ Logo.png")
    brand_logo_path = _first_existing_path("KnightLogicsLogo.png")
    header_logo = None
    brand_logo = None
    window_icon = None
    _tmp_ico_path = None

    if os.path.exists(logo_path):
        try:
            import tempfile
            with Image.open(logo_path) as base_logo:
                icon_img = base_logo.copy().convert("RGBA")
                icon_img.thumbnail((64, 64), Image.Resampling.LANCZOS)
                window_icon = ImageTk.PhotoImage(icon_img)
                win.iconphoto(True, window_icon)

                # Write a temp .ico so the Windows taskbar shows the correct icon.
                # iconphoto alone is not sufficient for the native Win32 taskbar button.
                _ico_fd, _tmp_ico_path = tempfile.mkstemp(suffix=".ico")
                os.close(_ico_fd)
                ico_src = base_logo.copy().convert("RGBA")
                ico_src.save(_tmp_ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
                win.iconbitmap(_tmp_ico_path)

                header_img = base_logo.copy().convert("RGBA")
                header_img.thumbnail((40, 40), Image.Resampling.LANCZOS)
                header_logo = ImageTk.PhotoImage(header_img)
        except Exception as e:
            logging.warning(f"Could not load app logo: {e}")

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

    root_frame = ttk.Frame(win, padding=(18, 16, 18, 12))
    root_frame.pack(fill=tk.BOTH, expand=True)

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

    brand_top = tk.Frame(header_row, bg="#0f1115")
    brand_top.pack(side=tk.RIGHT, anchor="ne", pady=(6, 0))

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

    # Keep bottom alignment stable while moving the card region down slightly.
    content_spacer = tk.Frame(root_frame, height=10, bg="#0f1115")
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
    setting_groups = [g for g in raw_groups if isinstance(g, dict)]
    initial_group = setting_groups[0] if setting_groups else {}
    selected_indices = set(initial_group.get("monitor_indices", []))
    if not selected_indices and cfg.get("monitors"):
        saved_geoms = {tuple(g) for g in cfg.get("monitors", [])}
        for idx, mon in enumerate(monitors):
            if tuple(mon["geometry"]) in saved_geoms:
                selected_indices.add(idx)
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

    left_card = ttk.Labelframe(content, text="Display Layout", style="Card.TLabelframe", padding=(12, 12, 12, 10))
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

    right_card = ttk.Labelframe(content, text="Protection Settings", style="Card.TLabelframe", padding=(14, 12, 14, 10))
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

    media_card = ttk.Labelframe(right_card, text="Media Attachments", style="Card.TLabelframe", padding=(12, 10, 12, 10))
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
    ttk.Radiobutton(detect_frame, text="Input (keyboard & mouse)", variable=detection_mode_var, value="input", style="Dark.TRadiobutton").pack(side=tk.LEFT, padx=(0, 10))
    ttk.Radiobutton(detect_frame, text="Activity (media / screen)", variable=detection_mode_var, value="activity", style="Dark.TRadiobutton").pack(side=tk.LEFT, padx=(0, 10))
    ttk.Radiobutton(detect_frame, text="Both (require all idle)", variable=detection_mode_var, value="both", style="Dark.TRadiobutton").pack(side=tk.LEFT)

    apply_row = ttk.Frame(right_card)
    apply_row.grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 6))

    enabled_check = ttk.Checkbutton(right_card, text="Enable background protection", variable=enabled_var)
    enabled_check.grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 4))

    help_card = ttk.Labelframe(right_card, text="How It Works", style="Card.TLabelframe", padding=(10, 8, 10, 6))
    help_card.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    help_card.columnconfigure(1, weight=1)

    _help_rows = [
        ("Idle Timeout",      "How long a display must be inactive before protection kicks in."),
        ("Overlay Mode",      "Blank turns the screen off; Image/Slideshow/Video shows media."),
        ("Slideshow Interval","Time between each image when using Slideshow mode."),
        ("Media Files",       "Required for Image, Slideshow, and Video modes."),
        ("Detection Scope",   "System-wide: whole PC idle. Per-monitor: each screen tracked separately."),
        ("Detection Mode",    "Input: activates when keyboard/mouse are idle. Activity: stays off while video/media is playing — screen pixel changes are detected, so pausing also resumes the idle timer. Both: only activates when both input AND screen are fully idle."),
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
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=320
        ).grid(row=_r, column=1, sticky="nw", pady=(0, 3))

    settings_list_card = ttk.Labelframe(left_card, text="Applied Settings", style="Card.TLabelframe", padding=(10, 8, 10, 8))
    settings_list_card.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    settings_list_card.columnconfigure(0, weight=1)

    settings_list_body = ttk.Frame(settings_list_card)
    settings_list_body.grid(row=0, column=0, sticky="ew")

    def get_current_payload(setting_name=None):
        selected = [idx for idx, sel in enumerate(monitor_selected) if sel]
        return {
            "name": setting_name or "Setting 1",
            "monitor_indices": selected,
            "mode": mode_var.get(),
            "file_paths": list(file_paths),
            "timeout": timeout_label_to_value.get(timeout_choice.get(), timeout_var.get()),
            "interval": interval_label_to_value.get(interval_choice.get(), interval_var.get()),
            "enabled": bool(enabled_var.get()),
            "scope": scope_var.get(),
            "detection_mode": detection_mode_var.get()
        }

    def next_setting_name():
        existing = {str(g.get("name", "")).strip().lower() for g in setting_groups}
        idx = 1
        while True:
            candidate = f"Setting {idx}"
            if candidate.lower() not in existing:
                return candidate
            idx += 1

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
            except Exception:
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
        logging.info("Settings applied from GUI.")

        bg_exe_path = _first_existing_path("overlay_bg.exe")
        bg_py_path = _first_existing_path("overlay_bg.py")
        if os.path.exists(bg_exe_path):
            tr_cmd = f'"{bg_exe_path}"'
        else:
            tr_cmd = f'"{os.path.join(os.path.dirname(sys.executable), "pythonw.exe")}" "{bg_py_path}"'
            tr_cmd = f'"{tr_cmd}"'

        task_name = "DisplayControlBackground"
        create_cmd = (
            f'SchTasks /Create /F /TN "{task_name}" '
            f'/TR {tr_cmd} '
            '/SC ONLOGON'
        )
        result = subprocess.run(create_cmd, shell=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0:
            msg = (result.stderr or "").strip()
            if "Access is denied" in msg:
                logging.warning(f"[TASK] Could not register startup task without admin rights: {msg}")
            else:
                logging.error(f"[TASK] Failed to register startup task: {msg}")

        # Also spawn background process immediately (don't wait for task scheduler)
        if not is_background_running():
            try:
                if os.path.exists(bg_exe_path):
                    subprocess.Popen([bg_exe_path], start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "pythonw.exe"), bg_py_path], start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW)
                logging.info("Background process spawned immediately.")
            except Exception as e:
                logging.warning(f"Could not spawn background process immediately: {e}")


        if show_success:
            messagebox.showinfo("Display Control+", f"{name} applied successfully.")
        return True

    def preview_overlay():
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
    actions.pack(fill=tk.X, pady=(8, 0))

    ttk.Button(actions, text="Preview", command=preview_overlay).pack(side=tk.LEFT)

    def apply_and_close():
        if apply_settings(show_success=False):
            win.destroy()

    ttk.Button(actions, text="Save & Close", command=apply_and_close).pack(side=tk.RIGHT)
    ttk.Button(actions, text="Close", command=on_close).pack(side=tk.RIGHT, padx=(8, 0))

    mode_var.trace_add("write", update_media_controls)
    update_media_controls()
    refresh_upload_summary()

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
        if _tmp_ico_path:
            try:
                os.remove(_tmp_ico_path)
            except Exception:
                pass

# --- Entry Point ---
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if "--background" in sys.argv:
        run_background_overlay()
    else:
        launch_gui()
