"""Backend de AI Launcher: detección, opciones, procesos y API HTTP local.

Funciona en macOS y Windows: lo específico de cada sistema está en platform_utils.
"""
import ast
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psutil

import installers
import platform_utils as pu
from platform_utils import HOME, IS_WIN, run

SUPPORT = pu.data_dir()
LOG_DIR = pu.log_dir()
CONFIG_FILE = SUPPORT / "config.json"
STATE_FILE = SUPPORT / "server.json"
HERE = pu.resource_dir()

pu.augment_path()

SUPPORT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------

def same_path(a, b):
    """Compara rutas sin importar mayúsculas (Windows) ni separadores."""
    return bool(a) and bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def path_in(path, text):
    """¿Aparece la carpeta `path` dentro de `text` (una línea de comando)?"""
    if not path or not text:
        return False
    p, t = os.path.normcase(os.path.normpath(path)), os.path.normcase(text)
    return p + os.sep in t or t.endswith(p) or (IS_WIN and p.replace("\\", "/") in t.replace("\\", "/"))


def http_json(url, data=None, method=None, timeout=3):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw.strip() else {}


def port_open(port, host="127.0.0.1", timeout=0.3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1024


# ----------------------------------------------------------------------------
# Configuración
# ----------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "ollama": {"mode": "cli", "env": {}, "presets": {}},
    "sd": {"selected": None, "manual": [], "args": {}, "extra": {}, "presets": {}},
    "stop_on_quit": False,
    "open_window_on_launch": True,
    "cache": {},  # ubicaciones detectadas: {"ollama": {...}, "sd": [...], "verified_at": ...}
}

_config_lock = threading.Lock()


def load_config():
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except Exception:
        data = {}
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


CONFIG = load_config()


def save_config():
    with _config_lock:
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(CONFIG, indent=2, ensure_ascii=False))
        tmp.replace(CONFIG_FILE)


# ----------------------------------------------------------------------------
# Detección de Ollama
# ----------------------------------------------------------------------------

def _valid_ollama_binary(p):
    return bool(p) and os.path.isfile(p) and os.access(p, os.X_OK)


def _running_ollama_binaries():
    """Si Ollama ya está corriendo, su propio ejecutable dice dónde está instalado."""
    out = []
    for pr in psutil.process_iter(["name", "exe"]):
        name = (pr.info.get("name") or "").lower()
        exe = pr.info.get("exe") or ""
        if name in ("ollama", "ollama.exe") and exe:
            out.append(exe)
    return out


def find_ollama(cached=None):
    """Busca el ejecutable y la app de Ollama sin asumir rutas fijas.

    Si `cached` trae rutas que siguen siendo válidas se usan directamente;
    si no, se busca: procesos en ejecución, PATH del usuario, Spotlight o
    registro de Windows, y la carpeta del instalador oficial.
    """
    if cached and _valid_ollama_binary(cached.get("binary")) and \
            (not cached.get("app") or os.path.exists(cached["app"])):
        return _ollama_info(cached["binary"], cached.get("app"), source="cache")
    binary = None
    for c in _running_ollama_binaries() + pu.ollama_binary_candidates():
        if _valid_ollama_binary(c):
            binary = os.path.realpath(c)
            break
    app = pu.ollama_app_for_binary(binary) if binary else next(iter(pu.ollama_app_candidates()), None)
    return _ollama_info(binary, app, source="detectado")


def _ollama_info(binary, app, source):
    version = None
    if binary:
        m = re.search(r"(\d+\.\d+\.\d+\S*)", run([binary, "--version"], 5))
        version = m.group(1) if m else None

    models_dir = CONFIG["ollama"]["env"].get("OLLAMA_MODELS") or os.environ.get("OLLAMA_MODELS") \
        or str(HOME / ".ollama" / "models")
    return {"binary": binary, "app": app, "version": version, "models_dir": models_dir,
            "found": bool(binary or app), "source": source,
            "arch": pu.preferred_arch_for_binary(binary),
            "arch_native": (pu.preferred_arch_for_binary(binary) == pu.native_arch()) if pu.IS_MAC and binary else None}


# Lista de respaldo por si `ollama serve --help` no se puede leer.
_OLLAMA_FALLBACK_ENV = [
    ("OLLAMA_HOST", "IP y puerto del servidor (default 127.0.0.1:11434)"),
    ("OLLAMA_MODELS", "Carpeta de modelos"),
    ("OLLAMA_KEEP_ALIVE", "Tiempo que los modelos quedan en memoria (default \"5m\")"),
    ("OLLAMA_CONTEXT_LENGTH", "Longitud de contexto por defecto"),
    ("OLLAMA_NUM_PARALLEL", "Máximo de pedidos en paralelo"),
    ("OLLAMA_MAX_LOADED_MODELS", "Máximo de modelos cargados"),
    ("OLLAMA_MAX_QUEUE", "Máximo de pedidos en cola"),
    ("OLLAMA_FLASH_ATTENTION", "Enabled flash attention"),
    ("OLLAMA_KV_CACHE_TYPE", "Cuantización del K/V cache (default: f16)"),
    ("OLLAMA_ORIGINS", "Orígenes permitidos, separados por coma"),
    ("OLLAMA_DEBUG", "Show additional debug information (e.g. OLLAMA_DEBUG=1)"),
    ("OLLAMA_NOPRUNE", "Do not prune model blobs on startup"),
    ("OLLAMA_LOAD_TIMEOUT", "Tiempo máximo de carga de un modelo (default \"5m\")"),
]

_OLLAMA_CHOICES = {
    "OLLAMA_KV_CACHE_TYPE": ["f16", "q8_0", "q4_0"],
    "LLAMA_ARG_FIT": ["on", "off"],
    "OLLAMA_DEBUG": ["1", "2"],
}
_OLLAMA_CATEGORY = [
    ("Red", ("HOST", "ORIGINS", "NO_CLOUD")),
    ("Memoria y rendimiento", ("KEEP_ALIVE", "CONTEXT", "PARALLEL", "LOADED", "QUEUE", "FLASH", "KV_CACHE",
                               "GPU", "SPREAD", "FIT", "LLM_LIBRARY", "LOAD_TIMEOUT", "TRANSFER")),
    ("Almacenamiento", ("MODELS", "NOPRUNE")),
]


def ollama_default_hostport(opts):
    """El host:puerto por defecto, leído de la ayuda de tu versión de Ollama."""
    for o in opts:
        if o["name"] == "OLLAMA_HOST":
            m = re.search(r"(\d+\.\d+\.\d+\.\d+:\d+)", o["help"] or "")
            if m:
                return m.group(1)
    return None


def ollama_options(binary):
    text = run([binary, "serve", "--help"], 5) if binary else ""
    found = []
    for line in text.splitlines():
        m = re.match(r"\s+([A-Z][A-Z0-9_]{2,})\s{2,}(.+)", line)
        if m:
            found.append((m.group(1), m.group(2).strip()))
    if not found:
        found = _OLLAMA_FALLBACK_ENV
    opts = []
    for name, desc in found:
        default = None
        m = re.search(r"\(default:?\s*\"?([^\")]+)\"?\)", desc)
        if m:
            default = m.group(1).strip()
        if name in _OLLAMA_CHOICES:
            kind = "choice"
        elif re.search(r"=1\)|^(enable|enabled|disable|do not|always|show)\b", desc, re.I):
            kind = "bool"
        elif name == "OLLAMA_MODELS":
            kind = "path"
        else:
            kind = "text"
        category = "Otros"
        for cat, keys in _OLLAMA_CATEGORY:
            if any(k in name for k in keys):
                category = cat
                break
        opts.append({"name": name, "help": desc, "kind": kind, "default": default,
                     "choices": _OLLAMA_CHOICES.get(name), "category": category})
    return opts


