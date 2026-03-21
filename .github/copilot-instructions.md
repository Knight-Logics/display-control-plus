
# OLED Protector: Copilot Instructions

## Project Overview
**Purpose:** Windows desktop utility protecting OLED screens by displaying overlays during user inactivity.

**Architecture:** Multi-process Python application with GUI, background service, and system integration.

## Core Components & Data Flow

### Process Architecture
- **GUI Process** (`main.py` → `overlay.py`): Configuration interface, testing tools
- **Background Service** (`overlay_bg.py`): Continuous idle monitoring and overlay activation  
- **Single Instance Enforcement**: Lock files prevent multiple background processes

### Key Modules
- `monitor_activity.py`: Idle detection using Windows APIs + pynput hooks
- `monitor_control.py`: Windows monitor enumeration (win32api + ctypes fallback)
- `overlay.py`: Overlay rendering functions (black, image, GIF, slideshow)
- `config.json`: Persistent settings (timeout, modes, monitor selection)

### Critical Pattern: Multiprocessing Compatibility
All overlay functions MUST be top-level (not nested) for `multiprocessing.Process()`:
```python
# ✅ Correct - top-level function
def show_black_overlay(geometry, demo=False):
    # Implementation

# ❌ Wrong - nested/lambda functions fail in multiprocessing
```

## Code Cleanup Priorities

- **Remove duplicate/legacy functions:**
  - `overlay.py` may contain unreachable, partial, or duplicate function definitions (especially at the end of the file, e.g. old overlay implementations or test stubs).
  - Only one complete, correct implementation of each overlay function should remain.
  - Remove any code referencing the old `OLEDProtector` naming or legacy modules.
- **Project Renaming:**
  - The project is now named **Display Control**. Remove or ignore files and references to `OLEDProtector` (e.g. `OLEDProtector.spec`, old installer scripts, etc.).
  - Ensure all new build artifacts, installer scripts, and scheduled tasks use the `Display Control` name.

## Development Workflows

### Launch & Testing
```powershell
python overlay.py              # GUI configuration
python overlay.py --background # Background service
python main.py                 # Entry point with auto-task creation
```

### Testing Guidance
- Multi-monitor and multi-Windows-version support is a core goal. When making changes, always:
  - Test overlay activation and idle detection on all connected monitors.
  - Confirm that overlays activate and close reliably on user input.
  - Validate that only one GUI/background process runs at a time (no duplicates).
  - Use the GUI "Test Overlay" feature for quick diagnostics.

### Build & Distribution
```powershell
cd installer
./build_installer.ps1          # PyInstaller → Inno Setup → installer
./oled_tray_task.ps1           # Register Task Scheduler integration
```

#### PyInstaller & Inno Setup Details
- **PyInstaller**
  - Uses `pyinstaller.spec` (or `DisplayControl.spec`) to build a single-file executable in `/dist`.
  - The `.ico` icon is required for branding and tray integration (see `installer/convert_logo_to_ico.ps1`).
  - Use `--windowed` to avoid console windows for GUI apps.
  - Always test the built `.exe` for correct GUI and background behavior (no duplicate GUIs, correct tray/task behavior).
- **Inno Setup**
  - `OLEDProtector.iss` (should be renamed for Display Control) creates the Windows installer.
  - The installer should:
    - Create a desktop shortcut (Display Control)
    - Register a Task Scheduler task for background protection
    - Optionally prompt for tray icon auto-start
    - Ensure the GUI launches correctly from the desktop shortcut (no duplicate GUIs)
  - If the installer or shortcut does not launch the GUI, check for admin rights, working directory, and correct executable path.

## Project-Specific Patterns

### Idle Detection Architecture
`MonitorActivityDetector` provides granular control:
- **Modes**: `input` (mouse/keyboard) vs `activity` (windows/focus) vs `both`
- **Scope**: `system` (global) vs `per-monitor` (cursor-based)
- **Filtering**: 2-pixel threshold prevents synthetic mouse events

### Monitor Geometry Handling  
Uses tuples as keys: `(left, top, right, bottom)` maps to idle times and config settings.

### Task Scheduler Integration
Background persistence via Windows Task Scheduler:
- `ensure_overlay_bg_task.py` creates scheduled task on first GUI run
- Handles both `.exe` (PyInstaller) and `.py` (development) execution paths
- Uses `pythonw.exe` for background execution without console windows
- Task/shortcut names should use `Display Control` (not `OLEDProtector`).

### Configuration Management
`config.json` structure drives all behavior:
```json
{
  "scope": "system",           // "system" | "per-monitor" 
  "detection_mode": "input",   // "input" | "activity" | "both"
  "mode": "blank",             // "blank" | "image" | "gif" | "slideshow"
  "timeout": 0.1667,           // Hours until overlay activation
  "monitor_modes": {}          // Per-monitor override settings
}
```

### Logging Strategy
All components log to `overlay.log` with `[DIAG]` prefixes for troubleshooting:
```python
logging.basicConfig(filename="overlay.log", level=logging.DEBUG, 
                   format="%(asctime)s %(levelname)s %(message)s")
```

## Critical Integration Points

### Windows API Dependencies
- **pywin32**: Primary monitor enumeration and system integration
- **ctypes**: Fallback when pywin32 unavailable  
- **pynput**: Cross-platform input event capture with Windows-specific filtering

### Error Handling Pattern
Always implement graceful degradation:
```python
try:
    # Primary approach (e.g., win32api)
except Exception as e:
    logging.warning(f"Primary failed: {e}")
    # Fallback approach (e.g., ctypes)
```

## Adding New Features
1. **Config Schema**: Add options to `config.json` structure
2. **Overlay Function**: Create top-level function in `overlay.py` 
3. **GUI Integration**: Connect to configuration interface
4. **Background Logic**: Update `overlay_bg.py` to handle new mode
5. **Testing**: Use GUI "Test Overlay" before production deployment
