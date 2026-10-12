# Third-party licenses

ps5-game-packer is GPL-3.0-or-later (see `LICENSE` at the root). This folder holds the licenses of what is
installed with it or shipped inside the Windows package. Generated with `tools/licenses.py`.

| Component | Version | License | Text | Web |
|---|---|---|---|---|
| cffi | 2.1.1 | MIT-0 | `cffi-LICENSE` | https://github.com/python-cffi/cffi |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | `cryptography-LICENSE`, `cryptography-LICENSE.APACHE`, `cryptography-LICENSE.BSD` | https://github.com/pyca/cryptography |
| customtkinter | 6.0.0 | MIT License | `customtkinter-LICENSE` | https://github.com/TomSchimansky/CustomTkinter |
| darkdetect | 0.8.0 | BSD License | `darkdetect-LICENSE` | https://github.com/albertosottile/darkdetect |
| isal | 1.8.0 | PSF-2.0 | `isal-LICENSE` | https://github.com/pycompression/python-isal |
| mkpfs | 1.1.0 | GNU GENERAL PUBLIC LICENSE | `mkpfs-LICENSE` | https://github.com/PSBrew/MkPFS |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | `packaging-LICENSE`, `packaging-LICENSE.APACHE`, `packaging-LICENSE.BSD` | https://github.com/pypa/packaging |
| pillow | 12.3.0 | MIT-CMU | `pillow-LICENSE` | https://github.com/python-pillow/Pillow |
| psutil | 7.2.2 | BSD-3-Clause | `psutil-LICENSE` | https://github.com/giampaolo/psutil |
| pycparser | 3.11 | BSD-3-Clause | `pycparser-LICENSE` | https://github.com/eliben/pycparser |
| pycryptodomex | 3.24.1 | BSD License | `pycryptodomex-LICENSE.rst` | https://github.com/Legrandin/pycryptodome |
| pyzipper | 0.4.0 | MIT | `pyzipper-AUTHORS.rst`, `pyzipper-LICENSE`, `pyzipper-LICENSE.python` | https://github.com/danifus/pyzipper |
| zlib-ng | 1.0.0 | PSF-2.0 | `zlib-ng-LICENSE` | https://github.com/pycompression/python-zlib-ng |
| Python | | PSF License (Python Software Foundation) | (see web) | https://docs.python.org/3/license.html |
| Tcl/Tk | | Tcl/Tk License (BSD-style) | (see web) | https://www.tcl.tk/software/tcltk/license.html |
| UnRAR | | RARLAB freeware (UnRAR license) | `UnRAR-RARLAB.txt` | https://www.rarlab.com/rar_add.htm |

UnRAR (RARLAB) is redistributed only inside the Windows package, with its own `license.txt`, as its license
allows; it is never used to create RAR archives.
