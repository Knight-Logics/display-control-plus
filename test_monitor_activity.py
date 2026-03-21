import time
from monitor_activity import MonitorActivityDetector

# Simulate two monitors
monitors = [
    {'geometry': (0, 0, 1920, 1080)},
    {'geometry': (1920, 0, 3840, 1080)}
]

detector = MonitorActivityDetector(monitors, mode="input", scope="system")
detector.start()

print("Move mouse or press any key to reset idle time. Ctrl+C to exit.")
try:
    while True:
        idle_times = detector.get_idle_times()
        print(f"System idle: {idle_times['system']:.2f} seconds")
        for geom in idle_times:
            if geom != 'system':
                print(f"Monitor {geom} idle: {idle_times[geom]:.2f} seconds")
        time.sleep(2)
except KeyboardInterrupt:
    detector.stop()
    print("Stopped idle detector.")
