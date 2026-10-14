"""Todo lo que depende del sistema operativo (macOS / Windows), en un solo lugar.

Nada de rutas fijas: las ubicaciones salen de variables de entorno, del PATH real
del usuario, de Spotlight (macOS) o del registro (Windows).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))  # empaquetado con PyInstaller
IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
APP_NAME = "AI Launcher"
HOME = Path.home()
EXE = ".exe" if IS_WIN else ""

# en Windows, que los comandos auxiliares no abran ventanas de consola
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def resource_dir():
    """Carpeta con ui.html, help.js e íconos (en el ejecutable empaquetado: sys._MEIPASS)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).resolve().parent)
    return Path(__file__).resolve().parent


def self_command(*args):
    """Comando para volver a ejecutar esta misma app (empaquetada o desde el código)."""
    if FROZEN:
        return [sys.executable] + list(args)
    return [sys.executable, str(Path(__file__).resolve().parent / "launcher.py")] + list(args)


def run(cmd, timeout=10, **kw):
    try:
        if IS_WIN:
            kw.setdefault("creationflags", _NO_WINDOW)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw).stdout
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# Carpetas de la app
# ----------------------------------------------------------------------------

def data_dir():
    if IS_WIN:
        return Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming") / APP_NAME
    if IS_MAC:
        return HOME / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / APP_NAME


def log_dir():
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData" / "Local") / APP_NAME / "Logs"
    if IS_MAC:
        return HOME / "Library" / "Logs" / APP_NAME
    return Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local" / "state") / APP_NAME


# ----------------------------------------------------------------------------
# PATH real del usuario
# ----------------------------------------------------------------------------

def augment_path():
    """Una app abierta desde el Launchpad/menú Inicio no hereda el PATH de la terminal.

    macOS: se le pregunta al shell de login del usuario cuál es su PATH.
    Windows: se lee el PATH de usuario y de sistema del registro.
    """
    extra = []
    if IS_WIN:
        try:
            import winreg
            for root, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                              (winreg.HKEY_LOCAL_MACHINE,
                               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                try:
                    with winreg.OpenKey(root, key) as k:
                        val, _ = winreg.QueryValueEx(k, "Path")
                        extra += os.path.expandvars(val).split(os.pathsep)
                except OSError:
                    pass
        except ImportError:
            pass
    else:
        shell = os.environ.get("SHELL") or shutil.which("zsh") or shutil.which("bash") or "sh"
        out = run([shell, "-l", "-c", "printf '%s' \"$PATH\""], timeout=5)
        extra += [p for p in out.strip().split(os.pathsep) if p.startswith("/")]
        if IS_MAC and os.path.exists("/usr/libexec/path_helper"):
            m = re.search(r'PATH="([^"]+)"', run(["/usr/libexec/path_helper", "-s"], 3))
            if m:
                extra += m.group(1).split(os.pathsep)
    current = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(current + [p for p in extra if p]))


# ----------------------------------------------------------------------------
# Abrir cosas
# ----------------------------------------------------------------------------

def open_target(target):
    """Abre una URL, archivo o carpeta con la app por defecto."""
    if IS_WIN:
        os.startfile(target)  # noqa
    elif IS_MAC:
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def reveal(path):
    """Muestra el archivo en Finder / Explorador."""
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path])
    else:
        open_target(os.path.dirname(path))


