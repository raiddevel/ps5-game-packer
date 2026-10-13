# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Scanning folders and archives: games, layers (update, backport, DLC) and warnings. Pure logic, no window."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

from . import environment
from .i18n import _, language

ROLE_BASE = "base"
ROLE_UPDATE = "update"
ROLE_BACKPORT = "backport"
ROLE_DLC = "dlc"
ROLE_LAYER = "layer"
LAYER_ROLES = [ROLE_UPDATE, ROLE_BACKPORT, ROLE_DLC, ROLE_LAYER]  # the ones the user can pick
ROLE_WEIGHT = {ROLE_BASE: -1, ROLE_UPDATE: 0, ROLE_LAYER: 1, ROLE_BACKPORT: 2, ROLE_DLC: 3}

# State of sce_sys/param.json: without a valid one, ShadowMountPlus does not register the game. listing.py uses the same values.
PARAM_OK = "ok"
PARAM_BROKEN = "broken"  # not JSON, not an object, or no titleId
PARAM_MISSING = "missing"

ID_RE = re.compile(r"\b([A-Z]{4}\d{5})\b", re.IGNORECASE)
DLC_RE = re.compile(r"dlc|add[-. ]?on|bonus|season|pass", re.IGNORECASE)
UPDATE_RE = re.compile(r"update|\bupd\b|patch|parche|fix", re.IGNORECASE)
BACKPORT_RE = re.compile(r"back[-. ]?port|\bbp\b", re.IGNORECASE)
IGNORED_LOOSE_FILES = (".nfo", ".txt", ".sfv", ".md5", ".sha1", ".url", ".jpg", ".png", ".diz", ".pkg")
ARCHIVE_DEPTH = 2  # subfolder levels searched for RAR/ZIP when the game is not extracted

# Container name: "Title [TITLE_ID].ffpfsc" or just "TITLE_ID.ffpfsc". ShadowMountPlus does not care.
NAME_MODE_TITLE = "title"
NAME_MODE_ID = "id"
NAME_MODES = (NAME_MODE_TITLE, NAME_MODE_ID)
NAME_MAX_LENGTH = 80
_INVALID_CHARS = set('\\/:*?"<>|')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_REPLACEMENTS = {"™": "", "®": "", "©": "", "–": "-", "—": "-", "‒": "-", "‘": "'", "’": "'", "“": "'", "”": "'",
                 "«": "'", "»": "'", "…": "...", "ß": "ss", "æ": "ae", "Æ": "AE",
                 "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L"}


def role_name(role: str) -> str:
    return {
        ROLE_BASE: _("base game"),
        ROLE_UPDATE: _("update"),
        ROLE_BACKPORT: _("backport"),
        ROLE_DLC: _("DLC"),
        ROLE_LAYER: _("layer"),
    }.get(role, role)


def gb(n: int | float) -> str:
    """Formats bytes as GB, or MB below 1 GB (decimal comma in Spanish)."""
    text = f"{n / 1e9:,.1f} GB" if n >= 1e9 or n == 0 else f"{n / 1e6:,.1f} MB"
    if language() == "es":
        return text.replace(",", "X").replace(".", ",").replace("X", ".")
    return text


def thousands(n: int) -> str:
    text = f"{n:,}"
    return text.replace(",", ".") if language() == "es" else text


