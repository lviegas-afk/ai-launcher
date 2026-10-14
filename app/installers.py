"""Instalar Ollama, A1111, Forge, ComfyUI y Kohya_ss desde cero, con las fuentes oficiales.

Cada instalación corre en un hilo, paso a paso; la salida de cada comando va a un log
(LOG_DIR/install-<app>.log). Todo lo que depende del sistema operativo sale de platform_utils.
Este módulo no importa `backend` al cargarse (backend lo importa a él): se usa de forma perezosa.
"""
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import platform_utils as pu
from platform_utils import HOME, IS_MAC, IS_WIN

PLATFORM = "mac" if IS_MAC else "windows" if IS_WIN else "linux"


def _be():
    import backend
    return backend


class InstallError(Exception):
    """Error pensado para mostrarse tal cual al usuario."""


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------
# Catálogo de programas
# ----------------------------------------------------------------------------

BREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

APPS = {
    "ollama": {
        "name": "Ollama",
        "purpose": "Corre modelos de IA de texto (tipo ChatGPT) en tu propia compu, sin internet y sin pagar.",
        "official_url": "https://ollama.com/download",
        "kind": None,
        "requirements": {"disk_gb": 4 if IS_WIN else 1, "ram_gb": 8, "download_gb": 1.2 if IS_WIN else 0.2,
                         "est_minutes": "2–5"},
    },
    "a1111": {
        "name": "Stable Diffusion WebUI (AUTOMATIC1111)",
        "purpose": "Genera imágenes con IA a partir de lo que escribís. Es el programa más conocido para eso.",
        "official_url": "https://github.com/AUTOMATIC1111/stable-diffusion-webui",
        "repo": "https://github.com/AUTOMATIC1111/stable-diffusion-webui",
        "folder": "stable-diffusion-webui", "kind": "a1111", "pyver": ["3.10"],
        "requirements": {"disk_gb": 15, "ram_gb": 8, "download_gb": 5, "est_minutes": "15–40"},
    },
    "forge": {
        "name": "Stable Diffusion WebUI Forge",
        "purpose": "Una versión más rápida y liviana de Stable Diffusion WebUI, buena si tu compu anda justa de memoria.",
        "official_url": "https://github.com/lllyasviel/stable-diffusion-webui-forge",
        "repo": "https://github.com/lllyasviel/stable-diffusion-webui-forge",
        "folder": "stable-diffusion-webui-forge", "kind": "forge", "pyver": ["3.10"],
        "requirements": {"disk_gb": 15, "ram_gb": 8, "download_gb": 5, "est_minutes": "15–40"},
    },
    "comfyui": {
        "name": "ComfyUI",
        "purpose": "Genera imágenes y videos con IA armando el proceso con cajitas conectadas: más flexible.",
        "official_url": "https://github.com/comfyanonymous/ComfyUI",
        "repo": "https://github.com/comfyanonymous/ComfyUI",
        "folder": "ComfyUI", "kind": "comfyui", "pyver": ["3.12", "3.11", "3.10"],
        "requirements": {"disk_gb": 8, "ram_gb": 8, "download_gb": 3, "est_minutes": "10–25"},
    },
    "kohya": {
        "name": "Kohya_ss",
        "purpose": "Entrena tus propios modelos de imágenes (LoRA) con tus fotos, para que la IA aprenda un estilo o un personaje.",
        "official_url": "https://github.com/bmaltais/kohya_ss",
        "repo": "https://github.com/bmaltais/kohya_ss",
        "folder": "kohya_ss", "kind": "kohya", "pyver": ["3.11", "3.10"],
        "requirements": {"disk_gb": 15, "ram_gb": 16, "download_gb": 6, "est_minutes": "15–40"},
    },
}


def _auto_support(app):
    """(auto_supported, nota) según el sistema operativo."""
    if PLATFORM == "linux":
        return False, "En Linux todavía no se instala automáticamente. Seguí la guía paso a paso."
    if app == "kohya" and IS_WIN:
        return False, ("En Windows el instalador de Kohya_ss es interactivo (te hace preguntas en una "
                       "ventana negra), así que no se puede automatizar. Seguí la guía paso a paso.")
    notes = {
        "ollama": "El instalador oficial no pide permisos de administrador." if IS_WIN else
                  "Copia la app a Aplicaciones (o a tu carpeta Aplicaciones si no hay permiso).",
        "a1111": "La primera vez descarga varios GB (incluye PyTorch)." +
                 (" En Mac con chip Apple corre más lento que con una placa NVIDIA." if IS_MAC else ""),
        "forge": "La primera vez descarga varios GB (incluye PyTorch)." +
                 (" En Mac con chip Apple corre más lento que con una placa NVIDIA." if IS_MAC else ""),
        "comfyui": "Descarga PyTorch (varios GB), así que puede tardar un buen rato.",
        "kohya": "Usa Homebrew para instalar Python y puede tardar bastante. Va a necesitar bastante memoria "
                 "para entrenar.",
    }
    return True, notes.get(app, "")


# ----------------------------------------------------------------------------
# Detección de requisitos (git, Python, Homebrew, winget)
# ----------------------------------------------------------------------------

_brew_prefix_cache = {}


def _brew_prefix():
    brew = shutil.which("brew")
    if not brew:
        return None
    if brew not in _brew_prefix_cache:
        _brew_prefix_cache[brew] = pu.run([brew, "--prefix"], 8).strip() or None
    return _brew_prefix_cache[brew]


def _py_version(exe):
    """'3.10' si `exe` es un Python que funciona y trae venv; si no, None."""
    out = pu.run([exe, "-c", "import sys, venv; print('%d.%d' % sys.version_info[:2])"], 10).strip()
    return out if re.fullmatch(r"\d+\.\d+", out) else None


