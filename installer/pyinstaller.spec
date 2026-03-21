# PyInstaller spec file for OLED Protector
# Generates a single-file Windows executable

block_cipher = None

from PyInstaller.utils.hooks import collect_data_files

a = Analysis([
    'main.py',
],
    pathex=['..'],
    binaries=[],
    datas=collect_data_files('PIL') + [('config.json', '.'), ('assets/*', 'assets')],
    hiddenimports=['tkinter', 'PIL', 'monitor_control', 'overlay', 'tray'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OLEDProtector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OLEDProtector'
)
