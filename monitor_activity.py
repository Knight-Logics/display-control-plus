import logging
import time
import threading
from pynput import mouse, keyboard

logging.basicConfig(filename="overlay.log", level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

class MonitorActivityDetector:
    def __init__(self, monitors, mode="input", scope="system", monitor_modes=None):
        self.monitors = monitors
        self.mode = mode
        self.scope = scope
        self.monitor_modes = monitor_modes or {}
        self._idle_times = {"system": 0}
        for i, m in enumerate(monitors):
            self._idle_times[tuple(m['geometry'])] = 0
        self._last_input_time = time.time()
        self._last_activity_time = time.time()
        self._running = False
        self._lock = threading.Lock()
    def start(self):
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()
        self._start_listeners()
    def _start_listeners(self):
        # Track last mouse position to filter synthetic/background events
        self._last_mouse_pos = None
        def on_mouse_move(x, y):
            # Only reset idle if mouse position changes by at least 2 pixels
            if self._last_mouse_pos:
                last_x, last_y = self._last_mouse_pos
                if abs(x - last_x) < 2 and abs(y - last_y) < 2:
                    # Ignore synthetic/background event
                    logging.debug(f"Ignored mouse move at ({x},{y}) - too small delta")
                    return
            self._last_mouse_pos = (x, y)
            with self._lock:
                self._last_input_time = time.time()
                # Per-monitor idle: update only the monitor under the cursor
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
                # Keyboard events: reset all monitors (cannot determine which monitor)
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
    def _run(self):
        # Initialize last_input_time for each monitor
        for m in self.monitors:
            m['last_input_time'] = time.time()
        event_count = 0
        last_idle_snapshot = None
        while self._running:
            now = time.time()
            with self._lock:
                input_idle = now - self._last_input_time
                activity_idle = now - self._last_activity_time
                # System-wide idle
                if self.mode == "input":
                    self._idle_times["system"] = input_idle
                elif self.mode == "activity":
                    self._idle_times["system"] = activity_idle
                elif self.mode == "both":
                    self._idle_times["system"] = min(input_idle, activity_idle)
                # Per-monitor idle: calculate for each monitor
                for m in self.monitors:
                    geom = tuple(m['geometry'])
                    self._idle_times[geom] = now - m.get('last_input_time', self._last_input_time)
                # Log current idle times for debugging
                logging.info(f"[DIAG] Idle times snapshot: {self._idle_times}")
                # If idle times are not incrementing, log a warning
                if last_idle_snapshot:
                    for k in self._idle_times:
                        if self._idle_times[k] < last_idle_snapshot.get(k, 0):
                            logging.warning(f"[DIAG] Idle time for {k} decreased (reset). Possible sensitivity issue or event detected.")
                last_idle_snapshot = self._idle_times.copy()
            time.sleep(1)
    def get_idle_times(self):
        with self._lock:
            return self._idle_times.copy()
    def stop(self):
        self._running = False
        self._mouse_listener.stop()
        self._keyboard_listener.stop()
