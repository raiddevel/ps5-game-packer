#!/usr/bin/env bash
#
# Installs PS5 Game Packer for the current user on Linux, no sudo needed:
#   - a venv with the MkPFS engine, CustomTkinter and pyzipper (~/.local/share/ps5-game-packer/venv)
#   - the launcher ~/.local/bin/ps5-game-packer, a menu entry and the icon
# Windows users: use the .exe from the release zip instead. See --help for the options.
# Instala PS5 Game Packer para el usuario actual en Linux, sin sudo (opciones en --help).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)" in Darwin) MAC=1 ;; *) MAC=0 ;; esac
if [ "$MAC" = 1 ]; then
  DATA_HOME="$HOME/Library/Application Support"
else
  DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
fi
VENV="${PS5_GAME_PACKER_VENV:-$DATA_HOME/ps5-game-packer/venv}"
# Engine: until MkPFS publishes a release with --archive-root and --layer on PyPI, it comes from the fork (branch or tag).
MKPFS_PIN="${MKPFS_PIN:-mkpfs @ git+https://github.com/raiddevel/MkPFS.git@layered-source}"
PYTHON="${PYTHON:-python3}"
FORK=""
NO_MENU=0
UNINSTALL=0

es() { case "${LANGUAGE:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}" in es*) return 0 ;; *) return 1 ;; esac; }
msg() { if es; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
warn() { if es; then printf '\033[33mAVISO: %s\033[0m\n' "$2"; else printf '\033[33mWARNING: %s\033[0m\n' "$1"; fi; }
fail() { if es; then printf '\033[31mERROR: %s\033[0m\n' "$2" >&2; else printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; fi; exit 1; }

usage() {
  cat <<'USAGE'
install.sh [--venv DIR] [--python PATH] [--fork PATH_OR_URL] [--no-menu] [--uninstall] [--help]

  --venv DIR          venv folder (default ~/.local/share/ps5-game-packer/venv)
                      carpeta del venv (por defecto la de arriba)
  --python PATH       python3 used to create the venv (it must have tkinter), e.g. one installed by uv
                      python3 con el que crear el venv (tiene que traer tkinter), p. ej. el de uv
  --fork PATH_OR_URL  install mkpfs from a local path or git URL instead of the pinned MKPFS_PIN,
                      e.g. --fork 'git+https://github.com/raiddevel/MkPFS@layered-source' or --fork ~/MkPFS
                      instalar mkpfs desde una ruta local o URL git en vez del pin MKPFS_PIN
  --no-menu           skip the launcher, menu entry and icon / sin lanzador, entrada de menú ni icono
  --uninstall         remove the launcher, menu entry and icon (the venv is kept) / quitarlos (el venv se deja)

Variables: MKPFS_PIN (pip requirement of the engine), PS5_GAME_PACKER_VENV, PYTHON.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --venv) VENV="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --fork) FORK="$2"; shift 2 ;;
    --no-menu) NO_MENU=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" "opción desconocida: $1" ;;
  esac
done

BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
LAUNCHER="$BIN_DIR/ps5-game-packer"
DESKTOP="$APPS_DIR/ps5-game-packer.desktop"

refresh_menu() {
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$ICONS_DIR" >/dev/null 2>&1 || true
}

if [ "$UNINSTALL" = 1 ]; then
  rm -f "${LAUNCHER:?}" "${DESKTOP:?}"
  for n in 16 22 24 32 48 64 128 256 512; do rm -f "${ICONS_DIR:?}/${n:?}x${n:?}/apps/ps5-game-packer.png"; done
  rm -f "${ICONS_DIR:?}/scalable/apps/ps5-game-packer.svg"
  refresh_menu
  msg "Launcher, menu entry and icon removed. The venv is still at $VENV (delete it yourself if you want)." \
      "Lanzador, entrada de menú e icono quitados. El venv sigue en $VENV (bórralo tú si quieres)."
  exit 0
fi

# checks
command -v "$PYTHON" >/dev/null 2>&1 || fail "$PYTHON not found" "no encuentro $PYTHON"
"$PYTHON" - <<'PY' || fail "Python 3.10 or newer is required" "hace falta Python 3.10 o superior"
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  if [ "$MAC" = 1 ]; then
    fail "that Python has no tkinter: install Python from python.org or 'brew install python-tk'" \
         "ese Python no trae tkinter: instala Python desde python.org o 'brew install python-tk'"
  else
    fail "tkinter missing: sudo apt install python3-tk (or pass --python with a Python that has it, e.g. one from uv)" \
         "falta tkinter: sudo apt install python3-tk (o usa --python con un Python que lo traiga, p. ej. el de uv)"
  fi
