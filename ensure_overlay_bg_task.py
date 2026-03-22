import logging
import os
import subprocess
import sys

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", "DisplayControlPlus")
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


def _set_run_key_startup(start_cmd):
    value_name = "DisplayControlPlusTray"
    reg_path = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
    result = subprocess.run(
        ["reg", "add", reg_path, "/v", value_name, "/t", "REG_SZ", "/d", start_cmd, "/f"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    logging.info(f"Run-key set result: rc={result.returncode} out={result.stdout} err={result.stderr}")
    return result.returncode == 0

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

    task_name = "DisplayControlBackground"

    del_result = subprocess.run(
        ["SchTasks", "/Delete", "/F", "/TN", task_name],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    logging.info(f"Task delete result: rc={del_result.returncode} out={del_result.stdout} err={del_result.stderr}")

    create_result = subprocess.run(
        ["SchTasks", "/Create", "/F", "/TN", task_name, "/TR", tr_cmd, "/SC", "ONLOGON"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    logging.info(f"Task create result: rc={create_result.returncode} out={create_result.stdout} err={create_result.stderr}")
    if create_result.returncode != 0:
        if "Access is denied" in (create_result.stderr or ""):
            if _set_run_key_startup(tr_cmd):
                print(f"Scheduled task denied; configured per-user Run startup instead: {tr_cmd}")
            else:
                print(f"Failed to create task '{task_name}' and failed to set Run-key startup.")
        else:
            print(f"Failed to create task '{task_name}'.")
        return

    run_result = subprocess.run(
        ["SchTasks", "/Run", "/TN", task_name],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    logging.info(f"Task run result: rc={run_result.returncode} out={run_result.stdout} err={run_result.stderr}")

    verify_result = subprocess.run(
        ["SchTasks", "/Query", "/TN", task_name],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if verify_result.returncode == 0:
        print(f"Task '{task_name}' is configured and started for {tr_cmd}")
    else:
        print(f"Task '{task_name}' created but query verification failed.")

if __name__ == "__main__":
    logging.basicConfig(filename=os.path.join(RUNTIME_DIR, "overlay.log"), level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    ensure_overlay_bg_task()
