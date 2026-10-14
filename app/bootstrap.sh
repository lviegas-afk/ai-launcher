#!/bin/bash
# Verifica el entorno Python de AI Launcher y lo (re)crea si falta o está roto.
# Así la app sigue funcionando aunque se actualice o cambie el Python del sistema.
SUPPORT="$HOME/Library/Application Support/AI Launcher"
VENV="$SUPPORT/venv"
LOG="$HOME/Library/Logs/AI Launcher/bootstrap.log"
mkdir -p "$SUPPORT" "$(dirname "$LOG")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

if "$VENV/bin/python" -c "import webview, rumps, psutil" 2>/dev/null; then
  exit 0
fi

# buscar un Python 3.9+ en cualquier lugar habitual
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  p="$(command -v "$c" 2>/dev/null)" || continue
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  osascript -e 'display alert "AI Launcher" message "No encontré Python 3.9 o superior. Instalá las herramientas de línea de comandos con: xcode-select --install"' >/dev/null 2>&1
  exit 1
fi

osascript -e 'display notification "Preparando el entorno por primera vez (≈1 min)…" with title "AI Launcher"' >/dev/null 2>&1 || true
{
  echo "=== $(date) usando $PY"
  rm -rf "$VENV"
  "$PY" -m venv "$VENV" &&
  "$VENV/bin/python" -m pip install -q --upgrade pip &&
  "$VENV/bin/python" -m pip install -q -r "$(dirname "$0")/requirements.txt"
} >>"$LOG" 2>&1
if ! "$VENV/bin/python" -c "import webview, rumps, psutil" 2>/dev/null; then
  osascript -e 'display alert "AI Launcher" message "No pude preparar el entorno de Python. Revisá ~/Library/Logs/AI Launcher/bootstrap.log (¿hay conexión a internet?)."' >/dev/null 2>&1
  exit 1
fi
