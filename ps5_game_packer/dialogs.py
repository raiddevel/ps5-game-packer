# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Folder and file pickers. On Linux the Tk one looks dated, so the desktop's own is used when there is one
(zenity or kdialog), with Tk as fallback; PS5_GAME_PACKER_DIALOGS=tk|zenity|kdialog forces one."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from tkinter import filedialog

from . import environment

TOOLS = ("zenity", "kdialog", "tk")
_open = False  # an external dialog is already on screen: do not open another one on top


def tool() -> str:
    """'zenity', 'kdialog' or 'tk'."""
    forced = os.environ.get("PS5_GAME_PACKER_DIALOGS", "").strip().lower()
    if forced in TOOLS:
        return forced if forced == "tk" or shutil.which(forced) else "tk"
    if not environment.IS_LINUX:
        return "tk"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    order = ("kdialog", "zenity") if "KDE" in desktop else ("zenity", "kdialog")
    for t in order:
        if shutil.which(t):
            return t
    return "tk"


def _initial(initial: str | None) -> str:
    return initial if initial and Path(initial).is_dir() else str(Path.home())


def _zenity_common(title: str, initial: str | None, window_id: int | None) -> list[str]:
    cmd = ["zenity", "--file-selection", f"--title={title}", "--modal", f"--filename={_initial(initial).rstrip('/')}/"]
    if window_id and os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11":  # --attach only makes sense on X11
        cmd.append(f"--attach={window_id}")
    return cmd


def folder_command(t: str, title: str, initial: str | None, window_id: int | None = None) -> list[str]:
    """Command to pick a folder with zenity or kdialog."""
    if t == "zenity":
        return [*_zenity_common(title, initial, window_id), "--directory"]
    if t == "kdialog":
        return ["kdialog", "--title", title, "--getexistingdirectory", _initial(initial)]
    raise ValueError(t)


def file_command(t: str, title: str, initial: str | None, filters: list[tuple[str, str]],
                 window_id: int | None = None) -> list[str]:
    """Command to pick a file. `filters` as in Tk: [(name, "*.rar *.zip"), ...]."""
    if t == "zenity":
        return [*_zenity_common(title, initial, window_id), *(f"--file-filter={n} | {p}" for n, p in filters)]
    if t == "kdialog":
        filter_text = "|".join(f"{n} ({p})" for n, p in filters)
        return ["kdialog", "--title", title, "--getopenfilename", _initial(initial), filter_text]
    raise ValueError(t)


def _window_id(parent) -> int | None:
    try:
        return int(parent.winfo_id())
    except Exception:
        return None


def _run(parent, cmd: list[str]) -> str | None:
    """Runs the external dialog without freezing the window. Returns the path, '' if cancelled or None if it could
    not be started."""
    global _open
    if _open:
        return ""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    _open = True
    try:
        while proc.poll() is None:
            try:
                parent.update()
            except Exception:
                pass
            time.sleep(0.05)
        output = (proc.stdout.read() if proc.stdout else "").strip()
    finally:
        _open = False
        if proc.stdout:
            proc.stdout.close()
    if proc.returncode != 0 or not output:
        return ""
    return output.splitlines()[0]


def choose_folder(parent, title: str, initial: str | None) -> str:
    """Path of the chosen folder, or '' if cancelled."""
    t = tool()
    if t != "tk":
        r = _run(parent, folder_command(t, title, initial, _window_id(parent)))
        if r is not None:
            return r
    return filedialog.askdirectory(parent=parent, title=title, initialdir=initial or None, mustexist=True) or ""


def choose_file(parent, title: str, initial: str | None, filters: list[tuple[str, str]]) -> str:
    """Path of the chosen file, or '' if cancelled."""
    t = tool()
    if t != "tk":
        r = _run(parent, file_command(t, title, initial, filters, _window_id(parent)))
        if r is not None:
            return r
    return filedialog.askopenfilename(parent=parent, title=title, initialdir=initial or None, filetypes=filters) or ""
