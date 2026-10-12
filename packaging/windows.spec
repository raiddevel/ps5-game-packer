# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller (Windows): ps5-game-packer.exe (window, no console) and mkpfs.exe (engine) in a single folder.
Run by packaging/windows.ps1."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # SPECPATH is defined by PyInstaller
ICON = str(ROOT / "data" / "icon" / "ps5-game-packer.ico")
UNRAR = ROOT / "packaging" / "unrar"
HOOKS = [str(ROOT / "packaging" / "hooks")]

datas = [
    (str(ROOT / "locale"), "locale"),
    (str(ROOT / "data"), "data"),
    (str(ROOT / "LICENSES"), "LICENSES"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "README.es.md"), "."),
]
for name in ("UnRAR.exe", "license.txt"):
    if (UNRAR / name).is_file():
        datas.append((str(UNRAR / name), "."))
datas += collect_data_files("customtkinter")

binaries = collect_dynamic_libs("zlib_ng") + collect_dynamic_libs("isal")
engine_hidden = [m for m in collect_submodules("mkpfs") if not m.startswith("mkpfs.gui")] + [
    "pyzipper", "zlib_ng", "isal", "cryptography", "Cryptodome",
]
window_hidden = collect_submodules("customtkinter") + engine_hidden + ["PIL", "PIL.Image", "PIL.ImageTk", "psutil"]
excluded = ["gi", "fuse", "pytest", "PyInstaller", "mkpfs.gui"]

window = Analysis(
    [str(ROOT / "ps5-game-packer.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=window_hidden,
    hookspath=HOOKS,
    excludes=excluded,
    noarchive=False,
)
engine = Analysis(
    [str(ROOT / "packaging" / "mkpfs_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=[],
    hiddenimports=engine_hidden,
    hookspath=HOOKS,
    excludes=excluded + ["customtkinter"],
    noarchive=False,
)

pyz_window = PYZ(window.pure)
exe_window = EXE(
    pyz_window,
    window.scripts,
    [],
    exclude_binaries=True,
    name="ps5-game-packer",
    icon=ICON,
    console=False,
    upx=False,
)
pyz_engine = PYZ(engine.pure)
exe_engine = EXE(
    pyz_engine,
    engine.scripts,
    [],
    exclude_binaries=True,
    name="mkpfs",
    icon=ICON,
    console=True,
    upx=False,
)
COLLECT(
    exe_window,
    window.binaries,
    window.datas,
    exe_engine,
    engine.binaries,
    engine.datas,
    upx=False,
    name="ps5-game-packer",
)
