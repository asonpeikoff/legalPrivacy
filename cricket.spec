# cricket.spec — PyInstaller build spec for noisyCricket
# Produces a single-file executable: cricket-launch (Linux/macOS) or cricket-launch.exe (Windows)
#
# Build locally:
#   pip install pyinstaller
#   pyinstaller cricket.spec
#
# Output: dist/cricket-launch  (or dist/cricket-launch.exe on Windows)

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['noisycricket/launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('data', 'data'),               # bridges, example configs
        ('profiles', 'profiles'),       # optional — omit if dir absent
    ],
    hiddenimports=[
        'noisycricket.cli',
        'noisycricket.config',
        'noisycricket.engines',
        'noisycricket.identity',
        'noisycricket.pacing',
        'noisycricket.relay',
        'noisycricket.scheduler',
        'noisycricket.transports',
        'yaml',
        'requests',
        'urllib3',
        'charset_normalizer',
        'idna',
        'certifi',
        'socks',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='cricket-launch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # terminal app — keep console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
