#!/usr/bin/env python3
"""Compila AI Launcher a un ejecutable sin dependencias de Python.

Uso (desde cualquier carpeta):  python packaging/build.py
Variables: APP_VERSION (por defecto 0.0.0)

Resultado en dist/:
  macOS   -> AI-Launcher-<ver>-macOS-<arch>.dmg   (y "AI Launcher.app")
  Windows -> AI-Launcher-<ver>-Windows-x64.zip    (y la carpeta "AI Launcher")
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "packaging" / "build-tmp"
VERSION = os.environ.get("APP_VERSION", "0.0.0").lstrip("v") or "0.0.0"
NAME = "AI Launcher"


def sh(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main():
    os.environ["APP_VERSION"] = VERSION
    print("== AI Launcher %s (%s) ==" % (VERSION, sys.platform))
    sh([sys.executable, ROOT / "tools" / "make_icon.py", ROOT / "build"])

    for d in (DIST / (NAME + ".app"), DIST / NAME, WORK):
        shutil.rmtree(d, ignore_errors=True)
    sh([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", WORK, ROOT / "packaging" / "ai_launcher.spec"])

    if sys.platform == "darwin":
        arch = platform.machine()  # arm64 / x86_64
        app = DIST / (NAME + ".app")
        if not app.exists():
            sys.exit("No se generó " + str(app))
        out = DIST / ("AI-Launcher-%s-macOS-%s.dmg" % (VERSION, arch))
        out.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / NAME
            stage.mkdir()
            sh(["ditto", app, stage / app.name])  # ditto conserva enlaces y atributos
            os.symlink("/Applications", stage / "Applications")
            sh(["hdiutil", "create", "-volname", NAME, "-srcfolder", stage, "-ov", "-format", "UDZO", out])
        print("OK:", out, "%.1f MB" % (out.stat().st_size / 1e6))
    elif sys.platform == "win32":
        folder = DIST / NAME
        if not folder.exists():
            sys.exit("No se generó " + str(folder))
        base = DIST / ("AI-Launcher-%s-Windows-x64" % VERSION)
        Path(str(base) + ".zip").unlink(missing_ok=True)
        shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir=NAME)
        out = Path(str(base) + ".zip")
        print("OK:", out, "%.1f MB" % (out.stat().st_size / 1e6))
    else:
        print("Plataforma sin empaquetado de distribución; queda la carpeta en dist/.")


if __name__ == "__main__":
    main()
