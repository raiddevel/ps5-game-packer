"""Entry point of mkpfs.exe on Windows: the normal `mkpfs` with UTF-8 output (MkPFS prints boxes and emoji) and
`freeze_support()` for the compression processes inside PyInstaller."""
import multiprocessing
import sys

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from mkpfs.cli import main

    sys.exit(main())