def _python_candidates(minor):
    short = minor.replace(".", "")
    cands = []
    w = shutil.which("python" + minor)
    if w:
        cands.append(w)
    if IS_WIN:
        out = pu.run(["py", "-" + minor, "-c", "import sys; print(sys.executable)"], 10).strip()
        if out and os.path.isfile(out):
            cands.append(out)
        for base in (os.environ.get("LOCALAPPDATA") and Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Python",
                     os.environ.get("ProgramFiles") and Path(os.environ["ProgramFiles"])):
            if base:
                cands.append(str(Path(base) / ("Python" + short) / "python.exe"))
    else:
        prefix = _brew_prefix()
        if prefix:
            cands.append(os.path.join(prefix, "opt", "python@" + minor, "bin", "python" + minor))
            cands.append(os.path.join(prefix, "bin", "python" + minor))
        cands.append("/Library/Frameworks/Python.framework/Versions/%s/bin/python%s" % (minor, minor))
    for name in (["python", "py"] if IS_WIN else ["python3", "python"]):
        w = shutil.which(name)
        if w:
            cands.append(w)
    return [c for c in dict.fromkeys(cands) if os.path.isfile(c)]


def find_python(minors):
    """Primer Python que cumpla alguna de las versiones pedidas (en orden de preferencia)."""
    for minor in minors:
        for exe in _python_candidates(minor):
            if _py_version(exe) == minor:
                return {"exe": exe, "version": minor}
    return None


def _git_status():
    """(ok, ruta). En macOS /usr/bin/git es un atajo que sólo anda si están las herramientas de Apple."""
    git = shutil.which("git")
    if not git:
        return False, None
    if IS_MAC and git == "/usr/bin/git":
        if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0:
            return False, None
    return bool(pu.run([git, "--version"], 8).strip()), git


def _prereq(pid, name, ok, detail, auto, how, terminal_cmd=None, install=None):
    return {"id": pid, "name": name, "ok": bool(ok), "detail": detail, "auto": bool(auto), "how": how,
            "terminal_cmd": terminal_cmd, "_install": install}


def _p_git():
    ok, path = _git_status()
    if ok:
        return _prereq("git", "Git", True, path, True, "Ya está instalado.")
    if IS_MAC:
        return _prereq("git", "Git", False, "No está instalado (ni las herramientas de línea de comandos de Apple)",
                       False, "Apple lo instala con sus «herramientas de desarrollo»: se abre una ventana de Apple "
                       "que te pide confirmar. Hacelo y después volvé a intentar.", "xcode-select --install")
    if IS_WIN and shutil.which("winget"):
        return _prereq("git", "Git", False, "No está instalado", True,
                       "Se instala solo con winget (Git.Git).",
                       "winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements",
                       ("winget", "Git.Git"))
    return _prereq("git", "Git", False, "No está instalado", False,
                   "Descargalo e instalalo desde https://git-scm.com/downloads y volvé a intentar.")


def _p_python(minors, winget_minor=None):
    found = find_python(minors)
    label = "Python " + " / ".join(minors) if len(minors) > 1 else "Python " + minors[0]
    if found:
        return _prereq("python", label, True, "Python %s en %s" % (found["version"], found["exe"]), True,
                       "Ya está instalado.")
    pref = winget_minor or minors[0]
    if IS_MAC:
        if shutil.which("brew"):
            return _prereq("python", label, False, "No se encontró", True,
                           "Se instala solo con Homebrew (python@%s)." % pref, "brew install python@" + pref,
                           ("brew", "python@" + pref))
        return _prereq("python", label, False, "No se encontró", False,
                       "Primero instalá Homebrew (mirá el requisito de Homebrew); después se instala Python con él.",
                       None)
    if IS_WIN and shutil.which("winget"):
        pid = "Python.Python." + pref
        return _prereq("python", label, False, "No se encontró", True,
                       "Se instala solo con winget (%s)." % pid,
                       "winget install --id %s -e --accept-package-agreements --accept-source-agreements" % pid,
                       ("winget", pid))
    return _prereq("python", label, False, "No se encontró", False,
                   "Descargá Python %s desde https://www.python.org/downloads/ y marcá «Add python.exe to PATH»." % pref)


def _p_brew():
    brew = shutil.which("brew")
    if brew:
        return _prereq("brew", "Homebrew", True, brew, True, "Ya está instalado.")
    return _prereq("brew", "Homebrew", False, "No está instalado", False,
                   "Homebrew es el instalador de programas de la Mac. Para instalarlo hay que escribir tu "
                   "contraseña, así que se hace desde la Terminal (botón «Abrir Terminal»).", BREW_INSTALL)


def _p_brew_deps():
    missing = [f for f, exe in (("cmake", "cmake"), ("protobuf", "protoc"), ("rust", "rustc"), ("wget", "wget"))
               if not shutil.which(exe)]
    if not missing:
        return _prereq("brew_deps", "Herramientas de compilación (cmake, protobuf, rust, wget)", True,
                       "Ya están instaladas", True, "Ya están instaladas.")
    ok_brew = bool(shutil.which("brew"))
    return _prereq("brew_deps", "Herramientas de compilación (cmake, protobuf, rust, wget)", False,
                   "Faltan: " + ", ".join(missing), ok_brew,
                   "Se instalan solas con Homebrew." if ok_brew else "Primero instalá Homebrew.",
                   "brew install " + " ".join(missing), ("brew", missing))


def _p_winget():
    w = shutil.which("winget")
    return _prereq("winget", "winget (instalador de Windows)", bool(w), w or "No está disponible", False,
                   "Viene con Windows 10/11. Si falta, instalá «Instalador de aplicación» desde la Microsoft Store.")


def prereqs_for(app):
    a = APPS[app]
    out = []
    if app == "ollama":
        return out
    if IS_MAC:
        needs_brew = app in ("a1111", "forge", "kohya")
        git, py = _p_git(), None
        if app in ("a1111", "forge", "comfyui"):
            py = _p_python(a["pyver"], "3.12" if app == "comfyui" else None)
            needs_brew = needs_brew or not py["ok"]
        out.append(git)
        if needs_brew:
            out.append(_p_brew())
        if py:
            out.append(py)
        if app in ("a1111", "forge"):
            out.append(_p_brew_deps())
    elif IS_WIN:
        git = _p_git()
        py = _p_python(a["pyver"], "3.12" if app == "comfyui" else None)
        out += [git, py]
        if (not git["ok"] or not py["ok"]) and not shutil.which("winget"):
            out.append(_p_winget())
    else:
        out += [_p_git(), _p_python(a["pyver"])]
    return out


def _public_prereq(p):
    return {k: v for k, v in p.items() if not k.startswith("_")}


