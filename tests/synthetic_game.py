"""SYNTHETIC PS5 game for tests and screenshots: made-up bytes, no real content at all.

Creates a folder laid out the way ShadowMountPlus expects (eboot.bin and sce_sys/param.json at the root) and, next
to it, an update (folder), a backport (folder) and a DLC (ZIP), all with the same TITLE_ID.

Usage: python3 tests/synthetic_game.py DESTINATION_FOLDER [--big] [--ampr]
"""
from __future__ import annotations

import json
import random
import sys
import zipfile
from pathlib import Path

TITLE_ID = "PPSA99999"
MB = 1 << 20


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def param_json(version: str, req_fw: str, name: str = "Sample Game") -> bytes:
    return json.dumps({
        "titleId": TITLE_ID,
        "contentId": f"EP0000-{TITLE_ID}_00-SAMPLE0000000000",
        "contentVersion": version,
        "requiredSystemSoftwareVersion": req_fw,
        "localizedParameters": {
            "defaultLanguage": "en-US",
            "en-US": {"titleName": name},
            "es-ES": {"titleName": "Juego de ejemplo"},
        },
    }, indent=2).encode("utf-8")


def data(seed: int, size: int) -> bytes:
    """Deterministic, poorly compressible bytes (seeded PRNG), so the container is not trivial."""
    return random.Random(seed).randbytes(size)


BROKEN_PARAM_BYTES = b"{ this is not JSON: truncated or corrupt param.json"


def make_base(root: Path, *, big: bool = False, ampr: bool = False, param: str = "ok") -> dict[str, bytes]:
    """Base game v1.00 (FW 9.00). Returns {relative path: bytes}.

    `param`: "ok" (normal sce_sys/param.json), "broken" (the file exists but is not JSON) or "missing" (no file).
    """
    files = {
        "eboot.bin": b"ELF-synthetic-base" + data(1, 64 * 1024),
        "sce_sys/param.json": param_json("01.000.000", "0x0900000000000000"),
        "sce_sys/icon0.png": data(2, 8 * 1024),
        "data/level1.pak": data(3, (12 if big else 3) * MB),
        "data/level2.pak": data(4, (8 if big else 2) * MB),
        "data/sound/music.bank": data(5, 1 * MB),
        "data/text/en.txt": b"Hello\n",
        "data/empty.dat": b"",
    }
    if ampr:
        files["fakelib/libSceAmpr.sprx"] = b"\x7fELF-fake-ampr" + data(6, 32 * 1024)
    if param == "broken":
        files["sce_sys/param.json"] = BROKEN_PARAM_BYTES
    elif param == "missing":
        del files["sce_sys/param.json"]
    for rel, b in files.items():
        _write(root / rel, b)
    return files


def make_loose_param(root: Path, version: str = "01.000.000", req_fw: str = "0x0900000000000000") -> dict[str, bytes]:
    """Folder with ONLY a (valid) sce_sys/param.json: what goes on top of a game whose param.json is missing or broken."""
    files = {"sce_sys/param.json": param_json(version, req_fw)}
    for rel, b in files.items():
        _write(root / rel, b)
    return files


def make_update(root: Path) -> dict[str, bytes]:
    """Update v1.05 (FW 9.00): new eboot, changed level1, one new file."""
    files = {
        "eboot.bin": b"ELF-synthetic-update" + data(11, 64 * 1024),
        "sce_sys/param.json": param_json("01.050.000", "0x0900000000000000"),
        "data/level1.pak": data(13, 3 * MB),
        "data/level3.pak": data(14, 1 * MB),
    }
    for rel, b in files.items():
        _write(root / rel, b)
    return files


def make_backport(root: Path) -> dict[str, bytes]:
    """Backport v1.05 that asks for a lower firmware (FW 5.50): just eboot and param.json."""
    files = {
        "eboot.bin": b"ELF-synthetic-backport" + data(21, 64 * 1024),
        "sce_sys/param.json": param_json("01.050.000", "0x0550000000000000"),
    }
    for rel, b in files.items():
        _write(root / rel, b)
    return files


def make_dlc_zip(zip_path: Path, *, with_eboot: bool = False, inner_folder: str = f"{TITLE_ID}-app") -> dict[str, bytes]:
    """DLC as a ZIP, with the game folder inside (the way they usually come)."""
    files = {
        "data/dlc/outfit.pak": data(31, 2 * MB),
        "data/dlc/map.pak": data(32, 1 * MB),
    }
    if with_eboot:
        files["eboot.bin"] = b"ELF-synthetic-dlc" + data(33, 64 * 1024)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{inner_folder}/", "")
        for rel, b in files.items():
            zf.writestr(f"{inner_folder}/{rel}", b)
        zf.writestr(f"{inner_folder}/readme.txt", "Sample DLC\n")
    files["readme.txt"] = b"Sample DLC\n"
    return files


def pack_zip(zip_path: Path, files: dict[str, bytes], inner_folder: str = f"{TITLE_ID}-app") -> None:
    """Puts a game or a layer (as returned by make_base/make_update...) into a ZIP, inside a folder."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, b in files.items():
            zf.writestr(f"{inner_folder}/{rel}", b)


def make_set(destination: Path, *, big: bool = False, ampr: bool = False) -> dict[str, Path]:
    """Folder with base + update + backport + DLC (zip), descriptive names."""
    paths = {
        "base": destination / f"Sample.Game.{TITLE_ID}",
        "update": destination / f"Sample.Game.Update.v1.05.{TITLE_ID}",
        "backport": destination / f"Sample.Game.v1.05.Backport.5.50.{TITLE_ID}",
        "dlc": destination / f"Sample.Game.DLC.Pack.{TITLE_ID}.zip",
    }
    make_base(paths["base"], big=big, ampr=ampr)
    make_update(paths["update"])
    make_backport(paths["backport"])
    make_dlc_zip(paths["dlc"])
    return paths


def merged_view(layers: list[dict[str, bytes]]) -> dict[str, bytes]:
    """What should be seen after stacking the layers (the last one wins)."""
    out: dict[str, bytes] = {}
    for layer in layers:
        out.update(layer)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    r = make_set(Path(sys.argv[1]), big="--big" in sys.argv, ampr="--ampr" in sys.argv)
    for k, v in r.items():
        print(f"{k}: {v}")
