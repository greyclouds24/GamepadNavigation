# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: single-file, windowless GamepadMacropad.exe.

Build with:  python -m PyInstaller gamepad_macropad.spec --noconfirm
"""

from PyInstaller.utils.hooks import collect_dynamic_libs

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=collect_dynamic_libs("pygame"),
    datas=[],
    hiddenimports=[
        "pygame._sdl2.controller",
        "tkinter",
        "tkinter.ttk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Keep the exe small: nothing in the app needs these pygame extras.
        "pygame.mixer", "pygame.mixer_music", "pygame.font", "pygame.freetype",
        "pygame.image", "pygame.camera", "pygame.scrap", "pygame.midi", "pygame.sndarray",
        "pygame.surfarray", "pygame.pixelcopy", "pygame.gfxdraw", "pygame.transform",
        "numpy", "PIL", "unittest", "pydoc", "doctest", "email", "http", "xml",
    ],
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
    name="GamepadMacropad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