# ----------------------------------------------------------------------------
# Guías manuales (para el sistema actual)
# ----------------------------------------------------------------------------

def _q(p):
    return '"%s"' % p if IS_WIN else shlex.quote(str(p))


def _cd(p):
    return "cd /d %s" % _q(p) if IS_WIN else "cd %s" % _q(p)


def _join(*cmds):
    return " && ".join(cmds)


def _guide_prereqs(app):
    """Pasos de 'qué instalar primero' para el sistema actual."""
    a = APPS[app]
    pv = "/".join(a["pyver"][::-1] if app == "comfyui" else a["pyver"])
    steps = []
    if IS_MAC:
        steps.append({"title": "Instalá Homebrew",
                      "text": "Homebrew es el «instalador de programas» de la Mac. Pegá este comando en la "
                              "<b>Terminal</b> (Aplicaciones → Utilidades → Terminal); te va a pedir tu contraseña "
                              "y puede tardar unos minutos. Más info en <a href=\"https://brew.sh\">brew.sh</a>. "
                              "Si ya lo tenés, salteá este paso.", "cmd": BREW_INSTALL})
        if app == "kohya":
            steps.append({"title": "Instalá Git",
                          "text": "Con Homebrew. (Python lo instala el propio instalador de Kohya_ss.)",
                          "cmd": "brew install git"})
        elif app in ("a1111", "forge"):
            steps.append({"title": "Instalá lo necesario",
                          "text": "Python 3.10, Git y unas herramientas de compilación, todo con un solo comando "
                                  "(es la lista oficial de la <a href=\"https://github.com/AUTOMATIC1111/"
                                  "stable-diffusion-webui/wiki/Installation-on-Apple-Silicon\">guía de A1111 para "
                                  "Apple Silicon</a>).",
                          "cmd": "brew install cmake protobuf rust python@3.10 git wget"})
        else:
            steps.append({"title": "Instalá Python y Git", "text": "Python 3.12 (sirve de 3.10 a 3.12) y Git.",
                          "cmd": "brew install python@3.12 git"})
    elif IS_WIN:
        steps.append({"title": "Instalá Git",
                      "text": "Abrí el menú Inicio, escribí <b>cmd</b> y abrí «Símbolo del sistema». Pegá el comando "
                              "(o bajalo de <a href=\"https://git-scm.com/downloads\">git-scm.com</a>).",
                      "cmd": "winget install --id Git.Git -e"})
        pyid = "Python.Python.3.12" if app == "comfyui" else "Python.Python.3.11" if app == "kohya" \
            else "Python.Python.3.10"
        steps.append({"title": "Instalá Python " + pv,
                      "text": "Tiene que ser una de estas versiones: <b>%s</b>. Si lo bajás de "
                              "<a href=\"https://www.python.org/downloads/\">python.org</a>, tildá «Add python.exe "
                              "to PATH» en la primera pantalla." % pv, "cmd": "winget install --id %s -e" % pyid})
        steps.append({"title": "Cerrá y volvé a abrir la ventana negra",
                      "text": "Así los programas nuevos quedan disponibles.", "cmd": None})
    else:
        steps.append({"title": "Instalá Git y Python " + pv,
                      "text": "Con el instalador de paquetes de tu distribución (apt, dnf, pacman...).", "cmd": None})
    return steps


def _guide_clone(app, parent, recursive=False):
    a = APPS[app]
    cmd = "git clone %s%s" % ("--recursive " if recursive else "", a["repo"])
    return {"title": "Descargá el programa",
            "text": "Esto baja el código oficial de <a href=\"%s\">%s</a> a una carpeta nueva dentro de "
                    "<code>%s</code>." % (a["official_url"], a["official_url"].replace("https://", ""), parent),
            "cmd": _join(_cd(parent), cmd)}


def _guide_detect(app, folder_path):
    return {"title": "Que AI Launcher lo encuentre",
            "text": "Volvé a AI Launcher y tocá <b>Buscar de nuevo</b> (o «Agregar instalación» y elegí la carpeta "
                    "<code>%s</code>). Desde ahí ya lo podés iniciar con un clic." % folder_path, "cmd": None}