def time_left(seconds: float) -> str:
    """'under 1 min', '12 min', '1 h 05 min'."""
    if seconds < 60:
        return _("under 1 min")
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return _("{n} min").format(n=minutes)
    return _("{h} h {m} min").format(h=minutes // 60, m=f"{minutes % 60:02d}")


def fw_text(req_fw: str) -> str:
    """'0x1270000000000000' -> '12.70'."""
    v = fw_num(req_fw)
    return f"{v >> 8:X}.{v & 0xFF:02X}" if v else ""


def clean_name(text: str, maximum: int = NAME_MAX_LENGTH) -> str:
    """A name valid on exFAT and Windows: no accents or emoji, none of \\ / : * ? " < > | and at most `maximum`
    characters. Windows reserved names (CON, NUL, COM1...) get an underscore appended."""
    flat = "".join(_REPLACEMENTS.get(c, c) for c in text)
    flat = unicodedata.normalize("NFKD", flat)
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    pieces: list[str] = []
    for c in flat:
        if c == ":":
            pieces.append(" - ")
        elif c in _INVALID_CHARS or ord(c) < 32:
            pieces.append(" ")
        elif c.isascii() and c.isprintable():
            pieces.append(c)
    clean = " ".join("".join(pieces).split())
    clean = re.sub(r"\s*-\s*-\s*", " - ", clean)  # "A: - B" after replacing twice
    clean = clean.rstrip(". ")
    if len(clean) > maximum:
        cut = clean[:maximum]
        space = cut.rfind(" ")
        if space >= maximum // 2:
            cut = cut[:space]
        clean = cut.rstrip(". -")
    if clean.split(".")[0].upper() in _WINDOWS_RESERVED:
        clean += "_"
    return clean


def container_name(job: dict, mode: str = NAME_MODE_TITLE) -> str:
    """Name of the .ffpfsc: "Title [TITLE_ID].ffpfsc", or "TITLE_ID.ffpfsc" in "ID only" mode or without a title."""
    tid = clean_name(str(job.get("title_id") or "").strip(), 40) or "game"
    title = str(job.get("name") or "").strip()
    if mode == NAME_MODE_ID or not title:
        return f"{tid}.ffpfsc"
    suffix = f" [{tid}]"
    clean = clean_name(title, NAME_MAX_LENGTH - len(suffix))
    return (f"{clean}{suffix}" if clean else tid) + ".ffpfsc"


def is_archive(p: Path) -> bool:
    n = p.name.lower()
    return p.is_file() and (n.endswith(environment.ARCHIVE_EXTENSIONS) or bool(re.search(r"\.r\d\d$", n)))


def archive_stem(p: Path) -> str:
    """Archive name without extension or volume number ("Game.part1.rar" -> "Game"), used as a stand-in TITLE_ID."""
    stem = re.sub(r"\.(rar|zip|r\d\d)$", "", p.name, flags=re.IGNORECASE)
    return re.sub(r"\.part\d+$", "", stem, flags=re.IGNORECASE) or p.name


def game_title(param: dict) -> str:
    """Game title in the interface language, or whichever there is."""
    lp = param.get("localizedParameters") or {}
    preferred = ("es-ES", "es-419") if language() == "es" else ("en-US", "en-GB")
    for key in (*preferred, lp.get("defaultLanguage", ""), "en-US", "en-GB", "es-ES"):
        v = lp.get(key)
        if isinstance(v, dict) and v.get("titleName"):
            return str(v["titleName"])
    for v in lp.values():
        if isinstance(v, dict) and v.get("titleName"):
            return str(v["titleName"])
    return ""


def measure_folder(path: Path) -> tuple[int, int]:
    """(files, bytes) of a folder."""
    total = 0
    n = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            n += 1
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return n, total


def find_games(source: Path, limit: int = 50) -> list[dict]:
    """Game folders (the ones with eboot.bin), with the state of their sce_sys/param.json in `param`."""
    games: list[dict] = []
    if not source.is_dir():
        return games
    for dirpath, dirnames, filenames in os.walk(source):
        d = Path(dirpath)
        if "eboot.bin" in filenames:
            state, param = param_state(d)
            title_id = title_id_of(param)
            if not title_id:
                m = ID_RE.search(d.name)
                title_id = m.group(1).upper() if m else d.name
            files, size = measure_folder(d)
            games.append({
                "path": d,
                "virtual": False,
                "subpath": "",
                "title_id": title_id,
                "name": game_title(param),
                "version": str(param.get("contentVersion") or ""),
                "req_fw": str(param.get("requiredSystemSoftwareVersion") or ""),
                "files": files,
                "bytes": size,
                "ampr": (d / "fakelib" / "libSceAmpr.sprx").is_file(),
                "ampr_index": (d / "ampr_emu.index").is_file(),
                "param": state,
            })
            dirnames[:] = []  # do not descend into a game
            if len(games) >= limit:
                break
    return games


_listing_cache: dict[tuple, dict] = {}


def list_archive(archive: Path, password: str) -> dict:
    """`environment.list_archive` cached by (path, size, mtime, password); `clear_cache` empties it."""
    try:
        st = archive.stat()
        k = (str(archive), st.st_size, st.st_mtime_ns, password)
    except OSError:
        return environment.list_archive(archive, password)
    info = _listing_cache.get(k)
    if info is None:
        info = environment.list_archive(archive, password)
        _listing_cache[k] = info
    return info


def clear_cache() -> None:
    _listing_cache.clear()


def password_for(path: Path, password: str, passwords: dict | None) -> str:
    """Password of an archive: its own (by path, in `passwords`) or, if it has none, the general one."""
    own = (passwords or {}).get(str(path), "")
    return own or password


def volumes_size(first: Path) -> int:
    """Bytes of an archive or, if it is the first part of a multi-volume RAR, of all its parts."""
    # no glob: the brackets in the names would be character classes
    m = re.match(r"(.*\.part)\d+\.rar$", first.name, re.IGNORECASE)
    try:
        if not m:
            return first.stat().st_size
        prefix = m.group(1).lower()
        return sum(p.stat().st_size for p in first.parent.iterdir()
                   if p.name.lower().startswith(prefix) and re.fullmatch(r"\d+\.rar", p.name[len(prefix):].lower()))
    except OSError:
        return 0


def error_text(info: dict) -> str:
    """Message for the user from a listing.py error (reading a RAR/ZIP with MkPFS)."""
    code = info.get("error_code", "")
    detail = (info.get("error") or "").strip()
    if code == "password":
        return _("Password needed (or it is wrong).")
    if code == "no_unrar":
        return _("unrar is not installed (on Debian, Ubuntu or Mint: sudo apt install unrar).")
    if code == "unrar":
        return _("unrar could not read the archive: {detail}").format(detail=detail)
    if code == "zip":
        return _("Could not open the ZIP: {detail}").format(detail=detail)
    if code == "format":
        return _("Unsupported format: only RAR and ZIP.")
    if code == "engine":
        return _("Cannot find the MkPFS engine at {path}.").format(path=detail)
    return _("Could not read the archive: {detail}").format(detail=detail or "?")


def is_first_volume(p: Path) -> bool:
    """True for .zip, single .rar and the FIRST part of a multi-volume RAR (the rest are skipped)."""
    n = p.name.lower()
    if n.endswith(".zip"):
        return True
    if re.search(r"\.r\d\d$", n):
        return False
    if not n.endswith(".rar"):
        return False
    m = re.search(r"\.part(\d+)\.rar$", n)
    return int(m.group(1)) == 1 if m else True


def layer_root(item: Path) -> Path:
    """In a folder that is not a whole game (update/DLC), where the content that goes on top of the game is."""
    for dirpath, dirnames, filenames in os.walk(item):
        d = Path(dirpath)
        dirnames.sort()
        if "eboot.bin" in filenames or "sce_sys" in dirnames or re.search(r"-app0?$", d.name, re.IGNORECASE):
            return d
    current = item
    for _i in range(4):  # a single subfolder (and nothing loose): go down
        try:
            children = [q for q in current.iterdir() if q.is_dir()]
            loose = [q for q in current.iterdir() if q.is_file() and not q.name.lower().endswith(IGNORED_LOOSE_FILES)]
        except OSError:
            break
        if len(children) == 1 and not loose:
            current = children[0]
        else:
            break
    return current


def fw_num(v: str) -> int:
    """'0x1270000000000000' -> 0x1270 (12.70); '' -> 0."""
    try:
        return int(str(v), 16) >> 48 if str(v).lower().startswith("0x") else 0
    except ValueError:
        return 0


def validate_param(data: bytes | str) -> tuple[str, dict]:
    """State of a param.json from its contents: (PARAM_OK, dict) if it is a JSON object with titleId; otherwise
    (PARAM_BROKEN, {})."""
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig", errors="replace")
        param = json.loads(data.lstrip("﻿"))
    except Exception:
        return PARAM_BROKEN, {}
    if not isinstance(param, dict) or not title_id_of(param):
        return PARAM_BROKEN, {}
    return PARAM_OK, param


def title_id_of(param: dict) -> str:
    """The titleId (or title_id) of a param.json, upper case; '' if it has none."""
    return str(param.get("titleId") or param.get("title_id") or "").strip().upper()


def param_state(root: Path) -> tuple[str, dict]:
    """(state, contents) of a folder's sce_sys/param.json: PARAM_OK, PARAM_BROKEN or PARAM_MISSING."""
    try:
        data = (root / "sce_sys" / "param.json").read_bytes()
    except FileNotFoundError:
        return PARAM_MISSING, {}
    except OSError:
        return PARAM_BROKEN, {}
    return validate_param(data)


def read_param(root: Path) -> dict:
    """Contents of a folder's sce_sys/param.json, or {} if missing or broken."""
    return param_state(root)[1]


def version_key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-_]", v)) if v else ()


