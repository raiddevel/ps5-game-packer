"""Window smoke tests (CustomTkinter): it builds in both languages and scans the synthetic game (needs a display)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ctk = pytest.importorskip("customtkinter")
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    pytest.skip("no display", allow_module_level=True)

from ps5_game_packer import i18n  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic_game as sg  # noqa: E402


def _open(window, auto: dict):
    # on Windows, Tk sometimes fails to start right after another window was destroyed: retry
    import tkinter as tk
    for attempt in range(3):
        try:
            return window.Window(auto)
        except tk.TclError as e:
            if not ("init.tcl" in str(e) or "tk.tcl" in str(e)) or attempt == 2:
                raise
            time.sleep(1.0)
    raise AssertionError("unreachable")


def _spin(w, seconds: float, until=None) -> None:
    t0 = time.time()
    while time.time() - t0 < seconds:
        w.update()
        if until is not None and until():
            return
        time.sleep(0.05)


@pytest.mark.parametrize("lang,title,button", [("en", "PS5 Game Packer", "Pack"), ("es", "Comprimir juegos PS5", "Comprimir")])
def test_window_in_each_language(tmp_path, lang, title, button):
    i18n.activate(lang)
    import importlib
    from ps5_game_packer import window
    importlib.reload(window)
    sg.make_set(tmp_path / "games")
    try:
        w = _open(window, {"source": str(tmp_path / "games"), "destination": str(tmp_path)})
    except Exception as e:
        pytest.skip(f"cannot open the window: {e}")
    try:
        _spin(w, 20, until=lambda: bool(w.games))
        assert w.title() == title
        assert w.b_pack.cget("text") == button
        assert len(w.games) == 1 and len(w.games[0]["layers"]) == 4
        assert w.l_destination.cget("text")  # free space text
        frame = w.scan_box.winfo_children()[0]
        assert "eboot.bin" in frame.winfo_children()[0].cget("text")
        # unticking and moving a layer repaints the frame without breaking anything
        w._move_layer(w.games[0], frame, 1, +1)
        _spin(w, 0.2)
        assert w.games[0]["layers"][2]["role"] == "update"
    finally:
        w.destroy()


def test_auto_packs(tmp_path):
    # --auto with --start and --quit-when-done: packs and closes by itself
    from ps5_game_packer import environment, window
    if not environment.MKPFS.is_file():
        pytest.skip("needs the mkpfs of the venv")
    sg.make_base(tmp_path / "game")
    w = _open(window, {"source": str(tmp_path / "game"), "destination": str(tmp_path), "start": True, "quit": True})
    w.s_level.set("1")
    t0 = time.time()
    closed = False
    while time.time() - t0 < 120:
        try:
            w.update()
            if not w.winfo_exists():
                closed = True
                break
        except Exception:
            closed = True
            break
        time.sleep(0.05)
    assert closed, "the window did not close by itself"
    assert (tmp_path / f"Sample Game [{sg.TITLE_ID}].ffpfsc").is_file()  # English title from param.json
    # automatic log (next to the settings, temporary in the tests): session, job and summary with sizes
    log = environment.log_path().read_text(encoding="utf-8")
    assert "=== 20" in log and "ps5-game-packer" in log and "level 1 →" in log
    assert "Done in" in log and "→" in log and "%) at level 1 in" in log and "Final image size" in log


def test_auto_without_game(tmp_path):
    # empty folder: it does not wait forever; exits with code 2
    from ps5_game_packer import window
    (tmp_path / "empty").mkdir()
    w = _open(window, {"source": str(tmp_path / "empty"), "destination": str(tmp_path), "start": True, "quit": True})
    t0 = time.time()
    closed = False
    while time.time() - t0 < 30:
        try:
            w.update()
            if not w.winfo_exists():
                closed = True
                break
        except Exception:
            closed = True
            break
        time.sleep(0.05)
    assert closed and w.exit_code == 2


def test_refresh_button(tmp_path):
    from ps5_game_packer import window
    sg.make_base(tmp_path / f"Game.{sg.TITLE_ID}")
    w = _open(window, {"source": str(tmp_path), "destination": str(tmp_path)})
    try:
        _spin(w, 20, until=lambda: bool(w.games))
        assert len(w.games) == 1 and not w.games[0].get("layers")
        sg.make_update(tmp_path / f"Game.Update.v1.05.{sg.TITLE_ID}")
        w._refresh_source()
        _spin(w, 20, until=lambda: bool(w.games) and bool(w.games[0].get("layers")))
        assert len(w.games[0]["layers"]) == 2 and w.e_source.get() == str(tmp_path)
    finally:
        w.destroy()


def test_password_card(tmp_path):
    # encrypted RAR in a subfolder: card with its own field, and typing the password brings it into the list
    import shutil
    from ps5_game_packer import analysis, environment, window
    try:
        import mkpfs.archive_source
    except ImportError:
        pytest.skip("needs the mkpfs library")
    if not environment.has_unrar():
        pytest.skip("needs unrar")
    rar = tmp_path / "Mini-game-PPSA99999" / "mini-game.password.rar"
    rar.parent.mkdir()
    shutil.copy(Path(__file__).resolve().parent / "fixtures" / "mini-game.password.rar", rar)
    w = _open(window, {"source": str(tmp_path), "destination": str(tmp_path)})
    try:
        _spin(w, 20, until=lambda: bool(w.pending))
        assert len(w.pending) == 1 and not w.games
        assert w.e_password.winfo_manager()  # the general field shows in folder mode too
        assert "password needed" in w.l_scan.cget("text") or "Password-protected archives" in w.l_scan.cget("text")
        entry = w._password_cards[str(rar)]
        entry.insert(0, "wrong")
        w._card_password_changed(str(rar), entry)
        _spin(w, 20, until=lambda: bool(w.pending) and w.pending[0].get("wrong_password"))
        assert w.pending[0]["wrong_password"] and not w.games
        entry = w._password_cards[str(rar)]
        assert entry.get() == "wrong"  # keeps the text through the repaint...
        focus = w.focus_get()
        assert focus is None or focus is entry._entry  # ...and the focus (None in sessions without a desktop, e.g. over SSH)
        entry.delete(0, "end")
        entry.insert(0, "Secreto123")
        w._card_password_changed(str(rar), entry)
        _spin(w, 20, until=lambda: bool(w.games))
        assert not w.pending and len(w.games) == 1
        j = w.games[0]
        assert j["encrypted"] and analysis.job_password(j) == "Secreto123" and j["title_id"] == "PPSA99999"
        assert "🔒" in w.l_scan.cget("text")
        assert w.e_password.winfo_manager()
        assert w.passwords == {str(rar): "Secreto123"}
    finally:
        w.destroy()


def test_no_space(tmp_path, monkeypatch, fake_mkpfs):
    # MkPFS fails for lack of space: the window says so in those words
    from ps5_game_packer import environment, window
    monkeypatch.setattr(environment, "MKPFS", fake_mkpfs)
    monkeypatch.setattr(environment, "_CAPABILITIES", {"v": {"archives", "layers"}})
    monkeypatch.setenv("FAKE_MKPFS_FAIL", "enospc")
    sg.make_base(tmp_path / "game")
    w = _open(window, {"source": str(tmp_path / "game"), "destination": str(tmp_path), "start": True})
    try:
        _spin(w, 60, until=lambda: w.running)
        _spin(w, 90, until=lambda: not w.running)
        assert not w.running
        log = w.log_box.get("1.0", "end")
        assert "MkPFS finished with an error" in log and "No space left at the destination" in log
        assert w.l_status.cget("text").startswith("No space left at the destination")
        assert not list(tmp_path.glob("*.ffpfsc*"))
    finally:
        w.destroy()


def test_log_trimmed(tmp_path):
    from ps5_game_packer import window
    w = _open(window, {"source": "", "destination": str(tmp_path)})
    try:
        for i in range(window.LOG_MAX_LINES + 500):
            w._log(f"line {i}\n")
        lines = int(w.log_box.index("end-1c").split(".")[0])
        assert lines <= window.LOG_MAX_LINES
        assert w.log_box.get("end-2l", "end").strip().endswith(f"line {window.LOG_MAX_LINES + 499}")
    finally:
        w.destroy()


def test_pause_and_resume(tmp_path, monkeypatch, fake_mkpfs):
    from ps5_game_packer import environment, window
    monkeypatch.setattr(environment, "MKPFS", fake_mkpfs)
    monkeypatch.setattr(environment, "_CAPABILITIES", {"v": {"archives", "layers"}})
    monkeypatch.delenv("FAKE_MKPFS_FAIL", raising=False)
    sg.make_base(tmp_path / "game")
    w = _open(window, {"source": str(tmp_path / "game"), "destination": str(tmp_path), "start": True})
    try:
        _spin(w, 60, until=lambda: w.running and "%" in w.l_bar.cget("text"))
        assert w.b_pause.cget("state") == "normal" and w.b_pause.cget("text") == "Pause"
        pid = w.process.pid if w.process is not None else None
        w._on_pause()
        assert w.paused and w.b_pause.cget("text") == "Resume"
        _spin(w, 0.5)
        frozen = w.l_bar.cget("text")
        assert frozen.startswith("Paused · ") and "%" in frozen
        if environment.IS_LINUX and pid:
            assert Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "T"
        _spin(w, 1.5)
        assert w.l_bar.cget("text") == frozen  # the time left does not move
        assert w.running
        w._on_pause()
        assert not w.paused and w.b_pause.cget("text") == "Pause"
        _spin(w, 90, until=lambda: not w.running)
        assert not w.running and w.l_bar.cget("text") == "Done"
        assert w.b_pause.cget("state") == "disabled"
        assert list(tmp_path.glob("*.ffpfsc"))
        log = w.log_box.get("1.0", "end")
        assert "Paused" in log and "Resumed" in log
    finally:
        w.destroy()


def test_cancel_while_paused(tmp_path, monkeypatch, fake_mkpfs):
    # cancelling while paused resumes the process before killing it
    from ps5_game_packer import environment, window
    monkeypatch.setattr(environment, "MKPFS", fake_mkpfs)
    monkeypatch.setattr(environment, "_CAPABILITIES", {"v": {"archives", "layers"}})
    monkeypatch.delenv("FAKE_MKPFS_FAIL", raising=False)
    sg.make_base(tmp_path / "game")
    w = _open(window, {"source": str(tmp_path / "game"), "destination": str(tmp_path), "start": True})
    try:
        _spin(w, 60, until=lambda: w.running and "%" in w.l_bar.cget("text"))
        w._on_pause()
        _spin(w, 0.3)
        w._on_cancel()
        _spin(w, 30, until=lambda: not w.running)
        assert not w.running and w.cancelled and w.l_bar.cget("text") == "Cancelled"
        assert not list(tmp_path.glob("*.ffpfsc*"))
    finally:
        w.destroy()


def test_progress_flood(tmp_path, monkeypatch, fake_mkpfs):
    # 200,000 progress lines in a few seconds: the interface does not stall
    from ps5_game_packer import environment, window
    monkeypatch.setattr(environment, "MKPFS", fake_mkpfs)
    monkeypatch.setattr(environment, "_CAPABILITIES", {"v": {"archives", "layers"}})
    sg.make_base(tmp_path / "game")
    w = _open(window, {"source": str(tmp_path / "game"), "destination": str(tmp_path), "start": True, "quit": True})
    t0 = time.time()
    worst = 0.0
    last = time.time()
    repaints = 0
    previous = ""
    while time.time() - t0 < 90:
        try:
            w.update()
            if not w.winfo_exists():
                break
        except Exception:
            break
        now = time.time()
        worst = max(worst, now - last)
        last = now
        text = w.l_bar.cget("text")
        if text != previous:
            repaints += 1
            previous = text
        time.sleep(0.01)
    assert worst < 1.0, f"the interface stalled for {worst:.1f} s"
    assert repaints > 20, f"the bar was only repainted {repaints} times"


def test_broken_param_in_window(tmp_path, monkeypatch):
    # batch with a good game and a broken one: red warning, unticked, and "Add param.json…" fixes it
    from ps5_game_packer import analysis, environment, window
    sg.make_base(tmp_path / "games" / f"Good.{sg.TITLE_ID}")
    sg.make_base(tmp_path / "games" / "Broken.PPSA99998", param="broken")
    w = _open(window, {"source": str(tmp_path / "games"), "destination": str(tmp_path)})
    try:
        _spin(w, 20, until=lambda: len(w.games) == 2)
        by_id = {g["title_id"]: g for g in w.games}
        assert by_id["PPSA99998"]["param"] == analysis.PARAM_BROKEN and by_id[sg.TITLE_ID]["param"] == analysis.PARAM_OK
        ticked = {g["title_id"]: bool(c.get()) for c, g in w.checks}
        assert ticked == {sg.TITLE_ID: True, "PPSA99998": False}
        assert [g["title_id"] for g in w._selected()] == [sg.TITLE_ID]
        # the card (layer frame) of the broken one starts with the red warning; the good one's, with the layers title
        children = w.scan_box.winfo_children()
        frames = [h for h in children if isinstance(h, ctk.CTkFrame)]
        assert len(frames) == 2
        broken_frame = next(m for c, g in w.checks for m in frames if g["title_id"] == "PPSA99998" and frames.index(m) == children.index(c) // 2)
        first = broken_frame.winfo_children()[0]
        assert first.cget("text").startswith("⚠ sce_sys/param.json cannot be read") and first.cget("text_color") == window.COLOR_ERROR
        assert "Add param.json" in first.cget("text")
        good_frame = next(m for m in frames if m is not broken_frame)
        assert "Updates, backport or DLC" in good_frame.winfo_children()[0].cget("text")
        # with the broken one ticked, Pack asks (and here the answer is no)
        questions: list[str] = []
        monkeypatch.setattr(w, "_ask", lambda text: questions.append(text) or False)
        for c, g in w.checks:
            if g["title_id"] == "PPSA99998":
                c.select()
        w._on_pack()
        assert len(questions) == 1 and "PPSA99998" in questions[0] and "sce_sys/param.json is missing or cannot be read" in questions[0]
        assert not w.running
        # a param.json that is no good: a message and nothing else
        messages: list[str] = []
        monkeypatch.setattr(w, "_message", lambda text: messages.append(text))
        bad = tmp_path / "bad.json"
        bad.write_text("[1, 2]")
        assert not w._param_chosen(by_id["PPSA99998"], broken_frame, bad)
        assert len(messages) == 1 and "is not a valid param.json" in messages[0] and not by_id["PPSA99998"].get("layers")
        # one from another game: it asks; here the answer is no
        other = tmp_path / "other" / "param.json"
        other.parent.mkdir()
        other.write_bytes(sg.param_json("01.000.000", "0x0900000000000000"))  # TITLE_ID = PPSA99999, not PPSA99998
        assert not w._param_chosen(by_id["PPSA99998"], broken_frame, other)
        assert len(questions) == 2 and "PPSA99999" in questions[1] and "PPSA99998" in questions[1]
        # the good one (same game): temporary layer on top, warning gone, new title and container name
        good = tmp_path / "downloads" / "param.json"
        good.parent.mkdir()
        good.write_bytes(sg.param_json("01.020.000", "0x0900000000000000").replace(sg.TITLE_ID.encode(), b"PPSA99998"))
        assert w._param_chosen(by_id["PPSA99998"], broken_frame, good)
        j = by_id["PPSA99998"]
        assert [c["role"] for c in j["layers"]] == [analysis.ROLE_BASE, analysis.ROLE_LAYER] and j["layers"][1]["temporary"]
        temporary = j["layers"][1]["path"]
        assert temporary.parent == environment.CACHE_DIR and (temporary / "sce_sys" / "param.json").read_bytes() == good.read_bytes()
        assert j["param"] == analysis.PARAM_OK and j["name"] == "Sample Game" and j["version"] == "01.020.000"
        assert analysis.container_name(j) == "Sample Game [PPSA99998].ffpfsc"
        _spin(w, 0.3)
        texts = [h.cget("text") for h in broken_frame.winfo_children() if isinstance(h, ctk.CTkLabel)]
        assert not any(t.startswith("⚠") for t in texts)
        assert 'param.json: the one from "downloads/param.json"' in texts[0]
        tags = [x.cget("text") for f in broken_frame.winfo_children() for x in f.winfo_children() if isinstance(x, ctk.CTkLabel)]
        assert "param.json" in tags
        box = next(c for c, g in w.checks if g is j)
        assert "Sample Game · v01.020.000" in box.cget("text") and "merge of 2 layers" in box.cget("text")
        # swapping the param.json for another removes the previous one and its folder (the new one is made from scratch, same name)
        (temporary / "mark.txt").write_text("old")
        assert w._param_chosen(j, broken_frame, good)
        assert len(j["layers"]) == 2 and not (temporary / "mark.txt").exists()
        temporary = j["layers"][1]["path"]
        assert (temporary / "sce_sys" / "param.json").is_file() and not list(environment.CACHE_DIR.glob("param-*-2"))
        # unticking the layer: the warning is back
        j["layers"][1]["_check"].deselect()
        w._layer_toggled(j, broken_frame, j["layers"][1])
        assert j["param"] == analysis.PARAM_BROKEN and broken_frame.winfo_children()[0].cget("text").startswith("⚠")
        assert "merge of 2 layers" in box.cget("text") and "Broken.PPSA99998" in box.cget("text")
    finally:
        w.destroy()
    assert not temporary.exists()  # closing deletes the temporary folder


def test_missing_param_alone(tmp_path):
    from ps5_game_packer import analysis, window
    sg.make_base(tmp_path / f"Game.{sg.TITLE_ID}", param="missing")
    w = _open(window, {"source": str(tmp_path / f"Game.{sg.TITLE_ID}"), "destination": str(tmp_path)})
    try:
        _spin(w, 20, until=lambda: bool(w.games))
        assert len(w.games) == 1 and w.games[0]["param"] == analysis.PARAM_MISSING
        assert w.l_scan.cget("text").startswith(f"{sg.TITLE_ID} · Game.{sg.TITLE_ID}\n")  # no title: the folder name
        frame = w.scan_box.winfo_children()[0]
        assert frame.winfo_children()[0].cget("text").startswith("⚠ sce_sys/param.json is missing: ShadowMountPlus will not be able to register")
    finally:
        w.destroy()


def test_ticks_and_param_survive_rescan(tmp_path):
    # unticking a game and fixing another with "Add param.json…" must survive a rescan of the same folder
    from ps5_game_packer import analysis, environment, window
    sg.make_base(tmp_path / "games" / f"Good.{sg.TITLE_ID}")
    sg.make_base(tmp_path / "games" / "Broken.PPSA99998", param="broken")
    good = tmp_path / "downloads" / "param.json"
    good.parent.mkdir()
    good.write_bytes(sg.param_json("01.020.000", "0x0900000000000000").replace(sg.TITLE_ID.encode(), b"PPSA99998"))
    w = _open(window, {"source": str(tmp_path / "games"), "destination": str(tmp_path)})
    try:
        _spin(w, 20, until=lambda: len(w.games) == 2)
        by_id = {g["title_id"]: g for g in w.games}
        checks = {g["title_id"]: c for c, g in w.checks}
        checks[sg.TITLE_ID].deselect()
        w._game_toggled(checks[sg.TITLE_ID], by_id[sg.TITLE_ID])
        children = w.scan_box.winfo_children()
        broken_frame = children[children.index(checks["PPSA99998"]) + 1]
        assert w._param_chosen(by_id["PPSA99998"], broken_frame, good)
        assert checks["PPSA99998"].get() == 1  # fixed: ticked on its own
        temporary = by_id["PPSA99998"]["layers"][1]["path"]
        w._refresh_source()
        _spin(w, 20, until=lambda: len(w.games) == 2 and w.games is not list(by_id.values()))
        _spin(w, 0.5)
        by_id = {g["title_id"]: g for g in w.games}
        checks = {g["title_id"]: c for c, g in w.checks}
        assert checks[sg.TITLE_ID].get() == 0  # the user's choice survived
        assert checks["PPSA99998"].get() == 1
        j = by_id["PPSA99998"]
        assert [c["role"] for c in j["layers"]] == [analysis.ROLE_BASE, analysis.ROLE_LAYER] and j["param"] == analysis.PARAM_OK
        assert j["layers"][1]["path"] == temporary and (temporary / "sce_sys" / "param.json").is_file()
        assert [g["title_id"] for g in w._selected()] == ["PPSA99998"]
        # another folder: ticks reset and the kept param.json layer is dropped with its folder
        sg.make_base(tmp_path / "other" / f"Other.{sg.TITLE_ID}")
        w._set_text(w.e_source, str(tmp_path / "other"))
        w._source_changed()
        _spin(w, 20, until=lambda: len(w.games) == 1 and w.games[0]["path"].name == f"Other.{sg.TITLE_ID}")
        assert not temporary.exists() and not w._kept_params and not w._manual_ticks
        assert not list(environment.CACHE_DIR.glob("param-PPSA99998*"))
    finally:
        w.destroy()


def test_long_list_scrolls(tmp_path):
    # many games: the list stops growing at 40 % of the screen and scrolls, so the buttons stay on screen
    from ps5_game_packer import window
    for i in range(8):  # eight different games (a different titleId inside each param.json)
        root = tmp_path / "games" / f"Game{i}.PPSA9999{i}"
        sg.make_base(root)
        (root / "sce_sys" / "param.json").write_bytes(sg.param_json("01.000.000", "0x0900000000000000").replace(sg.TITLE_ID.encode(), f"PPSA9999{i}".encode()))
    w = _open(window, {"source": str(tmp_path / "games"), "destination": str(tmp_path)})
    try:
        _spin(w, 30, until=lambda: len(w.games) == 8)
        _spin(w, 0.5)
        height = int(w.scan_box.cget("height"))
        assert 100 < height <= int(w.winfo_screenheight() * 0.4 / w.scan_box._get_widget_scaling()) + 1
        assert len(w.checks) == 8 and len(w.scan_box.winfo_children()) >= 16
        assert w.b_pack.winfo_rooty() < w.winfo_screenheight()  # the Pack button is on screen
    finally:
        w.destroy()