def guide_for(app, parent):
    a = APPS[app]
    dest = os.path.join(parent, a.get("folder") or "")
    if app == "ollama":
        if IS_MAC:
            return [
                {"title": "Bajá el instalador",
                 "text": "Entrá a <a href=\"https://ollama.com/download\">ollama.com/download</a> y elegí macOS "
                         "(archivo Ollama.dmg).", "cmd": None},
                {"title": "Instalalo",
                 "text": "Abrí el archivo descargado y arrastrá <b>Ollama</b> a la carpeta <b>Aplicaciones</b>.",
                 "cmd": None},
                {"title": "Abrilo una vez",
                 "text": "Abrí Ollama desde Aplicaciones: queda un ícono en la barra de menú de arriba.", "cmd": None},
                {"title": "Comprobá que anda", "text": "En la Terminal, este comando tiene que mostrar un número "
                 "de versión.", "cmd": "/Applications/Ollama.app/Contents/Resources/ollama --version"},
                _guide_detect(app, "Ollama.app")]
        if IS_WIN:
            return [
                {"title": "Bajá el instalador",
                 "text": "Entrá a <a href=\"https://ollama.com/download\">ollama.com/download</a> y bajá "
                         "<b>OllamaSetup.exe</b> para Windows.", "cmd": None},
                {"title": "Ejecutalo", "text": "Doble clic en el archivo y seguí los pasos. No pide ser administrador. "
                 "Al terminar queda un ícono en la bandeja (al lado del reloj).", "cmd": None},
                {"title": "Comprobá que anda", "text": "En una ventana negra (cmd), tiene que mostrar un número "
                 "de versión.", "cmd": "ollama --version"},
                _guide_detect(app, "Ollama")]
        return [{"title": "Instalá Ollama", "text": "Seguí las instrucciones de "
                 "<a href=\"https://ollama.com/download\">ollama.com/download</a> para Linux.",
                 "cmd": "curl -fsSL https://ollama.com/install.sh | sh"}, _guide_detect(app, "Ollama")]

    steps = _guide_prereqs(app)
    if app in ("a1111", "forge"):
        steps.append(_guide_clone(app, parent))
        if IS_MAC:
            steps.append({"title": "Decile dónde vive",
                          "text": "En Mac, el script de arranque supone que el programa está en tu carpeta personal. "
                                  "Este comando le avisa que está en la carpeta que elegiste.",
                          "cmd": _join(_cd(dest), "echo 'install_dir=\"%s\"' >> webui-user.sh" % parent,
                                       "echo 'clone_dir=\"%s\"' >> webui-user.sh" % a["folder"])})
            first = _join(_cd(dest), "python_cmd=python3.10 ./webui.sh --exit")
        else:
            first = _join(_cd(dest), "webui.bat --exit")
        steps.append({"title": "Primera ejecución (instala todo)",
                      "text": "Crea el entorno de Python y descarga PyTorch y el resto de las piezas, pero sin abrir "
                              "el programa (por eso <code>--exit</code>). Tarda bastante y baja varios GB: dejalo "
                              "terminar.", "cmd": first})
        steps.append({"title": "Poné un modelo",
                      "text": "Para generar imágenes hace falta al menos un modelo (checkpoint, archivo "
                              ".safetensors). Copialo en <code>%s</code>." %
                              os.path.join(dest, "models", "Stable-diffusion"), "cmd": None})
        steps.append({"title": "Abrilo",
                      "text": "Este comando inicia el programa; cuando diga «Running on local URL» abrí "
                              "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a> en el navegador.",
                      "cmd": _join(_cd(dest), "webui.bat" if IS_WIN else "./webui.sh")})
    elif app == "comfyui":
        steps.append(_guide_clone(app, parent))
        pyc = "py -3.12" if IS_WIN else "python3.12"
        vpy = r"venv\Scripts\python" if IS_WIN else "venv/bin/python"
        steps.append({"title": "Creá el entorno de Python",
                      "text": "Un «entorno virtual» es una carpeta donde queda todo lo del programa, sin mezclarse "
                              "con el resto de tu compu.", "cmd": _join(_cd(dest), "%s -m venv venv" % pyc)})
        steps.append({"title": "Instalá PyTorch",
                      "text": ("Si tenés una placa NVIDIA, este comando baja la versión con CUDA." if IS_WIN else
                               "PyTorch es el motor de IA; en Mac usa la GPU del chip Apple (MPS)."),
                      "cmd": _join(_cd(dest), "%s -m pip install --upgrade pip" % vpy,
                                   "%s -m pip install torch torchvision torchaudio%s" %
                                   (vpy, " --extra-index-url https://download.pytorch.org/whl/cu124" if IS_WIN else ""))})
        steps.append({"title": "Instalá el resto",
                      "text": "Las demás piezas de ComfyUI.", "cmd": _join(_cd(dest), "%s -m pip install -r requirements.txt" % vpy)})
        steps.append({"title": "Poné un modelo",
                      "text": "Copiá un checkpoint (.safetensors) en <code>%s</code>." %
                              os.path.join(dest, "models", "checkpoints"), "cmd": None})
        steps.append({"title": "Abrilo",
                      "text": "Cuando diga «To see the GUI go to» abrí <a href=\"http://127.0.0.1:8188\">"
                              "http://127.0.0.1:8188</a>.", "cmd": _join(_cd(dest), "%s main.py" % vpy)})
    elif app == "kohya":
        steps.append(_guide_clone(app, parent, recursive=True))
        if IS_MAC:
            steps.append({"title": "Ejecutá el instalador",
                          "text": "Instala Python (con Homebrew), crea el entorno y baja las dependencias. "
                                  "Usa el modo automático: no hace preguntas.",
                          "cmd": _join(_cd(dest), "./setup.sh -n -u -d %s" % _q(dest))})
            steps.append({"title": "Abrilo", "text": "Cuando diga «Running on local URL» abrí "
                          "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>.",
                          "cmd": _join(_cd(dest), "./gui.sh")})
        else:
            steps.append({"title": "Ejecutá el instalador",
                          "text": "Se abre un menú en la ventana negra: elegí <b>Install kohya_ss gui</b> y andá "
                                  "contestando (para una placa NVIDIA, elegí la opción de Torch 2 con CUDA). Si "
                                  "después pregunta por la configuración de «accelerate», elegí: This machine → "
                                  "No distributed training → CPU/GPU según tu placa → bf16 si tu placa lo permite.",
                          "cmd": _join(_cd(dest), "setup.bat")})
            steps.append({"title": "Abrilo", "text": "Cuando diga «Running on local URL» abrí "
                          "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>.",
                          "cmd": _join(_cd(dest), "gui.bat")})
    steps.append(_guide_detect(app, dest))
    return steps


# ----------------------------------------------------------------------------
# Catálogo para la API
# ----------------------------------------------------------------------------

def default_parent(launcher):
    """Carpeta donde se instalan los programas nuevos: la configurada, la que ya comparten tus instalaciones, o ~/AI."""
    cfg = _be().CONFIG.get("install_parent")
    if isinstance(cfg, str) and cfg.strip():
        return os.path.expanduser(cfg.strip())
    parents = [os.path.dirname(i["path"]) for i in launcher.installs]
    if parents:
        try:
            common = os.path.commonpath(parents)
        except ValueError:
            common = ""
        if common and os.path.dirname(common) != common:  # no la raíz del disco
            return common
    return str(HOME / "AI")


def _installed(launcher, app):
    if app == "ollama":
        paths = [p for p in (launcher.ollama.get("app"), launcher.ollama.get("binary")) if p]
        return bool(launcher.ollama.get("found")), paths
    paths = [i["path"] for i in launcher.installs if i["kind"] == APPS[app]["kind"]]
    return bool(paths), paths


def catalog(launcher):
    parent = default_parent(launcher)
    apps = []
    for aid, a in APPS.items():
        installed, paths = _installed(launcher, aid)
        auto, note = _auto_support(aid)
        apps.append({"id": aid, "name": a["name"], "purpose": a["purpose"], "installed": installed,
                     "installed_paths": paths, "official_url": a["official_url"], "auto_supported": auto,
                     "auto_note": note, "requirements": a["requirements"],
                     "prereqs": [_public_prereq(p) for p in prereqs_for(aid)],
                     "guide": guide_for(aid, parent)})
    return {"platform": PLATFORM, "default_parent": parent, "apps": apps, "jobs": jobs_dict()}