# ----------------------------------------------------------------------------
# Detección de Stable Diffusion (A1111, Forge, reForge, ComfyUI)
# ----------------------------------------------------------------------------

def classify_sd(path):
    p = Path(path)
    if (p / "launch.py").is_file() and (p / "modules" / "cmd_args.py").is_file():
        if (p / "modules_forge").is_dir():
            kind, label = "forge", "Stable Diffusion WebUI Forge"
        else:
            kind, label = "a1111", "Stable Diffusion WebUI (AUTOMATIC1111)"
        return {"path": str(p), "kind": kind, "label": label, "name": p.name,
                "args_file": str(p / "modules" / "cmd_args.py"), "default_port": 7860}
    if (p / "main.py").is_file() and (p / "comfy" / "cli_args.py").is_file():
        return {"path": str(p), "kind": "comfyui", "label": "ComfyUI", "name": p.name,
                "args_file": str(p / "comfy" / "cli_args.py"), "default_port": 8188}
    if (p / "kohya_gui.py").is_file() and (p / "kohya_gui").is_dir():
        return {"path": str(p), "kind": "kohya", "label": "Kohya_ss (entrenamiento)", "name": p.name,
                "args_file": str(p / "kohya_gui.py"), "default_port": 7860}
    return None


# cómo se indica el puerto en cada programa
PORT_FLAG = {"a1111": "--port", "forge": "--port", "comfyui": "--port", "kohya": "--server_port"}


def launch_script(info):
    """Script de arranque (webui.sh/.bat, gui.sh/.bat) o None si se lanza con python directo."""
    if info["kind"] == "comfyui":
        return None
    base = "gui" if info["kind"] == "kohya" else "webui"
    return Path(info["path"]) / (base + (".bat" if IS_WIN else ".sh"))


def _walk_for_sd(root, max_depth=5, include_protected=False):
    found = []
    root = Path(root)
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - base_depth
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$")) and d not in pu.SKIP_DIRS
                       and (include_protected or depth > 0 or d not in pu.PROTECTED_DIRS)]
        if depth >= max_depth:
            dirnames[:] = []
        if ("launch.py" in filenames or "main.py" in filenames or "kohya_gui.py" in filenames):
            info = classify_sd(dirpath)
            if info:
                found.append(dirpath)
                dirnames[:] = []
    return found


def verify_sd_cache(cached):
    """Devuelve las instalaciones en caché que siguen existiendo, o None si alguna desapareció."""
    if not cached:
        return None
    ok = [p for p in cached if classify_sd(p)]
    return ok if len(ok) == len(cached) else None


def _running_sd_dirs():
    """Carpetas de SD que ya están corriendo (por su directorio de trabajo)."""
    out = []
    for pr in psutil.process_iter(["name", "cmdline"]):
        cmd = " ".join(pr.info.get("cmdline") or [])
        if re.search(r"\b(launch|webui|kohya_gui)\.py\b|\b(webui|gui)\.(sh|bat)\b|\bmain\.py\b", cmd):
            try:
                out.append(pr.cwd())
            except (psutil.Error, OSError):
                pass
    return out


def find_sd_installs(deep=False, cached=None):
    if cached is not None:
        return _sd_infos(cached)
    # 1. agregadas a mano  2. las que están corriendo  3. índice de Spotlight (macOS)
    paths = list(CONFIG["sd"].get("manual", [])) + _running_sd_dirs()
    for f in pu.spotlight("cmd_args.py", HOME):
        if Path(f).parent.name == "modules":
            paths.append(str(Path(f).parent.parent))
    for f in pu.spotlight("cli_args.py", HOME):
        if Path(f).parent.name == "comfy":
            paths.append(str(Path(f).parent.parent))
    for f in pu.spotlight("kohya_gui.py", HOME):
        paths.append(str(Path(f).parent))
    # 4. si no apareció nada (o se pidió búsqueda profunda), recorrer el disco
    if deep or not any(classify_sd(p) for p in paths):
        for root in pu.search_roots():
            paths += _walk_for_sd(root, max_depth=5 if root == HOME else 3, include_protected=deep)
    return _sd_infos(paths)


def _sd_infos(paths):
    installs, seen = [], set()
    for p in paths:
        rp = os.path.normcase(os.path.realpath(p)) if IS_WIN else os.path.realpath(p)
        if rp in seen or re.search(r"[\\/](\.Trash|\$Recycle\.Bin|site-packages)[\\/]", rp):
            continue
        info = classify_sd(os.path.realpath(p))
        if info:
            seen.add(rp)
            info["has_venv"] = bool(pu.venv_python(info["path"]))
            info["arch"] = pu.preferred_arch_for(info["path"])
            info["arch_native"] = info["arch"] == pu.native_arch() if info["arch"] else None
            info["checkpoints"] = sd_checkpoints(info)
            installs.append(info)
    return installs


def sd_checkpoints(info):
    p = Path(info["path"])
    if info["kind"] == "kohya":
        return []
    dirs = [p / "models" / "Stable-diffusion"] if info["kind"] != "comfyui" else \
        [p / "models" / "checkpoints"]
    out = []
    for d in dirs:
        if d.is_dir():
            for f in sorted(d.rglob("*")):
                if f.suffix.lower() in (".safetensors", ".ckpt", ".gguf") and f.is_file():
                    out.append({"name": str(f.relative_to(d)), "path": str(f), "size": f.stat().st_size})
    return out


# ---- lectura de las opciones directamente del código de cada instalación ----

_SD_COMMON = {
    # A1111 / Forge
    "--api", "--listen", "--port", "--autolaunch", "--theme", "--medvram", "--medvram-sdxl", "--lowvram",
    "--no-half", "--no-half-vae", "--upcast-sampling", "--opt-sub-quad-attention", "--ckpt",
    "--skip-load-model-at-start", "--precision", "--share",
    # ComfyUI
    "--auto-launch", "--highvram", "--normalvram", "--cpu", "--force-fp16", "--preview-method",
    # Kohya_ss
    "--server_port", "--inbrowser", "--headless", "--username", "--password", "--language",
}

_SD_CATEGORIES = [
    ("Arranque e instalación", r"^--(skip|reinstall|update|install|dump|test-server|log-startup|loglevel)"),
    ("Red y API", r"listen|port|share|ngrok|\bapi\b|api-|cors|tls|server-name|gradio-auth|gradio-queue|"
                  r"nowebui|autolaunch|subpath|insecure|enable-cors|auto-launch|disable-auto-launch|multi-user|"
                  r"front-end|enable-manager|tls"),
    ("Memoria y rendimiento", r"vram|ram\b|lowram|half|precision|upcast|opt-|xformers|attention|sub-quad|"
                              r"use-cpu|device|ipex|cuda|mps|channelslast|nan|fp16|fp8|fp32|bf16|cpu|gpu|async|"
                              r"cache|pin|unet|vae-in|text-enc|deterministic|directml|oneapi|reserve|smart-memory|"
                              r"force-|fast|batch-cond|always-|no-hashing|disable-model-loading"),
    ("Arranque e instalación", r"launch\.py|skip|reinstall|update|install|version|loglevel|log-startup|"
                               r"dump|test-server|verbose|quick-test|extensions|disable-all|disable-extra|"
                               r"download|clip|prompt-history|administrator|-f\b"),
    ("Rutas", r"dir\b|-dir|path|file|ckpt|config|output|input|temp|user-directory|base-directory|database"),
    ("Interfaz", r"ui|theme|gradio|show-|hide-|freeze|textbox|preview|console|progressbar|localization|"
                 r"styles|negative|max-batch|filenames|unix|sanitiz"),
]


