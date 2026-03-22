
import sys
import os
import subprocess
import logging
import multiprocessing

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", "DisplayControlPlus")
os.makedirs(RUNTIME_DIR, exist_ok=True)

# If running as python.exe (console) and NOT in background mode, re-launch
# with pythonw.exe so no CMD window appears.  Works transparently in dev mode;
# packaged .exe already uses --windowed so this branch is never reached there.
if __name__ == "__main__" and "--background" not in sys.argv:
    if not getattr(sys, "frozen", False):
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
            if os.path.exists(pythonw):
                subprocess.Popen(
                    [pythonw] + sys.argv,
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                )
                sys.exit(0)

from overlay import launch_gui, run_background_overlay

logging.basicConfig(filename=os.path.join(RUNTIME_DIR, "overlay.log"), level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

def main():
    if "--background" in sys.argv:
        run_background_overlay()
    else:
        launch_gui()

if __name__ == "__main__":
    multiprocessing.freeze_support()  # Needed for PyInstaller to prevent recursive launches
    main()