# ----------------------------------------------------------------------------
# Trabajos de instalación
# ----------------------------------------------------------------------------

JOBS = {}  # app id -> Job (sólo de esta sesión)
_jobs_lock = threading.Lock()


class Job:
    def __init__(self, app, titles):
        self.app = app
        self.state = "running"
        self.steps = [{"title": t, "state": "pending", "detail": "", "frac": 0.0} for t in titles]
        self.log = str(_be().LOG_DIR / ("install-%s.log" % app))
        self.started = time.time()
        self.finished = None
        self.error = None
        self.hint = None
        self.result_path = None
        self.proc = None
        self.cancelled = False
        self.ctx_cleanup = None  # limpieza extra (Ollama: desmontar y borrar temporales)
        self.cleanup = []  # carpetas creadas por este trabajo (se borran si falla o se cancela)
        self.thread = None

    def progress(self):
        if not self.steps:
            return 0.0
        total = 0.0
        for s in self.steps:
            total += 1.0 if s["state"] in ("done", "skipped") else s["frac"] if s["state"] == "running" else 0.0
        return round(min(total / len(self.steps), 1.0), 3)

    def to_dict(self):
        return {"app": self.app, "state": self.state,
                "steps": [{k: s[k] for k in ("title", "state", "detail")} for s in self.steps],
                "log": self.log, "started": self.started, "finished": self.finished, "error": self.error,
                "hint": self.hint, "result_path": self.result_path,
                "progress": 1.0 if self.state == "done" else self.progress()}


def jobs_dict():
    with _jobs_lock:
        return {k: j.to_dict() for k, j in JOBS.items()}


def _log(job, text):
    with open(job.log, "ab", buffering=0) as f:
        f.write((text.rstrip("\n") + "\n").encode("utf-8", "replace"))


def _run(job, cmd, cwd=None, env=None):
    """Corre un comando guardando su salida en el log. Se puede cancelar desde afuera."""
    if job.cancelled:
        raise Cancelled()
    shown = subprocess.list2cmdline(cmd) if IS_WIN else " ".join(shlex.quote(c) for c in cmd)
    _log(job, "$ " + shown + ("   (en %s)" % cwd if cwd else ""))
    logf = open(job.log, "ab", buffering=0)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **pu.detached_kwargs())
        job.proc = proc
        if job.cancelled:  # se canceló justo mientras arrancaba
            _be().kill_tree(proc.pid, force=True)
        code = proc.wait()
    except FileNotFoundError as e:
        raise InstallError("No se encontró el programa «%s». %s" % (cmd[0], e))
    finally:
        job.proc = None
        logf.close()
    if job.cancelled:
        raise Cancelled()
    if code != 0:
        raise InstallError("El comando terminó con error (código %s)." % code)


def _host_ok(url, allowed):
    """https y host exactamente igual (o subdominio) a alguno de los dominios oficiales permitidos."""
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == d or host.endswith("." + d) for d in allowed)


def _download(job, st, url, dest, allowed_hosts=("ollama.com",)):
    """Descarga con progreso (st['frac'] y st['detail']). Sólo https desde los dominios oficiales dados."""
    _log(job, "Descargando %s" % url)
    if not _host_ok(url, allowed_hosts):
        raise InstallError("Descarga rechazada: la dirección no es https de un dominio oficial (%s)."
                           % ", ".join(allowed_hosts))
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Launcher"})
    part = dest + ".part"
    with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
        if not _host_ok(r.geturl(), allowed_hosts):  # una redirección no puede sacarnos del dominio oficial
            raise InstallError("Descarga rechazada: redirección a un sitio no oficial (%s)." % r.geturl())
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0
        while True:
            if job.cancelled:
                raise Cancelled()
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 0.3:
                last = time.time()
                st["frac"] = done / total if total else 0.0
                st["detail"] = "%s de %s" % (_be().human_bytes(done), _be().human_bytes(total)) if total \
                    else _be().human_bytes(done)
    os.replace(part, dest)
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        for blk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blk)
    _log(job, "SHA-256 de la descarga: %s" % h.hexdigest())
    st["frac"] = 1.0
    st["detail"] = _be().human_bytes(os.path.getsize(dest))


SIG_ERROR = ("La descarga de Ollama no pasó la verificación de firma de %s; por seguridad no se instaló. "
             "Probá de nuevo o instalá Ollama desde ollama.com.")


def _verify_mac_signature(job, app_path):
    """Verifica la firma de Apple de una .app (codesign + spctl). Falla cerrado si algo no está disponible."""
    _log(job, "Verificando firma de %s" % app_path)
    for cmd in (["/usr/bin/codesign", "--verify", "--deep", "--strict", app_path],
                ["/usr/sbin/spctl", "--assess", "--type", "execute", "-v", app_path]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            _log(job, "No se pudo ejecutar %s: %s" % (cmd[0], e))
            return False
        _log(job, "$ %s -> %s %s" % (" ".join(cmd[:2]), r.returncode, (r.stdout + r.stderr).strip()))
        if r.returncode != 0:
            return False
    return True


def _verify_win_signature(job, exe_path):
    """Verifica Authenticode con PowerShell (Status = Valid). Falla cerrado si no se puede."""
    _log(job, "Verificando firma de %s" % exe_path)
    ps = "(Get-AuthenticodeSignature -LiteralPath $args[0]).Status"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "& { " + ps + " }", exe_path],
                           capture_output=True, text=True, timeout=120, **pu.detached_kwargs())
    except (OSError, subprocess.SubprocessError) as e:
        _log(job, "No se pudo ejecutar PowerShell: %s" % e)
        return False
    _log(job, "Estado de la firma: %s" % (r.stdout + r.stderr).strip())
    return r.returncode == 0 and r.stdout.strip() == "Valid"


def _rmtree(path):
    def onerror(func, p, _exc):  # Windows: archivos de .git de sólo lectura
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)


# ---- pasos reutilizables ----------------------------------------------------