def _category(flag, help_):
    if flag in _SD_COMMON:
        return "Más usadas"
    hay = (flag + " " + (help_ or "")).lower()
    for cat, pattern in _SD_CATEGORIES:
        if re.search(pattern, flag.lower()):
            return cat
    for cat, pattern in _SD_CATEGORIES:
        if re.search(pattern, hay):
            return cat
    return "Otros"


def _lit(node, src):
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        seg = ast.get_source_segment(src, node)
        return seg


def parse_argparse_file(path):
    """Extrae todas las opciones de un archivo argparse sin ejecutarlo."""
    try:
        src = Path(path).read_text()
        tree = ast.parse(src)
    except Exception:
        return []
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "add_argument"]
    calls.sort(key=lambda n: n.lineno)
    opts, seen = [], set()
    for node in calls:
        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        long = next((f for f in flags if f.startswith("--")), None)
        if not long or long in seen:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        h = kw.get("help")
        if isinstance(h, ast.Attribute) and h.attr == "SUPPRESS":
            continue
        help_ = _lit(h, src)
        if not isinstance(help_, str):
            help_ = str(help_ or "")
        help_ = re.sub(r"^launch\.py argument:\s*", "", help_)
        action = _lit(kw.get("action"), src)
        choices = _lit(kw.get("choices"), src)
        nargs = _lit(kw.get("nargs"), src)
        try:  # sólo valores literales; una variable del código no le sirve al usuario
            default = ast.literal_eval(kw["default"]) if "default" in kw else None
        except Exception:
            default = None
        typ = ast.get_source_segment(src, kw["type"]) if "type" in kw else ""
        if not isinstance(choices, (list, tuple)):
            choices = None
        if action in ("store_true", "store_false", "store_const", "count"):
            kind = "bool"
        elif choices:
            kind = "choice"
        elif nargs in ("?",) and "const" in kw:
            kind = "flagvalue"
        elif typ in ("int", "float"):
            kind = "number"
        elif "path" in typ.lower() or re.search(r"(-dir|-path|-file|ckpt|config)$", long):
            kind = "path"
        else:
            kind = "text"
        if isinstance(default, (str, int, float)) and not isinstance(default, bool):
            default = str(default)
            if len(default) > 80 or "os.path" in default or "(" in default:
                default = None
        else:
            default = None
        seen.add(long)
        opts.append({"flag": long, "help": help_.strip(), "kind": kind, "default": default,
                     "choices": [str(c) for c in choices] if choices else None,
                     "multi": nargs in ("*", "+"), "category": _category(long, help_)})
    return opts


def sd_preset_args(info):
    """Argumentos que la instalación ya agrega sola (webui-macos-env.sh / webui-user.sh)."""
    out = {}
    for fname in ("webui-macos-env.sh", "webui-user.sh"):
        f = Path(info["path"]) / fname
        if f.is_file():
            for line in f.read_text(errors="ignore").splitlines():
                m = re.match(r"\s*export\s+COMMANDLINE_ARGS=[\"']?([^\"']*)", line)
                if m and m.group(1).strip():
                    out[fname] = m.group(1).strip()
    return out


def build_sd_args(info, values, extra):
    opts = {o["flag"]: o for o in parse_argparse_file(info["args_file"])}
    args = []
    for flag, val in (values or {}).items():
        o = opts.get(flag)
        if not o:
            continue
        if o["kind"] == "bool":
            if val is True:
                args.append(flag)
        elif o["kind"] == "flagvalue":
            if isinstance(val, dict) and val.get("on"):
                args.append(flag)
                if str(val.get("value", "")).strip():
                    args.append(str(val["value"]).strip())
        elif val not in (None, "", False):
            if o["multi"]:
                args += [flag] + shlex.split(str(val))
            else:
                args += [flag, str(val)]
    if extra and extra.strip():
        try:
            args += shlex.split(extra)
        except ValueError:
            args += extra.split()
    return args


def sd_python(info):
    p = pu.venv_python(info["path"])
    if p:
        return p
    # ComfyUI portable de Windows: python_embeded/ al lado de la carpeta ComfyUI
    emb = Path(info["path"]).parent / "python_embeded" / ("python" + pu.EXE)
    if emb.exists():
        return str(emb)
    return pu.system_python()


def sd_default_port(info, options):
    """Puerto por defecto según el propio código de la instalación."""
    flag = PORT_FLAG.get(info["kind"], "--port")
    for o in options:
        if o["flag"] == flag:
            if o.get("default") and str(o["default"]).isdigit() and int(o["default"]) > 0:
                return int(o["default"])
            m = re.search(r"defaults? to (\d{2,5})", o.get("help") or "")
            if m:
                return int(m.group(1))
    # Gradio (A1111/Forge/Kohya) usa 7860 si no se indica otro
    return 8188 if info["kind"] == "comfyui" else 7860


# ----------------------------------------------------------------------------
# Procesos (psutil: igual en macOS y Windows)
# ----------------------------------------------------------------------------

STARTED = {}  # pid -> {"service", "log", "label", "time"}
_STARTED_FILE = SUPPORT / "started.json"
try:
    STARTED = {int(k): v for k, v in json.loads(_STARTED_FILE.read_text()).items()}
except Exception:
    STARTED = {}


def _save_started():
    try:
        _STARTED_FILE.write_text(json.dumps(STARTED))
    except Exception:
        pass


FAILURES = {}  # clave (ruta de instalación o "ollama") -> info del último arranque fallido
KILLED = set()  # pids que cerramos a propósito (no cuentan como fallo)
FAIL_WINDOW = 180  # segundos: una salida antes de esto se considera fallo de arranque

_HINTS = [
    (r"ModuleNotFoundError|No module named",
     "La instalación está incompleta: faltan componentes de Python. Ejecutá el instalador del programa "
     "(por ejemplo setup.sh / setup.bat en Kohya, o webui.sh una vez desde la terminal en A1111) y volvé a intentar."),
    (r"address already in use|Cannot find empty port|Errno 98|Errno 48|WinError 10048|port.{0,40}(in use|already|busy)",
     "El puerto está ocupado por otro programa. Cambiá la opción de puerto (o usá el puerto sugerido) y volvé a intentar."),
    (r"out of memory|MPS backend out of memory|CUDA out of memory",
     "Se quedó sin memoria. Cerrá otras aplicaciones, o activá opciones que gasten menos "
     "memoria como --medvram-sdxl o --lowvram, y volvé a intentar."),
    (r"requires Python|Unsupported Python|Python \d+\.\d+.{0,40}(not supported|required)|incompatible Python",
     "La versión de Python no es la correcta para este programa. Instalá la versión que pide el programa y volvé a intentar."),
    (r"Permission denied|PermissionError|Access is denied",
     "Faltan permisos para ejecutar o escribir archivos. Revisá los permisos de la carpeta del programa y volvé a intentar."),
]