def choose_path(kind="folder", prompt="Elegí una carpeta"):
    """Diálogo nativo para elegir carpeta o archivo. Devuelve '' si se cancela."""
    if IS_MAC:
        what = "folder" if kind == "folder" else "file"
        out = run(["osascript", "-e", 'tell application "System Events" to activate',
                   "-e", 'POSIX path of (choose %s with prompt "%s")' % (what, prompt.replace('"', ""))],
                  timeout=600).strip()
        return out.rstrip("/") if kind == "folder" and len(out) > 1 else out
    if IS_WIN and FROZEN:
        # sin intérprete de Python: diálogo de Windows Forms vía PowerShell
        if kind == "folder":
            dlg = ("$d=New-Object System.Windows.Forms.FolderBrowserDialog;$d.Description=$env:AIL_PROMPT;"
                   "if($d.ShowDialog($f) -eq 'OK'){$d.SelectedPath}")
        else:
            dlg = ("$d=New-Object System.Windows.Forms.OpenFileDialog;$d.Title=$env:AIL_PROMPT;"
                   "if($d.ShowDialog($f) -eq 'OK'){$d.FileName}")
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;Add-Type -AssemblyName System.Windows.Forms;"
              "$f=New-Object System.Windows.Forms.Form;$f.TopMost=$true;$f.ShowInTaskbar=$false;"
              "$f.StartPosition='CenterScreen';$f.Size=New-Object System.Drawing.Size(0,0);" + dlg)
        env = dict(os.environ, AIL_PROMPT=prompt)
        out = run(["powershell", "-NoProfile", "-STA", "-Command", ps], timeout=600, env=env).strip()
        return os.path.normpath(out) if out else ""
    # Windows / Linux desde el código: tkinter en un proceso aparte (no choca con la ventana web)
    code = ("import tkinter as tk\nfrom tkinter import filedialog\nr=tk.Tk();r.withdraw()\n"
            "r.attributes('-topmost',True)\n"
            "p=filedialog.askdirectory(title=%r) if %r=='folder' else filedialog.askopenfilename(title=%r)\n"
            "print(p or '')" % (prompt, kind, prompt))
    py = sys.executable
    if IS_WIN and py.lower().endswith("pythonw.exe"):
        py = py[:-len("pythonw.exe")] + "python.exe"
    out = run([py, "-c", code], timeout=600).strip()
    return os.path.normpath(out) if out else ""


def open_terminal(args, title="Terminal"):
    """Abre una terminal nueva ejecutando `args` (por ej. ollama run modelo)."""
    if IS_WIN:
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        subprocess.Popen([comspec, "/c", "start", title, comspec, "/k"] + list(args))
    elif IS_MAC:
        import shlex
        cmd = " ".join(shlex.quote(a) for a in args)
        script = 'tell application "Terminal"\nactivate\ndo script "%s"\nend tell' % cmd.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", script])
    else:
        term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or "xterm"
        subprocess.Popen([term, "-e"] + list(args))


# ----------------------------------------------------------------------------
# Lanzar procesos desacoplados
# ----------------------------------------------------------------------------

def detached_kwargs():
    """Para que lo que lanzamos siga vivo aunque se cierre el launcher."""
    if IS_WIN:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW}
    return {"start_new_session": True}


_HAS_ARM64 = None  # cache: el Mac tiene chip Apple Silicon (arm64)


def _mac_has_arm64():
    global _HAS_ARM64
    if _HAS_ARM64 is None:
        _HAS_ARM64 = run(["sysctl", "-n", "hw.optional.arm64"], 3).strip() == "1"
    return _HAS_ARM64


def shell_script_command(script_path, args):
    """Cómo ejecutar el script de arranque de A1111/Forge en este sistema."""
    if IS_WIN:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(script_path)] + list(args)
    bash = shutil.which("bash") or "/bin/sh"
    # En Apple Silicon, si la app se lanzó con preferencia Rosetta (x86_64), esa preferencia se
    # hereda a los procesos que spawnea y `uname -m` devuelve x86_64: el script (kohya) elige
    # dependencias de Intel. Forzamos arm64 para que el script y lo que lanza sean nativos.
    if IS_MAC and _mac_has_arm64():
        return ["/usr/bin/arch", "-arm64", bash, str(script_path)] + list(args)
    return [bash, str(script_path)] + list(args)


def webui_script_name():
    return "webui.bat" if IS_WIN else "webui.sh"


def venv_python(folder):
    """Python de un venv (venv/ o .venv/) dentro de `folder`, si existe."""
    sub = ("Scripts", "python.exe") if IS_WIN else ("bin", "python")
    for v in ("venv", ".venv"):
        p = Path(folder, v, *sub)
        if p.exists():
            return str(p)
    return None


