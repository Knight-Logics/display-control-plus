import logging
import ctypes
import win32api
import win32con
import win32gui

logging.basicConfig(filename="overlay.log", level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

def get_monitors():
    monitors = []
    try:
        import win32api
        import win32con
        import win32gui
        def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            left, top, right, bottom = win32gui.GetMonitorInfo(hMonitor)['Monitor']
            idx = len(monitors)
            monitor_info = {'geometry': (left, top, right, bottom), 'index': idx}
            monitors.append(monitor_info)
            logging.info(f"Detected monitor {idx}: geometry=({left},{top},{right},{bottom})")
            return True
        # Try win32api fallback
        try:
            raw_monitors = win32api.EnumDisplayMonitors()
            for idx, (handle, hdc, rect) in enumerate(raw_monitors):
                left, top, right, bottom = rect
                monitor_info = {'geometry': (left, top, right, bottom), 'index': idx}
                monitors.append(monitor_info)
                logging.info(f"Detected monitor {idx}: geometry=({left},{top},{right},{bottom})")
        except Exception as e:
            logging.error(f"win32api.EnumDisplayMonitors failed: {e}")
    except Exception as e:
        logging.warning(f"pywin32 not available, using ctypes fallback: {e}")
        # ctypes fallback for monitor enumeration
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        CMonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_long * 4), ctypes.c_double)
        def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            rect = lprcMonitor.contents
            left, top, right, bottom = rect[0], rect[1], rect[2], rect[3]
            idx = len(monitors)
            monitor_info = {'geometry': (left, top, right, bottom), 'index': idx}
            monitors.append(monitor_info)
            logging.info(f"Detected monitor {idx}: geometry=({left},{top},{right},{bottom})")
            return 1
        enum_proc = CMonitorEnumProc(monitor_enum_proc)
        user32.EnumDisplayMonitors(0, 0, enum_proc, 0)
    logging.info(f"Enumerated monitors: {monitors}")
    if not monitors:
        logging.warning("No monitors detected!")
    return monitors

def set_monitor_power(state):
    # state: 'on', 'off', 'standby'
    HWND_BROADCAST = 0xFFFF
    WM_SYSCOMMAND = 0x0112
    SC_MONITORPOWER = 0xF170
    if state == 'off':
        win32gui.SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
    elif state == 'standby':
        win32gui.SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 1)
    elif state == 'on':
        win32gui.SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, -1)
    logging.info(f"Set monitor power: {state}")

# Add more hardware control functions as needed