def _step_prereqs(job, st, prereqs):
    """Instala con Homebrew / winget lo que falte (todo sin contraseña)."""
    brew_pkgs, winget_ids = [], []
    for p in prereqs:
        if p["ok"] or not p.get("_install"):
            continue
        kind, what = p["_install"]
        if kind == "brew":
            brew_pkgs += what if isinstance(what, list) else [what]
        else:
            winget_ids.append(what)
    if not brew_pkgs and not winget_ids:
        return "skipped"
    if brew_pkgs:
        env = dict(os.environ, HOMEBREW_NO_ENV_HINTS="1", HOMEBREW_NO_INSTALL_CLEANUP="1")
        st["detail"] = "brew install " + " ".join(brew_pkgs)
        _run(job, [shutil.which("brew") or "brew", "install"] + list(dict.fromkeys(brew_pkgs)), env=env)
    for wid in winget_ids:
        st["detail"] = "winget " + wid
        _run(job, ["winget", "install", "--id", wid, "-e", "--silent",
                   "--accept-package-agreements", "--accept-source-agreements"])
    pu.augment_path()  # que el PATH vea lo recién instalado


def _git_exe():
    ok, path = _git_status()
    if not ok:
        raise InstallError("No se encontró Git. Instalalo y volvé a intentar.")
    return path


def _step_clone(job, st, app, dest, recursive=False):
    cmd = [_git_exe(), "clone", "--progress"] + (["--recursive"] if recursive else []) + [APPS[app]["repo"], dest]
    job.cleanup.append(dest)
    _run(job, cmd)
    st["detail"] = dest


def _python_for(app):
    py = find_python(APPS[app]["pyver"])
    if not py:
        raise InstallError("No se encontró Python %s. Instalalo y volvé a intentar." % " o ".join(APPS[app]["pyver"]))
    return py


def _env_with_python(py):
    """PATH que prefiere el Python elegido (lo usan setup.sh de Kohya y webui.sh)."""
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(py["exe"]) + os.pathsep + env.get("PATH", "")
    return env


def _step_webui_firstrun(job, st, app, dest, parent):
    """A1111 / Forge: primera ejecución que instala todo, sin abrir la interfaz."""
    py = _python_for(app)
    env = _env_with_python(py)
    if IS_MAC:
        # el script de macOS supone que el programa está en ~; se le dice dónde vive de verdad
        with open(os.path.join(dest, "webui-user.sh"), "a") as f:
            f.write("\n# Agregado por AI Launcher: ubicación real de esta carpeta\n"
                    "install_dir=%s\nclone_dir=%s\n" % (shlex.quote(parent), shlex.quote(APPS[app]["folder"])))
        env["python_cmd"] = py["exe"]
        _run(job, pu.shell_script_command(os.path.join(dest, "webui.sh"), ["--exit"]), cwd=dest, env=env)
    else:
        env["PYTHON"] = py["exe"]
        _run(job, pu.shell_script_command(os.path.join(dest, "webui.bat"), ["--exit"]), cwd=dest, env=env)
    if not pu.venv_python(dest):
        raise InstallError("La primera ejecución terminó pero no se creó el entorno (venv). Mirá el log.")


def _step_comfy_venv(job, st, dest):
    py = _python_for("comfyui")
    st["detail"] = "Python " + py["version"]
    # instalación nueva: sin venv todavía, se usa la arquitectura nativa de la máquina
    _run(job, pu.arch_prefix(pu.native_arch()) + [py["exe"], "-m", "venv", os.path.join(dest, "venv")], cwd=dest)


def _step_comfy_pip(job, st, dest, args):
    vpy = pu.venv_python(dest)
    if not vpy:
        raise InstallError("No se encontró el entorno de Python (venv) de ComfyUI.")
    _run(job, pu.arch_prefix(pu.preferred_arch_for(dest)) + [vpy, "-m", "pip"] + args, cwd=dest)


def _step_kohya_setup(job, st, dest):
    if not IS_MAC:
        raise InstallError("La instalación automática de Kohya_ss no está disponible en este sistema.")
    # sin -i: usa la configuración de accelerate por defecto, sin preguntar; -u: no abre la interfaz
    _run(job, pu.shell_script_command(os.path.join(dest, "setup.sh"), ["-n", "-u", "-d", dest]), cwd=dest,
         env=dict(os.environ, PYTHONUNBUFFERED="1"))
    if not (Path(dest, "venv").is_dir()):
        raise InstallError("El instalador terminó pero no se creó el entorno (venv). Mirá el log.")


def _step_register(job, st, launcher, dest):
    """Lo agrega a la lista del launcher y lo detecta ya."""
    be = _be()
    if dest:
        real = os.path.realpath(dest)
        manual = be.CONFIG["sd"].setdefault("manual", [])
        if real not in manual:
            manual.append(real)
        be.save_config()
    launcher.detect()
    if dest is None:  # Ollama: Spotlight / el registro pueden tardar unos segundos en verlo
        for _ in range(6):
            if launcher.ollama.get("found"):
                break
            time.sleep(2)
            launcher.detect()
        if not launcher.ollama.get("found"):
            raise InstallError("Se instaló, pero no logré encontrarlo. Probá «Buscar de nuevo» en unos segundos.")
    else:
        if not any(same(i["path"], dest) for i in launcher.installs):
            raise InstallError("Se instaló, pero el launcher no lo reconoce como instalación válida.")
        job.result_path = os.path.realpath(dest)


def same(a, b):
    return _be().same_path(os.path.realpath(a), os.path.realpath(b))


# ----------------------------------------------------------------------------
# Hints de falla
# ----------------------------------------------------------------------------

_HINTS_INSTALL = [
    (r"Could not resolve host|Temporary failure in name resolution|getaddrinfo|Network is unreachable|"
     r"Connection (reset|refused|timed out)|Read timed out|URLError|RPC failed|early EOF|SSL|"
     r"Failed to establish a new connection|timed out",
     "Parece un problema de internet. Revisá tu conexión y volvé a intentar."),
    (r"No space left on device|ENOSPC|not enough space|espacio en disco|There is not enough space",
     "Te quedaste sin espacio en el disco. Liberá espacio (o elegí otra carpeta u otro disco) y volvé a intentar."),
    (r"git: command not found|No se encontró Git|is not recognized as an internal or external command.*git|"
     r"xcode-select: note|Command Line Tools",
     "Falta Git. Instalalo (en Mac: xcode-select --install; en Windows: winget install Git.Git) y volvé a intentar."),
    (r"brew: command not found|No se encontró el programa «brew»",
     "Falta Homebrew. Instalalo desde la Terminal (botón «Abrir Terminal») y volvé a intentar."),
    (r"Permission denied|PermissionError|Access is denied|Operation not permitted",
     "Faltan permisos para escribir en esa carpeta. Elegí una carpeta tuya (por ejemplo dentro de tu carpeta personal) "
     "y volvé a intentar."),
    (r"No se encontró Python|requires Python|Unsupported Python|Could not find a version that satisfies|"
     r"No matching distribution",
     "No se encontró la versión de Python que pide el programa. Instalala (la guía te dice cuál) y volvé a intentar."),
]


