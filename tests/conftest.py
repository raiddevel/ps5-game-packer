"""Settings and cache of the tests in temporary folders, the fake engine, and English as the starting language."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="ps5-game-packer-tests-")
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / "config")  # Linux
os.environ["XDG_CACHE_HOME"] = str(Path(_TMP) / "cache")
os.environ["APPDATA"] = str(Path(_TMP) / "roaming")  # Windows
os.environ["LOCALAPPDATA"] = str(Path(_TMP) / "local")

from ps5_game_packer import environment, i18n  # noqa: E402

# the tests never touch the user's settings or cache, whatever the environment variables say
environment.CONFIG_DIR = Path(_TMP) / "config" / environment.APP_ID
environment.CONFIG_FILE = environment.CONFIG_DIR / "config.json"
environment.CACHE_DIR = Path(_TMP) / "cache" / environment.APP_ID


@pytest.fixture(autouse=True)
def _english():
    # every test starts in English; the ones that look at Spanish texts switch themselves
    i18n.activate("en")
    yield
    i18n.activate("en")


@pytest.fixture
def fake_mkpfs(tmp_path_factory) -> Path:
    # the .py itself (it has a shebang) or, on Windows, a .cmd that runs it with this same Python
    script = Path(__file__).resolve().parent / "fake_mkpfs.py"
    if not sys.platform.startswith("win"):
        return script
    wrapper = tmp_path_factory.mktemp("fake-engine") / "fake_mkpfs.cmd"
    wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return wrapper
