Synthetic MINI game (seeded random bytes, no real content at all) laid out like a PS5 game
(`PPSA99999-app/eboot.bin`, `sce_sys/param.json`, `fakelib/libSceAmpr.sprx`...), about 280 KB.

- `mini-game.zip`: the source (it also defines the contents the tests expect).
- `mini-game.rar`: plain RAR 5 (WinRAR 7.23 on Windows 11, `rar a -r -ep1 -m3`).
- `mini-game.parts.part1..3.rar`: the same contents in 100 KB volumes (`-v100k`).
- `mini-game.password.rar`: solid and encrypted with the password `Secreto123` (`-s -pSecreto123`).
