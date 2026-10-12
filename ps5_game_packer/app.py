# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Start-up: language, command line and window. Options: --lang en|es, --version, --log FILE and, for testing,
--auto SOURCE DESTINATION [--start] [--quit-when-done] (the password goes in PS5_GAME_PACKER_PASSWORD)."""
from __future__ import annotations

import sys

from . import __version__, environment, i18n


def _lang_option(argv: list[str]) -> str | None:
    if "--lang" in argv:
        i = argv.index("--lang")
        return argv[i + 1] if len(argv) > i + 1 else None
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(f"ps5-game-packer {__version__}")
        return 0
    i18n.activate(i18n.detect_language(_lang_option(argv) or environment.load_config().get("language")))
    from .window import main as window_main

    return window_main(argv)
