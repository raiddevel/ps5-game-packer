"""Pure logic: game detection, layer roles, ordering and warnings."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ps5_game_packer import analysis, environment, i18n
from ps5_game_packer.analysis import (
    PARAM_BROKEN,
    PARAM_MISSING,
    PARAM_OK,
    ROLE_BACKPORT,
    ROLE_BASE,
    ROLE_DLC,
    ROLE_LAYER,
    ROLE_UPDATE,
    add_layer,
    analyze_folder,
    change_role,
    clean_name,
    container_name,
    delete_temp_layer,
    final_eboot,
    find_games,
    fw_num,
    fw_text,
    gb,
    group_layers,
    is_first_volume,
    job_param_state,
    layer_role,
    layer_warnings,
    param_json_layer,
    param_layer,
    param_state,
    param_warning,
    recompute_totals,
    thousands,
    time_left,
    title_id_of,
    validate_param,
    validate_param_file,
    version_key,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic_game as sg  # noqa: E402


@pytest.fixture(autouse=True)
def _engine_python(monkeypatch):
    # RAR/ZIP are listed with the MkPFS library of the test python (or of the venv)
    if environment.VENV_PY is not None and not environment.VENV_PY.exists():
        monkeypatch.setattr(environment, "VENV_PY", Path(sys.executable))


def test_version_and_firmware():
    assert fw_num("0x1270000000000000") == 0x1270
    assert fw_text("0x1270000000000000") == "12.70"
    assert fw_num("") == 0 and fw_num("junk") == 0
    assert version_key("01.050.000") > version_key("01.000.000")
    assert version_key("") == ()
    assert is_first_volume(Path("game.part1.rar")) and is_first_volume(Path("game.rar")) and is_first_volume(Path("x.zip"))
    assert not is_first_volume(Path("game.part2.rar")) and not is_first_volume(Path("game.r00"))


def test_volumes_size(tmp_path):
    # the brackets in the name are not character classes
    for n, size in ((1, 100), (2, 100), (3, 40)):
        (tmp_path / f"[Group]-Game.(v1).part{n:02d}.rar").write_bytes(b"x" * size)
    (tmp_path / "[Group]-Game.(v1).part01.rar.txt").write_bytes(b"x" * 5)  # not a part
    (tmp_path / "[Group]-Other.part01.rar").write_bytes(b"x" * 7)
    assert analysis.volumes_size(tmp_path / "[Group]-Game.(v1).part01.rar") == 240
    assert analysis.volumes_size(tmp_path / "[Group]-Other.part01.rar") == 7
    (tmp_path / "single.rar").write_bytes(b"x" * 3)
    assert analysis.volumes_size(tmp_path / "single.rar") == 3
    assert analysis.volumes_size(tmp_path / "does-not-exist.rar") == 0


def test_number_format_by_language():
    assert gb(1_500_000_000) == "1.5 GB" and gb(25_300_000) == "25.3 MB" and gb(0) == "0.0 GB" and thousands(12345) == "12,345"
    i18n.activate("es")
    assert gb(1_500_000_000) == "1,5 GB" and gb(25_300_000) == "25,3 MB" and thousands(12345) == "12.345"


def test_find_games(tmp_path):
    root = tmp_path / "Game"
    sg.make_base(root, ampr=True)
    games = find_games(tmp_path)
    assert len(games) == 1
    g = games[0]
    assert g["title_id"] == sg.TITLE_ID and g["path"] == root
    assert g["ampr"] and not g["ampr_index"]
    assert g["version"] == "01.000.000" and g["files"] == 9
    assert g["name"] == "Sample Game"
    i18n.activate("es")
    assert find_games(tmp_path)[0]["name"] == "Juego de ejemplo"


def test_layer_role():
    base = {"item": "Game", "version": "01.000.000", "req_fw": "0x0900000000000000", "has_eboot": True}
    assert layer_role({"item": "Game.Update.v1.05", "version": "", "req_fw": "", "has_eboot": True}, base) == ROLE_UPDATE
    assert layer_role({"item": "Game.DLC.Pack", "version": "", "req_fw": "", "has_eboot": False}, base) == ROLE_DLC
    assert layer_role({"item": "Game.Backport", "version": "", "req_fw": "", "has_eboot": True}, base) == ROLE_BACKPORT
    # neutral names: by contents
    assert layer_role({"item": "a", "version": "01.050.000", "req_fw": "0x0550000000000000", "has_eboot": True}, base) == ROLE_BACKPORT
    assert layer_role({"item": "b", "version": "01.050.000", "req_fw": "0x0900000000000000", "has_eboot": True}, base) == ROLE_UPDATE
    assert layer_role({"item": "c", "version": "", "req_fw": "", "has_eboot": False}, base) == ROLE_DLC
    assert layer_role({"item": "d", "version": "", "req_fw": "", "has_eboot": True}, base) == ROLE_LAYER


def test_layers_in_order(tmp_path):
    paths = sg.make_set(tmp_path)
    jobs, notes = analyze_folder(tmp_path, "")
    assert len(jobs) == 1, notes
    j = jobs[0]
    assert j["title_id"] == sg.TITLE_ID and j["path"] == paths["base"]
    roles = [c["role"] for c in j["layers"]]
    assert roles == [ROLE_BASE, ROLE_UPDATE, ROLE_BACKPORT, ROLE_DLC]
    assert [c["item"] for c in j["layers"]][1] == paths["update"].name
    dlc = j["layers"][3]
    assert dlc["virtual"] and dlc["path"] == paths["dlc"] and dlc["subpath"] == f"{sg.TITLE_ID}-app"
    assert not dlc["has_eboot"]
    assert j["version"] == "01.050.000"
    assert j["bytes"] == sum(c["bytes"] for c in j["layers"])
    # the final eboot is the backport's (last active layer with an eboot)
    assert final_eboot(j)["role"] == ROLE_BACKPORT
    assert layer_warnings(j) == []


def test_eboot_warnings(tmp_path):
    paths = sg.make_set(tmp_path)
    paths["dlc"].unlink()
    sg.make_dlc_zip(tmp_path / f"Sample.Game.DLC.{sg.TITLE_ID}.zip", with_eboot=True)
    jobs, _notes = analyze_folder(tmp_path, "")
    j = jobs[0]
    assert final_eboot(j)["role"] == ROLE_DLC
    warnings = layer_warnings(j)
    # two warnings: it covers the backport's eboot and the update's
    assert len(warnings) == 2 and "backport" in warnings[0] and "DLC" in warnings[1]
    # unticking the DLC hands control back to the backport
    j["layers"][-1]["active"] = False
    assert final_eboot(j)["role"] == ROLE_BACKPORT and layer_warnings(j) == []
    # no active layer with an eboot: warning that it will not start
    for c in j["layers"]:
        c["active"] = c["role"] == ROLE_BASE
    j["layers"][0]["has_eboot"] = False
    assert final_eboot(j) is None and "eboot.bin" in layer_warnings(j)[0]


def test_layers_with_neutral_names(tmp_path):
    sg.make_base(tmp_path / "A")
    sg.make_update(tmp_path / "B")
    sg.make_backport(tmp_path / "C")
    sg.make_dlc_zip(tmp_path / "D.zip", inner_folder=f"{sg.TITLE_ID}-app")
    jobs, notes = analyze_folder(tmp_path, "")
    assert len(jobs) == 1
    roles = [c["role"] for c in jobs[0]["layers"]]
    assert roles == [ROLE_BASE, ROLE_UPDATE, ROLE_BACKPORT, ROLE_DLC]


def test_duplicates_and_layers_without_base(tmp_path):
    sg.make_base(tmp_path / "One")
    sg.make_base(tmp_path / "Two")
    jobs, notes = analyze_folder(tmp_path, "")
    assert len(jobs) == 1 and any("same game" in n for n in notes)
    # a complete update (eboot + param.json) counts as a game: it can be packed on its own
    alone = tmp_path / "alone"
    sg.make_update(alone / f"Update.{sg.TITLE_ID}")
    jobs, notes = analyze_folder(alone, "")
    assert len(jobs) == 1 and jobs[0]["version"] == "01.050.000"
    # a DLC without the base game cannot be packed
    alone_dlc = tmp_path / "alone-dlc"
    sg.make_dlc_zip(alone_dlc / f"DLC.{sg.TITLE_ID}.zip")
    jobs, notes = analyze_folder(alone_dlc, "")
    assert jobs == [] and any("base game is missing" in n for n in notes)


def test_archives_in_subfolders(tmp_path):
    # a subfolder holding the unextracted RAR/ZIP counts as if the archive were loose
    base_zip = tmp_path / f"Game-{sg.TITLE_ID}-PS5" / f"Game-{sg.TITLE_ID}-PS5.zip"
    sg.pack_zip(base_zip, sg.make_base(tmp_path / "tmp-base"))
    upd_zip = tmp_path / "Patches" / "v1.05" / f"Game.Update.v1.05.{sg.TITLE_ID}.zip"
    sg.pack_zip(upd_zip, sg.make_update(tmp_path / "tmp-upd"))
    import shutil
    shutil.rmtree(tmp_path / "tmp-base")
    shutil.rmtree(tmp_path / "tmp-upd")
    (tmp_path / f"Game-{sg.TITLE_ID}-PS5" / "readme.txt").write_text("notes\n")
    jobs, notes = analyze_folder(tmp_path, "")
    assert len(jobs) == 1, notes
    assert not any("base game is missing" in n for n in notes)
    j = jobs[0]
    assert j["virtual"] and j["path"] == base_zip and j["subpath"] == f"{sg.TITLE_ID}-app"
    assert [c["role"] for c in j["layers"]] == [ROLE_BASE, ROLE_UPDATE]
    assert j["layers"][1]["path"] == upd_zip and j["layers"][1]["item"] == f"Patches/v1.05/{upd_zip.name}"
    assert j["layers"][0]["item"] == f"{base_zip.parent.name}/{base_zip.name}"
    # the TITLE_ID is taken from the folder when the archive name does not carry it
    no_id = tmp_path / f"Other-{sg.TITLE_ID}" / "content.zip"
    sg.pack_zip(no_id, {"data/extra.pak": b"x" * 100})
    pkgs: list[str] = []
    notes = []
    items = analysis.analyze_items(no_id.parent, "", pkgs, notes)
    assert len(items) == 1 and items[0]["title_id"] == sg.TITLE_ID and not items[0]["complete"]
    # the first folder with archives wins: levels are not mixed
    assert analysis.archives_in_folder(tmp_path / "Patches") == [upd_zip]
    assert analysis.archives_in_folder(tmp_path) == [base_zip, no_id]  # level 2 (there are no loose ones at level 1)


def test_add_layer_and_change_role(tmp_path):
    base_dir = tmp_path / "Game"
    sg.make_base(base_dir)
    j = find_games(tmp_path)[0]
    notes: list[str] = []
    pkgs: list[str] = []
    sg.make_update(tmp_path / "extra" / "Patch")
    it = analysis.analyze_item(tmp_path / "extra" / "Patch", "", pkgs, notes)
    assert it is not None and it["title_id"] == sg.TITLE_ID
    assert add_layer(j, it) is None
    assert [c["role"] for c in j["layers"]] == [ROLE_BASE, ROLE_UPDATE]
    assert add_layer(j, dict(it)) == "duplicate"
    change_role(j, j["layers"][1], ROLE_DLC)
    assert j["layers"][1]["role"] == ROLE_DLC
    assert j["bytes"] == j["layers"][0]["bytes"] + j["layers"][1]["bytes"]


def test_pkg_kept_aside(tmp_path):
    sg.make_base(tmp_path / "Game")
    (tmp_path / "Game.Update.pkg").write_bytes(b"\x7fCNT" + b"\0" * 64)
    jobs, notes = analyze_folder(tmp_path, "")
    assert len(jobs) == 1 and any("Package Installer" in n and ".pkg" in n for n in notes)


def test_role_name_translated():
    assert analysis.role_name(ROLE_UPDATE) == "update"
    assert analysis.role_name(ROLE_BASE) == "base game"
    i18n.activate("es")
    assert analysis.role_name(ROLE_UPDATE) == "actualización"


def test_clean_name():
    assert clean_name("Marvel's Spider-Man 2: Remastered") == "Marvel's Spider-Man 2 - Remastered"
    assert clean_name("Ratchet & Clank™: Una dimensión aparte") == "Ratchet & Clank - Una dimension aparte"
    assert clean_name('a\\b/c:d*e?f"g<h>i|j') == "a b c - d e f g h i j"
    assert clean_name("Game...  ") == "Game"
    assert clean_name("  Hello   world \t\n ") == "Hello world"
    assert clean_name("Año 🎮 Émilie Noël – «Œuvre»") == "Ano Emilie Noel - 'OEuvre'"
    assert clean_name("日本語のタイトル") == ""  # outside ASCII: it vanishes (the ID will be used)
    assert clean_name("CON") == "CON_" and clean_name("nul.x") == "nul.x_" and clean_name("Control") == "Control"
    long = clean_name("word " * 20)
    assert len(long) <= 80 and long.endswith("word") and not long.endswith(" ")
    assert len(clean_name("x" * 200)) == 80
    assert clean_name("Ends with dot and dash. -", 10) == "Ends with"


def test_container_name():
    j = {"title_id": "PPSA99999", "name": "Sample Game"}
    assert container_name(j) == "Sample Game [PPSA99999].ffpfsc"
    assert container_name(j, "id") == "PPSA99999.ffpfsc"
    assert container_name({"title_id": "PPSA99999", "name": ""}) == "PPSA99999.ffpfsc"
    assert container_name({"title_id": "PPSA99999", "name": "日本語"}) == "PPSA99999.ffpfsc"
    assert container_name({"title_id": "", "name": "No id"}) == "No id [game].ffpfsc"
    long = container_name({"title_id": "PPSA12345", "name": "A very long title indeed " * 10})
    assert long.endswith(" [PPSA12345].ffpfsc") and len(long) <= 80 + len(".ffpfsc")
    assert container_name({"title_id": "ppsa:1/2", "name": "x"}) == "x [ppsa - 1 2].ffpfsc"


def test_time_left():
    assert time_left(0) == "under 1 min"
    assert time_left(59) == "under 1 min"
    assert time_left(60) == "1 min"
    assert time_left(754) == "13 min"
    assert time_left(3600) == "1 h 00 min"
    assert time_left(75.3 * 60) == "1 h 15 min"
    assert time_left(2 * 3600 + 5 * 60 + 40) == "2 h 06 min"
    i18n.activate("es")
    assert time_left(30) == "menos de 1 min" and time_left(3900) == "1 h 05 min"


def test_param_broken_or_missing(tmp_path):
    # with the param.json broken or missing the game is still listed, with its state
    good = tmp_path / "Good"
    sg.make_base(good)
    sg.make_base(tmp_path / "Broken.PPSA99998", param="broken")
    sg.make_base(tmp_path / "Missing.PPSA99997", param="missing")
    games = {g["path"].name: g for g in find_games(tmp_path)}
    assert set(games) == {"Good", "Broken.PPSA99998", "Missing.PPSA99997"}
    assert games["Good"]["param"] == PARAM_OK and games["Good"]["name"] == "Sample Game"
    broken = games["Broken.PPSA99998"]
    assert broken["param"] == PARAM_BROKEN and broken["title_id"] == "PPSA99998" and broken["name"] == "" and broken["version"] == ""
    missing = games["Missing.PPSA99997"]
    assert missing["param"] == PARAM_MISSING and missing["title_id"] == "PPSA99997" and missing["files"] == 7
    assert param_state(good)[0] == PARAM_OK and param_state(good)[1]["titleId"] == sg.TITLE_ID
    assert param_state(tmp_path / "Broken.PPSA99998") == (PARAM_BROKEN, {}) and param_state(tmp_path / "Missing.PPSA99997") == (PARAM_MISSING, {})
    # what counts as broken: not JSON, not an object, no titleId; with a BOM and lower-case title_id it is fine
    assert validate_param(b"{ no json")[0] == PARAM_BROKEN
    assert validate_param(b"[1, 2]")[0] == PARAM_BROKEN
    assert validate_param(b'{"contentVersion": "01.000.000"}')[0] == PARAM_BROKEN
    assert validate_param('﻿{"title_id": " ppsa00001 "}') == (PARAM_OK, {"title_id": " ppsa00001 "})
    assert title_id_of({"title_id": " ppsa00001 "}) == "PPSA00001" and title_id_of({}) == ""
    assert validate_param_file(tmp_path / "does-not-exist.json") == (PARAM_BROKEN, {})
    # the card warning and the container name (no title: just the ID)
    assert param_warning(games["Good"]) == "" and job_param_state(games["Good"]) == PARAM_OK
    assert param_warning(broken).startswith("sce_sys/param.json cannot be read: ShadowMountPlus will not be able to register")
    assert param_warning(missing).startswith("sce_sys/param.json is missing: ") and '"Add param.json…"' in param_warning(missing)
    assert container_name(broken) == "PPSA99998.ffpfsc"
    i18n.activate("es")
    assert param_warning(missing).startswith("falta sce_sys/param.json: ShadowMountPlus no podrá registrar")
    i18n.activate("en")
    # analyze_folder lists all three (they are different TITLE_IDs)
    jobs, notes = analyze_folder(tmp_path, "")
    assert sorted(j["title_id"] for j in jobs) == ["PPSA99997", "PPSA99998", "PPSA99999"], notes
    assert {j["title_id"]: j["param"] for j in jobs} == {"PPSA99997": PARAM_MISSING, "PPSA99998": PARAM_BROKEN, "PPSA99999": PARAM_OK}


def test_winning_param(tmp_path):
    # the title and version come from the param.json of the highest active layer that has one
    paths = sg.make_set(tmp_path)
    (paths["base"] / "sce_sys" / "param.json").write_bytes(sg.BROKEN_PARAM_BYTES)
    jobs, notes = analyze_folder(tmp_path, "")
    assert len(jobs) == 1, notes
    j = jobs[0]
    assert [c["role"] for c in j["layers"]] == [ROLE_BASE, ROLE_UPDATE, ROLE_BACKPORT, ROLE_DLC]  # the broken one is still the base
    assert [c["param"] for c in j["layers"]] == [PARAM_BROKEN, PARAM_OK, PARAM_OK, PARAM_MISSING]
    assert param_layer(j) is j["layers"][2]  # the backport: the highest with a param.json (the DLC has none)
    assert j["param"] == PARAM_OK and param_warning(j) == ""
    assert j["name"] == "Sample Game" and j["version"] == "01.050.000" and j["req_fw"] == "0x0550000000000000"
    assert container_name(j) == f"Sample Game [{sg.TITLE_ID}].ffpfsc"
    # only the base active: the broken one is back, no title or version, and the container is named by the ID alone
    for c in j["layers"][1:]:
        c["active"] = False
    recompute_totals(j)
    assert j["param"] == PARAM_BROKEN and j["name"] == "" and j["version"] == "" and container_name(j) == f"{sg.TITLE_ID}.ffpfsc"
    assert param_warning(j).startswith("sce_sys/param.json cannot be read")
    j["layers"][1]["active"] = True
    recompute_totals(j)
    assert param_layer(j) is j["layers"][1] and j["version"] == "01.050.000" and j["req_fw"] == "0x0900000000000000"
    # base WITHOUT param.json + a folder with only sce_sys/param.json next to it (what used to be done by hand with "Add folder…")
    alone = tmp_path / "alone"
    sg.make_base(alone / f"Game.{sg.TITLE_ID}", param="missing")
    sg.make_loose_param(alone / f"param.{sg.TITLE_ID}", version="01.020.000")
    jobs, notes = analyze_folder(alone, "")
    assert len(jobs) == 1, notes
    j = jobs[0]
    assert len(j["layers"]) == 2 and j["layers"][0]["param"] == PARAM_MISSING and j["layers"][1]["param"] == PARAM_OK
    assert j["param"] == PARAM_OK and j["name"] == "Sample Game" and j["version"] == "01.020.000"
    assert final_eboot(j)["role"] == ROLE_BASE and layer_warnings(j) == []
    # a game on its own, no layers and no param.json: the job says so
    alone2 = tmp_path / "alone2"
    sg.make_base(alone2 / f"Game.{sg.TITLE_ID}", param="missing")
    jobs, _notes = analyze_folder(alone2, "")
    assert len(jobs) == 1 and jobs[0]["param"] == PARAM_MISSING and job_param_state(jobs[0]) == PARAM_MISSING


def test_temporary_param_layer(tmp_path):
    # temporary copy in the cache, layer on top of the game, deleted when removed
    sg.make_base(tmp_path / f"Game.{sg.TITLE_ID}", param="broken")
    j = find_games(tmp_path)[0]
    assert j["param"] == PARAM_BROKEN and container_name(j) == f"{sg.TITLE_ID}.ffpfsc"
    good = tmp_path / "downloads" / "param.json"
    good.parent.mkdir()
    good.write_bytes(sg.param_json("01.020.000", "0x0900000000000000"))
    state, param = validate_param_file(good)
    assert state == PARAM_OK and title_id_of(param) == sg.TITLE_ID
    it = param_json_layer(good, param)
    assert it["temporary"] and it["path"] == environment.CACHE_DIR / f"param-{sg.TITLE_ID}" and it["param"] == PARAM_OK
    assert (it["path"] / "sce_sys" / "param.json").read_bytes() == good.read_bytes()
    assert it["item"] == "downloads/param.json" and it["param_source"] == str(good) and not it["has_eboot"]
    assert add_layer(j, it, role=ROLE_LAYER) is None
    assert [c["role"] for c in j["layers"]] == [ROLE_BASE, ROLE_LAYER]
    assert param_layer(j) is j["layers"][1] and j["param"] == PARAM_OK and param_warning(j) == ""
    assert j["name"] == "Sample Game" and j["version"] == "01.020.000" and j["title_id"] == sg.TITLE_ID
    assert container_name(j) == f"Sample Game [{sg.TITLE_ID}].ffpfsc"
    assert j["bytes"] == j["layers"][0]["bytes"] + good.stat().st_size and j["files"] == 9
    # unticked, the broken one is in charge again
    j["layers"][1]["active"] = False
    recompute_totals(j)
    assert j["param"] == PARAM_BROKEN and j["name"] == ""
    # a second copy of the same ID does not overwrite the first
    it2 = param_json_layer(good, param)
    assert it2["path"].name == f"param-{sg.TITLE_ID}-2" and it["path"].exists()
    delete_temp_layer(it)
    delete_temp_layer(it2)
    assert not it["path"].exists() and not it2["path"].exists()
    # it only deletes its own (inside the cache and with the prefix)
    foreign = tmp_path / "param-foreign"
    foreign.mkdir()
    delete_temp_layer({"path": foreign, "temporary": True})
    delete_temp_layer(dict(it, temporary=False))
    assert foreign.exists()


def test_archive_without_id_and_broken_param(tmp_path):
    # a RAR/ZIP whose name has no TITLE_ID and whose param.json is broken keeps the archive name as stand-in ID
    assert analysis.archive_stem(Path("Some.Game.part1.rar")) == "Some.Game"
    assert analysis.archive_stem(Path("Some.Game.r00")) == "Some.Game" and analysis.archive_stem(Path("x.zip")) == "x"
    z = tmp_path / "games" / "Some.Game.zip"
    sg.pack_zip(z, sg.make_base(tmp_path / "work" / "src", param="broken"), inner_folder="Some.Game-app")  # no ID anywhere
    jobs, notes = analyze_folder(tmp_path / "games", "")
    assert len(jobs) == 1 and not notes, notes
    j = jobs[0]
    assert j["virtual"] and j["complete"] and j["param"] == PARAM_BROKEN and j["title_id"] == "Some.Game"
    assert container_name(j) == "Some.Game.ffpfsc" and param_warning(j).startswith("sce_sys/param.json cannot be read")
    # the fix: a param.json layer on top gives it the real TITLE_ID and title
    good = tmp_path / "downloads" / "param.json"
    good.parent.mkdir()
    good.write_bytes(sg.param_json("01.000.000", "0x0900000000000000"))
    it = param_json_layer(good, validate_param_file(good)[1])
    try:
        assert add_layer(j, it, role=ROLE_LAYER) is None
        assert j["param"] == PARAM_OK and j["title_id"] == sg.TITLE_ID and container_name(j) == f"Sample Game [{sg.TITLE_ID}].ffpfsc"
    finally:
        delete_temp_layer(it)


def test_lone_param_joins_unreadable_game(tmp_path):
    # game folder without a readable param.json (and no ID in its name) + a ZIP holding only the right param.json
    games = tmp_path / "games"
    sg.make_base(games / "Requiem", param="broken")
    sg.pack_zip(games / f"param-{sg.TITLE_ID}.zip", sg.make_loose_param(tmp_path / "work" / "p", version="01.020.000"))
    jobs, notes = analyze_folder(games, "")
    assert len(jobs) == 1 and not notes, notes
    j = jobs[0]
    assert [c["role"] for c in j["layers"]][0] == ROLE_BASE and len(j["layers"]) == 2 and j["layers"][1]["param_only"]
    assert j["param"] == PARAM_OK and j["title_id"] == sg.TITLE_ID and j["name"] == "Sample Game" and j["version"] == "01.020.000"
    assert final_eboot(j)["role"] == ROLE_BASE and container_name(j) == f"Sample Game [{sg.TITLE_ID}].ffpfsc"
    # the same with the param.json in a folder named after the ID
    other = tmp_path / "other"
    sg.make_base(other / "Requiem", param="missing")
    sg.make_loose_param(other / f"{sg.TITLE_ID}-param")
    jobs, notes = analyze_folder(other, "")
    assert len(jobs) == 1 and jobs[0]["param"] == PARAM_OK and jobs[0]["title_id"] == sg.TITLE_ID, notes
    # a real DLC (not just a param.json) of another ID is still kept apart with a note
    dlc = tmp_path / "dlc-case"
    sg.make_base(dlc / "Requiem", param="broken")
    sg.make_dlc_zip(dlc / "Other.Game.DLC.PPSA11111.zip", inner_folder="PPSA11111-app")
    jobs, notes = analyze_folder(dlc, "")
    assert len(jobs) == 1 and jobs[0]["param"] == PARAM_BROKEN and any("base game is missing" in n for n in notes)
