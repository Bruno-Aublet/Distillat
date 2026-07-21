# -*- mode: python ; coding: utf-8 -*-
"""Configuration PyInstaller (mode one-dir) pour générer l'exécutable Distillat."""

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('LICENSE', '.'), ('CHANGELOG.md', '.'), ('icons/open-book_4681875.png', 'icons'), ('locales', 'locales')]
    + collect_data_files('tzdata'),
    hiddenimports=[
        'google.genai',
        'ebooklib',
        'bs4',
        'reportlab',
        'pypdf',
        'pypdfium2',
        'keyring.backends.Windows',
        'send2trash.win',
        'tzdata',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Distillat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icons/distillat.ico',
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Distillat',
)
