# Display Control+ — Recent Fixes Applied

## Issues Fixed Today

### 1. **Empty Default Settings** ✅
- **Problem:** App was auto-creating "Setting 1" on startup even when no settings were configured
- **Solution:** Removed legacy config migration from both GUI and background process
- **Result:** App now starts with empty `setting_groups = []` (no default settings applied)

### 2. **Dark Blue Radio Button Spacing** ✅  
- **Problem:** Dark blue rectangular focus rings were appearing around radio button groups
- **Solution:** 
  - Removed `ttk.Radiobutton` styling map that applied focus effects
  - Converted all radio buttons from `ttk.Radiobutton` to `tk.Radiobutton`
  - Added explicit styling with `highlightthickness=0` to eliminate focus rings
  - Manually set colors: `bg="#161a22"`, `fg="#d4dde9"`, `activebackground="#161a22"`, `selectcolor="#30c18d"`
- **Result:** Clean, flat radiobutton appearance with no visual artifacts

### 3. **Settings Not Triggering Overlays** ✅
- **Problem:** Settings applied in GUI didn't actually spawn overlays when idle timeout reached
- **Root Cause:** Background process only scheduled via Task Scheduler (runs on next logon, not immediately)
- **Solution:**
  - Added immediate background process spawn in `apply_settings()` function
  - Background process now launches immediately, doesn't wait for task scheduler
  - Removed background process's legacy config migration
  - Ensured each setting group has `"enabled": true` field
- **Result:** Overlays now trigger within 10 seconds of idle timeout (or configured value)

### 4. **Task Scheduler / Background Process Safety** ✅
- **Added Check:** `if not is_background_running()` before spawning new process
- **Result:** Prevents multiple background instances from running simultaneously

## How to Test

### Test 1: Verify No Default Settings
1. Delete `config.json` to start fresh
2. Launch the app
3. Check "Applied Settings" list — should be **empty**
4. ✅ Expected: No settings applied by default

### Test 2: Verify Radiobutton Styling  
1. Look at "Overlay mode" section (Blank, Single Image, Slideshow, Video)
2. Look at "Detection scope" section (System-wide, Per-monitor)
3. Look at "Detection mode" section (Input, Activity, Both)
4. ✅ Expected: Clean round radio buttons with no dark blue boxes around them

### Test 3: Verify Settings Apply Immediately
1. Select **Display 1** (click on canvas)
2. Set **Overlay mode** to "Blank"
3. Set **Idle timeout** to "10 sec"
4. Check **"Enable background protection"** (should already be checked)
5. Click **Apply**
6. ✅ Expected: "Setting 1" appears in "Applied Settings" list
7. ✅ Expected: Background process starts immediately (should see in Task Manager)
8. Wait idle for 10 seconds (no keyboard/mouse input)
9. ✅ Expected: Black overlay appears on Display 1

### Test 4: Verify Settings Persistence
1. Close and reopen the app
2. ✅ Expected: "Setting 1" still appears in "Applied Settings" list
3. ✅ Expected: Same overlay mode/timeout settings are preserved

### Test 5: Verify Delete Works
1. Hover over "Setting 1" in Applied Settings
2. Click the red **X** button
3. ✅ Expected: Setting is removed immediately from list and config.json

## Files Modified
- `overlay.py` — All changes applied

## Next Steps if Issues Persist
- Check `config.json` to ensure `setting_groups` is populated correctly
- Add logging: `python overlay.py --background` in terminal to see background process debug output
- Verify `overlay_bg.lock` file is created (indicates background process is running)
- Check Windows Task Scheduler for "DisplayControlBackground" task (should exist after first apply)
