# Changes

## 2.1.4 - 2026-10-13

- A `param.json` on its own, as a loose file, alone inside a RAR/ZIP (even without the `sce_sys/` folder) or alone in
  a folder, is picked up as the fix for the game whose `sce_sys/param.json` is broken or missing: it goes on top as
  a layer, and the game takes its TITLE_ID, title and version.
- The list of games grows only up to 40 % of the screen and then scrolls, so the password cards, the destination
  and the "Pack" button stay visible on small screens even with many games.
- A DLC, update or backport that brings its own `eboot.bin` but no `param.json` is never taken as the base game:
  alone, it is reported as missing its base; next to the base, it is a layer.
- The screenshot tool no longer touches the real settings.

## 2.1.3 - 2026-10-13

- A RAR/ZIP whose name carries no TITLE_ID and whose `sce_sys/param.json` is broken or missing was silently dropped
  from the scan ("cannot tell which game"). It is now listed like a folder would be, with the archive name as a
  stand-in ID, so "Add param.json..." can fix it.
- Rescanning the same folder (typing a password, "Refresh") no longer resets the games you ticked or unticked, and
  keeps the `param.json` layers added with "Add param.json...".
- A game fixed with "Add param.json..." is ticked automatically.

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