def analyze_item(item: Path, password: str, pkgs: list[str], notes: list[str], passwords: dict | None = None) -> dict | None:
    """Scans a folder or an archive as a possible game or layer; notes any .pkg it finds. An encrypted archive without
    a password (or with a wrong one) is not dropped: it comes back as pending (`needs_password`, `wrong_password`)."""
    n = item.name
    if item.is_file():
        if n.lower().endswith(".pkg"):
            pkgs.append(n)
            return None
        if n.lower() == "param.json":  # a loose param.json next to the games: the fix for a broken one
            state, param = validate_param_file(item)
            if state != PARAM_OK:
                notes.append(_("{name} is not a valid param.json: it has to be a JSON object with titleId.").format(name=n))
                return None
            return bare_param_layer(item, item.read_bytes().decode("utf-8-sig", errors="replace"), param)
        if not (is_archive(item) and is_first_volume(item)):
            return None
        item_password = password_for(item, password, passwords)
        info = list_archive(item, item_password)
        if not info.get("ok"):
            if info.get("error_code") == "password":
                m = ID_RE.search(n)
                return {"path": item, "virtual": True, "subpath": "", "complete": False, "needs_password": True,
                        "wrong_password": bool(item_password), "encrypted": True, "password": "",
                        "title_id": m.group(1).upper() if m else "", "name": "", "version": "", "req_fw": "",
                        "has_eboot": False, "files": 0, "bytes": volumes_size(item),
                        "ampr": False, "ampr_index": False, "item": n}
            notes.append(f"{n}: {error_text(info)}")
            return None
        if info.get("pkgs") and not info.get("eboots"):  # a package, not a dump: nothing to pack
            notes.append(_("{item}: this is a PKG, not a game dump ({pkgs}). There is nothing to pack: install it on the "
                           "console with the Package Installer (Debug Settings).").format(item=n, pkgs=", ".join(info["pkgs"])))
            return None
        bare = info.get("bare_param")
        if bare:  # an archive with nothing but a param.json
            if bare.get("state") != PARAM_OK:
                notes.append(_("{name} is not a valid param.json: it has to be a JSON object with titleId.").format(name=n))
                return None
            return bare_param_layer(item, bare["text"], bare["param"])
        pkgs += [f"{n} → {q}" for q in info.get("pkgs", [])]
        encrypted = bool(info.get("encrypted"))
        common = {"path": item, "virtual": True, "item": n, "encrypted": encrypted, "password": item_password if encrypted else ""}
        games = info.get("games") or []
        if games:
            g = games[0]
            m = ID_RE.search(n) or ID_RE.search(g.get("subpath", ""))
            # like folders (which fall back to the folder name), a game whose param.json is missing or broken
            # keeps the archive name as a stand-in TITLE_ID instead of being dropped
            return {**common, "subpath": g.get("subpath", ""), "complete": True,
                    "title_id": (g.get("title_id") or "").upper() or (m.group(1).upper() if m else archive_stem(item)),
                    "name": game_title(g.get("param") or {}) or g.get("name", ""),
                    "version": g.get("version", ""), "req_fw": g.get("req_fw", ""),
                    "files": int(g.get("files", 0)), "has_eboot": True,
                    "bytes": int(g.get("bytes", 0)), "ampr": bool(g.get("ampr")),
                    "ampr_index": bool(g.get("ampr_index")), "param": g.get("param_state", PARAM_OK)}
        candidates = info.get("candidate_roots") or []
        sub = candidates[0] if candidates else info.get("single_root", "")
        pinfo = (info.get("params") or {}).get(sub) or {}  # the param.json of a layer (DLC/update without eboot), if any
        param = pinfo.get("param") or {}
        m = ID_RE.search(n) or ID_RE.search(sub) or ID_RE.search(title_id_of(param))
        return {**common, "subpath": sub, "complete": False,
                "title_id": m.group(1).upper() if m else "", "name": game_title(param),
                "param_only": int(info.get("files", 0)) == 1 and pinfo.get("state") == PARAM_OK,
                "version": str(param.get("contentVersion") or ""), "req_fw": str(param.get("requiredSystemSoftwareVersion") or ""),
                "has_eboot": ((sub + "/") if sub else "") + "eboot.bin" in set(info.get("eboots", [])),
                "files": int(info.get("files", 0)), "bytes": int(info.get("bytes", 0)),
                "ampr": False, "ampr_index": False, "param": pinfo.get("state", PARAM_MISSING)}
    if not item.is_dir():
        return None
    try:
        inside = sorted(q.relative_to(item).as_posix() for q in item.rglob("*.pkg"))
        if inside and not any(item.rglob("eboot.bin")):  # a package, not a dump: nothing to pack
            notes.append(_("{item}: this is a PKG, not a game dump ({pkgs}). There is nothing to pack: install it on the "
                           "console with the Package Installer (Debug Settings).").format(item=n, pkgs=", ".join(inside)))
            return None
        pkgs += [f"{n} → {q}" for q in inside]
    except OSError:
        pass
    games = find_games(item, limit=1)
    if games:
        g = dict(games[0])
        g["complete"] = True
        g["has_eboot"] = True
        g["item"] = n
        return g
    root = layer_root(item)
    files, size = measure_folder(root)
    if files == 0:
        return None
    if files == 1 and (root / "param.json").is_file():  # a folder with nothing but a loose param.json
        return analyze_item(root / "param.json", password, pkgs, notes, passwords)
    state, param = param_state(root)
    m = ID_RE.search(title_id_of(param)) or ID_RE.search(n) or ID_RE.search(root.name)
    return {"path": root, "virtual": False, "subpath": "", "complete": False,
            "title_id": m.group(1).upper() if m else "", "name": game_title(param),
            "version": str(param.get("contentVersion") or ""),
            "req_fw": str(param.get("requiredSystemSoftwareVersion") or ""),
            "has_eboot": (root / "eboot.bin").is_file(),
            "files": files, "bytes": size, "ampr": (root / "fakelib" / "libSceAmpr.sprx").is_file(),
            "ampr_index": (root / "ampr_emu.index").is_file(), "item": n, "param": state,
            "param_only": files == 1 and state == PARAM_OK}


