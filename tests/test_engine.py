"""The real engine: folder, ZIP and layer merging with MkPFS (--archive-root, --layer), and byte-by-byte
unpacking. Needs the mkpfs of the venv (or PS5_GAME_PACKER_VENV) with those options."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ps5_game_packer import analysis, engine, environment

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic_game as sg  # noqa: E402

pytestmark = pytest.mark.skipif(not environment.MKPFS.is_file(), reason="needs the mkpfs of the venv")


def _options(destination: Path) -> dict:
    return {"destination": destination, "level": 1, "cpu": 1, "verify": False, "two_pass": False, "temp_dir": destination}


def _unpack(container: Path, target: Path) -> dict[str, bytes]:
    r = subprocess.run([str(environment.MKPFS), "unpack", "--deep", str(container), str(target)],
                       capture_output=True, text=True, env=environment.engine_env(), timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    return {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}


def test_pack_command(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "_CAPABILITIES", {"v": {"archives", "layers"}})
    o = _options(tmp_path)
    j = {"path": tmp_path / "game", "virtual": False, "subpath": "", "title_id": "PPSA99999"}
    cmd = engine.pack_command(j, tmp_path / "x.ffpfsc", o)
    assert cmd[:3] == [str(environment.MKPFS), "pack", "folder"] and cmd[-2:] == [str(tmp_path / "game"), str(tmp_path / "x.ffpfsc")]
    assert "--layer" not in cmd and "--archive-root" not in cmd and "--verify" not in cmd
    j = {"path": tmp_path / "g.rar", "virtual": True, "subpath": "PPSA99999-app", "title_id": "PPSA99999"}
    cmd = engine.pack_command(j, tmp_path / "x.ffpfsc", dict(o, verify=True))
    assert cmd[cmd.index("--archive-root") + 1] == "PPSA99999-app" and "--verify" in cmd
    layers = [
        {"path": tmp_path / "base", "virtual": False, "subpath": "", "complete": True, "active": True},
        {"path": tmp_path / "upd", "virtual": False, "subpath": "", "complete": True, "active": True},
        {"path": tmp_path / "dlc.zip", "virtual": True, "subpath": "PPSA99999-app", "complete": False, "active": False},
        {"path": tmp_path / "dlc2.zip", "virtual": True, "subpath": "PPSA99999-app", "complete": False, "active": True},
    ]
    j = {"path": tmp_path / "base", "virtual": False, "subpath": "", "title_id": "PPSA99999", "layers": layers}
    cmd = engine.pack_command(j, tmp_path / "x.ffpfsc", o)
    assert cmd.count("--layer") == 2  # the unticked layer is left out
    assert f"{tmp_path / 'upd'}::" in cmd and f"{tmp_path / 'dlc2.zip'}::PPSA99999-app" in cmd
    assert not engine.allows_two_pass(j)
    # engine without --layer
    monkeypatch.setattr(environment, "_CAPABILITIES", {"v": {"archives"}})
    with pytest.raises(engine.EngineError, match="no_layers"):
        engine.pack_command(j, tmp_path / "x.ffpfsc", o)
    monkeypatch.setattr(environment, "_CAPABILITIES", {"v": set()})
    with pytest.raises(engine.EngineError, match="no_archives"):
        engine.pack_command({"path": tmp_path / "g.rar", "virtual": True, "subpath": ""}, tmp_path / "x.ffpfsc", o)


def test_pack_folder(tmp_path):
    base = sg.make_base(tmp_path / "game", ampr=True)
    j = analysis.find_games(tmp_path / "game")[0]
    output = tmp_path / "out" / f"{j['title_id']}.ffpfsc"
    output.parent.mkdir()
    code, lines = engine.pack(j, output, _options(output.parent))
    assert code == 0, "\n".join(lines)
    assert output.is_file() and any("Final image size" in ln for ln in lines)
    seen = _unpack(output, tmp_path / "unpacked")
    assert "ampr_emu.index" in seen
    del seen["ampr_emu.index"]
    assert seen == base


@pytest.mark.skipif(not environment.mkpfs_reads_archives(), reason="this mkpfs does not read archives (--archive-root)")
def test_pack_zip_without_extracting(tmp_path):
    import zipfile
    root = tmp_path / "src"
    files = sg.make_base(root)
    z = tmp_path / "Game.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for rel in files:
            zf.write(root / rel, f"{sg.TITLE_ID}-app/{rel}")
    pkgs: list[str] = []
    notes: list[str] = []
    j = analysis.analyze_item(z, "", pkgs, notes)
    assert j is not None and j["virtual"] and j["subpath"] == f"{sg.TITLE_ID}-app", notes
    output = tmp_path / "out" / f"{j['title_id']}.ffpfsc"
    output.parent.mkdir()
    code, lines = engine.pack(j, output, _options(output.parent))
    assert code == 0, "\n".join(lines)
    assert _unpack(output, tmp_path / "unpacked") == files


@pytest.mark.skipif(not environment.mkpfs_merges_layers(), reason="this mkpfs does not merge layers (--layer)")
def test_merge_layers(tmp_path):
    # base + update + backport + DLC: the container holds the stacked view and the regenerated AMPR index
    paths = sg.make_set(tmp_path / "games", ampr=True)
    base = sg.make_base(tmp_path / "expected-base", ampr=True)
    update = sg.make_update(tmp_path / "expected-update")
    backport = sg.make_backport(tmp_path / "expected-backport")
    dlc = sg.make_dlc_zip(tmp_path / "expected-dlc.zip")
    jobs, notes = analysis.analyze_folder(tmp_path / "games", "")
    assert len(jobs) == 1, notes
    j = jobs[0]
    assert [c["role"] for c in j["layers"]] == [analysis.ROLE_BASE, analysis.ROLE_UPDATE, analysis.ROLE_BACKPORT, analysis.ROLE_DLC]
    output = tmp_path / "out" / f"{j['title_id']}.ffpfsc"
    output.parent.mkdir()
    code, lines = engine.pack(j, output, _options(output.parent))
    assert code == 0, "\n".join(lines)
    assert any("Layer 3:" in ln for ln in lines)
    seen = _unpack(output, tmp_path / "unpacked")
    expected = sg.merged_view([base, update, backport, dlc])
    assert "ampr_emu.index" in seen and seen["ampr_emu.index"].startswith(b"AMPRIDX")
    del seen["ampr_emu.index"]
    assert seen == expected
    assert seen["eboot.bin"].startswith(b"ELF-synthetic-backport")
    # the base game was not touched
    assert not (paths["base"] / "ampr_emu.index").exists()

    # untick the backport: the update's eboot wins
    j["layers"][2]["active"] = False
    output2 = tmp_path / "out2" / f"{j['title_id']}.ffpfsc"
    output2.parent.mkdir()
    code, lines = engine.pack(j, output2, _options(output2.parent))
    assert code == 0, "\n".join(lines)
    seen2 = _unpack(output2, tmp_path / "unpacked2")
    assert seen2["eboot.bin"].startswith(b"ELF-synthetic-update")


def test_automatic_log(tmp_path):
    path = tmp_path / "config" / "log.txt"
    f = environment.open_log(path, maximum=1000)
    assert f is not None
    f.write("hello\n")
    f.close()
    assert path.read_text() == "hello\n"
    path.write_text("".join(f"línea {i:04d}\n" for i in range(300)))  # ~3.3 KB > 1000
    f = environment.open_log(path, maximum=1000)
    f.write("end\n")
    f.close()
    text = path.read_text(encoding="utf-8")
    assert len(text.encode()) <= 500 + 4 and text.startswith("línea ") and text.endswith("línea 0299\nend\n")
    odd = environment.open_log(tmp_path / "odd-log.txt" / "x.txt")  # an odd path does not blow up
    if odd is not None:
        odd.close()


def test_temporary_config():
    # conftest sends the settings to a temporary folder
    import tempfile
    assert str(environment.CONFIG_FILE).startswith(tempfile.gettempdir())
    environment.save_config({"level": 1})
    assert environment.load_config() == {"level": 1} and environment.CONFIG_FILE.is_file()
    environment.CONFIG_FILE.unlink()


def test_engine_env(monkeypatch):
    monkeypatch.setenv("MKPFS_ARCHIVE_PASSWORD", "old")
    env = environment.engine_env("")
    assert env["PYTHONUTF8"] == "1" and env["PYTHONUNBUFFERED"] == "1" and "MKPFS_ARCHIVE_PASSWORD" not in env
    assert environment.engine_env("Secreto123")["MKPFS_ARCHIVE_PASSWORD"] == "Secreto123"


def test_failure_cause():
    assert engine.failure_cause(["[INFO] Packing", "[ERROR] [Errno 28] No space left on device: 'x.ffpfsc.tmp'"]) == "space"
    assert engine.failure_cause(["OSError: [WinError 112] There is not enough space on the disk"]) == "space"
    assert engine.failure_cause(["Error: the archive needs a password (or the password is wrong); pass --password"]) == "password"
    assert engine.failure_cause(["Error: wrong password for game.rar: CRC error"]) == "password"
    assert engine.failure_cause(["Final image size: 1 GB", "Successfully wrote image"]) is None
    assert engine.failure_cause([]) is None


def test_low_priority(tmp_path):
    import shutil
    if environment.IS_WINDOWS:
        script = "import time; time.sleep(5)"
    else:
        script = "import os, sys, time; print(os.getpriority(os.PRIO_PROCESS, 0)); sys.stdout.flush(); time.sleep(5)"
    cmd = [sys.executable, "-c", script]
    command = engine.low_priority_command(cmd)
    assert command[-3:] == cmd
    if environment.IS_LINUX and shutil.which("ionice"):
        assert command[:3] == ["ionice", "-c", "3"]
    proc = engine.start(cmd, tmp_path)
    try:
        if environment.IS_WINDOWS:
            import psutil
            assert psutil.Process(proc.pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
        else:
            assert proc.stdout is not None
            assert proc.stdout.readline().strip() == b"10"
            if environment.IS_LINUX and shutil.which("ionice"):
                klass = subprocess.run(["ionice", "-p", str(proc.pid)], capture_output=True, text=True, timeout=10).stdout
                assert klass.strip().startswith("idle"), klass
    finally:
        engine.kill(proc, wait=1)
        proc.wait(timeout=15)


def _process_state(pid: int) -> str:
    # 'T' stopped, 'S' or 'R' running
    if environment.IS_LINUX:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    import psutil
    return "T" if psutil.Process(pid).status() == psutil.STATUS_STOPPED else "S"


def _wait_state(pid: int, state: str, seconds: float = 3.0) -> str:
    import time
    t0 = time.time()
    seen = ""
    while time.time() - t0 < seconds:
        seen = _process_state(pid)
        if seen == state:
            return seen
        time.sleep(0.05)
    return seen


def test_pause(tmp_path):
    # neither flag resumes what the other one stopped; children are not stopped
    import threading
    import time
    if not environment.IS_LINUX:
        pytest.importorskip("psutil")
    script = ("import subprocess, sys, time; h = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              "print(h.pid, flush=True); time.sleep(60)")
    proc = engine.start([sys.executable, "-c", script], tmp_path)
    assert proc.stdout is not None
    child = int(proc.stdout.readline().strip())
    pause = engine.Pause(proc)
    try:
        assert _wait_state(proc.pid, "S") == "S" and not pause.stopped
        assert pause.set("user", True) and _wait_state(proc.pid, "T") == "T"
        time.sleep(0.2)
        assert _process_state(child) != "T"  # the child keeps running: it stops by itself when its queue/pipe fills up
        pause.set("throttle", False)
        time.sleep(0.2)
        assert _process_state(proc.pid) == "T"  # the throttle does not wake what the user stopped
        pause.set("throttle", True)
        pause.set("user", False)
        time.sleep(0.2)
        assert _process_state(proc.pid) == "T"  # nor the user what the throttle holds
        pause.set("throttle", False)
        assert _wait_state(proc.pid, "S") == "S"
        # the real throttle, with the user paused: stopping it does not resume the process
        pause.set("user", True)
        assert _wait_state(proc.pid, "T") == "T"
        stop = threading.Event()
        thread = threading.Thread(target=engine.throttle, args=(proc, tmp_path / "x.ffpfsc", stop, pause), daemon=True)
        thread.start()
        time.sleep(0.6)
        stop.set()
        thread.join(5)
        assert _process_state(proc.pid) == "T" and pause.user
        pause.release()
        assert _wait_state(proc.pid, "S") == "S" and not pause.stopped
        with pytest.raises(ValueError):
            pause.set("other", True)
    finally:
        pause.release()
        engine.kill(proc, wait=1)
        proc.wait(timeout=15)
    assert engine.can_pause()
    # kill() does take the child with it (process group on Linux/macOS, taskkill /T on Windows)
    for _i in range(50):
        try:
            if _process_state(child) in ("Z", "X"):
                break
        except (FileNotFoundError, ProcessLookupError, Exception):
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the child is still alive after killing MkPFS")


def test_kill(tmp_path):
    sg.make_base(tmp_path / "game", big=True)
    j = analysis.find_games(tmp_path / "game")[0]
    output = tmp_path / f"{j['title_id']}.ffpfsc"
    cmd = engine.pack_command(j, output, dict(_options(tmp_path), level=9))
    proc = engine.start(cmd, tmp_path)
    try:
        assert proc.poll() is None
        engine.kill(proc, wait=2)
        proc.wait(timeout=15)
        assert proc.returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(not environment.mkpfs_merges_layers(), reason="this mkpfs does not merge layers (--layer)")
def test_param_layer_with_engine(tmp_path):
    # broken base + layer with the good param.json: the container carries the good one
    base_dir = tmp_path / f"Game.{sg.TITLE_ID}"
    base = sg.make_base(base_dir, param="broken")
    j = analysis.find_games(base_dir)[0]
    assert j["param"] == analysis.PARAM_BROKEN and analysis.container_name(j) == f"{sg.TITLE_ID}.ffpfsc"
    good = tmp_path / "downloads" / "param.json"
    good.parent.mkdir()
    good.write_bytes(sg.param_json("01.020.000", "0x0900000000000000"))
    state, param = analysis.validate_param_file(good)
    assert state == analysis.PARAM_OK
    it = analysis.param_json_layer(good, param)
    try:
        assert analysis.add_layer(j, it, role=analysis.ROLE_LAYER) is None
        assert j["param"] == analysis.PARAM_OK
        output = tmp_path / "out" / analysis.container_name(j)
        assert output.name == f"Sample Game [{sg.TITLE_ID}].ffpfsc"
        output.parent.mkdir()
        code, lines = engine.pack(j, output, _options(output.parent))
        assert code == 0, "\n".join(lines)
        assert any("Layer 1:" in ln for ln in lines) and any(f"{sg.TITLE_ID}.exfat" in ln for ln in lines)
        seen = _unpack(output, tmp_path / "unpacked")
        assert seen["sce_sys/param.json"] == good.read_bytes()  # the good one, byte for byte; the broken one is gone
        expected = dict(base, **{"sce_sys/param.json": good.read_bytes()})
        assert seen == expected
    finally:
        analysis.delete_temp_layer(it)
    assert not it["path"].exists()
    # the same with the base WITHOUT param.json and the param.json folder added by hand ("Add folder…")
    missing = tmp_path / "missing"
    base2 = sg.make_base(missing / f"Game.{sg.TITLE_ID}", param="missing")
    layer = sg.make_loose_param(missing / f"param.{sg.TITLE_ID}")
    jobs, notes = analysis.analyze_folder(missing, "")
    assert len(jobs) == 1 and jobs[0]["param"] == analysis.PARAM_OK, notes
    output2 = tmp_path / "out2" / analysis.container_name(jobs[0])
    output2.parent.mkdir()
    code, lines = engine.pack(jobs[0], output2, _options(output2.parent))
    assert code == 0, "\n".join(lines)
    assert _unpack(output2, tmp_path / "unpacked2") == sg.merged_view([base2, layer])