def hint_for(tail):
    for pat, text in _HINTS_INSTALL:
        if re.search(pat, tail or "", re.I):
            return text
    return _be().hint_for(tail)


# ----------------------------------------------------------------------------
# Plan de pasos de cada programa
# ----------------------------------------------------------------------------

def _plan(launcher, app, parent, prereqs, install_prereqs):
    """Lista de (título, función) para este programa."""
    a = APPS[app]
    dest = str(Path(parent) / a["folder"]) if a.get("folder") else None
    plan = []
    to_install = [p for p in prereqs if not p["ok"] and p.get("_install")]
    if install_prereqs and to_install:
        plan.append(("Instalar lo que falta (%s)" % ", ".join(p["name"] for p in to_install),
                     lambda j, st: _step_prereqs(j, st, to_install)))
    if app in ("a1111", "forge"):
        plan.append(("Descargar el programa (git clone)", lambda j, st: _step_clone(j, st, app, dest)))
        plan.append(("Primera ejecución: instalar todo (puede tardar mucho)",
                     lambda j, st: _step_webui_firstrun(j, st, app, dest, parent)))
    elif app == "comfyui":
        win_nvidia = IS_WIN and bool(shutil.which("nvidia-smi"))
        torch = ["install", "torch", "torchvision", "torchaudio"] + \
                (["--extra-index-url", "https://download.pytorch.org/whl/cu124"] if win_nvidia else [])
        plan.append(("Descargar el programa (git clone)", lambda j, st: _step_clone(j, st, app, dest)))
        plan.append(("Crear el entorno de Python", lambda j, st: _step_comfy_venv(j, st, dest)))
        plan.append(("Actualizar pip", lambda j, st: _step_comfy_pip(j, st, dest, ["install", "--upgrade", "pip"])))
        plan.append(("Instalar PyTorch (varios GB)", lambda j, st: _step_comfy_pip(j, st, dest, torch)))
        plan.append(("Instalar el resto de las piezas",
                     lambda j, st: _step_comfy_pip(j, st, dest, ["install", "-r", "requirements.txt"])))
    elif app == "kohya":
        plan.append(("Descargar el programa (git clone)", lambda j, st: _step_clone(j, st, app, dest, True)))
        plan.append(("Instalar con setup.sh (puede tardar mucho)", lambda j, st: _step_kohya_setup(j, st, dest)))
    plan.append(("Agregarlo al launcher", lambda j, st: _step_register(j, st, launcher, dest)))
    return plan, dest


def _run_job(job, plan):
    be = _be()
    current = None
    _log(job, "\n===== %s  Instalación de %s =====" % (time.strftime("%Y-%m-%d %H:%M:%S"), APPS[job.app]["name"]))
    try:
        for i, (_title, fn) in enumerate(plan):
            if job.cancelled:
                raise Cancelled()
            current = job.steps[i]
            current["state"] = "running"
            res = fn(job, current)
            current["state"] = "skipped" if res == "skipped" else "done"
            current["frac"] = 1.0
        job.state = "done"
        job.cleanup = []
    except Cancelled:
        if current and current["state"] == "running":
            current["state"], current["detail"] = "failed", "Cancelado"
        job.state = "cancelled"
    except Exception as e:  # noqa: BLE001 - cualquier falla se muestra al usuario
        if current:
            current["state"] = "failed"
            current["detail"] = str(e)[:300]
        tail = be._log_tail(job.log)
        job.error = "Falló el paso «%s»: %s" % (current["title"] if current else "?", e)
        job.hint = hint_for(tail + "\n" + str(e))
        job.state = "failed"
        _log(job, "ERROR: " + job.error)
    finally:
        for d in job.cleanup:  # no dejar carpetas a medias (así se puede reintentar)
            if os.path.isdir(d):
                _rmtree(d)
        job.cleanup = []
        if job.ctx_cleanup:
            try:
                job.ctx_cleanup()
            except Exception:  # noqa: BLE001
                pass
        job.finished = time.time()


# ----------------------------------------------------------------------------
# Entradas de la API
# ----------------------------------------------------------------------------

