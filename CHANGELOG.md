# Changes

## 2.1.2 - 2026-10-12, first public release

What this version does:

- Turns a PS5 game folder, or the RAR/ZIP it came in (multi-volume and password-protected too), into a `.ffpfsc`
  container for ShadowMountPlus. The archive is read without extracting it.
- Merges the base game with its updates, backport and DLC into a single container without copying anything, with
  the layer order proposed from names and contents and editable by hand.
- Batches: a folder with several games, each with its own card, checkbox and, if encrypted, password.
- Warns when `sce_sys/param.json` is missing or unreadable and lets you replace it with "Add param.json...".
- Pause and resume, low priority for the engine, a write throttle on Linux, a log of every session.
- Window in English and Spanish, a self-contained Windows package and an installer for Linux.