def hint_for(tail, kind=None):
    for pat, text in _HINTS:
        if re.search(pat, tail or "", re.I):
            return text
    return "Mirá las últimas líneas del log para ver qué pasó."


def _log_tail(path, lines=40):
    try:
        data = tail_file(path, None, 20_000)["text"]
    except Exception:
        return ""
    # sólo la última corrida (después del último encabezado "=====")
    idx = data.rfind("\n===== ")
    if idx >= 0:
        data = data[idx:]
    return "\n".join(data.replace("\r", "\n").splitlines()[-lines:])


def _watch(proc, key, service, label, path, log_path, port, started):
    code = proc.wait()
    if proc.pid in KILLED:
        KILLED.discard(proc.pid)
        return
    elapsed = time.time() - started
    reachable = bool(port) and port_open(port)
    if code == 0 and elapsed >= FAIL_WINDOW:
        return
    if elapsed <= FAIL_WINDOW or (code != 0 and not reachable):
        tail = _log_tail(log_path)
        FAILURES[key] = {"service": service, "label": label, "path": path, "time": time.time(), "code": code,
                         "log": str(log_path), "tail": tail, "hint": hint_for(tail, service)}


def spawn(service, label, cmd, cwd, env, log_name, port=None, key=None, path=None):
    log_path = LOG_DIR / log_name
    logf = open(log_path, "ab", buffering=0)
    logf.write(("\n===== %s  %s =====\n$ %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), label,
                                                   subprocess.list2cmdline(cmd) if IS_WIN else
                                                   " ".join(shlex.quote(c) for c in cmd))).encode())
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=logf, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **pu.detached_kwargs())
    logf.close()
    STARTED[proc.pid] = {"service": service, "log": str(log_path), "label": label, "time": time.time(),
                         "port": port}
    _save_started()
    # recoger el proceso cuando termine para que no quede zombie
    key = key or (path if service == "sd" else service)
    FAILURES.pop(key, None)
    threading.Thread(target=_watch, args=(proc, key, service, label, path, log_path, port, time.time()),
                     daemon=True).start()
    return proc.pid


_PROCS = {}  # pid -> psutil.Process (se reusan para que cpu_percent mida entre lecturas)


def ps_table():
    rows = {}
    alive = set()
    for pr in psutil.process_iter(["pid", "ppid", "name", "cmdline", "exe", "create_time", "memory_info"]):
        info = pr.info
        pid = info["pid"]
        alive.add(pid)
        cached = _PROCS.get(pid)
        if cached is None or cached.info.get("create_time") != info.get("create_time"):
            _PROCS[pid] = cached = pr
            try:
                pr.cpu_percent(None)
            except psutil.Error:
                pass
        cmdline = info.get("cmdline") or []
        rows[pid] = {"pid": pid, "ppid": info.get("ppid") or 0, "name": info.get("name") or "",
                     "exe": info.get("exe") or "", "argv": cmdline,
                     "cmd": subprocess.list2cmdline(cmdline) if IS_WIN else " ".join(cmdline),
                     "rss": info["memory_info"].rss if info.get("memory_info") else 0,
                     "create_time": info.get("create_time") or time.time(), "cpu": 0.0}
    for pid in list(_PROCS):
        if pid not in alive:
            _PROCS.pop(pid, None)
    return rows


def _cpu(pid):
    try:
        return _PROCS[pid].cpu_percent(None)
    except (KeyError, psutil.Error):
        return 0.0


def _listening_ports(pid):
    try:
        pr = psutil.Process(pid)
        conns = pr.net_connections(kind="inet") if hasattr(pr, "net_connections") else pr.connections(kind="inet")
        return {c.laddr.port for c in conns if c.status == psutil.CONN_LISTEN}
    except (psutil.Error, OSError):
        return set()


def process_cwd(pid):
    try:
        return psutil.Process(pid).cwd()
    except (psutil.Error, OSError):
        return ""


def children_map(table):
    kids = {}
    for p in table.values():
        kids.setdefault(p["ppid"], []).append(p["pid"])
    return kids


def descendants(pid, kids):
    out, stack = [], list(kids.get(pid, []))
    while stack:
        c = stack.pop()
        if c in out or c == pid:
            continue
        out.append(c)
        stack.extend(kids.get(c, []))
    return out


def _elapsed(create_time):
    s = int(time.time() - create_time)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return "%dd %02d:%02d" % (d, h, m)
    return "%02d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)


_OWN_PIDS = {os.getpid()}
_SD_SCRIPT = re.compile(r"\b(launch|webui|kohya_gui)\.py\b|\b(webui|gui)\.(sh|bat)\b")


def classify_proc(p, installs):
    if p["pid"] in _OWN_PIDS:
        return None
    cmd, argv = p["cmd"], p["argv"]
    low = cmd.lower()
    if "launcher.py" in low and ("ai-launcher" in low or "ai launcher" in low):
        return None
    exe_name = os.path.basename(argv[0] if argv else p["name"]).lower()
    name = (p["name"] or "").lower()
    if exe_name in ("ollama", "ollama.exe") or name in ("ollama", "ollama.exe"):
        sub = argv[1].lower() if len(argv) > 1 else ""
        if sub in ("serve", "start"):
            return ("ollama", "Servidor Ollama")
        if sub == "runner":
            return ("ollama", "Ollama · modelo en ejecución")
        if sub == "run":
            return ("ollama", "Ollama · chat (ollama run)")
        if not sub and p["exe"] and ".app" in p["exe"]:
            return ("ollama", "Ollama (app de la barra de menú)")
        return None
    if "ollama" in name and (name.endswith(".exe") or ".app/" in p["exe"]):
        # app de bandeja/barra de menú ("ollama app.exe", Ollama.app)
        return ("ollama", "Ollama (app de la barra de menú)" if pu.IS_MAC else "Ollama (app de la bandeja)")
    for inst in installs:
        if path_in(inst["path"], cmd):
            return ("sd", inst["label"])
    if _SD_SCRIPT.search(cmd) and ("python" in low or "bash" in low or "cmd" in low or " sh " in low):
        return ("sd", "Kohya_ss" if "kohya_gui" in low or re.search(r"\bgui\.(sh|bat)\b", low) else "Stable Diffusion")
    if "main.py" in cmd and "python" in low and "comfy" in low:
        return ("sd", "ComfyUI")
    return None


_TRAIN_RE = re.compile(r"\b(sdxl_|flux_|sd3_)?train(_network|_db|_textual_inversion|_controlnet)?\.py\b")


def _child_label(m, matched):
    if m["pid"] in matched:
        return matched[m["pid"]][1]
    t = _TRAIN_RE.search(m["cmd"])
    if t:
        return "Entrenamiento (%s)" % t.group(0)
    return os.path.basename(m["argv"][0] if m["argv"] else m["name"])


_SCAN_CACHE = {"t": 0, "data": None}
_scan_lock = threading.Lock()


