import os
import shutil

def copy_file(src, dst_folder):
    if os.path.exists(src):
        shutil.copy2(src, dst_folder)
        print(f"Copied {src} to {dst_folder}")
    else:
        print(f"File not found: {src}")

def copy_folder(src_folder, dst_folder):
    dst_path = os.path.join(dst_folder, os.path.basename(src_folder))
    if os.path.exists(src_folder):
        if os.path.exists(dst_path):
            shutil.rmtree(dst_path)
        shutil.copytree(src_folder, dst_path)
        print(f"Copied folder {src_folder} to {dst_path}")
    else:
        print(f"Folder not found: {src_folder}")

# Paths
base = os.path.abspath(os.path.dirname(__file__))
dist = os.path.join(base, "dist")
downloads = os.path.join(os.path.expanduser("~"), "Downloads")

# Copy main app executable
exe_path = os.path.join(dist, "DisplayControl.exe")
copy_file(exe_path, downloads)

# Copy background executable
bg_exe_path = os.path.join(dist, "overlay_bg.exe")
copy_file(bg_exe_path, downloads)

# Copy config.json
config_path = os.path.join(base, "config.json")
copy_file(config_path, downloads)

# Copy assets folder when present
assets_path = os.path.join(base, "assets")
copy_folder(assets_path, downloads)

print("All files copied to Downloads. Ready for transfer to a clean machine.")