def archives_in_folder(folder: Path, depth: int = ARCHIVE_DEPTH) -> list[Path]:
    """RAR/ZIP of a folder without the game extracted: the loose ones or, if none, those of the subfolders (down to `depth`)."""
    level = [folder]
    for _i in range(depth):
        found: list[Path] = []
        next_level: list[Path] = []
        for d in level:
            try:
                children = sorted(d.iterdir(), key=lambda q: q.name.lower())
            except OSError:
                continue
            for h in children:
                if h.is_dir():
                    next_level.append(h)
                elif is_archive(h) and is_first_volume(h):
                    found.append(h)
        if found:
            return found
        level = next_level
    return []


def analyze_items(item: Path, password: str, pkgs: list[str], notes: list[str], passwords: dict | None = None) -> list[dict]:
    """Like `analyze_item`, but a subfolder holding the unextracted RAR/ZIP is opened and its archives count as loose."""
    if item.is_dir() and not find_games(item, limit=1):
        archives = archives_in_folder(item)
        if archives:
            try:
                pkgs += [f"{item.name} → {q.relative_to(item)}" for q in item.rglob("*.pkg")]
            except OSError:
                pass
            items: list[dict] = []
            for a in archives:
                it = analyze_item(a, password, pkgs, notes, passwords)
                if it is None:
                    continue
                it["item"] = a.relative_to(item.parent).as_posix()  # "Folder/Game.part1.rar"
                if not it["title_id"]:
                    m = ID_RE.search(item.name)
                    it["title_id"] = m.group(1).upper() if m else ""
                items.append(it)
            return items
    it = analyze_item(item, password, pkgs, notes, passwords)
    return [it] if it is not None else []