def scan_processes(installs, max_age=1.5):
    with _scan_lock:
        if _SCAN_CACHE["data"] is not None and time.time() - _SCAN_CACHE["t"] < max_age:
            return _SCAN_CACHE["data"]
        table = ps_table()
        kids = children_map(table)
        matched = {}
        for p in table.values():
            cls = classify_proc(p, installs)
            if cls:
                matched[p["pid"]] = cls
        # procesos con rutas relativas (p.ej. "python launch.py") → ubicar la instalación por su cwd
        for pid, (svc, label) in list(matched.items()):
            if svc == "sd" and label in ("Stable Diffusion", "ComfyUI", "Kohya_ss"):
                cwd = process_cwd(pid)
                for inst in installs:
                    if same_path(cwd, inst["path"]):
                        matched[pid] = ("sd", inst["label"])
        roots = []
        for pid, (svc, label) in matched.items():
            # es raíz si ningún ancestro está también detectado
            anc, is_root, guard = table[pid]["ppid"], True, 0
            while anc in table and anc > 1 and guard < 64:
                if anc in matched:
                    is_root = False
                    break
                anc, guard = table[anc]["ppid"], guard + 1
            if not is_root:
                continue
            members = [table[pid]] + [table[d] for d in descendants(pid, kids) if d in table]
            for m in members:
                m["cpu"] = _cpu(m["pid"])
                m["ports"] = sorted(_listening_ports(m["pid"]))
            port_set = sorted({x for m in members for x in m["ports"]})
            inst_path = None
            if svc == "sd":
                for inst in installs:
                    if inst["label"] == label and len([i for i in installs if i["label"] == label]) == 1 \
                            or any(path_in(inst["path"], m["cmd"]) or same_path(process_cwd(m["pid"]), inst["path"])
                                   for m in members[:3]):
                        inst_path = inst["path"]
                        break
            roots.append({
                "pid": pid, "service": svc, "label": label,
                "cmd": table[pid]["cmd"], "etime": _elapsed(table[pid]["create_time"]),
                "cpu": round(sum(m["cpu"] for m in members), 1),
                "rss": sum(m["rss"] for m in members),
                "ports": port_set,
                "install": inst_path,
                "started_here": pid in STARTED,
                "launch_port": STARTED.get(pid, {}).get("port"),
                "log": STARTED.get(pid, {}).get("log"),
                "training": any(_TRAIN_RE.search(m["cmd"]) for m in members[1:]),
                "children": [{"pid": m["pid"], "label": _child_label(m, matched),
                              "cmd": m["cmd"], "cpu": round(m["cpu"], 1), "rss": m["rss"],
                              "ports": m["ports"]} for m in members[1:]],
            })
        roots.sort(key=lambda r: (r["service"], -r["rss"]))
        # olvidar los PIDs propios que ya no existen
        gone = [pid for pid in STARTED if pid not in table]
        for pid in gone:
            STARTED.pop(pid, None)
        if gone:
            _save_started()
        _SCAN_CACHE.update(t=time.time(), data=roots)
        return roots


def kill_tree(pid, force=False, grace=8):
    """Cierra un proceso y todos sus hijos. La raíz va primero para que no re-lance a sus hijos."""
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except psutil.Error:
        return
    KILLED.update(pr.pid for pr in procs)
    for pr in procs:
        try:
            pr.kill() if force else pr.terminate()
        except psutil.Error:
            pass
    if not force:
        def reap():
            _, alive = psutil.wait_procs(procs, timeout=grace)
            for pr in alive:
                try:
                    pr.kill()
                except psutil.Error:
                    pass
        threading.Thread(target=reap, daemon=True).start()
    _SCAN_CACHE["t"] = 0


def free_port(start, avoid=()):
    """Primer puerto libre desde `start` (comprobando que nadie escuche y que se pueda abrir)."""
    for port in range(start, start + 200):
        if port in avoid or port_open(port):
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
                sk.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("No encontré un puerto libre cerca de %d" % start)


def memory_info():
    vm = psutil.virtual_memory()
    return {"total": vm.total, "used": vm.total - vm.available}


