#!/bin/bash
# macOS: construye "AI Launcher.app" y la instala en /Applications (o ~/Applications).
# Para Windows usá install_windows.bat
# Uso: ./build_app.sh [carpeta_destino]
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="AI Launcher"
DEST_DIR="${1:-/Applications}"
[ -w "$DEST_DIR" ] || DEST_DIR="$HOME/Applications"
mkdir -p "$DEST_DIR"
APP="$DEST_DIR/$APP_NAME.app"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
VENV="$SUPPORT/venv"

echo "→ Entorno Python en: $VENV"
# si cambiaron las dependencias, que el bootstrap las vuelva a instalar
"$VENV/bin/python" -m pip install -q -r "$SRC/app/requirements.txt" 2>/dev/null || true
"$SRC/app/bootstrap.sh"

echo "→ Íconos"
"$VENV/bin/python" -m pip install -q pillow
"$VENV/bin/python" "$SRC/tools/make_icon.py" "$SRC/build"

# cerrar una instancia anterior para reemplazarla
if [ -f "$SUPPORT/server.json" ]; then
  OLD_PID="$("$VENV/bin/python" -c "import json,sys;print(json.load(open(sys.argv[1]))['pid'])" "$SUPPORT/server.json" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "→ Cerrando la instancia anterior (PID $OLD_PID)"
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$SUPPORT/server.json"
fi

echo "→ Armando $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
cp "$SRC/app/"*.py "$SRC/app/ui.html" "$SRC/app/bootstrap.sh" "$SRC/app/requirements.txt" "$SRC/app/help.js" "$APP/Contents/Resources/app/"
cp "$SRC/build/AppIcon.icns" "$SRC/build/menubar.png" "$APP/Contents/Resources/app/"
cp "$SRC/build/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

VERSION="$(date +%Y.%m.%d)"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>com.local.ailauncher</string>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/$APP_NAME" <<'LAUNCH'
#!/bin/bash
# Prepara (si hace falta) el entorno Python y arranca la app.
RES="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
"$RES/bootstrap.sh" || exit 1
exec "$HOME/Library/Application Support/AI Launcher/venv/bin/python" "$RES/launcher.py" "$@"
LAUNCH
chmod +x "$APP/Contents/MacOS/$APP_NAME" "$APP/Contents/Resources/app/bootstrap.sh"

# registrar en Launch Services para que aparezca en el Launchpad / Spotlight
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
touch "$APP"

echo "✓ Instalada en: $APP"
echo "  Abrila desde el Launchpad o con: open \"$APP\""