def layer_role(d: dict, base: dict) -> str:
    """Role of a layer relative to the base game: from the name and, if that says nothing, from the contents."""
    if BACKPORT_RE.search(d["item"]):
        return ROLE_BACKPORT
    if DLC_RE.search(d["item"]):
        return ROLE_DLC
    if UPDATE_RE.search(d["item"]):
        return ROLE_UPDATE
    fw, fw_base = fw_num(d.get("req_fw", "")), fw_num(base.get("req_fw", ""))
    if d.get("has_eboot") and fw and fw_base and fw < fw_base:
        return ROLE_BACKPORT      # same game asking for a lower firmware
    if d.get("version") and version_key(d["version"]) > version_key(base.get("version", "")):
        return ROLE_UPDATE        # game version higher than the base
    if not d.get("has_eboot"):
        return ROLE_DLC           # content only, no executable
    return ROLE_LAYER


def analyze_folder(folder: Path, password: str, passwords: dict | None = None) -> tuple[list[dict], list[str]]:
    """Games in a folder, as (jobs, notes). Game + update/DLC with the same TITLE_ID are grouped into one job with
    layers; encrypted archives without a password go at the end as pending."""
    notes: list[str] = []
    own = find_games(folder, limit=1)
    if own and own[0]["path"] == folder:
        return own, notes  # the chosen folder IS the game
    items: list[dict] = []
    pkgs: list[str] = []
    try:
        children = sorted(folder.iterdir(), key=lambda q: q.name.lower())
    except OSError:
        return [], notes
    for h in children:
        items += analyze_items(h, password, pkgs, notes, passwords)
    jobs = group_layers(items, notes)
    if pkgs:
        notes.append(_("Kept aside (not merged; install them later with the Package Installer): {items}").format(items=", ".join(pkgs)))
    return jobs, notes


