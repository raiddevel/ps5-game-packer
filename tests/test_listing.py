"""listing.py: contents of a ZIP read with the MkPFS library (in this process or with the venv's python)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ps5_game_packer import environment

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic_game as sg  # noqa: E402


def _list(archive: Path, password: str = "") -> dict:
    return environment.list_archive(archive, password)


def _has_engine() -> bool:
    try:
        import mkpfs.archive_source
        return True
    except ImportError:
        return environment.VENV_PY is not None and environment.VENV_PY.exists()


pytestmark = pytest.mark.skipif(not _has_engine(), reason="needs mkpfs (in this python or in the venv)")


def test_list_zip_dlc(tmp_path):
    z = tmp_path / "dlc.zip"
    sg.make_dlc_zip(z)
    info = _list(z)
    assert info["ok"] and info["games"] == []
    assert info["candidate_roots"] == [f"{sg.TITLE_ID}-app"]
    assert info["single_root"] == f"{sg.TITLE_ID}-app"
    assert info["files"] == 3 and info["bytes"] > 3 * sg.MB
    assert info["eboots"] == [] and info["pkgs"] == []
    assert info["kind"] == "zip" and not info["encrypted"] and not info["solid"]


def test_list_zip_game(tmp_path):
    root = tmp_path / "src"
    files = sg.make_base(root, ampr=True)
    z = tmp_path / "game.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for rel in files:
            zf.write(root / rel, f"Game-app/{rel}")
        zf.writestr("Game-app/extra.pkg", b"pkg")
    info = _list(z)
    assert info["ok"] and len(info["games"]) == 1
    g = info["games"][0]
    assert g["subpath"] == "Game-app" and g["title_id"] == sg.TITLE_ID and g["version"] == "01.000.000"
    assert g["ampr"] and not g["ampr_index"] and g["files"] == len(files) + 1  # + extra.pkg
    assert g["param"]["localizedParameters"]["en-US"]["titleName"] == "Sample Game"
    assert info["pkgs"] == ["Game-app/extra.pkg"]
    assert info["eboots"] == ["Game-app/eboot.bin"]


def test_list_broken_param(tmp_path):
    z = tmp_path / "game.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Broken-app/eboot.bin", b"x")
        zf.writestr("Broken-app/sce_sys/param.json", sg.BROKEN_PARAM_BYTES)
        zf.writestr("None-app/eboot.bin", b"y")
        zf.writestr("Dlc-app/data/extra.pak", b"z")
        zf.writestr("Dlc-app/sce_sys/param.json", sg.param_json("01.020.000", "0x0900000000000000"))
    info = _list(z)
    assert info["ok"], info
    by = {g["subpath"]: g for g in info["games"]}
    assert set(by) == {"Broken-app", "None-app"}
    assert by["Broken-app"]["param_state"] == "broken" and by["Broken-app"]["title_id"] == "" and by["Broken-app"]["files"] == 2
    assert by["None-app"]["param_state"] == "missing" and by["None-app"]["param"] == {}
    assert info["params"]["Broken-app"]["state"] == "broken" and info["params"]["None-app"]["state"] == "missing"
    assert info["params"]["Dlc-app"]["state"] == "ok" and info["params"]["Dlc-app"]["param"]["titleId"] == sg.TITLE_ID
    assert info["eboots"] == ["Broken-app/eboot.bin", "None-app/eboot.bin"]
    # with a good game inside, its state is ok
    z2 = tmp_path / "good.zip"
    sg.pack_zip(z2, sg.make_base(tmp_path / "src"))
    info = _list(z2)
    assert [g["param_state"] for g in info["games"]] == ["ok"] and info["params"][f"{sg.TITLE_ID}-app"]["state"] == "ok"


def test_list_format(tmp_path):
    f = tmp_path / "thing.7z"
    f.write_bytes(b"7z")
    info = _list(f)
    assert not info["ok"] and info["error_code"] == "format"


def test_list_broken_zip(tmp_path):
    f = tmp_path / "broken.zip"
    f.write_bytes(b"this is not a zip")
    info = _list(f)
    assert not info["ok"] and info["error_code"] == "zip"


def test_listing_as_program(tmp_path):
    # the standalone script, the way the program runs it when mkpfs is not in its python
    py = environment.VENV_PY if environment.VENV_PY is not None and environment.VENV_PY.exists() else Path(sys.executable)
    z = tmp_path / "dlc.zip"
    sg.make_dlc_zip(z)
    env = dict(os.environ, ARCHIVE_PASSWORD="")
    r = subprocess.run([str(py), str(environment.LISTING), str(z)], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["single_root"] == f"{sg.TITLE_ID}-app"
