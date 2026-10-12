# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Interface translation. The source strings (msgid) are English and every other language is a gettext catalog
`locale/<lang>.po`, read as is at run time. The language comes from the settings, from PS5_GAME_PACKER_LANG or
from the system variables; anything that is not Spanish uses English."""
from __future__ import annotations

import os
import re
from pathlib import Path

LANGUAGES = ("en", "es")
SOURCE_LANGUAGE = "en"
LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"

_catalog: dict[str, str] = {}
_language: str = SOURCE_LANGUAGE

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), text)


def _unquote(text: str) -> str:
    """Drops one pair of surrounding double quotes (a string may end in an escaped quote)."""
    text = text.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return _unescape(text)


def read_po(path: Path) -> dict[str, str]:
    """Reads a .po catalog and returns {msgid: msgstr} for the translated, non-fuzzy entries."""
    result: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    current: list[str] | None = None
    fuzzy = False

    def close() -> None:
        nonlocal msgid, msgstr, current, fuzzy
        if msgid is not None and msgstr is not None:
            k = "".join(msgid)
            v = "".join(msgstr)
            if k and v and not fuzzy:
                result[k] = v
        msgid = msgstr = current = None
        fuzzy = False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        s = line.strip()
        if not s:
            close()
            continue
        if s.startswith("#"):
            if s.startswith("#,") and "fuzzy" in s:
                fuzzy = True
            continue
        if s.startswith("msgctxt "):
            continue
        if s.startswith("msgid "):
            if msgid is not None and msgstr is not None:
                close()
            msgid = [_unquote(s[len("msgid "):])]
            current = msgid
            continue
        if s.startswith("msgstr "):
            msgstr = [_unquote(s[len("msgstr "):])]
            current = msgstr
            continue
        if s.startswith('"') and current is not None:
            current.append(_unquote(s))
    close()
    return result


def detect_language(preferred: str | None = None) -> str:
    """Language to use: the preferred one (en/es), PS5_GAME_PACKER_LANG, or the system's."""
    candidate = preferred if preferred in LANGUAGES else os.environ.get("PS5_GAME_PACKER_LANG", "").strip()
    if candidate not in LANGUAGES:
        candidate = ""
        for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            value = os.environ.get(variable, "").strip()
            if value:
                candidate = value.split(":")[0]
                break
    return "es" if candidate.lower().startswith("es") else SOURCE_LANGUAGE


def activate(lang: str) -> str:
    """Loads the catalog for `lang` (none for English). Returns the active language."""
    global _catalog, _language
    lang = lang if lang in LANGUAGES else SOURCE_LANGUAGE
    _catalog = {} if lang == SOURCE_LANGUAGE else read_po(LOCALE_DIR / f"{lang}.po")
    _language = lang
    return _language


def language() -> str:
    return _language


def _(text: str) -> str:
    return _catalog.get(text, text)