def job_password(job: dict) -> str:
    """Password for MkPFS: the one of the first encrypted archive in the job (MkPFS takes only one per game)."""
    layers = [c for c in job.get("layers", []) if c.get("active", True)] or [job]
    for c in layers:
        if c.get("password"):
            return str(c["password"])
    return ""


def mismatched_passwords(job: dict) -> list[dict]:
    """Active encrypted layers whose password is not the one that will be used (MkPFS takes only one per game)."""
    used = job_password(job)
    return [c for c in job.get("layers", []) if c.get("active", True) and c.get("password") and c["password"] != used]


def final_eboot(job: dict) -> dict | None:
    """Highest active layer that provides eboot.bin (the one that wins), or None."""
    active = [c for c in job.get("layers", []) if c.get("active", True)]
    with_eboot = [c for c in active if c.get("has_eboot")]
    return with_eboot[-1] if with_eboot else None


def layer_warnings(job: dict) -> list[str]:
    """Warnings about the layer order: above all, whether something covers the eboot.bin of a backport."""
    warnings: list[str] = []
    active = [c for c in job.get("layers", []) if c.get("active", True)]
    if not active:
        return warnings
    mismatched = mismatched_passwords(job)
    if mismatched:
        used = next(c for c in active if c.get("password") == job_password(job))
        warnings.append(_('Layers have different passwords and MkPFS takes only one per game: the one from "{item}" '
                          'will be used and reading {items} will fail. Leave those layers out or extract them.')
                        .format(item=used["item"], items=", ".join(f'"{c["item"]}"' for c in mismatched)))
    winner = final_eboot(job)
    if winner is None:
        warnings.append(_("No active layer provides eboot.bin: the game will not start."))
        return warnings
    for c in active:
        if c is winner or not c.get("has_eboot"):
            continue
        if c["role"] == ROLE_BACKPORT:
            warnings.append(_('"{winner}" ({role}) replaces the eboot.bin of the backport "{layer}": you lose what lets '
                              'the game run on lower firmwares. If you need it, move the backport above it (▲) or look '
                              'for an update or DLC already adapted to that backport.')
                            .format(winner=winner["item"], role=role_name(winner["role"]), layer=c["item"]))
    if winner["role"] == ROLE_DLC:
        below = [c for c in active if c.get("has_eboot") and c is not winner and c["role"] in (ROLE_UPDATE, ROLE_LAYER)]
        if below:
            warnings.append(_('The DLC "{dlc}" brings its own eboot.bin and covers the one from "{layer}": that is '
                              'usually fine, but check in the DLC notes that it is meant for this version of the game.')
                            .format(dlc=winner["item"], layer=below[-1]["item"]))
    return warnings


