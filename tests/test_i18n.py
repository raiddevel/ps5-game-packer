"""The Spanish catalog is complete, has no dead entries, and keeps the same {x} placeholders as the source."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from ps5_game_packer import i18n

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import strings  # noqa: E402

PO_ES = ROOT / "locale" / "es.po"


def test_read_po(tmp_path):
    po = tmp_path / "x.po"
    po.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                  '#: a.py:1\nmsgid "Hello"\nmsgstr "Hola"\n\n'
                  'msgid "Split "\n"line"\nmsgstr "Línea "\n"partida"\n\n'
                  '#, fuzzy\nmsgid "Doubtful"\nmsgstr "Dudosa"\n\n'
                  'msgid "With \\"quotes\\" and\\nbreak"\nmsgstr "Con \\"comillas\\" y\\nsalto"\n', encoding="utf-8")
    d = i18n.read_po(po)
    assert d == {"Hello": "Hola", "Split line": "Línea partida", 'With "quotes" and\nbreak': 'Con "comillas" y\nsalto'}


def test_detect_language(monkeypatch):
    for v in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG", "PS5_GAME_PACKER_LANG"):
        monkeypatch.delenv(v, raising=False)
    assert i18n.detect_language(None) == "en"
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert i18n.detect_language(None) == "es"
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert i18n.detect_language(None) == "en"
    monkeypatch.setenv("LANGUAGE", "es_MX:en")
    assert i18n.detect_language(None) == "es"
    monkeypatch.setenv("PS5_GAME_PACKER_LANG", "en")
    assert i18n.detect_language(None) == "en"
    assert i18n.detect_language("es") == "es"


def test_catalog_complete():
    catalog = i18n.read_po(PO_ES)
    code = strings.extract()
    assert code, "no strings found in the code"
    missing = [c for c in code if c not in catalog]
    extra = [c for c in catalog if c not in code]
    assert not missing, f"not translated to Spanish: {missing}"
    assert not extra, f"entries that no longer exist in the code: {extra}"


def test_placeholders():
    placeholder = re.compile(r"\{[a-z_]+\}")
    for msgid, msgstr in i18n.read_po(PO_ES).items():
        assert sorted(placeholder.findall(msgid)) == sorted(placeholder.findall(msgstr)), msgid
        assert msgstr.strip(), msgid


def test_activate_and_translate():
    i18n.activate("es")
    assert i18n.language() == "es"
    assert i18n._("PS5 Game Packer") == "Comprimir juegos PS5"
    assert i18n._("text that does not exist") == "text that does not exist"
    i18n.activate("en")
    assert i18n._("PS5 Game Packer") == "PS5 Game Packer"
