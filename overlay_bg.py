import logging
import multiprocessing
import os
import sys

from overlay import run_background_overlay

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", "DisplayControlPlus")
os.makedirs(RUNTIME_DIR, exist_ok=True)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    logging.basicConfig(
        filename=os.path.join(RUNTIME_DIR, "overlay.log"),
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    run_background_overlay()