def group_layers(items: list[dict], notes: list[str]) -> list[dict]:
    # Encrypted archives without a password cannot be read yet: they go at the end, apart, ungrouped.
    pending = [it for it in items if it.get("needs_password")]
    items = [it for it in items if not it.get("needs_password")]
    # The same whole game twice (say extracted and in RAR): keep one, preferably the folder.
    seen: dict[tuple, dict] = {}
    unique: list[dict] = []
    for it in sorted(items, key=lambda i: i["virtual"]):
        k = (it["title_id"], it["version"], it["bytes"]) if it["complete"] and it["title_id"] else None
        if k is not None and k in seen:
            notes.append(_("{item} is the same game as {other} (same version and size): ignored.")
                         .format(item=it["item"], other=seen[k]["item"]))
            continue
        if k is not None:
            seen[k] = it
        unique.append(it)
    items = sorted(unique, key=lambda i: i["item"].lower())
    groups: dict[str, list[dict]] = {}
    no_id: list[dict] = []
    for it in items:
        (groups.setdefault(it["title_id"], []) if it["title_id"] else no_id).append(it)
    with_base = [k for k, v in groups.items() if any(i["complete"] for i in v)]
    # A lone param.json (folder or archive) carrying a TITLE_ID, next to a single game whose own param.json cannot
    # be read (so its ID is a stand-in): assume it is the fix for that game and put it on top.
    stand_in = [k for k in with_base if not ID_RE.fullmatch(k)]
    if len(stand_in) == 1:
        for k in [k for k, v in groups.items() if k not in with_base and all(i.get("param_only") for i in v)]:
            groups[stand_in[0]] += groups.pop(k)
    if no_id:
        if len(with_base) == 1:
            groups[with_base[0]] += no_id
            notes.append(_("No TITLE_ID in the name; assumed to belong to {tid}: {items}")
                         .format(tid=with_base[0], items=", ".join(i["item"] for i in no_id)))
        else:
            notes.append(_("Ignored (cannot tell which game they belong to): {items}").format(items=", ".join(i["item"] for i in no_id)))
    jobs: list[dict] = []
    for tid, group in groups.items():
        # An eboot.bin without param.json whose name says DLC, update or backport is a layer, never the base
        # (a DLC with its own executable, packed alone, would give a container the console cannot run).
        complete = [i for i in group if i["complete"] and not (
            i.get("param", PARAM_OK) == PARAM_MISSING and (DLC_RE.search(i["item"]) or UPDATE_RE.search(i["item"]) or BACKPORT_RE.search(i["item"])))]
        if not complete:
            notes.append(_("{tid}: only an update/DLC was found ({items}); the base game is missing.")
                         .format(tid=tid, items=", ".join(i["item"] for i in group)))
            continue
        if len(group) == 1:
            jobs.append(group[0])
            continue
        base = max(complete, key=lambda i: i["bytes"])
        # The other complete ones (eboot.bin + param.json) are updates or backports. One with eboot.bin but no
        # param.json cannot be told from a DLC with an executable: it goes by name and contents like the other layers.
        with_param = [i for i in complete if i is not base and i.get("param", PARAM_OK) != PARAM_MISSING]
        updates = sorted(with_param, key=lambda i: (version_key(i["version"]), i["item"].lower()))
        others = sorted([i for i in group if i is not base and not any(i is c for c in with_param)], key=lambda i: i["item"].lower())

        def role_of(d: dict) -> str:
            return layer_role(d, base)

        # Order: base → updates → backport → DLC (can be changed with ▲▼)
        others.sort(key=lambda d: (ROLE_WEIGHT[role_of(d)], d["item"].lower()))
        updates = [u for u in updates if role_of(u) != ROLE_BACKPORT]
        backports = [u for u in with_param if role_of(u) == ROLE_BACKPORT]
        layers: list[dict] = []
        for it, role in ([(base, ROLE_BASE)] + [(u, ROLE_UPDATE) for u in updates]
                         + [(b, ROLE_BACKPORT) for b in backports] + [(d, role_of(d)) for d in others]):
            c = dict(it)
            c["role"] = role
            c["active"] = True
            layers.append(c)
        job = {"path": base["path"], "virtual": base["virtual"], "subpath": base["subpath"], "title_id": tid,
               "name": "", "version": "", "req_fw": "", "layers": layers}
        recompute_totals(job)  # name, version and param.json state: those of the highest layer that has one
        jobs.append(job)
    return jobs + pending


def ensure_layers(job: dict) -> None:
    """Turns a plain job into one with layers (the first one being the base game)."""
    if job.get("layers"):
        return
    base = dict(job)
    base.update({"role": ROLE_BASE, "active": True, "complete": True, "has_eboot": True, "item": job["path"].name})
    job["layers"] = [base]


def add_layer(job: dict, item: dict, role: str | None = None) -> str | None:
    """Adds a layer to a job with role `role` (or the one it deserves). Returns 'duplicate' or None."""
    ensure_layers(job)
    if any(c["path"] == item["path"] and c.get("subpath", "") == item.get("subpath", "") for c in job["layers"]):
        return "duplicate"
    base = job["layers"][0]
    item["role"] = role or layer_role(item, base)
    item["active"] = True
    rest = job["layers"][1:] + [item]
    rest.sort(key=lambda x: ROLE_WEIGHT.get(x["role"], 1))
    job["layers"] = [base] + rest
    recompute_totals(job)
    return None


def recompute_totals(job: dict) -> None:
    """Totals of a job with layers and, from the winning param.json (`param_layer`), title, version, firmware and TITLE_ID."""
    job["bytes"] = sum(c["bytes"] for c in job["layers"])
    job["files"] = sum(c["files"] for c in job["layers"])
    job["ampr"] = any(c.get("ampr") for c in job["layers"])
    job["ampr_index"] = any(c.get("ampr_index") for c in job["layers"])
    job["encrypted"] = any(c.get("encrypted") for c in job["layers"])
    winner = param_layer(job)
    if winner is not None and winner.get("param", PARAM_OK) == PARAM_OK:
        job["name"], job["version"], job["req_fw"] = winner.get("name", ""), winner.get("version", ""), winner.get("req_fw", "")
        if winner.get("title_id"):
            job["title_id"] = winner["title_id"]
    else:
        job["name"] = job["version"] = job["req_fw"] = ""
    job["param"] = job_param_state(job)