fi
if ! command -v unrar >/dev/null 2>&1; then
  if [ "$MAC" = 1 ]; then
    warn "unrar not installed: .rar sources cannot be read (RAR from RARLAB: brew install --cask rar)" \
         "unrar no está instalado: los .rar no se podrán leer (RAR de RARLAB: brew install --cask rar)"
  else
    warn "unrar not installed: .rar sources cannot be read (sudo apt install unrar; Debian keeps it in non-free)" \
         "unrar no está instalado: los .rar no se podrán leer (sudo apt install unrar; en Debian está en non-free)"
  fi
fi

# venv with MkPFS and CustomTkinter
if [ ! -x "$VENV/bin/python" ]; then
  msg "Creating the venv at $VENV…" "Creando el venv en $VENV…"
  mkdir -p "$(dirname "$VENV")"
  if ! "$PYTHON" -m venv "$VENV" 2>/dev/null; then
    if command -v uv >/dev/null 2>&1; then
      uv venv --seed -p "$PYTHON" "$VENV"
    else
      fail "could not create the venv (sudo apt install python3-venv)" "no se pudo crear el venv (sudo apt install python3-venv)"
    fi
  fi
fi
PIP="$VENV/bin/pip"
[ -x "$PIP" ] || "$VENV/bin/python" -m ensurepip --upgrade >/dev/null
msg "Installing dependencies into the venv…" "Instalando dependencias en el venv…"
"$PIP" install --quiet --upgrade pip >/dev/null 2>&1 || true
"$PIP" install --quiet --upgrade "customtkinter>=5.2" "pyzipper>=0.3"
if [ -n "$FORK" ]; then
  msg "Installing mkpfs from $FORK…" "Instalando mkpfs desde $FORK…"
  "$PIP" install --quiet --upgrade "$FORK"
else
  msg "Installing the engine: $MKPFS_PIN…" "Instalando el motor: $MKPFS_PIN…"
  "$PIP" install --quiet --upgrade "$MKPFS_PIN"
fi
"$VENV/bin/mkpfs" -V >/dev/null || fail "mkpfs does not run inside the venv" "mkpfs no arranca en el venv"
msg "Engine: $("$VENV/bin/mkpfs" -V 2>&1 | head -1)" "Motor: $("$VENV/bin/mkpfs" -V 2>&1 | head -1)"
PACK_HELP="$("$VENV/bin/mkpfs" pack folder --help 2>&1 || true)"
case "$PACK_HELP" in *--archive-root*) ;; *) warn "this MkPFS cannot read RAR/ZIP (no --archive-root)" "este MkPFS no lee RAR/ZIP (sin --archive-root)" ;; esac
case "$PACK_HELP" in *--layer*) ;; *) warn "this MkPFS cannot merge layers (no --layer)" "este MkPFS no fusiona capas (sin --layer)" ;; esac

# launcher, menu entry and icon
if [ "$NO_MENU" = 0 ]; then
  mkdir -p "$BIN_DIR"
  {
    echo '#!/bin/sh'
    echo "# ps5-game-packer launcher (written by install.sh)"
    echo "exec \"$VENV/bin/python\" \"$HERE/ps5-game-packer.py\" \"\$@\""
  } > "$LAUNCHER"
  chmod +x "$LAUNCHER"
  if [ "$MAC" = 0 ]; then
    mkdir -p "$APPS_DIR"
    sed "s|^Exec=ps5-game-packer|Exec=\"$LAUNCHER\" %f|" "$HERE/data/ps5-game-packer.desktop" > "$DESKTOP"
    for n in 16 22 24 32 48 64 128 256 512; do
      mkdir -p "$ICONS_DIR/${n}x${n}/apps"
      cp "$HERE/data/icon/ps5-game-packer-${n}.png" "$ICONS_DIR/${n}x${n}/apps/ps5-game-packer.png"
    done
    mkdir -p "$ICONS_DIR/scalable/apps"
    cp "$HERE/data/icon/ps5-game-packer.svg" "$ICONS_DIR/scalable/apps/ps5-game-packer.svg"
    refresh_menu
  fi
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not in your PATH; the menu entry works anyway" "$BIN_DIR no está en tu PATH; el menú funciona igual" ;; esac
  msg "Installed. Look for \"PS5 Game Packer\" in the menu or run: ps5-game-packer" \
      "Instalado. Búscalo en el menú como «Comprimir juegos PS5» o ejecuta: ps5-game-packer"
else
  msg "Venv ready. Run: \"$VENV/bin/python\" \"$HERE/ps5-game-packer.py\"" "Venv listo. Ejecuta: \"$VENV/bin/python\" \"$HERE/ps5-game-packer.py\""
fi
