# AI Launcher

[Español](README.md) · **English**

**AI Launcher** is an app for macOS and Windows that lets you start, monitor, and stop your local AI tools (**Ollama**, **Stable Diffusion** with AUTOMATIC1111, Forge, or ComfyUI, and **Kohya**) from a simple window and from a menu bar icon (macOS) or system tray icon (Windows). If you don't have any of those tools yet, it also installs them from scratch for you.

![AI Launcher: monitor for Ollama, Stable Diffusion and Kohya](docs/screenshot.png)

## Download

Download the latest version from **[Releases](../../releases/latest)**. You don't need to have Python installed.

**macOS**
1. Choose the `.dmg` that matches your Mac: `arm64` for Apple Silicon (M1, M2, M3, M4) or `x86_64` for Intel.
2. Open it and drag **AI Launcher** into the Applications folder.
3. The first time, **right-click the app → Open → Open**. (The app is not signed with an Apple certificate, which is why macOS asks for confirmation. If it still blocks the app: System Settings → Privacy & Security → "Open Anyway".)

**Windows**
1. Download the `.zip` (`Windows-x64`) and extract it into a folder.
2. Run `AI Launcher.exe`.
3. If "Windows protected your PC" appears: **More info → Run anyway**. (The app is not digitally signed.)
4. On Windows 10 you may need the WebView2 runtime; on Windows 11 it is already included.

## What it does

- **Monitor:** shows what is running, with its CPU, memory, ports, and uptime. It also detects processes that were not started from the app. You can stop them (or force-close them, including their subprocesses) and unload Ollama models or the SD checkpoint from memory.
- **Ollama:** all the `ollama serve` variables, read from `ollama serve --help` for the installed version. It can be started with `ollama serve` or with the official app. You can also download, load, delete, and chat with models.
- **Stable Diffusion:** all the command-line options, read from the code of each installation (`modules/cmd_args.py` or `comfy/cli_args.py`), grouped and searchable. It shows the command that will be run, lets you add extra arguments, and lets you save profiles.
- **Kohya:** detects and starts Kohya (LoRA training) like the other tools.
- **Install apps from scratch:** if you don't have Ollama, Stable Diffusion (A1111, Forge, ComfyUI), or Kohya, the app downloads and installs them for you.
- **Beginner help:** plain-language explanations next to the most important options and variables.
- **Memory and port conflict warnings:** warns you if what you're about to start competes for memory with something already running, or if the port is already in use.
- **Logs** for everything started from the app and for the Ollama app.
- **Tray or menu bar icon:** shows the status and gives you access to start, stop, unload models, and close processes without opening the window. Optionally starts with your session.

## Install from source

An alternative to the executables: the app runs with your own Python (3.9 or later) inside an isolated environment.

- **macOS:** `./build_app.sh` generates `AI Launcher.app` in `/Applications`. The environment is stored in `~/Library/Application Support/AI Launcher/venv` and is recreated automatically if it breaks.
- **Windows:** double-click `install_windows.bat` (adds the app to the Start menu; `install_windows.ps1 -Desktop` also adds a shortcut on the Desktop). It does not require administrator permissions and, if Python is missing, offers to install it with `winget`. The environment is stored in `%LOCALAPPDATA%\AI Launcher\venv`.

To try it without installing:

```bash
python3 -m pip install -r app/requirements.txt
python3 app/launcher.py
```

## Build the executables

The executables are built with [PyInstaller](https://pyinstaller.org). Build on a machine running the target system (you cannot build for Windows from macOS or the other way around):

```bash
python -m pip install -r packaging/requirements-build.txt
APP_VERSION=1.0.0 python packaging/build.py     # on Windows (PowerShell): $env:APP_VERSION="1.0.0"; python packaging/build.py
```

This generates the icons and leaves a `.dmg` (macOS) or a `.zip` (Windows) in `dist/`. To publish a version, create a `vX.Y.Z` tag and push it: the `.github/workflows/release.yml` workflow builds for macOS (arm64 and Intel) and Windows, and attaches the files to the Release.

Code layout:
- `app/backend.py`: detection, processes (with psutil), and the local HTTP API.
- `app/installers.py`: installation of Ollama, Stable Diffusion, and Kohya.
- `app/platform_utils.py`: everything that depends on the operating system.
- `app/launcher.py`: window (pywebview) and tray icon (rumps on macOS, pystray on Windows).
- `app/ui.html`, `app/help.js`: the interface and its help.
- `packaging/`: PyInstaller specification and build script.

## How each tool is found (no fixed paths)

On every startup, the app checks the paths saved in the cache (`config.json`). If any of them no longer exists, it searches again, in this order:

1. Running processes: if Ollama or SD is already running, its own executable or folder shows where it is installed.
2. The user's real `PATH`: on macOS, the one from the login shell; on Windows, the one from the registry.
3. Spotlight (macOS) or the programs registered in Windows.
4. Scanning the home folder and, on Windows, the other drives.

If it started from the cache, it also looks for new installations in the background.

## Privacy

Everything is local: the interface is served only on `127.0.0.1` (with a token per session), and there is no telemetry, accounts, or analytics. The app only connects to the Internet when you ask it to install something, and in that case it downloads from each tool's official sources (for example, their official repositories or websites).

## License

[MIT](LICENSE).
