#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Contents of a RAR/ZIP read with the MkPFS library, without extracting anything. As a program, `listing.py ARCHIVE`
prints JSON (the password comes in the ARCHIVE_PASSWORD variable); as a module, `summary(archive, password)`. It
imports nothing from ps5_game_packer so that it can run on its own inside the engine's venv."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PASSWORD_ENV = "ARCHIVE_PASSWORD"
_APP_RE = re.compile(r"-app0?$", re.IGNORECASE)


def _error_code(message: str, archive: Path) -> str:
    m = message.lower()
    if "unrar was not found" in m:
        return "no_unrar"
    if "password" in m or "encrypted" in m:
        return "password"
    if "unsupported archive type" in m:
        return "format"
    if "unrar" in m:
        return "unrar"
    if archive.name.lower().endswith(".zip"):
        return "zip"
    return "read"


PARAM_OK, PARAM_BROKEN, PARAM_MISSING = "ok", "broken", "missing"


def _param_of(subtree) -> tuple[str, dict]:
    """(state, contents) of a subtree's sce_sys/param.json: ok (JSON object with titleId), broken or missing."""
    if not subtree.has_file("sce_sys/param.json"):
        return PARAM_MISSING, {}
    try:
        data = json.loads(subtree.read_bytes("sce_sys/param.json", limit=4 * 1024 * 1024).decode("utf-8-sig"))
    except Exception:
        return PARAM_BROKEN, {}
    if not isinstance(data, dict) or not str(data.get("titleId") or data.get("title_id") or "").strip():
        return PARAM_BROKEN, {}
    return PARAM_OK, data


def _title(param: dict) -> str:
    lp = param.get("localizedParameters") or {}
    for key in (lp.get("defaultLanguage", ""), "en-US", "en-GB", "es-ES"):
        v = lp.get(key)
        if isinstance(v, dict) and v.get("titleName"):
            return str(v["titleName"])
    for v in lp.values():
        if isinstance(v, dict) and v.get("titleName"):
            return str(v["titleName"])
    return ""


def summary(archive: Path, password: str = "") -> dict:
    """Lists the archive and describes what is inside: complete games, candidate roots, loose .pkg files."""
    from mkpfs.archive_source import ArchiveError, ArchiveSource

    archive = Path(archive)
    if not archive.name.lower().endswith((".rar", ".zip")) and not re.search(r"\.r\d\d$", archive.name.lower()):
        return {"ok": False, "error_code": "format", "error": archive.name}
    try:
        source = ArchiveSource(archive, password=password or None)
    except ArchiveError as e:
        code = _error_code(str(e), archive)
        return {"ok": False, "error_code": code, "error": str(e), "encrypted": code == "password"}
    except Exception as e:
        return {"ok": False, "error_code": "read", "error": str(e)}

    paths = {e.rel_path for e in source.entries if not e.is_dir}
    dirs = {e.rel_path for e in source.entries if e.is_dir}
    for p in paths:
        parent = p.rpartition("/")[0]
        while parent and parent not in dirs:
            dirs.add(parent)
            parent = parent.rpartition("/")[0]
    candidates = sorted(
        d for d in dirs
        if _APP_RE.search(d.rsplit("/", 1)[-1]) or (d + "/eboot.bin") in paths or (d + "/sce_sys/param.json") in paths
    )
    single_root = ""
    try:
        from mkpfs.layered_source import detect_layer_root

        single_root = detect_layer_root(source.entries)
    except ImportError:
        single_root = candidates[0] if len(candidates) == 1 else ""
    except ArchiveError:
        single_root = ""

    # Game roots: the ones MkPFS finds plus folders with eboot.bin but no param.json (to warn that it is missing).
    eboots = sorted(p for p in paths if p == "eboot.bin" or p.endswith("/eboot.bin"))
    roots = set(source.game_roots())
    for e in eboots:
        base = e[: -len("eboot.bin")].rstrip("/")
        if base not in roots and (f"{base}/sce_sys/param.json" if base else "sce_sys/param.json") not in paths:
            roots.add(base)
    games = []
    for root in sorted(roots):
        sub = source.subtree(root)
        state, param = _param_of(sub)
        files = [e for e in sub.entries() if not e.is_dir]
        games.append({
            "subpath": root,
            "title_id": str(param.get("titleId") or param.get("title_id") or ""),
            "name": _title(param),
            "version": str(param.get("contentVersion") or ""),
            "req_fw": str(param.get("requiredSystemSoftwareVersion") or ""),
            "files": len(files),
            "bytes": sum(e.size for e in files),
            "ampr": sub.has_file("fakelib/libSceAmpr.sprx"),
            "ampr_index": sub.has_file("ampr_emu.index"),
            "param": param,
            "param_state": state,
        })
    # param.json of layers that are not games (DLC or update without eboot.bin), to know which one ends up inside.
    params = {g["subpath"]: {"state": g["param_state"], "param": g["param"]} for g in games}
    for p in paths:
        if p == "sce_sys/param.json" or p.endswith("/sce_sys/param.json"):
            d = p[: -len("sce_sys/param.json")].rstrip("/")
            if d not in params:
                state, param = _param_of(source.subtree(d))
                params[d] = {"state": state, "param": param}
    return {
        "ok": True,
        "archive": str(archive),
        "kind": source.kind,
        "encrypted": bool(source.encrypted),
        "solid": bool(source.solid),
        "files": len(paths),
        "bytes": source.total_size(),
        "games": games,
        "candidate_roots": candidates,
        "single_root": single_root,
        "params": params,
        "eboots": eboots,
        "pkgs": sorted(p for p in paths if p.lower().endswith(".pkg")),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    print(json.dumps(summary(Path(argv[0]), os.environ.get(PASSWORD_ENV, "")), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
