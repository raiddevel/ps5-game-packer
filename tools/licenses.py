#!/usr/bin/env python3
"""Collects in LICENSES/ the license texts of the dependencies installed in a venv, with an index.

Usage: tools/licenses.py VENV_PATH [OUTPUT_FOLDER]

packaging/windows.ps1 runs it before building the .exe, and it works to refresh LICENSES/ by hand. Only the
dependencies in PACKAGES (the run-time ones) are copied.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PACKAGES = {
    "mkpfs": "https://github.com/PSBrew/MkPFS",
    "customtkinter": "https://github.com/TomSchimansky/CustomTkinter",
    "darkdetect": "https://github.com/albertosottile/darkdetect",
    "packaging": "https://github.com/pypa/packaging",
    "typing_extensions": "https://github.com/python/typing_extensions",
    "pyzipper": "https://github.com/danifus/pyzipper",
    "psutil": "https://github.com/giampaolo/psutil",
    "cryptography": "https://github.com/pyca/cryptography",
    "cffi": "https://github.com/python-cffi/cffi",
    "pycparser": "https://github.com/eliben/pycparser",
    "zlib-ng": "https://github.com/pycompression/python-zlib-ng",
    "isal": "https://github.com/pycompression/python-isal",
    "pycryptodomex": "https://github.com/Legrandin/pycryptodome",
    "pillow": "https://github.com/python-pillow/Pillow",
}
FIXED = [
    ("Python", "PSF License (Python Software Foundation)", "https://docs.python.org/3/license.html", "Python.txt"),
    ("Tcl/Tk", "Tcl/Tk License (BSD-style)", "https://www.tcl.tk/software/tcltk/license.html", "Tcl-Tk.txt"),
    ("UnRAR", "RARLAB freeware (UnRAR license)", "https://www.rarlab.com/rar_add.htm", "UnRAR-RARLAB.txt"),
]


def _site_packages(venv: Path) -> Path:
    for c in sorted(venv.glob("lib/python3*/site-packages")) + [venv / "Lib" / "site-packages"]:
        if c.is_dir():
            return c
    raise SystemExit(f"cannot find site-packages in {venv}")


def _metadata(dist: Path) -> dict[str, str]:
    data: dict[str, str] = {"License": "", "License-Expression": "", "Classifier": ""}
    for line in (dist / "METADATA").read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            break  # end of the headers
        for key in ("Name", "Version", "License", "License-Expression", "Home-page"):
            if line.startswith(key + ":") and not data.get(key):
                data[key] = line.split(":", 1)[1].strip()
        if line.startswith("Classifier: License ::") and not data["Classifier"]:
            data["Classifier"] = line.split("::")[-1].strip()
    return data


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    venv = Path(argv[0]).expanduser().resolve()
    output = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "LICENSES"
    output.mkdir(parents=True, exist_ok=True)
    sp = _site_packages(venv)
    rows: list[tuple[str, str, str, str, list[str]]] = []
    for dist in sorted(sp.glob("*.dist-info")):
        m = _metadata(dist)
        name = m.get("Name", "")
        key = name.lower().replace("_", "-")
        if key not in {k.lower().replace("_", "-") for k in PACKAGES}:
            continue
        url = next(v for k, v in PACKAGES.items() if k.lower().replace("_", "-") == key)
        license_name = m["License-Expression"] or m["Classifier"] or (m["License"] if len(m["License"]) < 80 else m["License"].splitlines()[0])
        files: list[str] = []
        candidates = sorted((dist / "licenses").rglob("*")) if (dist / "licenses").is_dir() else []
        candidates += [p for p in dist.iterdir() if re.match(r"(?i)(LICEN[CS]E|COPYING)", p.name)]
        for p in candidates:
            if p.is_file():
                target = output / f"{name}-{p.name}"
                target.write_bytes(p.read_bytes())
                files.append(target.name)
        rows.append((name, m.get("Version", ""), license_name, url, files))
    lines = [
        "# Third-party licenses",
        "",
        "ps5-game-packer is GPL-3.0-or-later (see `LICENSE` at the root). This folder holds the licenses of what is",
        "installed with it or shipped inside the Windows package. Generated with `tools/licenses.py`.",
        "",
        "| Component | Version | License | Text | Web |",
        "|---|---|---|---|---|",
    ]
    for name, version, license_name, url, files in rows:
        texts = ", ".join(f"`{f}`" for f in files) or "(in the package)"
        lines.append(f"| {name} | {version} | {license_name} | {texts} | {url} |")
    for name, license_name, url, file in FIXED:
        exists = (output / file).is_file()
        lines.append(f"| {name} | | {license_name} | {'`' + file + '`' if exists else '(see web)'} | {url} |")
    lines += [
        "",
        "UnRAR (RARLAB) is redistributed only inside the Windows package, with its own `license.txt`, as its license",
        "allows; it is never used to create RAR archives.",
        "",
    ]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(rows)} packages; index at {output / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
