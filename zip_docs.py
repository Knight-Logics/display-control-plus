import os
import zipfile
import shutil

# Get workspace and Downloads folder paths
workspace_dir = os.path.dirname(os.path.abspath(__file__))
downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
zip_name = "OLED_Protector_Full.zip"
zip_path = os.path.join(downloads_dir, zip_name)

def zip_workspace(src_dir, zip_file):
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, src_dir)
                zipf.write(abs_path, rel_path)

if __name__ == "__main__":
    zip_workspace(workspace_dir, zip_path)
    print(f"Workspace zipped to: {zip_path}")