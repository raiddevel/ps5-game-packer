#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""PS5 Game Packer launcher. Run it with the python of the venv that has MkPFS and CustomTkinter or, on Windows,
use the bundled ps5-game-packer.exe."""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from ps5_game_packer.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
