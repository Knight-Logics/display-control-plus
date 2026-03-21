# OLED Protector Default Asset
# This is a simple black PNG image for blank overlays.
from PIL import Image
import os

assets_dir = os.path.join(os.path.dirname(__file__), 'assets')
os.makedirs(assets_dir, exist_ok=True)

# Create a 1920x1080 black image
img = Image.new('RGB', (1920, 1080), color='black')
img.save(os.path.join(assets_dir, 'blank.png'))

# Create a simple white PNG image for testing
img_white = Image.new('RGB', (1920, 1080), color='white')
img_white.save(os.path.join(assets_dir, 'white.png'))

print('Default assets restored: blank.png, white.png')
