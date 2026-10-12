"""Folder/file pickers: choice of tool (zenity, kdialog or Tk) and the commands that get built."""
from __future__ import annotations

import shutil
import sys

import pytest

from ps5_game_packer import dialogs, environment

FILTERS = [("RAR and ZIP archives", "*.rar *.RAR *.zip *.ZIP *.r00"), ("All files", "*")]


class _Window:
    def __init__(self) -> None:
        self.rounds = 0

    def update(self) -> None:
        self.rounds += 1

    def winfo_id(self) -> int:
        return 4242


def test_tool(monkeypatch):
    monkeypatch.delenv("PS5_GAME_PACKER_DIALOGS", raising=False)
    monkeypatch.setattr(environment, "IS_LINUX", False)
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    assert dialogs.tool() == "tk"  # Windows and macOS: always Tk
    monkeypatch.setattr(environment, "IS_LINUX", True)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    assert dialogs.tool() == "zenity"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert dialogs.tool() == "kdialog"
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/zenity" if n == "zenity" else None)
    assert dialogs.tool() == "zenity"  # KDE without kdialog: zenity
    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert dialogs.tool() == "tk"
    monkeypatch.setenv("PS5_GAME_PACKER_DIALOGS", "zenity")
    assert dialogs.tool() == "tk"  # forced but not installed
    monkeypatch.setenv("PS5_GAME_PACKER_DIALOGS", "tk")
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    assert dialogs.tool() == "tk"


def test_zenity_commands(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    cmd = dialogs.folder_command("zenity", "Choose the folder", str(tmp_path), 4242)
    assert cmd[:2] == ["zenity", "--file-selection"] and "--directory" in cmd and "--modal" in cmd
    assert "--title=Choose the folder" in cmd and f"--filename={tmp_path}/" in cmd and "--attach=4242" in cmd
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    cmd = dialogs.file_command("zenity", "Choose the archive", str(tmp_path), FILTERS, 4242)
    assert "--directory" not in cmd and not any(a.startswith("--attach") for a in cmd)
    assert "--file-filter=RAR and ZIP archives | *.rar *.RAR *.zip *.ZIP *.r00" in cmd
    assert "--file-filter=All files | *" in cmd
    # initial folder that does not exist: the home folder
    cmd = dialogs.folder_command("zenity", "x", str(tmp_path / "does-not-exist"))
    assert any(a.startswith("--filename=") and a.endswith("/") and "does-not-exist" not in a for a in cmd)


def test_kdialog_commands(tmp_path):
    assert dialogs.folder_command("kdialog", "Choose", str(tmp_path)) == ["kdialog", "--title", "Choose", "--getexistingdirectory", str(tmp_path)]
    cmd = dialogs.file_command("kdialog", "Choose", str(tmp_path), FILTERS)
    assert cmd == ["kdialog", "--title", "Choose", "--getopenfilename", str(tmp_path),
                   "RAR and ZIP archives (*.rar *.RAR *.zip *.ZIP *.r00)|All files (*)"]
    with pytest.raises(ValueError):
        dialogs.folder_command("tk", "x", None)


def test_run(tmp_path):
    w = _Window()
    chosen = str(tmp_path / "Chosen game")
    assert dialogs._run(w, [sys.executable, "-c", f"import time; time.sleep(0.3); print({chosen!r})"]) == chosen
    assert w.rounds >= 3  # the window kept updating meanwhile
    assert dialogs._run(w, [sys.executable, "-c", "import sys; sys.exit(1)"]) == ""  # cancelled
    assert dialogs._run(w, [str(tmp_path / "no-such-program")]) is None  # could not start → Tk


def test_choose(monkeypatch, tmp_path):
    w = _Window()
    calls: list[list[str]] = []
    monkeypatch.setattr(dialogs, "tool", lambda: "zenity")
    monkeypatch.setattr(dialogs, "_run", lambda parent, cmd: calls.append(cmd) or str(tmp_path))
    assert dialogs.choose_folder(w, "T", str(tmp_path)) == str(tmp_path)
    assert dialogs.choose_file(w, "T", str(tmp_path), FILTERS) == str(tmp_path)
    assert calls[0][0] == "zenity" and "--directory" in calls[0]
    assert calls[1][0] == "zenity" and "--directory" not in calls[1]
    # if the external one cannot start, Tk is used
    monkeypatch.setattr(dialogs, "_run", lambda parent, cmd: None)
    monkeypatch.setattr(dialogs.filedialog, "askdirectory", lambda **kw: str(tmp_path / "tk"))
    assert dialogs.choose_folder(w, "T", None) == str(tmp_path / "tk")
