# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec de AI Launcher (macOS y Windows). Uso: ver packaging/build.py
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821  (SPECPATH lo define PyInstaller)
APP = os.path.join(ROOT, "app")
BUILD = os.path.join(ROOT, "build")
VERSION = os.environ.get("APP_VERSION", "0.0.0")
NAME = "AI Launcher"

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

datas = [(os.path.join(APP, "ui.html"), "."), (os.path.join(APP, "help.js"), ".")]
hiddenimports = ["psutil", "webview"]

if IS_MAC:
    datas += [(os.path.join(BUILD, "menubar.png"), "."), (os.path.join(BUILD, "AppIcon.icns"), ".")]
    hiddenimports += ["webview.platforms.cocoa", "rumps", "AppKit", "Foundation", "WebKit", "objc", "PyObjCTools"]
    icon = os.path.join(BUILD, "AppIcon.icns")
elif IS_WIN:
    datas += [(os.path.join(BUILD, "tray.png"), "."), (os.path.join(BUILD, "AppIcon.ico"), ".")]
    hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr", "pythonnet",
                      "pystray", "pystray._win32", "PIL", "PIL.Image", "winreg"]
    icon = os.path.join(BUILD, "AppIcon.ico")
else:
    icon = None

a = Analysis(  # noqa: F821
    [os.path.join(APP, "launcher.py")],
    pathex=[APP],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,          # sin ventana de consola (equivale a pythonw)
    icon=icon,
)

coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name=NAME,
)

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name=NAME + ".app",
        icon=icon,
        bundle_identifier="com.local.ailauncher",
        version=VERSION,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            "LSArchitecturePriority": ["arm64", "x86_64"],
        },
    )
