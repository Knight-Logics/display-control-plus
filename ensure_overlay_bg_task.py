import logging
import os
import subprocess
import sys
import threading

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_PROFILE = os.environ.get(
    "DISPLAY_CONTROL_RUNTIME_PROFILE",
    "DisplayControlPlus" if getattr(sys, "frozen", False) else "DisplayControlPlus-DevLocal",
)
TASK_NAME = os.environ.get(
    "DISPLAY_CONTROL_TASK_NAME",
    "DisplayControlBackground" if getattr(sys, "frozen", False) else "DisplayControlBackground-DevLocal",
)
RUN_VALUE_NAME = os.environ.get(
    "DISPLAY_CONTROL_RUN_VALUE",
    "DisplayControlPlusTray" if getattr(sys, "frozen", False) else "DisplayControlPlusTray-DevLocal",
)
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", RUNTIME_PROFILE)
os.makedirs(RUNTIME_DIR, exist_ok=True)


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


def _no_window_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _set_run_key_startup(start_cmd, run_value_name=None, runner=None):
    reg_path = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
    value_name = run_value_name or RUN_VALUE_NAME
    run = runner or subprocess.run
    result = run(
        ["reg", "add", reg_path, "/v", value_name, "/t", "REG_SZ", "/d", start_cmd, "/f"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_no_window_flags(),
    )
    logging.info("Run-key set result: rc=%s out=%s err=%s", result.returncode, result.stdout, result.stderr)
    return result.returncode == 0


_STARTUP_LOCK = threading.Lock()
_STARTUP_FILE_LOCK = {"depth": 0, "handle": None}


class _StartupFileLock:
    """Keep the dashboard and the tray from deleting and recreating the task together."""

    def __enter__(self):
        if _STARTUP_FILE_LOCK["depth"] == 0:
            os.makedirs(RUNTIME_DIR, exist_ok=True)
            handle = open(os.path.join(RUNTIME_DIR, "startup.lock"), "a+")
            try:
                if os.name == "nt":
                    import msvcrt

                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write("0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                logging.warning("Could not lock startup registration: %s", exc)
            _STARTUP_FILE_LOCK["handle"] = handle
        _STARTUP_FILE_LOCK["depth"] += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        _STARTUP_FILE_LOCK["depth"] -= 1
        if _STARTUP_FILE_LOCK["depth"] > 0:
            return False
        _STARTUP_FILE_LOCK["depth"] = 0
        handle = _STARTUP_FILE_LOCK.get("handle")
        _STARTUP_FILE_LOCK["handle"] = None
        if handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                logging.warning("Could not unlock startup registration: %s", exc)
            try:
                handle.close()
            except OSError:
                pass
        return False


def register_per_user_startup(start_cmd, task_name=None, run_value_name=None, runner=None):
    """Register login startup for the current user without elevation.

    The HKCU Run key is the startup that works for a standard user. A limited
    interactive scheduled task is attempted afterwards. Access denied from
    schtasks (common when an elevated installer owns the task) does not fail
    startup when the Run key was written.
    """
    with _STARTUP_LOCK, _StartupFileLock():
        return _register_per_user_startup(start_cmd, task_name, run_value_name, runner)


def _register_per_user_startup(start_cmd, task_name=None, run_value_name=None, runner=None):
    run = runner or subprocess.run
    flags = _no_window_flags()
    resolved_task = task_name or TASK_NAME
    run_ok = _set_run_key_startup(start_cmd, run_value_name=run_value_name, runner=run)

    delete_result = run(
        ["schtasks", "/Delete", "/F", "/TN", resolved_task],
        capture_output=True,
        text=True,
        check=False,
        creationflags=flags,
    )
    logging.info(
        "Task delete result: rc=%s out=%s err=%s",
        delete_result.returncode,
        delete_result.stdout,
        delete_result.stderr,
    )

    create_result = run(
        [
            "schtasks",
            "/Create",
            "/F",
            "/TN",
            resolved_task,
            "/TR",
            start_cmd,
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/IT",
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=flags,
    )
    task_error = " ".join(
        part.strip()
        for part in ((create_result.stderr or ""), (create_result.stdout or ""))
        if part and part.strip()
    )
    task_ok = create_result.returncode == 0
    logging.info("Task create result: rc=%s out=%s err=%s", create_result.returncode, create_result.stdout, create_result.stderr)
    if not task_ok:
        logging.warning(
            "Per-user scheduled task was not created (%s). Run-key startup is %s.",
            task_error or f"exit {create_result.returncode}",
            "enabled" if run_ok else "unavailable",
        )

    return {
        "ok": bool(run_ok or task_ok),
        "run_key": run_ok,
        "scheduled_task": task_ok,
        "task_error": task_error,
        "command": start_cmd,
    }


def ensure_overlay_bg_task():
    # Prefer tray app at startup; tray keeps background alive and provides controls.
    base = app_base_dir()
    tray_exe_path = _first_existing_path("tray.exe")
    bg_exe_path = _first_existing_path("overlay_bg.exe")

    if tray_exe_path:
        tr_cmd = f'"{tray_exe_path}"'
        logging.info(f"[TASK] Using TRAY EXE for /TR: {tr_cmd}")
    elif bg_exe_path:
        tr_cmd = f'"{bg_exe_path}"'
        logging.info(f"[TASK] Using BG EXE fallback for /TR: {tr_cmd}")
    else:
        py_path = os.path.join(base, "tray.py")
        if not os.path.exists(py_path):
            py_path = os.path.join(base, "overlay_bg.py")
        if not os.path.exists(py_path):
            print("tray.exe/tray.py or overlay_bg.exe/overlay_bg.py not found.")
            logging.error("No startup target found. Aborting task creation.")
            return

        pythonw_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw_exe):
            pythonw_exe = sys.executable
        tr_cmd = f'"{pythonw_exe}" "{py_path}"'
        logging.info(f"[TASK] Using PY fallback for /TR: {tr_cmd}")

    result = register_per_user_startup(tr_cmd)
    if result["run_key"]:
        print(f"Per-user startup registered for {tr_cmd}")
    elif result["scheduled_task"]:
        print(f"Scheduled task '{TASK_NAME}' is configured for {tr_cmd}")
    else:
        detail = result["task_error"] or "Run key and scheduled task both failed"
        print(f"Failed to register startup for '{TASK_NAME}': {detail}")
    return result

if __name__ == "__main__":
    logging.basicConfig(filename=os.path.join(RUNTIME_DIR, "overlay.log"), level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    ensure_overlay_bg_task()