def _nearest_existing(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def start(launcher, b):
    app = b.get("app")
    if app not in APPS:
        raise InstallError("Programa desconocido: %s" % app)
    a = APPS[app]
    auto, note = _auto_support(app)
    if not auto:
        raise InstallError("Este programa no se puede instalar automáticamente acá. %s" % note)
    with _jobs_lock:
        for j in JOBS.values():
            if j.state == "running":
                raise InstallError("Ya hay una instalación en curso (%s). Esperá a que termine o cancelala."
                                   % APPS[j.app]["name"])

    explicit = b.get("parent")
    parent = os.path.expanduser(explicit or default_parent(launcher))
    dest = str(Path(parent) / a["folder"]) if a.get("folder") else None
    if dest and os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise InstallError("La carpeta de destino ya existe y no está vacía: %s. Elegí otra carpeta." % dest)
    base = _nearest_existing(parent if dest else (Path("/Applications") if IS_MAC else HOME))
    if dest and not os.access(base, os.W_OK):
        raise InstallError("No tengo permiso para escribir en %s. Elegí otra carpeta." % base)
    need = a["requirements"]["disk_gb"]
    free = shutil.disk_usage(base).free / 1024 ** 3
    if free < need:
        raise InstallError("No hay espacio suficiente: hacen falta unos %s GB libres y hay %.1f GB en ese disco."
                           % (need, free))

    prereqs = prereqs_for(app)
    for p in prereqs:
        if p["ok"]:
            continue
        if not p["auto"]:
            msg = "Falta %s. %s" % (p["name"], p["how"])
            if p.get("terminal_cmd"):
                msg += " Podés abrir la terminal con el botón correspondiente o correr: %s" % p["terminal_cmd"]
            raise InstallError(msg)
        if not b.get("install_prereqs"):
            raise InstallError("Falta %s. Tildá «instalar lo que falta» para que lo instale yo, o instalalo vos: %s"
                               % (p["name"], p.get("terminal_cmd") or p["how"]))

    if app == "ollama":
        job = Job(app, [])
        ctx = {"tmp": tempfile.mkdtemp(prefix="ailauncher-ollama-"), "mounted": False}
        plan = _ollama_plan(ctx, launcher)
    else:
        plan, dest = _plan(launcher, app, parent, prereqs, bool(b.get("install_prereqs")))
        job = Job(app, [])
        ctx = None
    job.steps = [{"title": t, "state": "pending", "detail": "", "frac": 0.0} for t, _ in plan]
    if ctx is not None:
        job.ctx_cleanup = lambda: _ollama_cleanup_ctx(ctx)
    if dest:
        Path(parent).mkdir(parents=True, exist_ok=True)
        if explicit:  # recordar la elección para la próxima instalación
            be = _be()
            be.CONFIG["install_parent"] = parent
            be.save_config()
    with _jobs_lock:
        JOBS[app] = job
    job.thread = threading.Thread(target=_run_job, args=(job, plan), daemon=True)
    job.thread.start()
    return {"ok": True, "job": app}


def _ollama_cleanup_ctx(ctx):
    if ctx.get("mounted") and ctx.get("mnt"):
        pu.run(["hdiutil", "detach", ctx["mnt"], "-force"], 60)
        ctx["mounted"] = False
    shutil.rmtree(ctx["tmp"], ignore_errors=True)


def _ollama_plan(ctx, launcher):
    steps = _ollama_steps_ctx(ctx)
    steps.append(("Agregarlo al launcher", lambda j, st: _step_register(j, st, launcher, None)))
    return steps


def _ollama_steps_ctx(ctx):
    tmp = ctx["tmp"]
    if IS_MAC:
        def download(j, st):
            _download(j, st, "https://ollama.com/download/Ollama.dmg", os.path.join(tmp, "Ollama.dmg"))

        def mount(j, st):
            ctx["mnt"] = os.path.join(tmp, "mnt")
            os.makedirs(ctx["mnt"], exist_ok=True)
            ctx["mounted"] = True
            _run(j, ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", ctx["mnt"],
                     os.path.join(tmp, "Ollama.dmg")])

        def verify(j, st):
            src = os.path.join(ctx["mnt"], "Ollama.app")
            if not os.path.isdir(src):
                raise InstallError("El instalador descargado no trae Ollama.app.")
            if not _verify_mac_signature(j, src):
                raise InstallError(SIG_ERROR % "Apple")
            st["detail"] = "Firma válida"

        def copy(j, st):
            src = os.path.join(ctx["mnt"], "Ollama.app")
            if not os.path.isdir(src):
                raise InstallError("El instalador descargado no trae Ollama.app.")
            apps = Path("/Applications")
            if not os.access(apps, os.W_OK):
                apps = HOME / "Applications"
                apps.mkdir(parents=True, exist_ok=True)
            dst = str(apps / "Ollama.app")
            if os.path.exists(dst):
                _rmtree(dst)
            _run(j, ["ditto", src, dst])  # ditto conserva la firma de la app
            j.result_path = dst
            st["detail"] = dst
            if shutil.which("mdimport"):
                pu.run(["mdimport", dst], 30)  # para que Spotlight la vea enseguida

        def detach(j, st):
            _ollama_cleanup_ctx(ctx)

        return [("Descargar Ollama", download), ("Montar el instalador", mount),
                ("Verificando firma…", verify),
                ("Copiar Ollama a Aplicaciones", copy), ("Cerrar el instalador", detach)]

    def download(j, st):
        _download(j, st, "https://ollama.com/download/OllamaSetup.exe", os.path.join(tmp, "OllamaSetup.exe"))

    def verify(j, st):
        if not _verify_win_signature(j, os.path.join(tmp, "OllamaSetup.exe")):
            raise InstallError(SIG_ERROR % "Windows (Authenticode)")
        st["detail"] = "Firma válida"

    def run_setup(j, st):
        _run(j, [os.path.join(tmp, "OllamaSetup.exe"), "/VERYSILENT", "/NORESTART"])
        local = os.environ.get("LOCALAPPDATA")
        j.result_path = str(Path(local) / "Programs" / "Ollama") if local else None

    return [("Descargar Ollama", download), ("Verificando firma…", verify), ("Instalar Ollama", run_setup)]


def status(app):
    job = JOBS.get(app)
    if not job:
        raise InstallError("No hay una instalación de %s en esta sesión." % app)
    return job.to_dict()


def cancel(app):
    job = JOBS.get(app)
    if not job or job.state != "running":
        raise InstallError("No hay una instalación en curso de ese programa.")
    job.cancelled = True
    proc = job.proc
    if proc is not None:
        _be().kill_tree(proc.pid, force=True)
    if job.thread:
        job.thread.join(timeout=15)
    if job.state == "running":  # el hilo no alcanzó a terminar: se marca igual
        job.state = "cancelled"
        job.finished = time.time()
    return {"ok": True, "job": job.to_dict()}


def open_terminal(launcher, b):
    app, pid = b.get("app"), b.get("prereq")
    if app not in APPS:
        raise InstallError("Programa desconocido: %s" % app)
    p = next((x for x in prereqs_for(app) if x["id"] == pid), None)
    if not p or not p.get("terminal_cmd"):
        raise InstallError("Ese requisito no tiene un comando para la terminal.")
    cmd = p["terminal_cmd"]
    if IS_WIN:
        args = cmd.split(" ")
    else:
        args = ["/bin/bash", "-c", cmd + "; echo; echo 'Listo. Ya podés cerrar esta ventana y volver a AI Launcher.'"]
    pu.open_terminal(args, "AI Launcher: " + p["name"])
    return {"ok": True}
