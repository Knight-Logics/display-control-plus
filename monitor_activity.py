import logging
import os
import time
import threading
from pynput import mouse, keyboard

APPDATA_ROOT = os.environ.get("APPDATA", os.path.expanduser("~"))
RUNTIME_DIR = os.path.join(APPDATA_ROOT, "KnightLogics", "DisplayControlPlus")
os.makedirs(RUNTIME_DIR, exist_ok=True)
LOG_PATH = os.path.join(RUNTIME_DIR, "overlay.log")

try:
    logging.basicConfig(filename=LOG_PATH, level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
except Exception:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")


class MonitorActivityDetector:
    # Mean grayscale pixel-change threshold (0-255) to consider a monitor "active".
    # 4.0 catches smooth video/animation; ignores cursor blinks and subtle rendering noise.
    _ACTIVITY_THRESHOLD = 4.0
    # Seconds between screen captures for Activity detection.
    _SCREEN_POLL_INTERVAL = 2.0

    def __init__(self, monitors, mode="input", scope="system", monitor_modes=None):
        self.monitors = monitors
        self.mode = mode
        self.scope = scope
        self.monitor_modes = monitor_modes or {}
        self._idle_times = {"system": 0}
        for m in monitors:
            self._idle_times[tuple(m['geometry'])] = 0
        self._last_input_time = time.time()
        self._last_activity_time = time.time()
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        self._running = True
        threading.Thread(target=self._run, daemon=True, name="monitor-idle-tracker").start()
        self._start_listeners()
        if self.mode in ("activity", "both"):
            threading.Thread(target=self._screen_poll_loop, daemon=True, name="screen-activity-poll").start()

    def _start_listeners(self):
        self._last_mouse_pos = None

        def on_mouse_move(x, y):
            if self._last_mouse_pos:
                last_x, last_y = self._last_mouse_pos
                if abs(x - last_x) < 2 and abs(y - last_y) < 2:
                    logging.debug(f"Ignored mouse move at ({x},{y}) - too small delta")
                    return
            self._last_mouse_pos = (x, y)
            with self._lock:
                self._last_input_time = time.time()
                for m in self.monitors:
                    left, top, right, bottom = m['geometry']
                    if left <= x < right and top <= y < bottom:
                        self._idle_times[tuple(m['geometry'])] = 0
                        m['last_input_time'] = time.time()
                        logging.info(f"[DIAG] Mouse move event: ({x},{y}) on monitor {tuple(m['geometry'])}. Idle time reset.")
            logging.debug(f"Mouse moved at ({x},{y})")

        def on_mouse_click(x, y, button, pressed):
            on_mouse_move(x, y)

        def on_mouse_scroll(x, y, dx, dy):
            on_mouse_move(x, y)

        def on_keyboard_event(*args, **kwargs):
            with self._lock:
                self._last_input_time = time.time()
                for m in self.monitors:
                    self._idle_times[tuple(m['geometry'])] = 0
                    m['last_input_time'] = time.time()
                    logging.info(f"[DIAG] Keyboard event detected. Idle time reset for monitor {tuple(m['geometry'])}.")
            logging.debug("Keyboard event detected")

        self._mouse_listener = mouse.Listener(
            on_move=on_mouse_move,
            on_click=on_mouse_click,
            on_scroll=on_mouse_scroll)
        self._keyboard_listener = keyboard.Listener(
            on_press=on_keyboard_event,
            on_release=on_keyboard_event)
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def _screen_poll_loop(self):
        """
        Background thread: detect per-monitor pixel changes to identify active media.

        Takes a 64x64 downsampled grayscale screenshot of each monitor every
        _SCREEN_POLL_INTERVAL seconds and compares it to the previous frame.
        If the mean pixel luminance difference exceeds _ACTIVITY_THRESHOLD the monitor
        is considered active (video/animation playing) and the activity idle timer is
        reset — preventing the overlay from triggering.

        Pausing a video stops frame changes, so the idle timer resumes normally after
        the user-configured timeout. Resuming playback resets the timer immediately.
        """
        try:
            from PIL import ImageGrab, ImageChops
        except ImportError:
            logging.warning("[ACTIVITY] Pillow not available; Activity mode cannot detect screen changes.")
            return

        SAMPLE = (64, 64)
        prev = {}

        while self._running:
            now = time.time()
            for m in self.monitors:
                geom = tuple(m['geometry'])
                left, top, right, bottom = m['geometry']
                try:
                    raw = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
                    small = raw.resize(SAMPLE).convert("L")  # grayscale — fast comparison
                    if geom in prev:
                        diff = ImageChops.difference(small, prev[geom])
                        pixel_data = list(diff.getdata())
                        mean_diff = sum(pixel_data) / len(pixel_data)
                        if mean_diff > self._ACTIVITY_THRESHOLD:
                            with self._lock:
                                self._last_activity_time = now
                                m['last_activity_time'] = now
                            logging.debug(
                                f"[ACTIVITY] Screen change on {geom}: "
                                f"mean_diff={mean_diff:.2f}, activity timer reset."
                            )
                    prev[geom] = small
                except Exception as e:
                    logging.debug(f"[ACTIVITY] Screen capture error on {geom}: {e}")
            time.sleep(self._SCREEN_POLL_INTERVAL)

    def _run(self):
        for m in self.monitors:
            m['last_input_time'] = time.time()
            m['last_activity_time'] = time.time()
        last_idle_snapshot = None
        while self._running:
            now = time.time()
            with self._lock:
                input_idle = now - self._last_input_time
                activity_idle = now - self._last_activity_time

                # System-wide idle — mode-aware
                if self.mode == "input":
                    self._idle_times["system"] = input_idle
                elif self.mode == "activity":
                    self._idle_times["system"] = activity_idle
                elif self.mode == "both":
                    self._idle_times["system"] = min(input_idle, activity_idle)

                # Per-monitor idle — mode-aware, uses per-monitor timestamps
                for m in self.monitors:
                    geom = tuple(m['geometry'])
                    m_input_idle = now - m.get('last_input_time', self._last_input_time)
                    m_activity_idle = now - m.get('last_activity_time', self._last_activity_time)
                    if self.mode == "input":
                        self._idle_times[geom] = m_input_idle
                    elif self.mode == "activity":
                        self._idle_times[geom] = m_activity_idle
                    elif self.mode == "both":
                        self._idle_times[geom] = min(m_input_idle, m_activity_idle)
                    else:
                        self._idle_times[geom] = m_input_idle

                if last_idle_snapshot:
                    for k in self._idle_times:
                        if self._idle_times[k] < last_idle_snapshot.get(k, 0):
                            logging.warning(f"[DIAG] Idle time for {k} decreased (reset). Activity detected.")
                last_idle_snapshot = self._idle_times.copy()
            time.sleep(1)

    def get_idle_times(self):
        with self._lock:
            return self._idle_times.copy()

    def stop(self):
        self._running = False
        try:
            self._mouse_listener.stop()
        except Exception:
            pass
        try:
            self._keyboard_listener.stop()
        except Exception:
            pass