def system_python():
    for name in (["python", "py"] if IS_WIN else ["python3", "python"]):
        p = shutil.which(name)
        if p:
            return p
    return ("python" if IS_WIN else "python3") if FROZEN else sys.executable


# ----------------------------------------------------------------------------
# Búsqueda de archivos
# ----------------------------------------------------------------------------

def spotlight(name, onlyin=None):
    """Busca archivos por nombre con el índice del sistema (sólo macOS)."""
    if not IS_MAC or not shutil.which("mdfind"):
        return []
    cmd = ["mdfind"] + (["-onlyin", str(onlyin)] if onlyin else []) + ["kMDItemFSName == '%s'" % name]
    return [line for line in run(cmd, 20).splitlines() if line.strip()]


def search_roots():
    """Carpetas donde buscar instalaciones recorriendo el disco."""
    roots = [HOME]
    if IS_WIN:
        # todas las unidades montadas (C:\, D:\, ...)
        try:
            drives = os.listdrives()  # Python 3.12+
        except AttributeError:
            import string
            drives = ["%s:\\" % d for d in string.ascii_uppercase if os.path.exists("%s:\\" % d)]
        roots += [Path(d) for d in drives]
    return roots


# carpetas que nunca vale la pena recorrer
SKIP_DIRS = {"node_modules", "venv", ".venv", "site-packages", "__pycache__", ".git", "repositories",
             "extensions", "Library", "Applications", "Pictures", "Music", "Movies", "AppData", "Windows",
             "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin", "System Volume Information",
             "Recovery", "PerfLogs", "OneDriveTemp"}
# carpetas de macOS protegidas: recorrerlas dispara pedidos de permiso
PROTECTED_DIRS = {"Desktop", "Documents", "Downloads"} if IS_MAC else set()


# ----------------------------------------------------------------------------
# Ubicación de Ollama
# ----------------------------------------------------------------------------

def ollama_binary_candidates():
    """Lugares donde buscar el ejecutable `ollama`, sin rutas fijas."""
    cands = []
    w = shutil.which("ollama")
    if w:
        cands.append(w)
    if IS_MAC:
        for app in ollama_app_candidates():
            cands.append(os.path.join(app, "Contents", "Resources", "ollama"))
        brew = shutil.which("brew")
        if brew:
            prefix = run([brew, "--prefix"], 5).strip()
            if prefix:
                cands.append(os.path.join(prefix, "bin", "ollama"))
    if IS_WIN:
        for d in _windows_install_dirs("Ollama"):
            cands.append(os.path.join(d, "ollama.exe"))
        local = os.environ.get("LOCALAPPDATA")
        if local:  # ubicación del instalador oficial (por usuario)
            cands.append(os.path.join(local, "Programs", "Ollama", "ollama.exe"))
    return cands


def ollama_app_candidates():
    """La app "de bandeja/barra de menú" de Ollama, si existe."""
    if IS_MAC:
        apps = []
        for q in ("kMDItemCFBundleIdentifier == 'com.electron.ollama'", "kMDItemFSName == 'Ollama.app'"):
            apps += [a for a in run(["mdfind", q], 20).splitlines() if a.endswith(".app")]
        return [a for a in dict.fromkeys(apps) if os.path.isdir(a) and "/.Trash/" not in a]
    return []


def ollama_app_for_binary(binary):
    """Dada la ruta del ejecutable, devuelve la app de bandeja asociada."""
    if not binary:
        return None
    if IS_MAC:
        if ".app/" in binary:
            return binary.split(".app/")[0] + ".app"
        apps = ollama_app_candidates()
        return apps[0] if apps else None
    if IS_WIN:
        d = os.path.dirname(binary)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.lower().startswith("ollama app") and f.lower().endswith(".exe"):
                return os.path.join(d, f)
    return None


def ollama_log_dirs():
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA")
        return [Path(local) / "Ollama"] if local else []
    return [HOME / ".ollama" / "logs"]


