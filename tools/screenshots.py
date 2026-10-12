#!/usr/bin/env python3
"""Window screenshots for the README (docs/screenshots/*.png) with the synthetic game: main window (scan with
layers), packing, done, param-json and param-json-fixed in English, and only the main window in Spanish.

Usage: python tools/screenshots.py [OUTPUT_FOLDER] [--quick]
Run it with the Python of the installed venv (needs a display, Pillow and the engine). On a small screen use a nested
one: `Xephyr :99 -screen 1600x1200 &` and `DISPLAY=:99 ...`. With --quick the game is small and "packing" is skipped.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from PIL import ImageGrab  # noqa: E402

import synthetic_game as sg  # noqa: E402
from ps5_game_packer import environment, i18n  # noqa: E402

GEOMETRY = "1000x860+40+20"


def fatten(base: Path, megabytes: int = 1536) -> None:
    """Adds big, half-compressible files (about 63 %) to the base game so that packing takes a few seconds."""
    random_block, zero_block = 640 * 1024, 384 * 1024
    per_file = 256
    for n in range(megabytes // per_file):
        path = base / "data" / "world" / f"zone{n + 1}.pak"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            for _ in range(per_file):
                f.write(os.urandom(random_block))
                f.write(b"\0" * zero_block)


def prepare(demo: Path, quick: bool) -> dict[str, Path]:
    if demo.exists():
        shutil.rmtree(demo)
    paths = sg.make_set(demo / "games", big=True)
    if not quick:
        fatten(paths["base"])
    # Batch for the param.json case: one fine, one with the file broken and one without it, plus a valid param.json apart
    sg.make_base(demo / "param" / "Sample.Game.PPSA99999")
    sg.make_base(demo / "param" / "Another.Game.PPSA99998", param="broken")
    sg.make_base(demo / "param" / "Third.Game.PPSA99997", param="missing")
    loose = demo / "downloads" / "param.json"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(json.dumps({
        "titleId": "PPSA99998", "contentId": "EP0000-PPSA99998_00-SAMPLE0000000000", "contentVersion": "01.020.000",
        "requiredSystemSoftwareVersion": "0x0900000000000000",
        "localizedParameters": {"defaultLanguage": "en-US", "en-US": {"titleName": "Another Game"}, "es-ES": {"titleName": "Otro juego"}},
    }, indent=2).encode("utf-8"))
    (demo / "PS5" / "homebrew").mkdir(parents=True)
    return {"games": demo / "games", "param": demo / "param", "loose": loose, "destination": demo / "PS5" / "homebrew"}


def open_window(lang: str, auto: dict):
    i18n.activate(lang)
    from ps5_game_packer import window
    importlib.reload(window)
    w = window.Window(auto)
    w.geometry(GEOMETRY)
    for _ in range(5):
        w.update()
        time.sleep(0.05)
    try:  # pointer away from the window: over a button its tooltip would show in the screenshot
        w.event_generate("<Motion>", warp=True, x=w.winfo_width() + 300, y=w.winfo_height() + 100)
    except Exception:
        pass
    w._ask = lambda text: True  # the program's questions (space, overwrite...) are answered "yes"
    w._message = lambda text: print("message:", text)
    return w


def wait_for(w, cond, seconds: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        w.update()
        if cond():
            return True
        time.sleep(0.05)
    return False


def grab(w, target: Path) -> None:
    for _ in range(8):
        w.update()
        time.sleep(0.05)
    x, y, width, height = w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    extra = {"xdisplay": os.environ.get("DISPLAY", ":0")} if environment.IS_LINUX else {}
    im = ImageGrab.grab(bbox=(x, y, x + width, y + height), **extra)
    im.save(target)
    print(target, im.size)


def frame_of(w, job):
    """Layer frame of a job: it comes right after its checkbox."""
    children = w.scan_box.winfo_children()
    for c, g in w.checks:
        if g is job:
            return children[children.index(c) + 1]
    return children[0]


def percent(w) -> int:
    m = re.search(r"(\d+) %", w.l_bar.cget("text"))
    return int(m.group(1)) if m else -1


def language_shots(lang: str, output: Path, r: dict[str, Path], quick: bool, main_only: bool = False) -> None:
    suffix = "" if lang == "en" else f"-{lang}"
    for old in r["destination"].iterdir():
        old.unlink()
    w = open_window(lang, {"source": str(r["games"]), "destination": str(r["destination"])})
    if not wait_for(w, lambda: w.games and not w._scanning, 60):
        raise SystemExit("the scan did not finish")
    grab(w, output / f"main-window{suffix}.png")
    if main_only:
        w.destroy()
        return
    w.s_level.set("7")
    w.c_verify.deselect()
    w._on_pack()
    if not wait_for(w, lambda: w.running, 30):
        raise SystemExit("packing did not start")
    if not quick and wait_for(w, lambda: percent(w) >= 30 or not w.running, 600) and w.running:
        grab(w, output / f"packing{suffix}.png")
    wait_for(w, lambda: not w.running, 1200)
    grab(w, output / f"done{suffix}.png")
    w.destroy()

    w = open_window(lang, {"source": str(r["param"]), "destination": str(r["destination"])})
    if not wait_for(w, lambda: w.games and not w._scanning, 60):
        raise SystemExit("the batch scan did not finish")
    grab(w, output / f"param-json{suffix}.png")
    broken = next(g for g in w.games if g["title_id"] == "PPSA99998")
    w._param_chosen(broken, frame_of(w, broken), r["loose"])
    grab(w, output / f"param-json-fixed{suffix}.png")
    w.destroy()


def main(argv: list[str]) -> int:
    quick = "--quick" in argv
    argv = [a for a in argv if a != "--quick"]
    output = Path(argv[0]) if argv else ROOT / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    demo = Path(tempfile.gettempdir()) / "ps5-game-packer-demo"
    r = prepare(demo, quick)
    try:
        language_shots("en", output, r, quick)
        language_shots("es", output, r, quick, main_only=True)
    finally:
        shutil.rmtree(demo, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
