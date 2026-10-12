# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Paths, settings and access to the engine: the `mkpfs` of a venv or, when frozen with PyInstaller, the
`mkpfs.exe` next to the program."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "ps5-game-packer"
ARCHIVE_EXTENSIONS = (".rar", ".zip")
# Engine installed by install.sh (and bundled in the Windows package): the fork branch with --archive-root and --layer.
MKPFS_PIN = "mkpfs @ git+https://github.com/raiddevel/MkPFS.git@layered-source"

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC
FROZEN = bool(getattr(sys, "frozen", False))  # bundled with PyInstaller


def _resource_root() -> Path:
    """Folder holding `locale/` and `data/`: the repository, the package installed with pip or the .exe."""
    package = Path(__file__).resolve().parent
    candidates = [package.parent, package]
    if FROZEN:
        candidates.insert(0, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
        candidates.insert(1, Path(sys.executable).resolve().parent)
    for c in candidates:
        if (c / "locale").is_dir():
            return c
    return package.parent


ROOT = _resource_root()
LOCALE_DIR = ROOT / "locale"
DATA_DIR = ROOT / "data"
LISTING = Path(__file__).resolve().parent / "listing.py"


def _env_dir(variable: str, default: Path) -> Path:
    value = os.environ.get(variable, "").strip()
    return Path(value).expanduser() if value else default


if IS_WINDOWS:
    _roaming = _env_dir("APPDATA", Path.home() / "AppData" / "Roaming")
    _local = _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    CONFIG_DIR = _roaming / APP_ID
    CACHE_DIR = _local / APP_ID / "cache"
    DATA_HOME = _local
elif IS_MAC:
    CONFIG_DIR = Path.home() / "Library" / "Application Support" / APP_ID
    CACHE_DIR = Path.home() / "Library" / "Caches" / APP_ID
    DATA_HOME = Path.home() / "Library" / "Application Support"
else:
    CONFIG_DIR = _env_dir("XDG_CONFIG_HOME", Path.home() / ".config") / APP_ID
    CACHE_DIR = _env_dir("XDG_CACHE_HOME", Path.home() / ".cache") / APP_ID
    DATA_HOME = _env_dir("XDG_DATA_HOME", Path.home() / ".local/share")
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_VENV = DATA_HOME / APP_ID / "venv"


def venv_executable(venv: Path, name: str) -> Path:
    """Path of a program inside a venv (`bin/x` or `Scripts\\x.exe`)."""
    if IS_WINDOWS:
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def venv_dir() -> Path:
    """Venv with MkPFS: PS5_GAME_PACKER_VENV, the venv the program runs from, or the default one."""
    forced = os.environ.get("PS5_GAME_PACKER_VENV", "").strip()
    if forced:
        return Path(forced).expanduser()
    own = Path(sys.prefix)
    if not FROZEN and venv_executable(own, "mkpfs").exists():
        return own
    return DEFAULT_VENV


VENV = venv_dir()
if FROZEN:
    _next_to_exe = Path(sys.executable).resolve().parent
    MKPFS = _next_to_exe / ("mkpfs.exe" if IS_WINDOWS else "mkpfs")
    VENV_PY: Path | None = None
else:
    MKPFS = venv_executable(VENV, "mkpfs")
    VENV_PY = venv_executable(VENV, "python")


def _bundled_unrar() -> Path | None:
    """The UnRAR.exe shipped with the .exe: next to it (if the user drops a newer one) or in _internal/."""
    if not FROZEN:
        return None
    folders = [Path(sys.executable).resolve().parent, Path(getattr(sys, "_MEIPASS", ""))]
    for folder in folders:
        for name in ("UnRAR.exe", "unrar.exe", "unrar"):
            p = folder / name
            if str(folder) and p.is_file():
                return p
    return None


# The MkPFS library running in this process must see the bundled UnRAR.exe too.
if FROZEN and _bundled_unrar() is not None and not os.environ.get("MKPFS_UNRAR", "").strip():
    os.environ["MKPFS_UNRAR"] = str(_bundled_unrar())


def unrar_path() -> str | None:
    """The `unrar` MkPFS will use: the bundled one, MKPFS_UNRAR, or the one on PATH."""
    own = _bundled_unrar()
    if own is not None:
        return str(own)
    forced = os.environ.get("MKPFS_UNRAR", "").strip()
    if forced:
        return forced if Path(forced).is_file() else shutil.which(forced)
    for name in ("unrar", "UnRAR", "UnRAR.exe", "unrar.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def has_unrar() -> bool:
    return unrar_path() is not None


def engine_env(password: str = "") -> dict[str, str]:
    """Environment for running MkPFS: UTF-8 console, bundled unrar and the password (never on the command line)."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # unbuffered: the informational lines of MkPFS come out in order with the progress bar
    env["PYTHONUNBUFFERED"] = "1"
    unrar = _bundled_unrar()
    if unrar is not None:
        env["MKPFS_UNRAR"] = str(unrar)
    if password:
        env["MKPFS_ARCHIVE_PASSWORD"] = password
    else:
        env.pop("MKPFS_ARCHIVE_PASSWORD", None)
    return env


LOG_MAX = 2 * 1024 * 1024  # bytes of the automatic log; past that, the last half is kept


def log_path() -> Path:
    """Log of every session, next to the settings."""
    return CONFIG_DIR / "log.txt"


def open_log(path: Path, maximum: int = LOG_MAX):
    """Opens a log for appending; past `maximum` bytes only the last half is kept. None if it cannot be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > maximum:
            data = path.read_bytes()[-(maximum // 2):]
            cut = data.find(b"\n")
            path.write_bytes(data[cut + 1:] if cut >= 0 else data)
        return open(path, "a", encoding="utf-8")  # lives for the whole session
    except OSError:
        return None


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_CAPABILITIES: dict[str, set[str]] = {}


def engine_capabilities() -> set[str]:
    """What the installed MkPFS can do, read from its --help: 'archives' (--archive-root) and 'layers' (--layer)."""
    if "v" not in _CAPABILITIES:
        caps: set[str] = set()
        try:
            r = subprocess.run([str(MKPFS), "pack", "folder", "--help"], capture_output=True, text=True,
                               timeout=60, env=engine_env(), **quiet_flags())
            text = r.stdout + r.stderr
            if "--archive-root" in text:
                caps.add("archives")
            if "--layer" in text:
                caps.add("layers")
        except Exception:
            pass
        _CAPABILITIES["v"] = caps
    return _CAPABILITIES["v"]


def mkpfs_reads_archives() -> bool:
    """True if the installed MkPFS takes .rar/.zip directly (--archive-root)."""
    return "archives" in engine_capabilities()


def mkpfs_merges_layers() -> bool:
    """True if the installed MkPFS merges layers by itself (--layer)."""
    return "layers" in engine_capabilities()


def mkpfs_version() -> str:
    try:
        r = subprocess.run([str(MKPFS), "-V"], capture_output=True, text=True, timeout=60,
                           env=engine_env(), **quiet_flags())
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    lines = [ln.strip() for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
    return lines[0] if lines and len(lines[0]) < 80 else ""


def quiet_flags() -> dict:
    """On Windows, keep console subprocesses from opening a black window."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def list_archive(archive: Path, password: str) -> dict:
    """Contents of a RAR/ZIP through the MkPFS library: in this process if it can be imported, otherwise with
    listing.py inside the venv."""
    try:
        import mkpfs.archive_source

        from . import listing
        return listing.summary(archive, password)
    except ImportError:
        pass
    except Exception as e:
        return {"ok": False, "error_code": "read", "error": str(e)}
    if VENV_PY is None or not VENV_PY.exists():
        return {"ok": False, "error_code": "engine", "error": str(MKPFS)}
    env = engine_env()
    env["ARCHIVE_PASSWORD"] = password
    try:
        r = subprocess.run([str(VENV_PY), str(LISTING), str(archive)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=600, **quiet_flags())
        if r.returncode != 0 or not r.stdout.strip():
            return {"ok": False, "error_code": "read", "error": (r.stderr or r.stdout).strip()[-300:]}
        return json.loads(r.stdout)
    except Exception as e:
        return {"ok": False, "error_code": "read", "error": str(e)}


def free_space(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def fs_type(path: Path) -> str:
    """File system holding the path ('vfat', 'exfat', 'ntfs', 'apfs'...), or '' if unknown."""
    if IS_WINDOWS:
        return _fs_type_windows(path)
    if IS_MAC:
        return _fs_type_mac(path)
    return fs_type_linux(path)[0]


def fs_type_linux(path: Path) -> tuple[str, str]:
    """(FSTYPE, SOURCE) of the mount point holding the path (Linux only, with findmnt)."""
    try:
        out = subprocess.run(["findmnt", "-T", str(path), "-no", "FSTYPE,SOURCE"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        if len(out) >= 2:
            return out[0], out[1]
    except Exception:
        pass
    return "", ""


def _fs_type_windows(path: Path) -> str:
    try:
        import ctypes

        root = Path(os.path.abspath(path)).anchor  # 'D:\\' or '\\\\server\\share\\'
        if not root:
            return ""
        fs_name = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root), None, 0, None, None, None, fs_name, 64)
        return fs_name.value.lower() if ok else ""
    except Exception:
        return ""


def _fs_type_mac(path: Path) -> str:
    try:
        import re

        output = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
        target = os.path.realpath(path)
        best = ("", "")
        for m in re.finditer(r"^\S+ on (.+?) \((\w+)", output, re.MULTILINE):
            point, fs = m.group(1), m.group(2)
            if (target == point or target.startswith(point.rstrip("/") + "/")) and len(point) > len(best[0]):
                best = (point, fs)
        return best[1].lower()
    except Exception:
        return ""


def is_fat32(path: Path) -> bool:
    return fs_type(path) in ("vfat", "msdos", "fat32", "fat", "fat16")


def physical_disk(path: Path) -> str:
    """Name of the physical disk (nvme0n1, sda...) under the path, looking through ecryptfs. Linux only."""
    if not IS_LINUX:
        return ""
    current = path
    for _i in range(4):
        fs, src = fs_type_linux(current)
        if fs == "ecryptfs" and src and Path(src).exists():
            current = Path(src)
            continue
        if src.startswith("/dev/"):
            try:
                pk = subprocess.run(["lsblk", "-no", "PKNAME", src], capture_output=True, text=True, timeout=5).stdout.strip()
                return pk.splitlines()[0] if pk else Path(src).name
            except Exception:
                return Path(src).name
        return src
    return ""


def in_ecryptfs(path: Path) -> bool:
    """True if the path is inside a home folder encrypted with ecryptfs (Linux only)."""
    return IS_LINUX and fs_type_linux(path)[0] == "ecryptfs"