def launch_ollama_app(app, env_vars, all_names):
    """Abre la app de Ollama aplicándole las variables de configuración."""
    if IS_MAC:
        # la app de macOS lee su entorno de launchctl
        for k in all_names:
            v = env_vars.get(k)
            if v not in (None, ""):
                run(["launchctl", "setenv", k, str(v)], 3)
            else:
                run(["launchctl", "unsetenv", k], 3)
        run(["open", "-g", app], 10)
    else:
        # en Windows la app hereda el entorno de quien la lanza
        env = dict(os.environ)
        for k in all_names:
            env.pop(k, None)
        env.update({k: str(v) for k, v in env_vars.items() if v not in (None, "")})
        subprocess.Popen([app], env=env, **detached_kwargs())


def _windows_install_dirs(display_name):
    """InstallLocation de los programas registrados en Windows."""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    keys = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sub:
                            name = winreg.QueryValueEx(sub, "DisplayName")[0]
                            if display_name.lower() in str(name).lower():
                                loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                                if loc:
                                    out.append(loc)
                    except OSError:
                        continue
        except OSError:
            continue
    return out


# ----------------------------------------------------------------------------
# Iniciar con la sesión
# ----------------------------------------------------------------------------

_LOGIN_LABEL = "com.local.ailauncher"


def _launch_command(background=True):
    """Comando para arrancar esta misma instalación del launcher."""
    if FROZEN:
        return self_command(*(["--background"] if background else []))
    launcher = str(Path(__file__).resolve().parent / "launcher.py")
    py = sys.executable
    if IS_WIN and py.lower().endswith("python.exe"):
        pyw = py[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(pyw):
            py = pyw
    return [py, launcher] + (["--background"] if background else [])


def app_bundle_path():
    p = str(Path(sys.executable).resolve()) if FROZEN else str(Path(__file__).resolve())
    return p.split(".app/")[0] + ".app" if IS_MAC and ".app/" in p else None


def login_item_enabled():
    if IS_MAC:
        return (HOME / "Library" / "LaunchAgents" / (_LOGIN_LABEL + ".plist")).exists()
    if IS_WIN:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, APP_NAME)
                return True
        except OSError:
            return False
    return False


def set_login_item(enabled):
    if IS_MAC:
        plist = HOME / "Library" / "LaunchAgents" / (_LOGIN_LABEL + ".plist")
        if not enabled:
            plist.unlink(missing_ok=True)
            return
        bundle = app_bundle_path()
        args = ["/usr/bin/open", "-g", bundle, "--args", "--background"] if bundle else _launch_command()
        from xml.sax.saxutils import escape
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            '  <key>Label</key><string>%s</string>\n'
            '  <key>ProgramArguments</key><array>%s</array>\n'
            '  <key>RunAtLoad</key><true/>\n'
            '</dict></plist>\n' % (_LOGIN_LABEL, "".join("<string>%s</string>" % escape(a) for a in args)))
    elif IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                                  subprocess.list2cmdline(_launch_command()))
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except OSError:
                    pass
    else:
        raise RuntimeError("No soportado en este sistema")


# ----------------------------------------------------------------------------
# Diálogos simples para el ícono de bandeja
# ----------------------------------------------------------------------------

def native_confirm(title, message, ok="Aceptar"):
    if IS_WIN:
        import ctypes
        MB_OKCANCEL, MB_ICONWARNING, MB_TOPMOST = 0x1, 0x30, 0x40000
        return ctypes.windll.user32.MessageBoxW(0, message, title, MB_OKCANCEL | MB_ICONWARNING | MB_TOPMOST) == 1
    return True


def native_alert(title, message):
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x40000)


def focus_window(pid, title):
    """Trae al frente la ventana del launcher si ya está abierta."""
    if IS_MAC:
        try:
            from AppKit import NSRunningApplication
            ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if ra:
                ra.activateWithOptions_(1 << 1)
        except Exception:
            pass
    elif IS_WIN:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, title)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
