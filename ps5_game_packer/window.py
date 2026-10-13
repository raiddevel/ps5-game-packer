# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Main window (CustomTkinter). All the work is done by MkPFS as a subprocess (engine.py)."""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from . import __version__, analysis, dialogs, engine, environment
from .analysis import (
    LAYER_ROLES,
    NAME_MODE_ID,
    NAME_MODE_TITLE,
    NAME_MODES,
    PARAM_OK,
    ROLE_BASE,
    ROLE_LAYER,
    archive_stem,
    container_name,
    final_eboot,
    fw_text,
    gb,
    is_archive,
    job_param_state,
    layer_warnings,
    param_layer,
    param_warning,
    role_name,
    thousands,
    time_left,
)
from .i18n import _, language

PROGRESS_RE = re.compile(r"\[(?P<bar>[#-]{4,})\]\s*(?P<pct>\d{1,3})%\s*(?P<label>.*)")
FINAL_SIZE_RE = re.compile(r"Final image size:\s+([\d.,]+\s*[KMGT]?B)")
ELAPSED_RE = re.compile(r"Elapsed time:\s+([\d.]+)s")
ETA_RE = re.compile(r"\s*ETA\s+(\d+(?:\.\d+)?)([sm])\b")  # MkPFS: 'ETA 1234s' or, past the hour, 'ETA 75.3m'
LANGUAGE_CHOICES = ["auto", "en", "es"]
COLOR_ERROR = "#c0392b"
COLOR_OK = "#1e8449"
COLOR_WARNING = "#b9770e"
COLOR_DIM = "#7f8c8d"
TEXT_WIDTH = 760
CONTAINER_RATIO = 0.75  # real containers end up between 60 % and 75 %: below that, warn
LOG_MAX_LINES = 4000  # the "Details" log is trimmed so it does not grow without limit in long batches
LOW_SPACE = 200 * 1024 * 1024  # after a failure, less free space than this is taken as the cause


def app_name() -> str:
    return _("PS5 Game Packer")


def phases() -> dict[str, str]:
    """Translation of the phases MkPFS prints in its progress bar."""
    return {
        "compress": _("compressing"),
        "write": _("writing"),
        "read": _("reading"),
        "verify": _("verifying"),
        "scan": _("scanning"),
        "copy": _("copying"),
        "exfat": _("packing"),
    }


def notify(title: str, text: str) -> None:
    try:
        if environment.IS_LINUX and shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", app_name(), title, text])
        elif environment.IS_MAC and shutil.which("osascript"):
            script = f'display notification "{text.replace(chr(34), " ")}" with title "{title.replace(chr(34), " ")}"'
            subprocess.Popen(["osascript", "-e", script])
    except Exception:
        pass


def strip_tags(text: str) -> str:
    """Removes the <tt> marks some texts carry."""
    return re.sub(r"</?tt>", "", text)


class Tooltip:
    """Pop-up help text for a widget."""

    def __init__(self, widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _e=None) -> None:
        if self.window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self.window = tk.Toplevel(self.widget)
            self.window.wm_overrideredirect(True)
            self.window.wm_geometry(f"+{x}+{y}")
            tk.Label(self.window, text=self.text, justify="left", background="#fffbe6", foreground="#222",
                     relief="solid", borderwidth=1, wraplength=420, padx=6, pady=3).pack()
        except tk.TclError:
            self.window = None

    def _hide(self, _e=None) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


