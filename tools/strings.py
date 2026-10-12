#!/usr/bin/env python3
"""Extracts the translatable `_("...")` strings from the code and compares them with a .po catalog.

Usage:
  tools/strings.py                 lists the msgids found in the code (order of appearance, no repeats)
  tools/strings.py locale/es.po    shows what is missing or left over in that catalog (exit 1 if something is missing)

It walks the Python syntax tree, no gettext involved; tests/test_i18n.py uses the same.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [*sorted((ROOT / "ps5_game_packer").glob("*.py")), ROOT / "ps5-game-packer.py"]


def extract(sources: list[Path] | None = None) -> list[str]:
    found: list[str] = []
    for path in sources or SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                text = node.args[0].value
                if text and text not in found:
                    found.append(text)
    return found


def compare(po: Path) -> int:
    sys.path.insert(0, str(ROOT))
    from ps5_game_packer.i18n import read_po

    catalog = read_po(po)
    code = extract()
    missing = [c for c in code if c not in catalog]
    extra = [c for c in catalog if c not in code]
    for c in missing:
        print(f"MISSING  {c!r}")
    for c in extra:
        print(f"EXTRA    {c!r}")
    print(f"{len(code)} strings in the code, {len(catalog)} translated, {len(missing)} missing, {len(extra)} extra")
    return 1 if missing else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(compare(Path(sys.argv[1])))
    for c in extract():
        print(repr(c))