def param_layer(job: dict) -> dict | None:
    """Highest active layer with sce_sys/param.json (the one that will end up in the container), or None."""
    layers = [c for c in job.get("layers", []) if c.get("active", True)] or [job]
    with_param = [c for c in layers if c.get("param", PARAM_OK) != PARAM_MISSING]
    return with_param[-1] if with_param else None


def job_param_state(job: dict) -> str:
    """State of the param.json that will go into the container: PARAM_OK, PARAM_BROKEN or PARAM_MISSING."""
    winner = param_layer(job)
    return PARAM_MISSING if winner is None else str(winner.get("param", PARAM_OK))


def param_warning(job: dict) -> str:
    """Warning (in red on the card) when the container's param.json is missing or unreadable; '' if it is fine."""
    state = job_param_state(job)
    if state == PARAM_OK:
        return ""
    what = _("sce_sys/param.json cannot be read") if state == PARAM_BROKEN else _("sce_sys/param.json is missing")
    return what + ": " + _('ShadowMountPlus will not be able to register the game. Replace it or add a valid one with "Add param.json…".')


def validate_param_file(file: Path) -> tuple[str, dict]:
    """Like `validate_param`, reading the file (PARAM_BROKEN if it cannot be read)."""
    try:
        return validate_param(file.read_bytes())
    except OSError:
        return PARAM_BROKEN, {}


def param_json_layer(file: Path, param: dict) -> dict:
    """Copies a valid param.json to `CACHE_DIR/param-<TITLE_ID>/sce_sys/` and returns the (`temporary`) layer for `add_layer`."""
    tid = title_id_of(param)
    name = f"param-{clean_name(tid, 40) or 'game'}"
    folder = environment.CACHE_DIR / name
    n = 1
    while folder.exists():  # another window (or another game with a foreign param.json) already uses that folder
        n += 1
        folder = environment.CACHE_DIR / f"{name}-{n}"
    (folder / "sce_sys").mkdir(parents=True)
    shutil.copyfile(file, folder / "sce_sys" / "param.json")
    size = (folder / "sce_sys" / "param.json").stat().st_size
    return {"path": folder, "virtual": False, "subpath": "", "complete": False, "title_id": tid,
            "name": game_title(param), "version": str(param.get("contentVersion") or ""),
            "req_fw": str(param.get("requiredSystemSoftwareVersion") or ""), "has_eboot": False,
            "files": 1, "bytes": size, "ampr": False, "ampr_index": False, "param": PARAM_OK,
            "item": f"{file.parent.name}/{file.name}" if file.parent.name else file.name,
            "temporary": True, "param_source": str(file)}


def bare_param_layer(source: Path, text: str, param: dict) -> dict:
    """A param.json found on its own (a loose file, or alone inside an archive or folder) becomes a layer like
    `param_json_layer`. The cache folder is named after the ID and the source, so a rescan lands on the same one."""
    tid = title_id_of(param)
    tag = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:8]
    folder = environment.CACHE_DIR / f"param-{clean_name(tid, 40) or 'game'}-{tag}"
    (folder / "sce_sys").mkdir(parents=True, exist_ok=True)
    (folder / "sce_sys" / "param.json").write_text(text, encoding="utf-8")
    size = (folder / "sce_sys" / "param.json").stat().st_size
    return {"path": folder, "virtual": False, "subpath": "", "complete": False, "title_id": tid,
            "name": game_title(param), "version": str(param.get("contentVersion") or ""),
            "req_fw": str(param.get("requiredSystemSoftwareVersion") or ""), "has_eboot": False,
            "files": 1, "bytes": size, "ampr": False, "ampr_index": False, "param": PARAM_OK,
            "item": source.name, "temporary": True, "param_only": True, "param_source": str(source)}


def delete_temp_layer(layer: dict) -> None:
    """Deletes the temporary folder of a layer made by `param_json_layer` (only if it is inside the cache)."""
    path = Path(layer.get("path", ""))
    if layer.get("temporary") and path.parent == environment.CACHE_DIR and path.name.startswith("param-"):
        shutil.rmtree(path, ignore_errors=True)


def change_role(job: dict, layer: dict, role: str) -> None:
    """Changes the role of a layer and reorders (stable: same role, same order as before)."""
    layer["role"] = role
    base = [x for x in job["layers"] if x["role"] == ROLE_BASE]
    rest = [x for x in job["layers"] if x["role"] != ROLE_BASE]
    rest.sort(key=lambda x: ROLE_WEIGHT.get(x["role"], 1))
    job["layers"] = base + rest