class Window(ctk.CTk):
    def __init__(self, auto: dict | None = None) -> None:
        super().__init__()
        self.title(app_name())
        self.geometry("900x780")
        self.minsize(780, 620)
        self.settings = environment.load_config()
        self.games: list[dict] = []
        self.checks: list[tuple[ctk.CTkCheckBox, dict]] = []
        self.process: subprocess.Popen | None = None
        self.pause: engine.Pause | None = None  # pause control of the running process
        self.paused = False  # the user pressed "Pause" (it carries over to the next game of the batch)
        self.thread: threading.Thread | None = None
        self.cancelled = False
        self.running = False
        self.quit_when_done = bool(auto and auto.get("quit"))
        self._scan_id = 0
        self._source_timer: str | None = None
        self._scanned_password = ""  # the general password used by the last scan
        self._loading = True
        self._scanning = False
        self.exit_code = 0
        # file logs: the automatic one and, if asked for, the --log one
        self.logs: list = []
        auto_log = environment.open_log(environment.log_path())
        if auto_log is not None:
            self.logs.append(auto_log)
        # MkPFS writes one progress line per block: only the last one is kept and repainted every 50 ms
        self._last_progress: tuple[float, str] | None = None
        self._progress_pending = False
        if auto and auto.get("log"):
            try:
                self.logs.append(open(auto["log"], "a", encoding="utf-8"))  # lives for the whole session
            except OSError:
                pass
        self._to_logs(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} · ps5-game-packer {__version__} · {sys.platform} ===")
        self._queue: queue.Queue = queue.Queue()
        self.archive_info: dict = {}
        # per-archive passwords (path → password), memory only
        self.passwords: dict[str, str] = {}
        self.pending: list[dict] = []  # encrypted archives missing their password (or with a wrong one)
        self._password_cards: dict[str, ctk.CTkEntry] = {}
        self._scan_header = ""  # first line of the scan, to repaint the description
        # temporary layers from "Add param.json…", deleted when the batch ends
        self._pending_temporaries: list[dict] = []
        # what survives a rescan of the same folder: the user's ticks and the added param.json layers, by job key
        self._manual_ticks: dict[str, bool] = {}
        self._kept_params: dict[str, dict] = {}
        self._password_focus: str | None = None
        self._scanned_text = ""
        self.bold_font = ctk.CTkFont(weight="bold")
        self.small_font = ctk.CTkFont(size=11)
        self.title_font = ctk.CTkFont(size=18, weight="bold")
        self.mono_font = ctk.CTkFont(family="Courier" if environment.IS_WINDOWS else "monospace", size=11)

        self._build()
        self._load_values(auto)
        self._loading = False
        self._set_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._pump_id: str | None = self.after(50, self._pump)

    def _on_ui(self, fn, *args) -> None:
        """Runs `fn(*args)` on the interface thread (from any thread)."""
        self._queue.put((fn, args))

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self._queue.get_nowait()
                try:
                    fn(*args)
                except Exception as e:  # never take the loop down because of a painting error
                    if sys.stderr is not None:  # no console (Windows .exe) means no stderr
                        print(f"[ui] {fn.__name__}: {e}", file=sys.stderr)
        except queue.Empty:
            pass
        try:
            self._pump_id = self.after(50, self._pump)
        except tk.TclError:
            self._pump_id = None

    def _set_icon(self) -> None:
        folder = environment.DATA_DIR / "icon"
        ico = folder / "ps5-game-packer.ico"
        png = folder / "ps5-game-packer-128.png"
        try:
            if environment.IS_WINDOWS and ico.is_file():
                # CustomTkinter sets its own icon 200 ms after creating the window: ours goes after that.
                self.after(350, lambda: self.iconbitmap(str(ico)))
            elif png.is_file():
                self._icon = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon)
        except Exception:
            pass

    @staticmethod
    def _set_text(entry: ctk.CTkEntry, text: str) -> None:
        entry.delete(0, "end")
        entry.insert(0, text)

    @staticmethod
    def _get_text(entry: ctk.CTkEntry) -> str:
        return entry.get().strip()

    def _label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, anchor="e", text_color=COLOR_DIM)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 2))
        ctk.CTkLabel(header, text=app_name(), font=self.title_font, anchor="w").pack(anchor="w")
        ctk.CTkLabel(header, text=_("game folder or RAR/ZIP (+ update + DLC) → .ffpfsc container for ShadowMountPlus"),
                     font=self.small_font, text_color=COLOR_DIM, anchor="w").pack(anchor="w")

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew", padx=14, pady=4)
        grid.grid_columnconfigure(1, weight=1)

        # Source
        self._label(grid, _("Game:")).grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
        self.e_source = ctk.CTkEntry(grid, placeholder_text=_(
            "Game folder, a .rar/.zip, or a folder holding the game + its updates/DLC (folders or archives)"))
        self.e_source.grid(row=0, column=1, sticky="ew", pady=4)
        self.e_source.bind("<KeyRelease>", self._source_typed)
        self.e_source.bind("<Return>", lambda _e: self._source_changed())
        source_buttons = ctk.CTkFrame(grid, fg_color="transparent")
        source_buttons.grid(row=0, column=2, sticky="w", padx=(6, 0))
        ctk.CTkButton(source_buttons, text=_("Folder…"), width=90,
                      command=lambda: self._choose_folder(self.e_source, _("Choose the game folder"))).pack(side="left", padx=(0, 4))
        ctk.CTkButton(source_buttons, text=_("RAR/ZIP…"), width=90, command=self._choose_file).pack(side="left", padx=(0, 4))
        b_refresh = ctk.CTkButton(source_buttons, text=_("Refresh"), width=90, command=self._refresh_source,
                                  fg_color="gray40", hover_color="gray30")
        b_refresh.pack(side="left")
        Tooltip(b_refresh, _("Scans the current path again without changing it (e.g. after extracting or adding files to the folder)"))

        # Password (archives only)
        self.l_password = self._label(grid, _("Password:"))
        self.e_password = ctk.CTkEntry(grid, show="•", placeholder_text=_("Only if the archive asks for one (in batches it applies to every archive; each card can have its own)"))
        self.e_password.bind("<Return>", lambda _e: self._rescan_for_password())
        self.e_password.bind("<FocusOut>", lambda _e: self._rescan_for_password())
        self.c_show_password = ctk.CTkCheckBox(grid, text=_("Show"), command=self._toggle_show_password)
        self.l_password.grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        self.e_password.grid(row=1, column=1, sticky="ew", pady=4)
        self.c_show_password.grid(row=1, column=2, sticky="w", padx=(6, 0))
        self._show_password_field(False)

        # Scan
        self.scan_frame = ctk.CTkFrame(grid)
        self.scan_frame.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        self.scan_frame.grid_columnconfigure(0, weight=1)
        self.l_scan = ctk.CTkLabel(self.scan_frame, text=_("Choose a folder or an archive to scan it."),
                                   anchor="w", justify="left", wraplength=TEXT_WIDTH)
        self.l_scan.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        # the list of games grows with its contents up to a share of the screen, then it scrolls
        self.scan_box = ctk.CTkScrollableFrame(self.scan_frame, fg_color="transparent", height=1)
        self.scan_box.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))
        self.scan_box.grid_columnconfigure(0, weight=1)

        # Destination
        self._label(grid, _("Save to:")).grid(row=3, column=0, sticky="e", padx=(0, 8), pady=4)
        self.e_destination = ctk.CTkEntry(grid, placeholder_text=_("Folder for the .ffpfsc (e.g. the PS5 USB drive)"))
        self.e_destination.grid(row=3, column=1, sticky="ew", pady=4)
        self.e_destination.bind("<KeyRelease>", lambda _e: self._update_warnings())
        ctk.CTkButton(grid, text=_("Choose…"), width=90,
                      command=lambda: self._choose_folder(self.e_destination, _("Choose where to save the container"))).grid(row=3, column=2, sticky="w", padx=(6, 0))
        self.l_destination = ctk.CTkLabel(grid, text="", anchor="w", justify="left", wraplength=TEXT_WIDTH, font=self.small_font)
        self.l_destination.grid(row=4, column=1, columnspan=2, sticky="ew")

        # Advanced
        self.b_advanced = ctk.CTkButton(self, text="▸ " + _("Advanced options"), fg_color="transparent", anchor="w",
                                        text_color=("gray20", "gray80"), hover=False, command=self._toggle_advanced)
        self.b_advanced.grid(row=2, column=0, sticky="w", padx=14)
        self.advanced = ctk.CTkFrame(self)
        self.advanced.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 4))
        self.advanced.grid_columnconfigure(2, weight=1)
        self.advanced.grid_remove()
        self._advanced_visible = False

        self._label(self.advanced, _("Compression level:")).grid(row=0, column=0, sticky="e", padx=8, pady=4)
        self.s_level = ctk.CTkOptionMenu(self.advanced, values=[str(n) for n in range(1, 10)], width=70)
        self.s_level.set("7")
        self.s_level.grid(row=0, column=1, sticky="w", pady=4)
        ctk.CTkLabel(self.advanced, text=_("7 recommended · 4 faster and a bit larger · 9 maximum and slow"), anchor="w",
                     font=self.small_font).grid(row=0, column=2, sticky="w", padx=8)

        self._label(self.advanced, _("CPU cores:")).grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.s_cpu = ctk.CTkOptionMenu(self.advanced, values=[str(n) for n in range(0, (os.cpu_count() or 8) + 1)], width=70)
        self.s_cpu.set("0")
        self.s_cpu.grid(row=1, column=1, sticky="w", pady=4)
        ctk.CTkLabel(self.advanced, text=_("0 = automatic (all but one)"), anchor="w", font=self.small_font).grid(row=1, column=2, sticky="w", padx=8)

        self.c_verify = ctk.CTkCheckBox(self.advanced, text=_("Verify the image when done (slow: reads it all again)"))
        self.c_verify.grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        self.c_two_pass = ctk.CTkCheckBox(self.advanced, text=_(
            "Legacy two-pass method (temporary PFS image + compression). Only if a game does not mount with the normal method"),
            command=self._update_warnings)
        self.c_two_pass.grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=4)

        self._label(self.advanced, _("Temporary folder:")).grid(row=4, column=0, sticky="e", padx=8, pady=4)
        self.e_temp = ctk.CTkEntry(self.advanced, placeholder_text=_("Empty = the destination folder. Only used by the two-pass method"))
        self.e_temp.grid(row=4, column=1, columnspan=2, sticky="ew", pady=4)
        ctk.CTkButton(self.advanced, text=_("Choose…"), width=90,
                      command=lambda: self._choose_folder(self.e_temp, _("Choose the temporary folder"))).grid(row=4, column=3, sticky="w", padx=8)

        self._label(self.advanced, _("Container name:")).grid(row=5, column=0, sticky="e", padx=8, pady=4)
        self.name_mode_labels = [_("Title [ID]"), _("ID only")]
        self.c_name = ctk.CTkOptionMenu(self.advanced, values=self.name_mode_labels, width=220, command=lambda _n: self._update_warnings())
        self.c_name.grid(row=5, column=1, sticky="w", pady=4)
        ctk.CTkLabel(self.advanced, text=_('"Title [ID].ffpfsc" with the title from param.json, or just "ID.ffpfsc". '
                                           "ShadowMountPlus does not care about the name: it reads the title from inside."),
                     anchor="w", justify="left", wraplength=520, font=self.small_font).grid(row=5, column=2, columnspan=2, sticky="w", padx=8)

        self._label(self.advanced, _("Language:")).grid(row=6, column=0, sticky="e", padx=8, pady=4)
        self.language_labels = [_("Automatic (system language)"), "English", "Español"]
        self.c_language = ctk.CTkOptionMenu(self.advanced, values=self.language_labels, command=self._language_changed, width=220)
        self.c_language.grid(row=6, column=1, sticky="w", pady=4)
        ctk.CTkLabel(self.advanced, text=_("The change applies when the program restarts"), anchor="w", font=self.small_font).grid(row=6, column=2, sticky="w", padx=8)

        # Warnings
        self.l_warning = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=TEXT_WIDTH + 80, text_color=COLOR_WARNING)
        self.l_warning.grid(row=4, column=0, sticky="ew", padx=14)

        # Buttons + progress
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=5, column=0, sticky="ew", padx=14, pady=4)
        row.grid_columnconfigure(3, weight=1)
        self.b_pack = ctk.CTkButton(row, text=_("Pack"), command=self._on_pack, width=120, font=self.bold_font)
        self.b_pack.grid(row=0, column=0, padx=(0, 8))
        self.b_cancel = ctk.CTkButton(row, text=_("Cancel"), command=self._on_cancel, width=100, state="disabled",
                                      fg_color="gray40", hover_color="gray30")
        self.b_cancel.grid(row=0, column=1, padx=(0, 8))
        self.b_pause = ctk.CTkButton(row, text=_("Pause"), command=self._on_pause, width=100, state="disabled",
                                     fg_color="gray40", hover_color="gray30")
        self.b_pause.grid(row=0, column=2, padx=(0, 8))
        Tooltip(self.b_pause, _("Stops MkPFS without cancelling, and resumes it; the time left freezes meanwhile"))
        self.l_status = ctk.CTkLabel(row, text=_("Ready."), anchor="w")
        self.l_status.grid(row=0, column=3, sticky="ew")
        self.bar = ctk.CTkProgressBar(row)
        self.bar.set(0)
        self.bar.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self.l_bar = ctk.CTkLabel(row, text="", anchor="w", font=self.small_font)
        self.l_bar.grid(row=2, column=0, columnspan=4, sticky="ew")

        # Log
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=6, column=0, sticky="nsew", padx=14, pady=(2, 12))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(log_frame, text=_("Details"), anchor="w", text_color=COLOR_DIM).grid(row=0, column=0, sticky="w", padx=8, pady=(4, 0))
        self.log_box = ctk.CTkTextbox(log_frame, wrap="word", font=self.mono_font)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        for name, color in (("error", COLOR_ERROR), ("ok", COLOR_OK), ("warning", COLOR_WARNING), ("dim", COLOR_DIM)):
            self.log_box.tag_config(name, foreground=color)
        self.log_box.configure(state="disabled")

    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced.grid()
            self.b_advanced.configure(text="▾ " + _("Advanced options"))
        else:
            self.advanced.grid_remove()
            self.b_advanced.configure(text="▸ " + _("Advanced options"))

    def _toggle_show_password(self) -> None:
        self.e_password.configure(show="" if self.c_show_password.get() else "•")

    def _load_values(self, auto: dict | None) -> None:
        s = self.settings
        self._set_text(self.e_destination, s.get("destination", ""))
        self._set_text(self.e_temp, s.get("temp_dir", ""))
        self.s_level.set(str(min(9, max(1, int(s.get("level", 7))))))
        self.s_cpu.set(str(max(0, int(s.get("cpu", 0)))))
        # "Verify the image" is not remembered between sessions
        self.c_verify.deselect()
        mode = s.get("container_name", NAME_MODE_TITLE)
        self.c_name.set(self.name_mode_labels[NAME_MODES.index(mode) if mode in NAME_MODES else 0])
        pref = s.get("language", "auto")
        self.c_language.set(self.language_labels[LANGUAGE_CHOICES.index(pref) if pref in LANGUAGE_CHOICES else 0])
        self._log(_("PS5 Game Packer {version} · engine: {engine}").format(
            version=__version__, engine=environment.mkpfs_version() or _("MkPFS not found")) + "\n", "dim")
        if not environment.MKPFS.is_file():
            self._log(_("Cannot find the MkPFS engine at {path}.\nInstall it with the program's install.sh "
                        "(or set PS5_GAME_PACKER_VENV to a venv that has mkpfs and pyzipper).")
                      .format(path=environment.MKPFS.parent) + "\n", "error")
            self.b_pack.configure(state="disabled")
        else:
            if not environment.mkpfs_reads_archives():
                self._log(_("This MkPFS cannot read RAR/ZIP by itself (no --archive-root option): archives cannot be "
                            "packed without extracting them. Update the engine with install.sh.") + "\n", "warning")
            if not environment.mkpfs_merges_layers():
                self._log(_("This MkPFS cannot merge layers (no --layer option): game + update + DLC cannot be combined. "
                            "Update the engine with install.sh.") + "\n", "warning")
            if not environment.has_unrar():
                if environment.IS_WINDOWS:
                    self._log(_("Cannot find UnRAR.exe: .rar sources cannot be read. Put it next to the program or install WinRAR.") + "\n", "warning")
                elif environment.IS_MAC:
                    self._log(_("unrar is not installed: .rar sources cannot be read (install RAR from RARLAB, e.g. brew install --cask rar).") + "\n", "warning")
                else:
                    self._log(_("unrar is not installed: .rar sources cannot be read (sudo apt install unrar).") + "\n", "warning")
        if auto:
            test_password = os.environ.get("PS5_GAME_PACKER_PASSWORD", "")  # automated tests only (--auto)
            if test_password:
                self._set_text(self.e_password, test_password)
            if auto.get("source"):
                self._set_text(self.e_source, auto["source"])
                self._source_changed()
            if auto.get("destination"):
                self._set_text(self.e_destination, auto["destination"])
            if auto.get("start"):
                self.after(1500, self._auto_start)
        self._update_warnings()

    def _auto_start(self) -> None:
        if self.games and not self.running:
            self._on_pack()
            return
        if not self._scanning:  # the scan finished without finding anything to pack
            self._log(_("--auto: there is no game to pack in the source.") + "\n", "error")
            self.exit_code = 2
            if self.quit_when_done:
                self.after(500, self.destroy)
            return
        self.after(500, self._auto_start)  # retry until the scan finishes

    def _language_changed(self, label: str) -> None:
        if self._loading:
            return
        chosen = LANGUAGE_CHOICES[self.language_labels.index(label)] if label in self.language_labels else "auto"
        self.settings["language"] = chosen
        environment.save_config(self.settings)
        from .i18n import detect_language
        if detect_language(None if chosen == "auto" else chosen) == language():
            return
        if self.running:
            self._message(_("The language will change the next time you open the program."))
            return
        if self._ask(_("Restart the program now to change the language?")):
            self._restart()

    def _restart(self) -> None:
        args = list(sys.argv[1:])
        if "--lang" in args:
            i = args.index("--lang")
            del args[i:i + 2]
        if environment.FROZEN:
            command = [sys.executable, *args]
        else:
            command = [sys.executable, sys.argv[0], *args]
        try:
            extra = {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)} if environment.IS_WINDOWS else {"start_new_session": True}
            subprocess.Popen(command, **extra)
        except OSError as e:
            self._message(str(e))
            return
        self.destroy()

    def _initial_folder(self, entry: ctk.CTkEntry) -> str | None:
        current = self._get_text(entry)
        if current and Path(current).is_dir():
            return current
        if current and Path(current).is_file():
            return str(Path(current).parent)
        last = self.settings.get("last_source", "")
        if entry is self.e_source and last and Path(last).is_dir():
            return last
        return None

    def _choose_folder(self, entry: ctk.CTkEntry, title: str) -> None:
        initial = self._initial_folder(entry)
        path = dialogs.choose_folder(self, title, initial)
        if path:
            self._set_text(entry, os.path.normpath(path))
            if entry is self.e_source:
                self._source_changed()
            else:
                self._update_warnings()

    @staticmethod
    def _file_filters(with_r00: bool) -> list[tuple[str, str]]:
        patterns = "*.rar *.RAR *.zip *.ZIP" + (" *.r00" if with_r00 else "")
        return [(_("RAR and ZIP archives"), patterns), (_("All files"), "*")]

    def _choose_file(self) -> None:
        initial = self._initial_folder(self.e_source)
        path = dialogs.choose_file(self, _("Choose the game's RAR or ZIP archive"), initial, self._file_filters(True))
        if path:
            self._set_text(self.e_source, os.path.normpath(path))
            self._source_changed()

    def _refresh_source(self) -> None:
        analysis.clear_cache()
        self._source_changed()

    def _source_typed(self, _e=None) -> None:
        if self._source_timer is not None:
            self.after_cancel(self._source_timer)
        self._source_timer = self.after(600, self._source_changed)

    def _rescan_for_password(self) -> None:
        """Rescans with the general password once it is typed (Enter, or leaving the field), if it changed."""
        if self.e_password.get() == self._scanned_password:
            return
        text = self._get_text(self.e_source)
        if text and (is_archive(Path(text)) or Path(text).is_dir()):
            self._source_changed()

    def _card_password_changed(self, path: str, entry: ctk.CTkEntry) -> None:
        try:
            text = entry.get()
        except tk.TclError:  # the card was already rebuilt
            return
        if text == self.passwords.get(path, ""):
            return
        if text:
            self.passwords[path] = text
        else:
            self.passwords.pop(path, None)
        self._password_focus = path
        self._source_changed()

    def _show_password_field(self, visible: bool) -> None:
        for w in (self.l_password, self.e_password, self.c_show_password):
            if visible:
                w.grid()
            else:
                w.grid_remove()

    def _clear_box(self) -> None:
        for child in list(self.scan_box.winfo_children()):
            child.destroy()
        self.checks = []
        self._password_cards = {}
        self._fit_scan_box()

    def _fit_scan_box(self) -> None:
        """Sizes the list to its contents, up to 40 % of the screen; past that it scrolls."""
        try:
            self.update_idletasks()
            scale = self.scan_box._get_widget_scaling()
            need = sum(c.winfo_reqheight() + 4 for c in self.scan_box.winfo_children()) / scale
            cap = max(160, int(self.winfo_screenheight() * 0.4 / scale))
            self.scan_box.configure(height=max(1, min(int(need), cap)))
        except tk.TclError:
            pass

    def _source_changed(self, *_a) -> None:
        self._source_timer = None
        self._scan_id += 1
        my_id = self._scan_id
        text = self._get_text(self.e_source)
        path = Path(text) if text else None
        if text != self._scanned_text:  # another folder: start clean
            self._manual_ticks = {}
            self._drop_kept_params()
        self._scanned_text = text
        self._scanned_password = self.e_password.get()
        self._keep_params(self.games)
        self.games = []
        self.pending = []
        self.archive_info = {}
        self._scanning = False
        self._clear_box()
        if path is None or not path.exists():
            self._show_password_field(False)
            self.l_scan.configure(text=_("Choose a folder or an archive to scan it."), text_color=("gray10", "gray90"))
            self._update_warnings()
            return
        if is_archive(path):
            self._show_password_field(True)
            self.l_scan.configure(text=_("Reading the archive contents…"), text_color=("gray10", "gray90"))
            password = self.e_password.get()
            self._scanning = True

            def archive_work() -> None:
                info = analysis.list_archive(path, password)
                self._on_ui(self._show_archive_scan, my_id, path, info, password)

            threading.Thread(target=archive_work, daemon=True).start()
            return
        if not path.is_dir():
            self._show_password_field(False)
            self.l_scan.configure(text=_("That is neither a folder nor a .rar/.zip archive."), text_color=COLOR_ERROR)
            self._update_warnings()
            return
        # in folder mode the general field is decided after the scan, without hiding it first (it would lose focus)
        self.l_scan.configure(text=_("Scanning…"), text_color=("gray10", "gray90"))
        password = self.e_password.get()
        passwords = dict(self.passwords)
        self._scanning = True

        def work() -> None:
            games, notes = analysis.analyze_folder(path, password, passwords)
            self._on_ui(self._show_scan, my_id, path, games, "", notes)

        threading.Thread(target=work, daemon=True).start()

    def _show_archive_scan(self, my_id: int, archive: Path, info: dict, password: str = "") -> None:
        if my_id != self._scan_id:
            return
        self._scanning = False
        self.archive_info = info
        if not info.get("ok"):
            err = analysis.error_text(info)
            if info.get("encrypted") or info.get("error_code") == "password":
                self.l_scan.configure(text=f"🔒 {err} " + _("Type it above."), text_color=COLOR_WARNING)
            else:
                self.l_scan.configure(text=err, text_color=COLOR_ERROR)
            self._log(f"{archive.name}: {err} [{info.get('error_code', '')}] {info.get('error', '')}".rstrip() + "\n", "warning")
            self.games = []
            self._update_warnings()
            return
        games = []
        encrypted = bool(info.get("encrypted"))
        for g in info.get("games", []):
            m = analysis.ID_RE.search(archive.name) or analysis.ID_RE.search(g.get("subpath", ""))
            games.append({
                "path": archive, "virtual": True, "subpath": g.get("subpath", ""),
                "title_id": (g.get("title_id") or "").upper() or (m.group(1).upper() if m else archive_stem(archive)),
                "param": g.get("param_state", PARAM_OK),
                "name": analysis.game_title(g.get("param") or {}) or g.get("name", ""),
                "version": g.get("version", ""), "req_fw": g.get("req_fw", ""),
                "files": int(g.get("files", 0)), "bytes": int(g.get("bytes", 0)),
                "ampr": bool(g.get("ampr")), "ampr_index": bool(g.get("ampr_index")),
                "encrypted": encrypted, "password": password if encrypted else "",
            })
        header = _("{archive_size} archive with {content_size} inside · it will be packed").format(
            archive_size=gb(archive.stat().st_size), content_size=gb(info.get("bytes", 0)))
        header += " " + _("without extracting it") + " (" + _("read directly by MkPFS") + ")"
        if info.get("encrypted"):
            header += " · 🔒 " + _("password-protected")
        if info.get("solid"):
            header += " · " + _("solid RAR: it will be slower")
        self._show_scan(my_id, archive, games, header)

    def _show_scan(self, my_id: int, path: Path, games: list[dict], header: str = "",
                   notes: list[str] | None = None) -> None:
        if my_id != self._scan_id:
            return
        self._scanning = False
        self.pending = [g for g in games if g.get("needs_password")]
        games = [g for g in games if not g.get("needs_password")]
        for g in games:
            kept = self._kept_params.pop(self._job_key(g), None)
            if kept is not None:  # the param.json added before the rescan goes back on top
                analysis.add_layer(g, kept, role=ROLE_LAYER)
        self._drop_kept_params()  # the ones whose game is no longer there
        self.games = games
        self._clear_box()
        if path.is_dir():
            any_encrypted = bool(self.pending) or any(g.get("encrypted") for g in games)
            self._show_password_field(any_encrypted)
        pre = (header + "\n") if header else ""
        self._scan_header = pre
        row = 0
        if not games and self.pending:
            self.l_scan.configure(text=pre + _("Password-protected archives ({n}): type the password above (applies to all) or in each card.")
                                  .format(n=len(self.pending)), text_color=COLOR_WARNING)
        elif not games:
            self.l_scan.configure(text=pre + strip_tags(_("No game here: looking for folders with <tt>eboot.bin</tt> and <tt>sce_sys/param.json</tt>.")),
                                  text_color=COLOR_ERROR)
        elif len(games) == 1:
            self.l_scan.configure(text=pre + self._description(games[0]), text_color=("gray10", "gray90"))
            self._layer_frame(games[0]).grid(row=row, column=0, sticky="ew", padx=4, pady=2)
            row += 1
        else:
            self.l_scan.configure(text=pre + _("{n} games found.").format(n=len(games)) + " " + _("Tick the ones you want to pack:"),
                                  text_color=("gray10", "gray90"))
            for g in games:
                c = ctk.CTkCheckBox(self.scan_box, text=self._description(g))
                c.configure(command=lambda c=c, g=g: self._game_toggled(c, g))
                # without a valid param.json it stays unticked (the card says why); the user's own choice survives a rescan
                if self._manual_ticks.get(self._job_key(g), job_param_state(g) == PARAM_OK):
                    c.select()
                c.grid(row=row, column=0, sticky="w", padx=4, pady=2)
                row += 1
                self.checks.append((c, g))
                self._layer_frame(g).grid(row=row, column=0, sticky="ew", padx=4, pady=2)
                row += 1
        for p in self.pending:
            self._password_card(p).grid(row=row, column=0, sticky="ew", padx=4, pady=2)
            row += 1
        for note in notes or []:
            ctk.CTkLabel(self.scan_box, text=note, anchor="w", justify="left", wraplength=TEXT_WIDTH,
                         text_color=COLOR_WARNING).grid(row=row, column=0, sticky="ew", padx=4, pady=2)
            row += 1
        focus = self._password_cards.get(self._password_focus or "")
        self._password_focus = None
        if focus is not None:  # the user was typing in that card: keep the focus through the repaint
            try:
                focus.focus_set()
                focus.icursor("end")
            except tk.TclError:
                pass
        if path.is_file():
            self.settings["last_source"] = str(path.parent)
        else:
            self.settings["last_source"] = str(path.parent if len(games) == 1 and games[0]["path"] == path else path)
        self._fit_scan_box()
        self._update_warnings()

    @staticmethod
    def _job_key(job: dict) -> str:
        return f"{job['path']}::{job.get('subpath', '')}"

    def _game_toggled(self, check: ctk.CTkCheckBox, job: dict) -> None:
        self._manual_ticks[self._job_key(job)] = bool(check.get())
        self._update_warnings()

    def _tick_game(self, job: dict) -> None:
        """Ticks a game's checkbox (after its param.json has been fixed)."""
        self._manual_ticks[self._job_key(job)] = True
        for c, g in self.checks:
            if g is job:
                c.select()

    def _keep_params(self, games: list[dict]) -> None:
        """Before a rescan, sets aside the "Add param.json…" layers so they can go back on the same games."""
        for g in games:
            for c in g.get("layers", []):
                if c.get("temporary"):
                    self._kept_params[self._job_key(g)] = c

    def _drop_kept_params(self) -> None:
        layers = list(self._kept_params.values())
        self._kept_params = {}
        if self.running:  # a batch may still be packing from those folders: delete them when it ends
            self._pending_temporaries += layers
            return
        for c in layers:
            analysis.delete_temp_layer(c)

    @staticmethod
    def _description(job: dict) -> str:
        name = job["name"] or (job["subpath"] or job["path"].name)
        ver = f" · v{job['version']}" if job["version"] else ""
        extra = ""
        if job["ampr"]:
            extra = " · APR-EMU" + ("" if job["ampr_index"] else " " + _("(no index; MkPFS will generate it)"))
        if job.get("encrypted"):
            extra += " · 🔒 " + _("password-protected")
        if job.get("layers"):
            return (f"{job['title_id']} · {name}{ver} · " + _("merge of {n} layers").format(n=len(job["layers"])) + "\n"
                    + _("up to {size} · {n} files").format(size=gb(job["bytes"]), n=thousands(job["files"])) + extra)
        return (f"{job['title_id']} · {name}{ver}\n"
                + _("{size} · {n} files").format(size=gb(job["bytes"]), n=thousands(job["files"])) + extra)

    def _refresh_description(self, job: dict) -> None:
        """Repaints the description of a job after its layers changed."""
        for c, g in self.checks:
            if g is job:
                c.configure(text=self._description(job))
                return
        if len(self.games) == 1 and self.games[0] is job:
            self.l_scan.configure(text=self._scan_header + self._description(job))

    def _layer_frame(self, job: dict) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.scan_box)
        frame.grid_columnconfigure(0, weight=1)
        self._draw_layers(job, frame)
        return frame

    def _password_card(self, p: dict) -> ctk.CTkFrame:
        """Card of an encrypted archive missing its password, with its own field."""
        frame = ctk.CTkFrame(self.scan_box)
        frame.grid_columnconfigure(1, weight=1)
        path = str(p["path"])
        if p.get("wrong_password"):
            text, color = "🔒 " + _("{item}: wrong password").format(item=p["item"]), COLOR_ERROR
        else:
            text, color = "🔒 " + _("{item}: password needed").format(item=p["item"]), COLOR_WARNING
        text += f" · {gb(p['bytes'])}"
        label = ctk.CTkLabel(frame, text=text, anchor="w", justify="left", wraplength=TEXT_WIDTH - 20, text_color=color)
        label.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 0))
        Tooltip(label, path)
        self._label(frame, _("Password:")).grid(row=1, column=0, sticky="e", padx=(8, 6), pady=(2, 6))
        entry = ctk.CTkEntry(frame, show="•", placeholder_text=_("Password for this archive (otherwise the one above is used)"))
        if self.passwords.get(path):
            entry.insert(0, self.passwords[path])
        entry.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(2, 6))
        entry.bind("<Return>", lambda _e, r=path, en=entry: self._card_password_changed(r, en))
        entry.bind("<FocusOut>", lambda _e, r=path, en=entry: self._card_password_changed(r, en))
        self._password_cards[path] = entry
        return frame

    @staticmethod
    def _layers_title(job: dict) -> str:
        if not job.get("layers"):
            return _("Updates, backport or DLC for this game (optional)")
        winner = final_eboot(job)
        who = (_('final eboot.bin: the one from "{item}" ({role})').format(item=winner["item"], role=role_name(winner["role"]))
               if winner else _("no eboot.bin"))
        param = param_layer(job)
        if param is not None and param is not job["layers"][0] and param.get("param", PARAM_OK) == PARAM_OK:
            who += " · " + _('param.json: the one from "{item}"').format(item=param["item"])
        return _("Layers, top to bottom (the top one overrides those below) · {who}").format(who=who)

    def _draw_layers(self, job: dict, frame: ctk.CTkFrame) -> None:
        for h in list(frame.winfo_children()):
            h.destroy()
        row = 0
        warning = param_warning(job)
        if warning:  # without a valid param.json in the container, ShadowMountPlus does not register the game: red, on top
            ctk.CTkLabel(frame, text="⚠ " + warning, anchor="w", justify="left", wraplength=TEXT_WIDTH - 20,
                         text_color=COLOR_ERROR).grid(row=row, column=0, sticky="ew", padx=6, pady=(4, 0))
            row += 1
        ctk.CTkLabel(frame, text=self._layers_title(job), anchor="w", justify="left", wraplength=TEXT_WIDTH - 20,
                     font=self.small_font, text_color=COLOR_DIM).grid(row=row, column=0, sticky="ew", padx=6, pady=(4, 2))
        row += 1
        n = len(job.get("layers", []))
        for idx in range(n - 1, -1, -1):  # the last layer (the one that wins) on top
            c = job["layers"][idx]
            f = ctk.CTkFrame(frame, fg_color="transparent")
            f.grid(row=row, column=0, sticky="ew", padx=4)
            f.grid_columnconfigure(0, weight=1)
            row += 1
            text = f"{idx + 1}. {c['item']} · {gb(c['bytes'])}"
            if c.get("version"):
                text += f" · v{c['version']}"
            if c.get("req_fw") and fw_text(c["req_fw"]):
                text += " · FW " + fw_text(c["req_fw"])
            if c["virtual"]:
                text += " · " + _("not extracted")
            if c.get("encrypted"):
                text += " · 🔒"
            chk = ctk.CTkCheckBox(f, text=text, command=lambda c=c, m=frame: self._layer_toggled(job, m, c))
            if c.get("active", True):
                chk.select()
            chk.grid(row=0, column=0, sticky="w", pady=2)
            Tooltip(chk, c.get("param_source") or str(c["path"]))
            c["_check"] = chk
            col = 1
            if c.get("temporary"):
                et = ctk.CTkLabel(f, text="param.json", text_color=COLOR_OK, font=self.small_font)
                et.grid(row=0, column=col, padx=4)
                Tooltip(et, _('This param.json replaces the game\'s own (temporary layer added with "Add param.json…")'))
                col += 1
            if c.get("has_eboot"):
                et = ctk.CTkLabel(f, text="eboot", text_color=COLOR_DIM, font=self.small_font)
                et.grid(row=0, column=col, padx=4)
                Tooltip(et, _("This layer provides eboot.bin (the game executable)"))
                col += 1
            if c["role"] == ROLE_BASE:
                ctk.CTkLabel(f, text=role_name(ROLE_BASE), text_color=COLOR_DIM, font=self.small_font).grid(row=0, column=col, padx=6)
            else:
                names = [role_name(r) for r in LAYER_ROLES]
                combo = ctk.CTkOptionMenu(f, values=names, width=130,
                                          command=lambda name, c=c, m=frame: self._change_role(job, m, c, LAYER_ROLES[names.index(name)]))
                combo.set(role_name(c["role"]) if c["role"] in LAYER_ROLES else names[-1])
                combo.grid(row=0, column=col, padx=4)
                Tooltip(combo, _("What this layer is. If unsure, check the notes that come with the update or the DLC."))
            col += 1
            b_up = ctk.CTkButton(f, text="▲", width=30, state="normal" if idx < n - 1 else "disabled",
                                 command=lambda idx=idx, m=frame: self._move_layer(job, m, idx, +1))
            b_up.grid(row=0, column=col, padx=2)
            Tooltip(b_up, _("Move up: it will take priority over the ones below"))
            b_down = ctk.CTkButton(f, text="▼", width=30, state="normal" if idx > 0 else "disabled",
                                   command=lambda idx=idx, m=frame: self._move_layer(job, m, idx, -1))
            b_down.grid(row=0, column=col + 1, padx=2)
        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=row, column=0, sticky="w", padx=4, pady=(2, 4))
        for label, folder in ((_("Add folder…"), True), (_("Add RAR/ZIP…"), False)):
            b = ctk.CTkButton(buttons, text=label, width=140, command=lambda folder=folder, m=frame: self._add_layer(job, m, folder))
            b.pack(side="left", padx=(0, 6))
            Tooltip(b, _("Add an update, a backport or a DLC stored somewhere else"))
        b = ctk.CTkButton(buttons, text=_("Add param.json…"), width=150, command=lambda m=frame: self._add_param(job, m),
                          fg_color=COLOR_ERROR if warning else None, hover_color="#922b21" if warning else None)
        b.pack(side="left")
        Tooltip(b, _("Replaces the game's sce_sys/param.json with a valid one: it is copied to a temporary folder and added "
                     "as a layer on top (the top one wins). Use it when the game's own is missing or cannot be read."))

    def _layer_toggled(self, job: dict, frame: ctk.CTkFrame, c: dict) -> None:
        c["active"] = bool(c["_check"].get())
        self._layers_changed(job, frame)

    def _layers_changed(self, job: dict, frame: ctk.CTkFrame) -> None:
        """After ticking, moving, re-roling or adding a layer."""
        analysis.recompute_totals(job)
        self._draw_layers(job, frame)
        self._refresh_description(job)
        self._fit_scan_box()
        self._update_warnings()

    def _add_param(self, job: dict, frame: ctk.CTkFrame) -> None:
        base_path = job["path"] if job["path"].is_dir() else job["path"].parent
        initial = str(base_path if base_path.exists() else Path.home())
        chosen = dialogs.choose_file(self, _("Choose the right param.json for this game"), initial,
                                     [(_("JSON files"), "*.json *.JSON"), (_("All files"), "*")])
        if chosen:
            self._param_chosen(job, frame, Path(chosen))

    def _param_chosen(self, job: dict, frame: ctk.CTkFrame, path: Path) -> bool:
        """Validates the chosen param.json (asks if it belongs to another game) and adds it as a layer on top.
        Returns whether it was added."""
        state, param = analysis.validate_param_file(path)
        if state != PARAM_OK:
            self._message(_("{name} is not a valid param.json: it has to be a JSON object with titleId.").format(name=path.name))
            return False
        tid = analysis.title_id_of(param)
        if tid and analysis.ID_RE.fullmatch(job["title_id"] or "") and tid != job["title_id"] and not self._ask(
                _("That param.json belongs to {other}, not to {this}.\n\nAdd it anyway?").format(other=tid, this=job["title_id"])):
            return False
        for c in [c for c in job.get("layers", []) if c.get("temporary")]:  # only one added param.json at a time
            analysis.delete_temp_layer(c)
            job["layers"].remove(c)
        it = analysis.param_json_layer(path, param)
        analysis.add_layer(job, it, role=ROLE_LAYER)
        self._log(_("param.json added as a layer on top of {game}: {source}").format(game=job["title_id"], source=path) + "\n", "warning")
        self._layers_changed(job, frame)
        if job_param_state(job) == PARAM_OK:
            self._tick_game(job)
        return True

    def _drop_temporaries(self, games: list[dict], force: bool = False) -> None:
        """Deletes the temporary folders of "Add param.json…"; if they are being packed, when the batch ends."""
        layers = [c for g in games for c in g.get("layers", []) if c.get("temporary")]
        if self.running and not force:
            self._pending_temporaries += layers
            return
        for c in layers + self._pending_temporaries:
            analysis.delete_temp_layer(c)
        self._pending_temporaries = []

    def _add_layer(self, job: dict, frame: ctk.CTkFrame, folder: bool) -> None:
        base_path = job["path"] if job["path"].is_dir() else job["path"].parent
        initial = str(base_path.parent if base_path.parent.exists() else Path.home())
        if folder:
            chosen = dialogs.choose_folder(self, _("Choose the update or DLC folder"), initial)
        else:
            chosen = dialogs.choose_file(self, _("Choose the update or DLC RAR or ZIP archive"), initial, self._file_filters(False))
        if not chosen:
            return
        path = Path(chosen)
        password = self.e_password.get()
        self.l_status.configure(text=_("Scanning {name}…").format(name=path.name))

        def work() -> None:
            pkgs: list[str] = []
            notes: list[str] = []
            items = analysis.analyze_items(path, password, pkgs, notes)
            self._on_ui(self._layer_scanned, job, frame, path, items, pkgs, notes)

        threading.Thread(target=work, daemon=True).start()

    def _layer_scanned(self, job: dict, frame: ctk.CTkFrame, path: Path, items: list[dict], pkgs: list[str], notes: list[str]) -> None:
        self.l_status.configure(text=_("Ready."))
        if not items:
            self._message(_("There is nothing to add in {name}").format(name=path.name) + (": " + "; ".join(notes) if notes else "."))
            return
        added = 0
        for it in items:
            name = it.get("item") or path.name
            if it["title_id"] and it["title_id"] != job["title_id"]:
                if not self._ask(_("{name} seems to belong to another game ({other}, not {this}).\n\nAdd it anyway?")
                                 .format(name=name, other=it["title_id"], this=job["title_id"])):
                    continue
            problem = analysis.add_layer(job, it)
            if problem == "duplicate":
                self._message(_("That layer is already in the list."))
                continue
            added += 1
        if not added:
            return
        if pkgs:
            self._log(_("Kept aside (Package Installer later): {items}").format(items=", ".join(pkgs)) + "\n", "warning")
        for n in notes:
            self._log(n + "\n", "warning")
        self._layers_changed(job, frame)

    def _change_role(self, job: dict, frame: ctk.CTkFrame, c: dict, role: str) -> None:
        if c["role"] == role:
            return
        analysis.change_role(job, c, role)
        self._layers_changed(job, frame)

    def _move_layer(self, job: dict, frame: ctk.CTkFrame, idx: int, delta: int) -> None:
        new = idx + delta
        if 0 <= new < len(job["layers"]):
            job["layers"][idx], job["layers"][new] = job["layers"][new], job["layers"][idx]
            self._layers_changed(job, frame)

    def name_mode(self) -> str:
        """'title' or 'id', as set in the advanced options."""
        try:
            return NAME_MODES[self.name_mode_labels.index(self.c_name.get())]
        except ValueError:
            return NAME_MODE_TITLE

    def _selected(self) -> list[dict]:
        if self.checks:
            return [g for c, g in self.checks if c.get()]
        return list(self.games)

    def _update_warnings(self, *_a) -> None:
        warnings: list[str] = []
        destination_text = self._get_text(self.e_destination)
        destination = Path(destination_text) if destination_text else None
        sel = self._selected()
        total = sum(g["bytes"] for g in sel)
        if destination and destination.is_dir():
            free = environment.free_space(destination)
            fs = environment.fs_type(destination)
            text = _("Free at destination: {free}").format(free=gb(free))
            if fs:
                text += " · " + _("file system {fs}").format(fs=fs)
            if total:
                text += " · " + _("the game takes {size} uncompressed (the container usually ends up between 60 % and 75 %)").format(size=gb(total))
                if free < total * CONTAINER_RATIO:
                    warnings.append(_("It may not fit: {free} free and the game takes {size}.").format(free=gb(free), size=gb(total)))
            self.l_destination.configure(text=text, text_color=COLOR_DIM)
            if environment.is_fat32(destination):
                warnings.append(_("The destination is FAT32: it cannot hold files over 4 GB. Use exFAT."))
            if sel:
                d_src = environment.physical_disk(sel[0]["path"])
                d_dst = environment.physical_disk(destination)
                if d_src and d_src == d_dst:
                    warnings.append(_("Source and destination are on the same disk ({disk}): reading and writing at once makes it slower.")
                                    .format(disk=d_src))
            if self.c_two_pass.get():
                temp_text = self._get_text(self.e_temp)
                temp = Path(temp_text) if temp_text else destination
                free_t = environment.free_space(temp)
                if total and free_t < total * 1.05:
                    warnings.append(_("The two-pass method needs {size} free in the temporary folder ({temp}) and there is {free}.")
                                    .format(size=gb(total), temp=temp, free=gb(free_t)))
        elif destination:
            self.l_destination.configure(text=_("The destination folder does not exist."), text_color=COLOR_ERROR)
        else:
            self.l_destination.configure(text="")
        for g in sel:
            warnings += layer_warnings(g)
        if sel and environment.in_ecryptfs(sel[0]["path"]):
            warnings.append(_("The source is inside an encrypted home folder (ecryptfs): reading will be slower. "
                              "Next time, keep the games in an unencrypted folder."))
        self.l_warning.configure(text=("⚠ " + "\n⚠ ".join(warnings)) if warnings else "")

    def _to_logs(self, text: str) -> None:
        for f in self.logs:
            try:
                f.write(text if text.endswith("\n") else text + "\n")
                f.flush()
            except (OSError, ValueError):
                pass

    def _log(self, text: str, tag: str | None = None) -> None:
        self._to_logs(text)
        self.log_box.configure(state="normal")
        if tag:
            self.log_box.insert("end", text, tag)
        else:
            self.log_box.insert("end", text)
        try:
            lines = int(self.log_box.index("end-1c").split(".")[0])
            if lines > LOG_MAX_LINES:  # drop the first quarter at once, not line by line
                self.log_box.delete("1.0", f"{lines - LOG_MAX_LINES * 3 // 4}.0")
        except (tk.TclError, ValueError):
            pass
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_line(self, line: str) -> None:
        lower = line.lower()
        if any(p in lower for p in ("error", "traceback", "exception", "failed")):
            tag = "error"
        elif any(p in lower for p in ("warning", "warn")):
            tag = "warning"
        elif "final image size" in lower or "successfully" in lower or "elapsed time" in lower:
            tag = "ok"
        elif line.startswith("====") or line.startswith("  ") or line.startswith("[INFO]"):
            tag = "dim"
        else:
            tag = None
        self._log(line + "\n", tag)

    def _pack_options(self, destination: Path) -> dict:
        temp_text = self._get_text(self.e_temp)
        return {
            "destination": destination,
            "level": int(self.s_level.get()),
            "cpu": int(self.s_cpu.get()),
            "verify": bool(self.c_verify.get()),
            "two_pass": bool(self.c_two_pass.get()),
            "temp_dir": Path(temp_text) if temp_text else destination,
            "password": self.e_password.get(),
            "name_mode": self.name_mode(),
        }

    def _on_pack(self, *_a) -> None:
        if self.running:
            return
        sel = self._selected()
        destination_text = self._get_text(self.e_destination)
        destination = Path(destination_text) if destination_text else None
        if not sel:
            self._message(_("Choose a folder or an archive that contains a PS5 game."))
            return
        if not destination or not destination.is_dir():
            self._message(_("Choose a destination folder that exists."))
            return
        no_param = [g for g in sel if job_param_state(g) != PARAM_OK]
        if no_param and not self._ask(
                _("{items}: sce_sys/param.json is missing or cannot be read, and ShadowMountPlus will not be able to "
                  'register the game. Replace it with "Add param.json…".\n\nPack anyway?')
                .format(items=", ".join(g["title_id"] for g in no_param))):
            return
        total = sum(g["bytes"] for g in sel)
        free = environment.free_space(destination)
        if free < total * CONTAINER_RATIO and not self._ask(
                _("There is {free} free at the destination and the game takes {size} uncompressed.\n"
                  "The container usually ends up between 60 % and 75 %, but that is not guaranteed.\n\nStart anyway?")
                .format(free=gb(free), size=gb(total))):
            return
        mode = self.name_mode()
        existing = [destination / container_name(g, mode) for g in sel if (destination / container_name(g, mode)).exists()]
        if existing and not self._ask(_("These files already exist and will be overwritten:") + "\n\n"
                                      + "\n".join(p.name for p in existing) + "\n\n" + _("Continue?")):
            return

        options = self._pack_options(destination)
        self.settings.update({
            "destination": str(destination),
            "temp_dir": self._get_text(self.e_temp),
            "level": options["level"],
            "cpu": options["cpu"],
            "container_name": options["name_mode"],
        })
        environment.save_config(self.settings)

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.cancelled = False
        self.running = True
        self.paused = False
        self.b_pack.configure(state="disabled")
        self.b_cancel.configure(state="normal")
        self.b_pause.configure(text=_("Pause"), state="normal" if engine.can_pause() else "disabled")
        self.bar.set(0)
        self.l_bar.configure(text="")
        self.thread = threading.Thread(target=self._work, args=(sel, options), daemon=True)
        self.thread.start()

    def _work(self, games: list[dict], options: dict) -> None:
        results: list[dict] = []
        batch_start = time.time()
        for i, job in enumerate(games, 1):
            if self.cancelled:
                break
            output = options["destination"] / container_name(job, options.get("name_mode", NAME_MODE_TITLE))
            self._on_ui(self._status, f"[{i}/{len(games)}] {job['title_id']} · {job['name'] or job['path'].name}")
            mode = " · " + _("from the archive, without extracting") if job.get("virtual") else ""
            if job.get("layers"):
                mode = " · " + _("merge of {n} layers, nothing copied").format(n=len([c for c in job["layers"] if c.get("active", True)]))
            started = time.time()
            self._on_ui(self._log, f"=== {time.strftime('%H:%M:%S')} · {job['title_id']} · {job['name'] or job['path'].name} · {gb(job['bytes'])}{mode}"
                        + " · " + _("level {n}").format(n=options["level"]) + f" → {output.name} ===\n", "ok")
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                ok, summary = self._pack_one(job, output, options)
            except Exception as e:
                self._on_ui(self._log, _("Failure: {error}").format(error=e) + "\n", "error")
                ok, summary = False, ""
            if self.cancelled:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
                break
            if not ok:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                final = output.stat().st_size if ok else 0
            except OSError:
                final = 0
            results.append({"job": job, "ok": ok, "summary": summary, "name": output.name, "final": final,
                            "level": options["level"], "seconds": time.time() - started})
        self._on_ui(self._finished, results, time.time() - batch_start)

    @staticmethod
    def _duration(seconds: float) -> str:
        """'10 min 39 s' or '42 s' for the summaries."""
        if seconds >= 60:
            return _("{m} min {s} s").format(m=int(seconds // 60), s=f"{int(seconds % 60):02d}")
        return _("{n} s").format(n=f"{seconds:.0f}")

    @staticmethod
    def _result_summary(r: dict) -> str:
        """'name.ffpfsc: 71.1 GB → 44.4 GB (63 %) at level 7 in 10 min 39 s'."""
        source = int(r["job"].get("bytes") or 0)
        pct = f"{100 * r['final'] / source:.0f}" if source and r["final"] else "?"
        return _("{name}: {source} → {final} ({pct} %) at level {level} in {time}").format(
            name=r["name"], source=gb(source), final=gb(r["final"]), pct=pct, level=r["level"], time=Window._duration(r["seconds"]))

    def _engine_error_text(self, e: engine.EngineError) -> str:
        code = str(e)
        if code == "no_base":
            return _("none of the active layers contains the whole game (eboot.bin + sce_sys/param.json)")
        if code == "no_layers":
            return _("the installed MkPFS cannot merge layers (no --layer option): update the engine with install.sh")
        if code == "no_archives":
            return _("the installed MkPFS cannot read RAR/ZIP (no --archive-root option): update the engine with install.sh")
        return code

    def _pack_one(self, job: dict, output: Path, options: dict) -> tuple[bool, str]:
        layers = [c for c in job.get("layers", []) if c.get("active", True)]
        if layers:
            self._on_ui(self._log, _("Layers (bottom to top): ") + " → ".join(f"{role_name(c['role'])}: {c['item']}" for c in layers) + "\n", "dim")
        if job.get("virtual") or (layers and layers[0].get("virtual")):
            self._on_ui(self._log, _("MkPFS reads the archive directly, without extracting it.") + "\n", "dim")
        password = analysis.job_password(job) or options["password"]  # the one that scanned its archives, or the general one
        if options["two_pass"]:
            if engine.allows_two_pass(job):
                step1, step2, temp_dat = engine.two_pass_commands(job, output, options)
                try:
                    ok, _res = self._run(step1, temp_dat, password)
                    if not ok or self.cancelled:
                        return False, ""
                    return self._run(step2, output, password)
                finally:
                    try:
                        temp_dat.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._on_ui(self._log, _("The two-pass method only works for a plain folder without layers: using the normal method.") + "\n", "warning")
        try:
            cmd = engine.pack_command(job, output, options)
        except engine.EngineError as e:
            raise RuntimeError(self._engine_error_text(e)) from e
        return self._run(cmd, output, password)

    def _run(self, cmd: list[str], output: Path, password: str) -> tuple[bool, str]:
        self._on_ui(self._log, "$ " + " ".join(cmd) + "\n", "dim")
        try:
            self.process = engine.start(cmd, output.parent, password)
        except OSError as e:
            self._on_ui(self._log, _("Could not start MkPFS: {error}").format(error=e) + "\n", "error")
            return False, ""
        self.pause = engine.Pause(self.process)
        if self.paused:  # the user paused the batch: the next game starts already stopped
            self.pause.set("user", True)
        stop = threading.Event()
        threading.Thread(target=engine.throttle, args=(self.process, output, stop, self.pause), daemon=True).start()
        try:
            return self._read_output(output)
        finally:
            stop.set()
            self.pause = None

    def _read_output(self, output: Path) -> tuple[bool, str]:
        summary_size = ""
        summary_time = ""
        last: list[str] = []  # tail of lines, to explain a failure
        assert self.process is not None
        for line in engine.read_lines(self.process):
            self._process_line(line)
            last = (last + [line])[-40:]
            mt = FINAL_SIZE_RE.search(line)
            if mt:
                summary_size = mt.group(1)
            me = ELAPSED_RE.search(line)
            if me:
                summary_time = me.group(1)
        code = self.process.wait()
        self.process = None
        if code != 0 and not self.cancelled:
            self._on_ui(self._log, _("MkPFS finished with an error (code {code}).").format(code=code) + "\n", "error")
            cause = engine.failure_cause(last)
            free = environment.free_space(output.parent)
            if cause == "space" or (cause is None and free < LOW_SPACE):
                text = _("No space left at the destination: MkPFS could not write the container ({free} free).").format(free=gb(free))
                self._on_ui(self._log, "⚠ " + text + "\n", "error")
                self._on_ui(self._status, text)
            elif cause == "password":
                self._on_ui(self._log, "🔒 " + _("Wrong password (or encrypted archive without a password): MkPFS could not read the archive.") + "\n", "error")
            return False, ""
        if summary_time:
            t = float(summary_time)
            summary_size += " " + _("in {seconds} s").format(seconds=f"{t:.1f}" if t < 10 else f"{t:.0f}")
        return code == 0, summary_size

    def _process_line(self, line: str) -> None:
        line = line.rstrip()
        if not line.strip():
            return
        m = PROGRESS_RE.search(line)
        if m:
            pct = max(0, min(100, int(m.group("pct"))))
            label = m.group("label").strip()
            for phase, translated in phases().items():
                if label.startswith(phase):
                    label = translated + label[len(phase):]
                    break
            m_eta = ETA_RE.search(label)
            if m_eta:
                seconds = float(m_eta.group(1)) * (60 if m_eta.group(2) == "m" else 1)
                label = label[:m_eta.start()] + " · " + _("ETA") + " " + time_left(seconds)
            if language() == "es":
                label = label.replace("@", "a")
            self._last_progress = (pct / 100, f"{pct} % · {label}")
            if not self._progress_pending:
                self._progress_pending = True
                self._on_ui(self._apply_progress)
            return
        self._on_ui(self._log_line, line)

    def _apply_progress(self) -> None:
        self._progress_pending = False
        if self.paused:  # while paused the bar says "Paused" and the time left does not move
            return
        last = self._last_progress
        if last is not None:
            self._progress(*last)

    def _progress(self, fraction: float, text: str) -> None:
        self.bar.set(fraction)
        self.l_bar.configure(text=text)

    def _status(self, text: str) -> None:
        self.l_status.configure(text=text)

    def _finished(self, results: list[dict], seconds: float) -> None:
        self.running = False
        self.paused = False
        self._drop_temporaries([])  # the param.json layers of jobs already removed from the list
        self.b_pack.configure(state="normal")
        self.b_cancel.configure(state="disabled")
        self.b_pause.configure(text=_("Pause"), state="disabled")
        if self.cancelled:
            self.bar.set(0)
            self.l_bar.configure(text=_("Cancelled"))
            self.l_status.configure(text=_("Cancelled. The half-written file has been deleted."))
            self._log(_("Cancelled by the user.") + "\n", "warning")
        else:
            good = [r for r in results if r["ok"]]
            bad = [r for r in results if not r["ok"]]
            self.bar.set(1.0 if not bad else 0)
            for r in good:
                self._log("✔ " + self._result_summary(r) + "\n", "ok")
            if bad:
                self.l_bar.configure(text=_("Finished with errors"))
                text = _("{good} succeeded, {bad} failed. See the details.").format(good=len(good), bad=len(bad))
                if len(bad) == 1 and self.l_status.cget("text").startswith(_("No space left at the destination")):
                    text = self.l_status.cget("text")  # the concrete cause beats the count
                self.l_status.configure(text=text)
                notify(app_name(), text)
            else:
                self.l_bar.configure(text=_("Done"))
                text = _("Done in {time}: ").format(time=self._duration(seconds)) + ", ".join(self._result_summary(r) for r in good)
                self.l_status.configure(text=text)
                self._log(text + "\n", "ok")
                notify(app_name(), text)
        self._update_warnings()
        if self.quit_when_done:
            self.after(1500, self.destroy)

    def _on_pause(self, *_a) -> None:
        """The "Pause"/"Resume" button; it applies to the whole batch."""
        if not self.running:
            return
        self.paused = not self.paused
        pause = self.pause
        if pause is not None:
            pause.set("user", self.paused)
        if self.paused:
            self.b_pause.configure(text=_("Resume"))
            before = self.l_bar.cget("text")
            self.l_bar.configure(text=_("Paused") + (" · " + before if before else ""))
            self._log(_('Paused: MkPFS is stopped until you press "Resume".') + "\n", "warning")
        else:
            self.b_pause.configure(text=_("Pause"))
            self._log(_("Resumed.") + "\n", "warning")
            self._apply_progress()

    def _on_cancel(self, *_a) -> None:
        if not self.running:
            return
        self.cancelled = True
        self.l_status.configure(text=_("Cancelling…"))
        self.b_cancel.configure(state="disabled")
        self.b_pause.configure(state="disabled")
        if self.pause is not None:
            self.pause.release()  # a stopped process does not answer the terminate signal
        engine.kill(self.process)

    def _on_close(self, *_a) -> None:
        if self.running:
            self.cancelled = True
            if self.pause is not None:
                self.pause.release()
            engine.kill(self.process)
        self.destroy()

    def destroy(self) -> None:
        """Cancels the own timers before closing (avoids Tk warnings)."""
        for attribute in ("_pump_id", "_source_timer"):
            ident = getattr(self, attribute, None)
            if ident is not None:
                try:
                    self.after_cancel(ident)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)
        try:
            self._drop_temporaries(getattr(self, "games", []), force=True)
            self._drop_kept_params()
        except Exception:
            pass
        super().destroy()
        for f in self.logs:
            try:
                f.close()
            except OSError:
                pass
        self.logs = []

    def _message(self, text: str) -> None:
        messagebox.showinfo(app_name(), text, parent=self)

    def _ask(self, text: str) -> bool:
        return bool(messagebox.askyesno(app_name(), text, parent=self))


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"ps5-game-packer {__version__}")
        return 0
    auto: dict | None = None
    if "--auto" in argv:
        i = argv.index("--auto")
        auto = {
            "source": argv[i + 1] if len(argv) > i + 1 else "",
            "destination": argv[i + 2] if len(argv) > i + 2 else "",
            "start": "--start" in argv,
            "quit": "--quit-when-done" in argv,
        }
    if "--log" in argv:
        i = argv.index("--log")
        if len(argv) > i + 1:
            auto = dict(auto or {}, log=argv[i + 1])
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    w = Window(auto)
    w.mainloop()
    return w.exit_code
