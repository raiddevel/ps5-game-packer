"""Real RAR fixtures: listing and packing with the engine from a plain RAR, a multi-volume one and a solid one
with a password, with a byte-by-byte comparison after unpacking."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ps5_game_packer import analysis, engine, environment

sys.path.insert(0, str(Path(__file__).resolve().parent))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSWORD = "Secreto123"
CASES = [("mini-game.rar", ""), ("mini-game.parts.part1.rar", ""), ("mini-game.password.rar", PASSWORD)]


def _has_engine() -> bool:
    try:
        import mkpfs.archive_source
        return True
    except ImportError:
        return environment.VENV_PY is not None and environment.VENV_PY.exists()


pytestmark = pytest.mark.skipif(not environment.has_unrar() or not _has_engine(), reason="needs unrar and mkpfs")


def _expected() -> dict[str, bytes]:
    with zipfile.ZipFile(FIXTURES / "mini-game.zip") as zf:
        return {i.filename.split("/", 1)[1]: zf.read(i) for i in zf.infolist() if not i.is_dir()}


@pytest.mark.parametrize("name,password", CASES)
def test_list_rar(name, password):
    info = environment.list_archive(FIXTURES / name, password)
    assert info["ok"], info
    assert info["files"] == 9 and len(info["games"]) == 1
    g = info["games"][0]
    assert g["subpath"] == "PPSA99999-app" and g["title_id"] == "PPSA99999" and g["files"] == 9
    assert g["ampr"] and not g["ampr_index"]
    assert info["encrypted"] == bool(password) and info["solid"] == bool(password)


def test_list_encrypted():
    info = environment.list_archive(FIXTURES / "mini-game.password.rar", "")
    assert not info["ok"] and info["error_code"] == "password" and info["encrypted"]
    info = environment.list_archive(FIXTURES / "mini-game.password.rar", "wrong")
    assert not info["ok"] and info["error_code"] == "password"


def test_rar_in_subfolder(tmp_path):
    import shutil
    sub = tmp_path / "Mini-game-PPSA99999-PS5"
    sub.mkdir()
    for p in FIXTURES.glob("mini-game.parts.part*.rar"):
        shutil.copy(p, sub / p.name)
    (sub / "mini-game.nfo").write_text("x")
    jobs, notes = analysis.analyze_folder(tmp_path, "")
    assert len(jobs) == 1 and not notes, notes
    j = jobs[0]
    assert j["virtual"] and j["path"] == sub / "mini-game.parts.part1.rar" and j["title_id"] == "PPSA99999"
    assert j["files"] == 9 and not j.get("layers")


def _batch_with_password(tmp_path):
    # the base game as an encrypted RAR inside its folder and an update as a ZIP next to it
    import shutil
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import synthetic_game as sg
    rar = tmp_path / "Mini-game-PPSA99999" / "mini-game.password.rar"
    rar.parent.mkdir()
    shutil.copy(FIXTURES / "mini-game.password.rar", rar)
    upd = tmp_path / "Patch" / "Game.Update.v1.05.PPSA99999.zip"
    # smaller than the mini game (280 KB): the base is the biggest complete one
    update = {"eboot.bin": b"ELF-synthetic-update" + sg.data(11, 16 * 1024),
              "sce_sys/param.json": sg.param_json("01.050.000", "0x0900000000000000"),
              "data/level3.pak": sg.data(14, 8 * 1024)}
    sg.pack_zip(upd, update)
    return rar, upd, update


def test_batch_passwords(tmp_path):
    # without a password it stays pending, with a wrong one it is flagged, with the right one it joins the batch
    rar, upd, _update = _batch_with_password(tmp_path)
    jobs, notes = analysis.analyze_folder(tmp_path, "")
    # the update (eboot + param.json) counts as a game on its own; the encrypted RAR stays pending, at the end
    assert [j.get("needs_password") for j in jobs] == [None, True] and jobs[0]["version"] == "01.050.000"
    p = jobs[1]
    assert p["path"] == rar and p["item"] == "Mini-game-PPSA99999/mini-game.password.rar" and p["title_id"] == "PPSA99999"
    assert not p["wrong_password"] and p["encrypted"] and p["bytes"] == rar.stat().st_size
    # wrong password of its own
    jobs, _n = analysis.analyze_folder(tmp_path, "", {str(rar): "wrong"})
    assert jobs[-1]["needs_password"] and jobs[-1]["wrong_password"]
    # right general password
    jobs, notes = analysis.analyze_folder(tmp_path, PASSWORD)
    assert len(jobs) == 1 and not jobs[0].get("needs_password"), notes
    j = jobs[0]
    assert [c["role"] for c in j["layers"]] == [analysis.ROLE_BASE, analysis.ROLE_UPDATE]
    assert j["encrypted"] and j["layers"][0]["encrypted"] and j["layers"][0]["password"] == PASSWORD and not j["layers"][1]["encrypted"]
    assert analysis.job_password(j) == PASSWORD and analysis.mismatched_passwords(j) == []
    # the archive's own password wins over the general one
    jobs, _n = analysis.analyze_folder(tmp_path, "wrong", {str(rar): PASSWORD})
    assert len(jobs) == 1 and analysis.job_password(jobs[0]) == PASSWORD
    # two layers with different passwords: warning
    j["layers"][1]["encrypted"], j["layers"][1]["password"] = True, "other"
    assert analysis.mismatched_passwords(j) == [j["layers"][1]]
    assert any("different passwords" in w for w in analysis.layer_warnings(j))


@pytest.mark.skipif(not environment.MKPFS.is_file() or not environment.mkpfs_reads_archives() or not environment.mkpfs_merges_layers(),
                    reason="needs mkpfs with --archive-root and --layer")
def test_pack_encrypted_batch(tmp_path):
    import synthetic_game as sg
    rar, upd, update = _batch_with_password(tmp_path)
    j = analysis.analyze_folder(tmp_path, "", {str(rar): PASSWORD})[0][0]
    output = tmp_path / f"{j['title_id']}.ffpfsc"
    o = {"destination": tmp_path, "level": 1, "cpu": 1, "verify": False, "two_pass": False, "temp_dir": tmp_path}
    code, lines = engine.pack(j, output, o)
    assert code == 0, "\n".join(lines)
    target = tmp_path / "unpacked"
    r = subprocess.run([str(environment.MKPFS), "unpack", "--deep", str(output), str(target)],
                       capture_output=True, text=True, env=environment.engine_env(), timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    seen = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    seen.pop("ampr_emu.index", None)
    assert seen == sg.merged_view([_expected(), update])
    # with a wrong password in the job, MkPFS fails and the cause is recognised
    j["layers"][0]["password"] = "wrong"
    code, lines = engine.pack(j, tmp_path / "wrong.ffpfsc", o)
    assert code != 0 and engine.failure_cause(lines) == "password", "\n".join(lines[-5:])


@pytest.mark.skipif(not environment.MKPFS.is_file() or not environment.mkpfs_reads_archives(), reason="needs mkpfs with --archive-root")
def test_pack_rar_from_subfolder(tmp_path):
    import shutil
    sub = tmp_path / "Mini-game-PPSA99999-PS5"
    sub.mkdir()
    for p in FIXTURES.glob("mini-game.parts.part*.rar"):
        shutil.copy(p, sub / p.name)
    j = analysis.analyze_folder(tmp_path, "")[0][0]
    output = tmp_path / f"{j['title_id']}.ffpfsc"
    o = {"destination": tmp_path, "level": 1, "cpu": 1, "verify": False, "two_pass": False, "temp_dir": tmp_path}
    code, lines = engine.pack(j, output, o)
    assert code == 0, "\n".join(lines)
    target = tmp_path / "unpacked"
    r = subprocess.run([str(environment.MKPFS), "unpack", "--deep", str(output), str(target)],
                       capture_output=True, text=True, env=environment.engine_env(), timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    seen = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    seen.pop("ampr_emu.index", None)
    assert seen == _expected()


@pytest.mark.skipif(not environment.MKPFS.is_file() or not environment.mkpfs_reads_archives(), reason="needs mkpfs with --archive-root")
@pytest.mark.parametrize("name,password", CASES)
def test_pack_rar_without_extracting(tmp_path, name, password):
    pkgs: list[str] = []
    notes: list[str] = []
    j = analysis.analyze_item(FIXTURES / name, password, pkgs, notes)
    assert j is not None and j["virtual"] and j["title_id"] == "PPSA99999", notes
    output = tmp_path / f"{j['title_id']}.ffpfsc"
    o = {"destination": tmp_path, "level": 1, "cpu": 1, "verify": False, "two_pass": False, "temp_dir": tmp_path}
    code, lines = engine.pack(j, output, o, password)
    assert code == 0, "\n".join(lines)
    target = tmp_path / "unpacked"
    r = subprocess.run([str(environment.MKPFS), "unpack", "--deep", str(output), str(target)],
                       capture_output=True, text=True, env=environment.engine_env(), timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    seen = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert seen.pop("ampr_emu.index", b"").startswith(b"AMPRIDX")  # the fixture is APR-EMU
    assert seen == _expected()
