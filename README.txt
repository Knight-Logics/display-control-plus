Display Control+
================

Display Control+ is a Windows utility for multi-monitor burn-in protection and idle overlays.
It allows each display to behave differently when idle, so one monitor can keep playing content
while another monitor switches to blank/image/slideshow protection.

Why it exists
-------------
- Protect OLED/LCD panels from static-content burn-in on multi-display setups.
- Avoid forcing all displays into the same idle behavior.
- Keep settings persistent so protection runs in the background after setup.

Key Features
------------
- Per-monitor overlay behavior based on idle activity.
- System-wide or monitor-aware idle detection modes.
- Overlay modes: blank, single image, slideshow, video playlist loop.
- Background process support for always-on protection.
- Installer-based deployment for non-technical users.
- Persistent settings and runtime state in AppData for packaged builds.

Installation
------------
1. Run the generated installer (Inno Setup output).
2. Install Display Control+ and create shortcuts as needed.
3. Launch Display Control+, configure monitor behavior, then click Apply.

Usage
-----
1. Select monitors to protect.
2. Choose timeout and detection mode.
3. Choose overlay mode (blank/image/gif/slideshow).
4. Save and enable background protection.

Professional Startup Behavior
-----------------------------
- Tray app should run at user logon via the `DisplayControlBackground` scheduled task.
- Tray keeps background protection alive and provides quick controls.
- The GUI is on-demand for configuration and does not need to auto-open at startup.
- Closing the GUI does not clear saved settings; protection continues when background is active.
- Tray menu includes: Open Dashboard, Pause/Resume Protection, Exit Completely.

Typical use case
----------------
- Monitor A: watching video (active content)
- Monitor B: idle desktop/app left open
- Result: Monitor B can switch to protection overlay while Monitor A continues normally.

Build Notes
-----------
- Main app entry: main.py
- Background process entry: overlay_bg.py
- Installer script: installer/OLEDProtector.iss
- Build script: installer/build_installer.ps1

License
-------
Commercial use and redistribution prohibited without license.
Copyright (c) 2025-2026 Display Control+.