class Launcher:
    def __init__(self):
        self.ollama = {}
        self.ollama_opts = []
        self.installs = []
        self.sd_opts = {}
        self.pulls = {}
        self.detect_lock = threading.Lock()
        self.detecting = False
        self._sd_api_cache = {}
        self.detect_report = {}
        self.detect(use_cache=True)
        if self.detect_report.get("sd") == "cache":
            self.background_refresh()

    # ---------- detección ----------
    def detect(self, deep=False, use_cache=False):
        """Ubica Ollama y Stable Diffusion.

        use_cache=True: verifica las rutas guardadas y sólo busca de nuevo lo que
        ya no está. Las ubicaciones encontradas se guardan en CONFIG["cache"].
        """
        with self.detect_lock:
            self.detecting = True
            try:
                cache = CONFIG.setdefault("cache", {})
                report = {}
                self.ollama = find_ollama(cache.get("ollama") if use_cache and not deep else None)
                if use_cache and self.ollama["source"] != "cache" and cache.get("ollama"):
                    self.ollama["source"] = "redetectado (la ruta guardada ya no existía)"
                report["ollama"] = self.ollama["source"]

                sd_cached = verify_sd_cache(cache.get("sd")) if use_cache and not deep else None
                self.installs = find_sd_installs(deep, cached=sd_cached)
                if sd_cached is not None:
                    report["sd"] = "cache"
                elif use_cache and cache.get("sd"):
                    report["sd"] = "redetectado (alguna ruta guardada ya no existía)"
                else:
                    report["sd"] = "detectado"
                for i in self.installs:
                    i["source"] = report["sd"]

                cache["ollama"] = {"binary": self.ollama["binary"], "app": self.ollama["app"]}
                cache["sd"] = [i["path"] for i in self.installs]
                cache["verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                report["time"] = cache["verified_at"]
                self.detect_report = report

                self.ollama_opts = ollama_options(self.ollama["binary"])
                self.ollama_default = ollama_default_hostport(self.ollama_opts) or "127.0.0.1:11434"
                self.sd_opts = {}
                for i in self.installs:
                    self.sd_opts[i["path"]] = parse_argparse_file(i["args_file"])
                    i["default_port"] = sd_default_port(i, self.sd_opts[i["path"]])
                paths = [i["path"] for i in self.installs]
                if CONFIG["sd"].get("selected") not in paths:
                    CONFIG["sd"]["selected"] = paths[0] if paths else None
                save_config()
            finally:
                self.detecting = False

    def background_refresh(self):
        """Tras arrancar desde la caché, busca instalaciones nuevas sin bloquear la UI."""
        def work():
            known = {i["path"] for i in self.installs}
            fresh = find_sd_installs()
            new = [i for i in fresh if i["path"] not in known]
            if new:
                with self.detect_lock:
                    for i in new:
                        i["source"] = "nueva (encontrada en segundo plano)"
                        self.installs.append(i)
                        self.sd_opts[i["path"]] = parse_argparse_file(i["args_file"])
                        i["default_port"] = sd_default_port(i, self.sd_opts[i["path"]])
                    CONFIG.setdefault("cache", {})["sd"] = [i["path"] for i in self.installs]
                    save_config()
        threading.Thread(target=work, daemon=True).start()

    def install(self, path=None):
        path = path or CONFIG["sd"].get("selected")
        return next((i for i in self.installs if i["path"] == path), None)

    def state(self):
        out = []
        for i in self.installs:
            d = dict(i)
            d["options"] = self.sd_opts.get(i["path"], [])
            d["preset_args"] = sd_preset_args(i)
            d["values"] = CONFIG["sd"]["args"].get(i["path"], {})
            d["extra"] = CONFIG["sd"]["extra"].get(i["path"], "")
            d["preview"] = self.sd_command_preview(i)
            out.append(d)
        return {"config": CONFIG, "ollama": self.ollama, "ollama_options": self.ollama_opts,
                "installs": out, "log_dir": str(LOG_DIR), "config_file": str(CONFIG_FILE),
                "detect_report": self.detect_report, "login_item": pu.login_item_enabled(),
                "platform": "mac" if pu.IS_MAC else "windows" if IS_WIN else "linux"}

    # ---------- Ollama ----------
    def ollama_port(self, procs=None):
        procs = procs if procs is not None else scan_processes(self.installs)
        for r in procs:
            if r["service"] == "ollama" and r["ports"]:
                for c in r["children"]:
                    if "serve" in c["cmd"] and c["ports"]:
                        return c["ports"][0]
                if "serve" in r["cmd"] or "start" in r["cmd"]:
                    return r["ports"][0]
        h = (CONFIG["ollama"]["env"].get("OLLAMA_HOST") or os.environ.get("OLLAMA_HOST") or "").strip()
        m = re.search(r":(\d+)\s*/?$", h) or re.search(r":(\d+)$", self.ollama_default)
        return int(m.group(1))

    def ollama_url(self, procs=None):
        return "http://127.0.0.1:%d" % self.ollama_port(procs)

    def ollama_env(self):
        env = dict(os.environ)
        for k, v in CONFIG["ollama"]["env"].items():
            if v not in (None, "", False):
                env[k] = "1" if v is True else str(v)
        return env

    def start_ollama(self):
        mode = CONFIG["ollama"].get("mode", "cli")
        port = self.ollama_port()
        if port_open(port):
            raise RuntimeError("Ya hay un Ollama escuchando en el puerto %d. Detenelo primero desde el Monitor." % port)
        if mode == "app":
            if not self.ollama["app"]:
                raise RuntimeError("No encontré la app de Ollama")
            env = {k: ("1" if v is True else str(v)) for k, v in CONFIG["ollama"]["env"].items()
                   if v not in (None, "", False)}
            pu.launch_ollama_app(self.ollama["app"], env, [o["name"] for o in self.ollama_opts])
            return {"ok": True, "message": "Abriendo la app de Ollama"}
        if not self.ollama["binary"]:
            raise RuntimeError("No encontré el ejecutable de Ollama")
        pid = spawn("ollama", "Servidor Ollama", pu.arch_prefix(pu.preferred_arch_for_binary(self.ollama["binary"])) + [self.ollama["binary"], "serve"], str(HOME),
                    self.ollama_env(), "ollama.log", key="ollama")
        return {"ok": True, "pid": pid}

    def stop_service(self, service, install=None):
        procs = scan_processes(self.installs, max_age=0)
        targets = [r for r in procs if r["service"] == service and (install is None or r["install"] in (None, install))]
        # primero la app de Ollama, que re-lanza el servidor si queda viva
        targets.sort(key=lambda r: 0 if "app" in r["label"] else 1)
        for r in targets:
            kill_tree(r["pid"])
            if "app" in r["label"]:
                time.sleep(0.5)
        return {"ok": True, "stopped": [r["pid"] for r in targets]}

    def ollama_models(self, procs=None):
        base = self.ollama_url(procs)
        try:
            tags = http_json(base + "/api/tags", timeout=1.5).get("models", [])
            ps = http_json(base + "/api/ps", timeout=1.5).get("models", [])
            for m in tags:
                m["info"] = self.ollama_model_info(base, m)
            return {"up": True, "installed": tags, "loaded": ps}
        except Exception:
            return {"up": False, "installed": [], "loaded": []}

    _model_info_cache = {}

    def ollama_model_info(self, base, m):
        """Capacidades y datos de un modelo (visión, herramientas, razonamiento…), con caché por digest."""
        key = (m.get("name"), m.get("digest"))
        if key in self._model_info_cache:
            return self._model_info_cache[key]
        info = {}
        try:
            show = http_json(base + "/api/show", {"model": m["name"]}, timeout=3)
            info["capabilities"] = show.get("capabilities") or []
            det = show.get("details") or {}
            info["family"] = det.get("family") or ""
            info["parameter_size"] = det.get("parameter_size") or ""
            info["quantization"] = det.get("quantization_level") or ""
            mi = show.get("model_info") or {}
            ctx = next((v for k, v in mi.items() if k.endswith(".context_length")), None)
            info["context_length"] = ctx
        except Exception:
            return {}
        self._model_info_cache[key] = info
        return info

    def ollama_unload(self, model):
        http_json(self.ollama_url() + "/api/generate", {"model": model, "keep_alive": 0, "stream": False}, timeout=30)
        return {"ok": True}

    def ollama_load(self, model, keep_alive=None):
        body = {"model": model, "stream": False}
        if keep_alive:
            body["keep_alive"] = keep_alive
        threading.Thread(target=lambda: _quiet(http_json, self.ollama_url() + "/api/generate", body, timeout=600),
                         daemon=True).start()
        return {"ok": True}

    def ollama_delete(self, model):
        http_json(self.ollama_url() + "/api/delete", {"model": model}, method="DELETE", timeout=30)
        return {"ok": True}

    def ollama_pull(self, model):
        model = model.strip()
        if not model:
            raise RuntimeError("Escribí el nombre del modelo")
        self.pulls[model] = {"status": "iniciando…", "completed": 0, "total": 0, "done": False}

        def worker():
            st = self.pulls[model]
            try:
                req = urllib.request.Request(self.ollama_url() + "/api/pull",
                                             data=json.dumps({"model": model, "stream": True}).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    for line in r:
                        if not line.strip():
                            continue
                        ev = json.loads(line)
                        if ev.get("error"):
                            st["error"] = ev["error"]
                            break
                        st["status"] = ev.get("status", st["status"])
                        if ev.get("total"):
                            st["total"], st["completed"] = ev["total"], ev.get("completed", 0)
            except Exception as e:
                st["error"] = str(e)
            st["done"] = True
        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def ollama_chat_terminal(self, model):
        pu.open_terminal([self.ollama["binary"] or "ollama", "run", model], title="Ollama " + model)
        return {"ok": True}

    # ---------- Stable Diffusion ----------
    def sd_command(self, info, port=None):
        values = dict(CONFIG["sd"]["args"].get(info["path"], {}))
        if port:  # puerto alternativo sólo para este arranque (no se guarda)
            values[PORT_FLAG.get(info["kind"], "--port")] = str(port)
        extra = CONFIG["sd"]["extra"].get(info["path"], "")
        args = build_sd_args(info, values, extra)
        script = launch_script(info)
        if script is None:
            # ComfyUI corre directo con su Python: si es universal, evita heredar Rosetta
            return pu.arch_prefix(pu.preferred_arch_for(info["path"])) + [sd_python(info), "main.py"] + args
        return pu.shell_script_command(script, args)

    def sd_command_preview(self, info):
        """El comando tal como lo escribirías en una terminal, dentro de la carpeta."""
        cmd = self.sd_command(info)
        script = launch_script(info)
        if script is None:
            cmd = ["python"] + cmd[len(pu.arch_prefix(pu.preferred_arch_for(info["path"]))) + 1:]
        else:
            idx = next(i for i, c in enumerate(cmd) if c.endswith(script.name))
            cmd = [script.name if IS_WIN else "./" + script.name] + cmd[idx + 1:]
        return subprocess.list2cmdline(cmd) if IS_WIN else " ".join(shlex.quote(c) for c in cmd)

    def configured_port(self, info):
        v = CONFIG["sd"]["args"].get(info["path"], {}).get(PORT_FLAG.get(info["kind"], "--port"))
        return int(v) if str(v or "").isdigit() and int(v) > 0 else info["default_port"]

    def sd_port(self, info, procs=None):
        procs = procs if procs is not None else scan_processes(self.installs)
        for r in procs:
            if r["service"] == "sd" and r["install"] == info["path"] and r["ports"]:
                # el puerto con el que lo lanzamos; si no, el configurado; si no, el más bajo
                for want in (r.get("launch_port"), self.configured_port(info)):
                    if want in r["ports"]:
                        return want
                return r["ports"][0]
        return self.configured_port(info)

    def image_conflicts(self, info, procs=None):
        """Otras apps de imagen (SD/ComfyUI/Kohya) que ya están corriendo."""
        procs = procs if procs is not None else scan_processes(self.installs)
        out = []
        for r in procs:
            if r["service"] == "sd" and r["install"] != info["path"]:
                other = self.install(r["install"]) if r["install"] else None
                expected = r.get("launch_port") or (self.configured_port(other) if other else None)
                out.append({"pid": r["pid"], "label": r["label"], "rss": r["rss"], "ports": r["ports"],
                            "install": r["install"], "training": r.get("training", False),
                            "kind": other["kind"] if other else None, "expected_port": expected})
        return out

    def start_plan(self, path=None):
        """Qué pasaría al iniciar: puerto, conflictos y puerto alternativo sugerido."""
        info = self.install(path)
        procs = scan_processes(self.installs, max_age=0)
        port = self.configured_port(info)
        conflicts = self.image_conflicts(info, procs)
        busy = {p for c in conflicts for p in c["ports"]} | {c["expected_port"] for c in conflicts if c.get("expected_port")}
        port_taken = port_open(port) or port in busy
        suggested = free_port(port + 1, avoid=busy) if port_taken else port
        return {"info": info, "port": port, "port_taken": port_taken, "conflicts": conflicts,
                "suggested_port": suggested if port_taken else port}

    def start_sd(self, path=None, port=None, force=False):
        """Inicia una app de imagen.

        Si ya hay otra corriendo (o el puerto está ocupado) y no se pasa force=True,
        no inicia nada: devuelve {"conflict": ...} con un puerto libre sugerido.
        """
        info = self.install(path)
        if not info:
            raise RuntimeError("No hay ninguna instalación seleccionada")
        script = launch_script(info)
        if script is not None and not script.exists():
            raise RuntimeError("No encontré %s en %s" % (script.name, info["path"]))
        procs = scan_processes(self.installs, max_age=0)
        if any(r["service"] == "sd" and r["install"] == info["path"] for r in procs):
            raise RuntimeError("%s ya está corriendo" % info["label"])
        plan = self.start_plan(path)
        if (plan["conflicts"] or plan["port_taken"]) and not force:
            return {"ok": False, "conflict": True, "label": info["label"], "path": info["path"],
                    "port": plan["port"], "port_taken": plan["port_taken"],
                    "suggested_port": plan["suggested_port"], "running": plan["conflicts"],
                    "memory": memory_info()}
        use_port = port or (plan["suggested_port"] if plan["port_taken"] else None)
        if use_port and port_open(use_port):
            raise RuntimeError("El puerto %d también está ocupado" % use_port)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", info["name"]).strip("-").lower()
        final_port = use_port or plan["port"]
        pid = spawn("sd", info["label"], self.sd_command(info, port=use_port), info["path"], env,
                    "sd-%s.log" % slug, port=final_port, key=info["path"], path=info["path"])
        return {"ok": True, "pid": pid, "port": final_port, "url": "http://127.0.0.1:%d" % final_port}

    def sd_api(self, info, procs=None):
        """Estado del checkpoint vía API (sólo A1111/Forge con --api)."""
        if info["kind"] not in ("a1111", "forge"):
            return None
        port = self.sd_port(info, procs)
        if not port_open(port):
            return None
        try:
            opts = http_json("http://127.0.0.1:%d/sdapi/v1/options" % port, timeout=1.5)
            return {"api": True, "checkpoint": opts.get("sd_model_checkpoint")}
        except urllib.error.HTTPError as e:
            return {"api": e.code != 404, "checkpoint": None}
        except Exception:
            return {"api": False, "checkpoint": None}

    def sd_call(self, path, action, payload=None):
        info = self.install(path)
        port = self.sd_port(info)
        base = "http://127.0.0.1:%d/sdapi/v1/" % port
        if action == "unload":
            http_json(base + "unload-checkpoint", {}, timeout=60)
        elif action == "reload":
            http_json(base + "reload-checkpoint", {}, timeout=300)
        elif action == "set_model":
            http_json(base + "options", {"sd_model_checkpoint": payload}, timeout=600)
        return {"ok": True}

    # ---------- monitor ----------
    def monitor(self):
        procs = scan_processes(self.installs)
        models = self.ollama_models(procs)
        sd = []
        for info in self.installs:
            running = [r for r in procs if r["service"] == "sd" and r["install"] == info["path"]]
            port = self.sd_port(info, procs)
            up = bool(running) and port_open(port)
            api = self.sd_api(info, procs) if up else None
            sd.append({"path": info["path"], "label": info["label"], "name": info["name"], "kind": info["kind"],
                       "training": any(r.get("training") for r in running),
                       "running": bool(running), "up": up, "port": port,
                       "url": "http://127.0.0.1:%d" % port, "api": api,
                       "failure": FAILURES.get(info["path"])})
        ollama_running = any(r["service"] == "ollama" for r in procs)
        return {"processes": procs, "memory": memory_info(),
                "ollama": {"running": ollama_running, "up": models["up"], "url": self.ollama_url(procs),
                           "installed": models["installed"], "loaded": models["loaded"],
                           "failure": FAILURES.get("ollama")},
                "sd": sd, "failures": FAILURES, "pulls": self.pulls, "detecting": self.detecting, "time": time.time(),
                "install_jobs": installers.jobs_dict()}

    def stop_started(self):
        for pid in list(STARTED):
            kill_tree(pid)


def _quiet(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:
        return None


def tail_file(path, offset=None, max_bytes=200_000):
    p = Path(path)
    if not p.is_file():
        return {"text": "", "offset": 0}
    size = p.stat().st_size
    if offset is None or offset > size:
        offset = max(0, size - max_bytes)
    with p.open("rb") as f:
        f.seek(offset)
        data = f.read(max_bytes)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", data.decode("utf-8", "replace"))
    return {"text": text, "offset": offset + len(data)}


def log_sources(launcher):
    srcs = []
    for f in sorted(LOG_DIR.glob("*.log")):
        srcs.append({"id": str(f), "label": "Launcher · " + f.stem})
    for d in pu.ollama_log_dirs():
        for f in sorted(Path(d).glob("*.log")) if Path(d).is_dir() else []:
            srcs.append({"id": str(f), "label": "App de Ollama · " + f.name})
    return srcs


# ----------------------------------------------------------------------------
# Servidor HTTP local
# ----------------------------------------------------------------------------

TOKEN = secrets.token_urlsafe(24)


def make_handler(launcher, on_show_window=None):
    def allowed_logs(p):
        rp = os.path.normcase(str(Path(p).resolve()))
        return any(rp.startswith(os.path.normcase(str(Path(d).resolve()))) for d in [LOG_DIR] + pu.ollama_log_dirs())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            q = parse_qs(urlparse(self.path).query)
            return self.headers.get("X-Token") == TOKEN or q.get("t", [None])[0] == TOKEN

        def do_GET(self):
            url = urlparse(self.path)
            if not self._authorized():
                return self._send(403, {"error": "forbidden"})
            if url.path == "/":
                html = (HERE / "ui.html").read_text().replace("__TOKEN__", TOKEN)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if url.path == "/help.js":
                js = (HERE / "help.js").read_bytes()
                return self._send(200, js, "application/javascript; charset=utf-8")
            try:
                if url.path == "/api/state":
                    return self._send(200, launcher.state())
                if url.path == "/api/monitor":
                    return self._send(200, launcher.monitor())
                if url.path == "/api/installers":
                    return self._send(200, installers.catalog(launcher))
                if url.path == "/api/install/status":
                    try:
                        return self._send(200, installers.status(parse_qs(url.query).get("app", [""])[0]))
                    except installers.InstallError as e:
                        return self._send(404, {"error": str(e)})
                if url.path == "/api/logs":
                    return self._send(200, {"sources": log_sources(launcher)})
                if url.path == "/api/log":
                    q = parse_qs(url.query)
                    path = q.get("path", [""])[0]
                    if not allowed_logs(path):
                        return self._send(403, {"error": "ruta no permitida"})
                    off = q.get("offset", [None])[0]
                    return self._send(200, tail_file(path, int(off) if off else None))
            except Exception as e:
                return self._send(500, {"error": str(e)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.headers.get("X-Token") != TOKEN:
                return self._send(403, {"error": "forbidden"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            try:
                return self._send(200, self.route(urlparse(self.path).path, body) or {"ok": True})
            except urllib.error.HTTPError as e:
                return self._send(400, {"error": "HTTP %s: %s" % (e.code, e.read()[:300].decode(errors='ignore'))})
            except Exception as e:
                return self._send(400, {"error": str(e)})

        def route(self, path, b):
            L = launcher
            if path == "/api/config":
                if "ollama_env" in b:
                    CONFIG["ollama"]["env"] = {k: v for k, v in b["ollama_env"].items() if v not in (None, "", False)}
                if "ollama_mode" in b:
                    CONFIG["ollama"]["mode"] = b["ollama_mode"]
                if "sd_selected" in b:
                    CONFIG["sd"]["selected"] = b["sd_selected"]
                if "sd_values" in b and b.get("sd_path"):
                    CONFIG["sd"]["args"][b["sd_path"]] = {k: v for k, v in b["sd_values"].items()
                                                          if v not in (None, "", False)}
                if "sd_extra" in b and b.get("sd_path"):
                    CONFIG["sd"]["extra"][b["sd_path"]] = b["sd_extra"]
                for k in ("stop_on_quit", "open_window_on_launch"):
                    if k in b:
                        CONFIG[k] = bool(b[k])
                if "login_item" in b:
                    pu.set_login_item(bool(b["login_item"]))
                save_config()
                info = L.install(b.get("sd_path"))
                return {"ok": True, "preview": L.sd_command_preview(info) if info else ""}
            if path == "/api/preset/save":
                CONFIG[b["service"]]["presets"][b["name"]] = b["values"]
                save_config()
                return {"ok": True}
            if path == "/api/preset/delete":
                CONFIG[b["service"]]["presets"].pop(b["name"], None)
                save_config()
                return {"ok": True}
            if path == "/api/rescan":
                L.detect(deep=bool(b.get("deep")))
                return L.state()
            if path == "/api/add_install":
                p = b.get("path") or pu.choose_path("folder", "Elegí la carpeta de A1111, Forge, ComfyUI o Kohya_ss")
                if not p:
                    return {"ok": False}
                if not classify_sd(p):
                    raise RuntimeError("Esa carpeta no parece una instalación de A1111, Forge, ComfyUI o Kohya_ss")
                CONFIG["sd"].setdefault("manual", []).append(p)
                CONFIG["sd"]["selected"] = os.path.realpath(p)
                L.detect()
                return L.state()
            if path == "/api/choose":
                return {"path": pu.choose_path(b.get("kind", "folder"))}
            if path == "/api/ollama/start":
                return L.start_ollama()
            if path == "/api/ollama/stop":
                return L.stop_service("ollama")
            if path == "/api/ollama/unload":
                return L.ollama_unload(b["model"])
            if path == "/api/ollama/load":
                return L.ollama_load(b["model"], b.get("keep_alive"))
            if path == "/api/ollama/delete":
                return L.ollama_delete(b["model"])
            if path == "/api/ollama/pull":
                return L.ollama_pull(b["model"])
            if path == "/api/ollama/pull_clear":
                L.pulls.pop(b["model"], None)
                return {"ok": True}
            if path == "/api/ollama/chat":
                return L.ollama_chat_terminal(b["model"])
            if path == "/api/sd/start":
                return L.start_sd(b.get("path"), port=b.get("port"), force=bool(b.get("force")))
            if path == "/api/sd/stop":
                return L.stop_service("sd", b.get("path"))
            if path == "/api/sd/call":
                return L.sd_call(b.get("path"), b["action"], b.get("value"))
            if path == "/api/failures/clear":
                FAILURES.pop(b.get("key"), None)
                return {"ok": True}
            if path == "/api/kill":
                pid = int(b["pid"])
                procs = scan_processes(L.installs, max_age=0)
                allowed = {r["pid"] for r in procs} | {c["pid"] for r in procs for c in r["children"]}
                if pid not in allowed:
                    raise RuntimeError("Ese proceso no es de Ollama ni de Stable Diffusion")
                kill_tree(pid, force=bool(b.get("force")))
                return {"ok": True}
            if path == "/api/open":
                target = b.get("url") or b.get("path")
                if target:
                    if b.get("reveal"):
                        pu.reveal(target)
                    else:
                        pu.open_target(target)
                return {"ok": True}
            if path == "/api/install/start":
                return installers.start(L, b)
            if path == "/api/install/cancel":
                return installers.cancel(b.get("app"))
            if path == "/api/install/open_terminal":
                return installers.open_terminal(L, b)
            if path == "/api/show_window":
                if on_show_window:
                    on_show_window()
                return {"ok": True}
            raise RuntimeError("Ruta desconocida: " + path)

    return Handler


def start_server(launcher, on_show_window=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(launcher, on_show_window))
    server.daemon_threads = True
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    STATE_FILE.write_text(json.dumps({"port": port, "token": TOKEN, "pid": os.getpid()}))
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass
    return port


def running_instance():
    """Si ya hay un launcher corriendo, devuelve (port, token)."""
    try:
        st = json.loads(STATE_FILE.read_text())
        req = urllib.request.Request("http://127.0.0.1:%d/api/state" % st["port"],
                                     headers={"X-Token": st["token"]})
        urllib.request.urlopen(req, timeout=1.5).read()
        return st["port"], st["token"]
    except Exception:
        return None
