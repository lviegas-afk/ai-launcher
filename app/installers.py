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
from backend_i18n import tr
from platform_utils import HOME, IS_MAC, IS_WIN

PLATFORM = "mac" if IS_MAC else "windows" if IS_WIN else "linux"


def _be():
    import backend
    return backend


class InstallError(Exception):
    """Error pensado para mostrarse tal cual al usuario.

    `code` es un marcador estable (independiente del idioma) que usan los hints de falla.
    """

    def __init__(self, msg="", code=None):
        super().__init__(msg)
        self.code = code


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------
# Catálogo de programas
# ----------------------------------------------------------------------------

BREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

APPS = {
    "ollama": {
        "name": "Ollama",
        "official_url": "https://ollama.com/download",
        "kind": None,
        "requirements": {"disk_gb": 4 if IS_WIN else 1, "ram_gb": 8, "download_gb": 1.2 if IS_WIN else 0.2,
                         "est_minutes": "2–5"},
    },
    "a1111": {
        "name": "Stable Diffusion WebUI (AUTOMATIC1111)",
        "official_url": "https://github.com/AUTOMATIC1111/stable-diffusion-webui",
        "repo": "https://github.com/AUTOMATIC1111/stable-diffusion-webui",
        "folder": "stable-diffusion-webui", "kind": "a1111", "pyver": ["3.10"],
        "requirements": {"disk_gb": 15, "ram_gb": 8, "download_gb": 5, "est_minutes": "15–40"},
    },
    "forge": {
        "name": "Stable Diffusion WebUI Forge",
        "official_url": "https://github.com/lllyasviel/stable-diffusion-webui-forge",
        "repo": "https://github.com/lllyasviel/stable-diffusion-webui-forge",
        "folder": "stable-diffusion-webui-forge", "kind": "forge", "pyver": ["3.10"],
        "requirements": {"disk_gb": 15, "ram_gb": 8, "download_gb": 5, "est_minutes": "15–40"},
    },
    "comfyui": {
        "name": "ComfyUI",
        "official_url": "https://github.com/comfyanonymous/ComfyUI",
        "repo": "https://github.com/comfyanonymous/ComfyUI",
        "folder": "ComfyUI", "kind": "comfyui", "pyver": ["3.12", "3.11", "3.10"],
        "requirements": {"disk_gb": 8, "ram_gb": 8, "download_gb": 3, "est_minutes": "10–25"},
    },
    "kohya": {
        "name": "Kohya_ss",
        "official_url": "https://github.com/bmaltais/kohya_ss",
        "repo": "https://github.com/bmaltais/kohya_ss",
        "folder": "kohya_ss", "kind": "kohya", "pyver": ["3.11", "3.10"],
        "requirements": {"disk_gb": 15, "ram_gb": 16, "download_gb": 6, "est_minutes": "15–40"},
    },
}


def _purpose(app):
    """Descripción corta del programa en el idioma activo."""
    if app == "ollama":
        return tr("Corre modelos de IA de texto (tipo ChatGPT) en tu propia compu, sin internet y sin pagar.",
                  en="Runs text AI models (ChatGPT-style) on your own computer, offline and for free.",
                  de="Führt KI-Textmodelle (wie ChatGPT) auf deinem eigenen Computer aus, ohne Internet und kostenlos.",
                  fr="Fait tourner des modèles d'IA de texte (type ChatGPT) sur votre propre ordinateur, sans internet et gratuitement.",
                  ja="ChatGPTのようなテキストAIモデルを、あなたのパソコン上でインターネットなし・無料で動かします。",
                  pt="Roda modelos de IA de texto (tipo ChatGPT) no seu próprio computador, sem internet e de graça.")
    if app == "a1111":
        return tr("Genera imágenes con IA a partir de lo que escribís. Es el programa más conocido para eso.",
                  en="Generates images with AI from what you type. It is the best-known program for this.",
                  de="Erzeugt KI-Bilder aus dem, was du schreibst. Das bekannteste Programm dafür.",
                  fr="Génère des images avec l'IA à partir de ce que vous écrivez. C'est le programme le plus connu pour cela.",
                  ja="入力した文章からAIで画像を生成します。この用途で最もよく知られているプログラムです。",
                  pt="Gera imagens com IA a partir do que você escreve. É o programa mais conhecido para isso.")
    if app == "forge":
        return tr("Una versión más rápida y liviana de Stable Diffusion WebUI, buena si tu compu anda justa de memoria.",
                  en="A faster, lighter version of Stable Diffusion WebUI, good if your computer is short on memory.",
                  de="Eine schnellere, schlankere Version von Stable Diffusion WebUI, gut, wenn dein Computer wenig Speicher hat.",
                  fr="Une version plus rapide et plus légère de Stable Diffusion WebUI, utile si votre ordinateur manque de mémoire.",
                  ja="Stable Diffusion WebUIをより速く軽くしたバージョンで、パソコンのメモリに余裕がない場合に向いています。",
                  pt="Uma versão mais rápida e leve do Stable Diffusion WebUI, boa se o seu computador está apertado de memória.")
    if app == "comfyui":
        return tr("Genera imágenes y videos con IA armando el proceso con cajitas conectadas: más flexible.",
                  en="Generates images and videos with AI by building the process from connected boxes: more flexible.",
                  de="Erzeugt Bilder und Videos mit KI, indem du den Ablauf aus verbundenen Kästchen zusammensetzt: flexibler.",
                  fr="Génère des images et des vidéos avec l'IA en construisant le processus avec des blocs reliés : plus flexible.",
                  ja="つなげたボックスで処理の流れを組み立てて、AIで画像や動画を生成します。より柔軟です。",
                  pt="Gera imagens e vídeos com IA montando o processo com caixinhas conectadas: mais flexível.")
    if app == "kohya":
        return tr("Entrena tus propios modelos de imágenes (LoRA) con tus fotos, para que la IA aprenda un estilo o un personaje.",
                  en="Trains your own image models (LoRA) with your photos, so the AI learns a style or a character.",
                  de="Trainiert eigene Bildmodelle (LoRA) mit deinen Fotos, damit die KI einen Stil oder eine Figur lernt.",
                  fr="Entraîne vos propres modèles d'images (LoRA) avec vos photos, pour que l'IA apprenne un style ou un personnage.",
                  ja="あなたの写真で独自の画像モデル(LoRA)を学習させ、AIにスタイルやキャラクターを覚えさせます。",
                  pt="Treina seus próprios modelos de imagens (LoRA) com suas fotos, para que a IA aprenda um estilo ou um personagem.")
    return ""


def _auto_support(app):
    """(auto_supported, nota) según el sistema operativo."""
    if PLATFORM == "linux":
        return False, tr("En Linux todavía no se instala automáticamente. Seguí la guía paso a paso.",
                         en="On Linux, automatic installation is not available yet. Follow the step-by-step guide.",
                         de="Unter Linux gibt es noch keine automatische Installation. Folge der Schritt-für-Schritt-Anleitung.",
                         fr="Sous Linux, l'installation automatique n'est pas encore disponible. Suivez le guide pas à pas.",
                         ja="Linuxではまだ自動インストールに対応していません。ステップバイステップのガイドに従ってください。",
                         pt="No Linux ainda não há instalação automática. Siga o guia passo a passo.")
    if app == "kohya" and IS_WIN:
        return False, tr("En Windows el instalador de Kohya_ss es interactivo (te hace preguntas en una "
                         "ventana negra), así que no se puede automatizar. Seguí la guía paso a paso.",
                         en="On Windows the Kohya_ss installer is interactive (it asks you questions in a "
                            "black console window), so it cannot be automated. Follow the step-by-step guide.",
                         de="Unter Windows ist der Kohya_ss-Installer interaktiv (er stellt dir Fragen in einem "
                            "schwarzen Konsolenfenster) und lässt sich daher nicht automatisieren. Folge der "
                            "Schritt-für-Schritt-Anleitung.",
                         fr="Sous Windows, l'installateur de Kohya_ss est interactif (il pose des questions dans une "
                            "fenêtre noire), il ne peut donc pas être automatisé. Suivez le guide pas à pas.",
                         ja="WindowsではKohya_ssのインストーラーが対話式(黒いウィンドウで質問されます)のため、"
                            "自動化できません。ステップバイステップのガイドに従ってください。",
                         pt="No Windows o instalador do Kohya_ss é interativo (faz perguntas numa janela preta), "
                            "então não dá para automatizar. Siga o guia passo a passo.")
    first_gb = tr("La primera vez descarga varios GB (incluye PyTorch).",
                  en="The first time it downloads several GB (including PyTorch).",
                  de="Beim ersten Mal werden mehrere GB heruntergeladen (inklusive PyTorch).",
                  fr="La première fois, il télécharge plusieurs Go (PyTorch inclus).",
                  ja="初回は数GBをダウンロードします(PyTorchを含む)。",
                  pt="Na primeira vez baixa vários GB (inclui o PyTorch).")
    mac_slow = tr(" En Mac con chip Apple corre más lento que con una placa NVIDIA.",
                  en=" On a Mac with an Apple chip it runs slower than with an NVIDIA card.",
                  de=" Auf einem Mac mit Apple-Chip läuft es langsamer als mit einer NVIDIA-Grafikkarte.",
                  fr=" Sur un Mac avec puce Apple, il tourne plus lentement qu'avec une carte NVIDIA.",
                  ja="Appleチップ搭載のMacでは、NVIDIAのグラフィックボードより動作が遅くなります。",
                  pt=" No Mac com chip Apple roda mais devagar do que com uma placa NVIDIA.")
    notes = {
        "ollama": tr("El instalador oficial no pide permisos de administrador.",
                     en="The official installer does not ask for administrator permissions.",
                     de="Der offizielle Installer verlangt keine Administratorrechte.",
                     fr="L'installateur officiel ne demande pas de droits d'administrateur.",
                     ja="公式インストーラーは管理者権限を必要としません。",
                     pt="O instalador oficial não pede permissões de administrador.") if IS_WIN else
                  tr("Copia la app a Aplicaciones (o a tu carpeta Aplicaciones si no hay permiso).",
                     en="Copies the app to Applications (or to your own Applications folder if there is no permission).",
                     de="Kopiert die App nach Programme (oder in deinen eigenen Programme-Ordner, falls die Berechtigung fehlt).",
                     fr="Copie l'application dans Applications (ou dans votre dossier Applications personnel si l'autorisation manque).",
                     ja="アプリを「アプリケーション」フォルダにコピーします(権限がない場合は、ユーザーのアプリケーションフォルダにコピーします)。",
                     pt="Copia o app para Aplicativos (ou para a sua pasta Aplicativos, se não houver permissão)."),
        "a1111": first_gb + (mac_slow if IS_MAC else ""),
        "forge": first_gb + (mac_slow if IS_MAC else ""),
        "comfyui": tr("Descarga PyTorch (varios GB), así que puede tardar un buen rato.",
                      en="It downloads PyTorch (several GB), so it can take quite a while.",
                      de="Es lädt PyTorch herunter (mehrere GB) und kann daher eine ganze Weile dauern.",
                      fr="Il télécharge PyTorch (plusieurs Go), cela peut donc prendre un bon moment.",
                      ja="PyTorch(数GB)をダウンロードするため、かなり時間がかかることがあります。",
                      pt="Baixa o PyTorch (vários GB), então pode demorar bastante."),
        "kohya": tr("Usa Homebrew para instalar Python y puede tardar bastante. Va a necesitar bastante memoria "
                    "para entrenar.",
                    en="It uses Homebrew to install Python and can take quite long. It will need a lot of "
                       "memory for training.",
                    de="Es nutzt Homebrew, um Python zu installieren, und kann recht lange dauern. Zum Trainieren "
                       "braucht es viel Arbeitsspeicher.",
                    fr="Il utilise Homebrew pour installer Python et peut prendre longtemps. Il faudra beaucoup "
                       "de mémoire pour l'entraînement.",
                    ja="PythonのインストールにHomebrewを使うため、かなり時間がかかることがあります。"
                       "学習にはたくさんのメモリが必要です。",
                    pt="Usa o Homebrew para instalar o Python e pode demorar bastante. Vai precisar de muita "
                       "memória para treinar."),
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


def _t_already():
    return tr("Ya está instalado.", en="Already installed.", de="Bereits installiert.",
              fr="Déjà installé.", ja="インストール済みです。", pt="Já está instalado.")


def _t_not_installed():
    return tr("No está instalado", en="Not installed", de="Nicht installiert",
              fr="Pas installé", ja="インストールされていません", pt="Não está instalado")


def _t_not_found():
    return tr("No se encontró", en="Not found", de="Nicht gefunden",
              fr="Introuvable", ja="見つかりません", pt="Não encontrado")


def _p_git():
    ok, path = _git_status()
    if ok:
        return _prereq("git", "Git", True, path, True, _t_already())
    if IS_MAC:
        return _prereq("git", "Git", False,
                       tr("No está instalado (ni las herramientas de línea de comandos de Apple)",
                          en="Not installed (nor Apple's command line tools)",
                          de="Nicht installiert (auch nicht die Befehlszeilentools von Apple)",
                          fr="Pas installé (ni les outils en ligne de commande d'Apple)",
                          ja="インストールされていません(Appleのコマンドラインツールもありません)",
                          pt="Não está instalado (nem as ferramentas de linha de comando da Apple)"),
                       False,
                       tr("Apple lo instala con sus «herramientas de desarrollo»: se abre una ventana de Apple "
                          "que te pide confirmar. Hacelo y después volvé a intentar.",
                          en="Apple installs it with its “developer tools”: an Apple window opens asking you "
                             "to confirm. Do that and then try again.",
                          de="Apple installiert es mit seinen „Entwicklertools“: Es öffnet sich ein Apple-Fenster, "
                             "das um Bestätigung bittet. Bestätige dort und versuche es danach erneut.",
                          fr="Apple l'installe avec ses « outils de développement » : une fenêtre d'Apple s'ouvre "
                             "et vous demande de confirmer. Faites-le, puis réessayez.",
                          ja="Appleの「デベロッパツール」と一緒にインストールされます。Appleのウィンドウが開いて"
                             "確認を求められるので、承認してからもう一度お試しください。",
                          pt="A Apple instala com as suas “ferramentas de desenvolvimento”: abre-se uma janela da "
                             "Apple pedindo confirmação. Confirme e depois tente de novo."),
                       "xcode-select --install")
    if IS_WIN and shutil.which("winget"):
        return _prereq("git", "Git", False, _t_not_installed(), True,
                       tr("Se instala solo con winget (Git.Git).",
                          en="Installs itself with winget (Git.Git).",
                          de="Wird automatisch mit winget installiert (Git.Git).",
                          fr="S'installe tout seul avec winget (Git.Git).",
                          ja="winget(Git.Git)で自動的にインストールされます。",
                          pt="Instala-se sozinho com o winget (Git.Git)."),
                       "winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements",
                       ("winget", "Git.Git"))
    return _prereq("git", "Git", False, _t_not_installed(), False,
                   tr("Descargalo e instalalo desde https://git-scm.com/downloads y volvé a intentar.",
                      en="Download and install it from https://git-scm.com/downloads and try again.",
                      de="Lade es von https://git-scm.com/downloads herunter, installiere es und versuche es erneut.",
                      fr="Téléchargez-le et installez-le depuis https://git-scm.com/downloads, puis réessayez.",
                      ja="https://git-scm.com/downloads からダウンロードしてインストールし、もう一度お試しください。",
                      pt="Baixe e instale em https://git-scm.com/downloads e tente de novo."))


def _p_python(minors, winget_minor=None):
    found = find_python(minors)
    label = "Python " + " / ".join(minors) if len(minors) > 1 else "Python " + minors[0]
    if found:
        return _prereq("python", label, True,
                       tr("Python {v} en {p}", en="Python {v} at {p}", de="Python {v} unter {p}",
                          fr="Python {v} dans {p}", ja="Python {v}({p})", pt="Python {v} em {p}",
                          v=found["version"], p=found["exe"]), True, _t_already())
    pref = winget_minor or minors[0]
    if IS_MAC:
        if shutil.which("brew"):
            return _prereq("python", label, False, _t_not_found(), True,
                           tr("Se instala solo con Homebrew (python@{v}).",
                              en="Installs itself with Homebrew (python@{v}).",
                              de="Wird automatisch mit Homebrew installiert (python@{v}).",
                              fr="S'installe tout seul avec Homebrew (python@{v}).",
                              ja="Homebrew(python@{v})で自動的にインストールされます。",
                              pt="Instala-se sozinho com o Homebrew (python@{v}).", v=pref),
                           "brew install python@" + pref, ("brew", "python@" + pref))
        return _prereq("python", label, False, _t_not_found(), False,
                       tr("Primero instalá Homebrew (mirá el requisito de Homebrew); después se instala Python con él.",
                          en="First install Homebrew (see the Homebrew requirement); then Python is installed with it.",
                          de="Installiere zuerst Homebrew (siehe die Homebrew-Voraussetzung); danach wird Python damit installiert.",
                          fr="Installez d'abord Homebrew (voir le prérequis Homebrew) ; Python s'installe ensuite avec lui.",
                          ja="先にHomebrewをインストールしてください(Homebrewの要件を参照)。その後、Homebrewで Python をインストールします。",
                          pt="Primeiro instale o Homebrew (veja o requisito do Homebrew); depois o Python é instalado com ele."),
                       None)
    if IS_WIN and shutil.which("winget"):
        pid = "Python.Python." + pref
        return _prereq("python", label, False, _t_not_found(), True,
                       tr("Se instala solo con winget ({pid}).",
                          en="Installs itself with winget ({pid}).",
                          de="Wird automatisch mit winget installiert ({pid}).",
                          fr="S'installe tout seul avec winget ({pid}).",
                          ja="winget({pid})で自動的にインストールされます。",
                          pt="Instala-se sozinho com o winget ({pid}).", pid=pid),
                       "winget install --id %s -e --accept-package-agreements --accept-source-agreements" % pid,
                       ("winget", pid))
    return _prereq("python", label, False, _t_not_found(), False,
                   tr("Descargá Python {v} desde https://www.python.org/downloads/ y marcá «Add python.exe to PATH».",
                      en="Download Python {v} from https://www.python.org/downloads/ and check “Add python.exe to PATH”.",
                      de="Lade Python {v} von https://www.python.org/downloads/ herunter und aktiviere „Add python.exe to PATH“.",
                      fr="Téléchargez Python {v} depuis https://www.python.org/downloads/ et cochez « Add python.exe to PATH ».",
                      ja="https://www.python.org/downloads/ から Python {v} をダウンロードし、「Add python.exe to PATH」にチェックを入れてください。",
                      pt="Baixe o Python {v} em https://www.python.org/downloads/ e marque “Add python.exe to PATH”.",
                      v=pref))


def _p_brew():
    brew = shutil.which("brew")
    if brew:
        return _prereq("brew", "Homebrew", True, brew, True, _t_already())
    return _prereq("brew", "Homebrew", False, _t_not_installed(), False,
                   tr("Homebrew es el instalador de programas de la Mac. Para instalarlo hay que escribir tu "
                      "contraseña, así que se hace desde la Terminal (botón «Abrir Terminal»).",
                      en="Homebrew is the Mac's program installer. Installing it requires typing your "
                         "password, so it is done from the Terminal (“Open Terminal” button).",
                      de="Homebrew ist der Programm-Installer für den Mac. Zur Installation musst du dein "
                         "Passwort eingeben, daher geschieht das im Terminal (Schaltfläche „Terminal öffnen“).",
                      fr="Homebrew est l'installateur de programmes du Mac. Pour l'installer, il faut saisir votre "
                         "mot de passe ; cela se fait donc depuis le Terminal (bouton « Ouvrir le Terminal »).",
                      ja="HomebrewはMac用のプログラムインストーラーです。インストールにはパスワードの入力が必要なため、"
                         "ターミナルから行います(「ターミナルを開く」ボタン)。",
                      pt="O Homebrew é o instalador de programas do Mac. Para instalá-lo é preciso digitar a sua "
                         "senha, então isso é feito pelo Terminal (botão “Abrir Terminal”)."),
                   BREW_INSTALL)


def _p_brew_deps():
    missing = [f for f, exe in (("cmake", "cmake"), ("protobuf", "protoc"), ("rust", "rustc"), ("wget", "wget"))
               if not shutil.which(exe)]
    name = tr("Herramientas de compilación (cmake, protobuf, rust, wget)",
              en="Build tools (cmake, protobuf, rust, wget)",
              de="Build-Werkzeuge (cmake, protobuf, rust, wget)",
              fr="Outils de compilation (cmake, protobuf, rust, wget)",
              ja="ビルドツール(cmake、protobuf、rust、wget)",
              pt="Ferramentas de compilação (cmake, protobuf, rust, wget)")
    if not missing:
        return _prereq("brew_deps", name, True,
                       tr("Ya están instaladas", en="Already installed", de="Bereits installiert",
                          fr="Déjà installés", ja="インストール済み", pt="Já estão instaladas"),
                       True,
                       tr("Ya están instaladas.", en="Already installed.", de="Bereits installiert.",
                          fr="Déjà installés.", ja="インストール済みです。", pt="Já estão instaladas."))
    ok_brew = bool(shutil.which("brew"))
    return _prereq("brew_deps", name, False,
                   tr("Faltan: {m}", en="Missing: {m}", de="Es fehlen: {m}", fr="Manquent : {m}",
                      ja="不足: {m}", pt="Faltam: {m}", m=", ".join(missing)),
                   ok_brew,
                   tr("Se instalan solas con Homebrew.", en="They install themselves with Homebrew.",
                      de="Werden automatisch mit Homebrew installiert.",
                      fr="Ils s'installent tout seuls avec Homebrew.",
                      ja="Homebrewで自動的にインストールされます。",
                      pt="Instalam-se sozinhas com o Homebrew.") if ok_brew else
                   tr("Primero instalá Homebrew.", en="Install Homebrew first.",
                      de="Installiere zuerst Homebrew.", fr="Installez d'abord Homebrew.",
                      ja="先にHomebrewをインストールしてください。", pt="Primeiro instale o Homebrew."),
                   "brew install " + " ".join(missing), ("brew", missing))


def _p_winget():
    w = shutil.which("winget")
    return _prereq("winget",
                   tr("winget (instalador de Windows)", en="winget (Windows installer)",
                      de="winget (Windows-Installer)", fr="winget (installateur de Windows)",
                      ja="winget(Windowsのインストーラー)", pt="winget (instalador do Windows)"),
                   bool(w),
                   w or tr("No está disponible", en="Not available", de="Nicht verfügbar",
                           fr="Non disponible", ja="利用できません", pt="Não está disponível"),
                   False,
                   tr("Viene con Windows 10/11. Si falta, instalá «Instalador de aplicación» desde la Microsoft Store.",
                      en="It comes with Windows 10/11. If it is missing, install “App Installer” from the Microsoft Store.",
                      de="Ist in Windows 10/11 enthalten. Falls es fehlt, installiere „App-Installer“ aus dem Microsoft Store.",
                      fr="Il est fourni avec Windows 10/11. S'il manque, installez « Programme d'installation d'application » depuis le Microsoft Store.",
                      ja="Windows 10/11に付属しています。ない場合は、Microsoft Storeから「アプリ インストーラー」をインストールしてください。",
                      pt="Vem com o Windows 10/11. Se estiver faltando, instale o “Instalador de Aplicativo” pela Microsoft Store."))


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
        steps.append({"title": tr("Instalá Homebrew", en="Install Homebrew", de="Homebrew installieren",
                                  fr="Installez Homebrew", ja="Homebrewをインストールする",
                                  pt="Instale o Homebrew"),
                      "text": tr("Homebrew es el «instalador de programas» de la Mac. Pegá este comando en la "
                                 "<b>Terminal</b> (Aplicaciones → Utilidades → Terminal); te va a pedir tu contraseña "
                                 "y puede tardar unos minutos. Más info en <a href=\"https://brew.sh\">brew.sh</a>. "
                                 "Si ya lo tenés, salteá este paso.",
                                 en="Homebrew is the Mac's “program installer”. Paste this command into the "
                                    "<b>Terminal</b> (Applications → Utilities → Terminal); it will ask for your "
                                    "password and may take a few minutes. More info at "
                                    "<a href=\"https://brew.sh\">brew.sh</a>. If you already have it, skip this step.",
                                 de="Homebrew ist der „Programm-Installer“ des Mac. Füge diesen Befehl ins "
                                    "<b>Terminal</b> ein (Programme → Dienstprogramme → Terminal); er fragt nach "
                                    "deinem Passwort und kann einige Minuten dauern. Mehr Infos unter "
                                    "<a href=\"https://brew.sh\">brew.sh</a>. Falls du es schon hast, überspringe "
                                    "diesen Schritt.",
                                 fr="Homebrew est l'« installateur de programmes » du Mac. Collez cette commande dans "
                                    "le <b>Terminal</b> (Applications → Utilitaires → Terminal) ; il vous demandera "
                                    "votre mot de passe et peut prendre quelques minutes. Plus d'infos sur "
                                    "<a href=\"https://brew.sh\">brew.sh</a>. Si vous l'avez déjà, passez cette étape.",
                                 ja="HomebrewはMacの「プログラムインストーラー」です。このコマンドを<b>ターミナル</b>"
                                    "(アプリケーション → ユーティリティ → ターミナル)に貼り付けてください。"
                                    "パスワードを求められ、数分かかることがあります。詳しくは"
                                    "<a href=\"https://brew.sh\">brew.sh</a>をご覧ください。すでにある場合は、"
                                    "この手順は不要です。",
                                 pt="O Homebrew é o “instalador de programas” do Mac. Cole este comando no "
                                    "<b>Terminal</b> (Aplicativos → Utilitários → Terminal); ele vai pedir a sua "
                                    "senha e pode levar alguns minutos. Mais informações em "
                                    "<a href=\"https://brew.sh\">brew.sh</a>. Se você já o tem, pule este passo."),
                      "cmd": BREW_INSTALL})
        if app == "kohya":
            steps.append({"title": tr("Instalá Git", en="Install Git", de="Git installieren",
                                      fr="Installez Git", ja="Gitをインストールする", pt="Instale o Git"),
                          "text": tr("Con Homebrew. (Python lo instala el propio instalador de Kohya_ss.)",
                                     en="With Homebrew. (Python is installed by the Kohya_ss installer itself.)",
                                     de="Mit Homebrew. (Python wird vom Kohya_ss-Installer selbst installiert.)",
                                     fr="Avec Homebrew. (Python est installé par l'installateur de Kohya_ss lui-même.)",
                                     ja="Homebrewを使います。(PythonはKohya_ssのインストーラー自身がインストールします。)",
                                     pt="Com o Homebrew. (O Python é instalado pelo próprio instalador do Kohya_ss.)"),
                          "cmd": "brew install git"})
        elif app in ("a1111", "forge"):
            steps.append({"title": tr("Instalá lo necesario", en="Install what is needed",
                                      de="Das Nötige installieren", fr="Installez le nécessaire",
                                      ja="必要なものをインストールする", pt="Instale o necessário"),
                          "text": tr("Python 3.10, Git y unas herramientas de compilación, todo con un solo comando "
                                     "(es la lista oficial de la <a href=\"https://github.com/AUTOMATIC1111/"
                                     "stable-diffusion-webui/wiki/Installation-on-Apple-Silicon\">guía de A1111 para "
                                     "Apple Silicon</a>).",
                                     en="Python 3.10, Git and some build tools, all with a single command "
                                        "(it is the official list from the <a href=\"https://github.com/AUTOMATIC1111/"
                                        "stable-diffusion-webui/wiki/Installation-on-Apple-Silicon\">A1111 guide for "
                                        "Apple Silicon</a>).",
                                     de="Python 3.10, Git und einige Build-Werkzeuge, alles mit einem einzigen Befehl "
                                        "(es ist die offizielle Liste aus der <a href=\"https://github.com/AUTOMATIC1111/"
                                        "stable-diffusion-webui/wiki/Installation-on-Apple-Silicon\">A1111-Anleitung "
                                        "für Apple Silicon</a>).",
                                     fr="Python 3.10, Git et quelques outils de compilation, le tout avec une seule "
                                        "commande (c'est la liste officielle du <a href=\"https://github.com/AUTOMATIC1111/"
                                        "stable-diffusion-webui/wiki/Installation-on-Apple-Silicon\">guide A1111 pour "
                                        "Apple Silicon</a>).",
                                     ja="Python 3.10、Git、いくつかのビルドツールを1つのコマンドでまとめてインストールします"
                                        "(<a href=\"https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/"
                                        "Installation-on-Apple-Silicon\">Apple Silicon向けA1111ガイド</a>の公式リストです)。",
                                     pt="Python 3.10, Git e algumas ferramentas de compilação, tudo com um único comando "
                                        "(é a lista oficial do <a href=\"https://github.com/AUTOMATIC1111/"
                                        "stable-diffusion-webui/wiki/Installation-on-Apple-Silicon\">guia do A1111 para "
                                        "Apple Silicon</a>)."),
                          "cmd": "brew install cmake protobuf rust python@3.10 git wget"})
        else:
            steps.append({"title": tr("Instalá Python y Git", en="Install Python and Git",
                                      de="Python und Git installieren", fr="Installez Python et Git",
                                      ja="PythonとGitをインストールする", pt="Instale o Python e o Git"),
                          "text": tr("Python 3.12 (sirve de 3.10 a 3.12) y Git.",
                                     en="Python 3.12 (anything from 3.10 to 3.12 works) and Git.",
                                     de="Python 3.12 (3.10 bis 3.12 funktionieren) und Git.",
                                     fr="Python 3.12 (de 3.10 à 3.12 conviennent) et Git.",
                                     ja="Python 3.12(3.10~3.12であれば動作します)とGit。",
                                     pt="Python 3.12 (serve de 3.10 a 3.12) e Git."),
                          "cmd": "brew install python@3.12 git"})
    elif IS_WIN:
        steps.append({"title": tr("Instalá Git", en="Install Git", de="Git installieren",
                                  fr="Installez Git", ja="Gitをインストールする", pt="Instale o Git"),
                      "text": tr("Abrí el menú Inicio, escribí <b>cmd</b> y abrí «Símbolo del sistema». Pegá el comando "
                                 "(o bajalo de <a href=\"https://git-scm.com/downloads\">git-scm.com</a>).",
                                 en="Open the Start menu, type <b>cmd</b> and open “Command Prompt”. Paste the command "
                                    "(or download it from <a href=\"https://git-scm.com/downloads\">git-scm.com</a>).",
                                 de="Öffne das Startmenü, tippe <b>cmd</b> und öffne die „Eingabeaufforderung“. Füge den "
                                    "Befehl ein (oder lade es von <a href=\"https://git-scm.com/downloads\">git-scm.com</a> "
                                    "herunter).",
                                 fr="Ouvrez le menu Démarrer, tapez <b>cmd</b> et ouvrez « Invite de commandes ». Collez "
                                    "la commande (ou téléchargez-le depuis <a href=\"https://git-scm.com/downloads\">"
                                    "git-scm.com</a>).",
                                 ja="スタートメニューを開き、<b>cmd</b>と入力して「コマンド プロンプト」を開きます。"
                                    "コマンドを貼り付けてください(または<a href=\"https://git-scm.com/downloads\">"
                                    "git-scm.com</a>からダウンロードします)。",
                                 pt="Abra o menu Iniciar, digite <b>cmd</b> e abra o “Prompt de Comando”. Cole o comando "
                                    "(ou baixe em <a href=\"https://git-scm.com/downloads\">git-scm.com</a>)."),
                      "cmd": "winget install --id Git.Git -e"})
        pyid = "Python.Python.3.12" if app == "comfyui" else "Python.Python.3.11" if app == "kohya" \
            else "Python.Python.3.10"
        steps.append({"title": tr("Instalá Python {v}", en="Install Python {v}", de="Python {v} installieren",
                                  fr="Installez Python {v}", ja="Python {v}をインストールする",
                                  pt="Instale o Python {v}", v=pv),
                      "text": tr("Tiene que ser una de estas versiones: <b>{v}</b>. Si lo bajás de "
                                 "<a href=\"https://www.python.org/downloads/\">python.org</a>, tildá «Add python.exe "
                                 "to PATH» en la primera pantalla.",
                                 en="It must be one of these versions: <b>{v}</b>. If you download it from "
                                    "<a href=\"https://www.python.org/downloads/\">python.org</a>, check “Add python.exe "
                                    "to PATH” on the first screen.",
                                 de="Es muss eine dieser Versionen sein: <b>{v}</b>. Wenn du es von "
                                    "<a href=\"https://www.python.org/downloads/\">python.org</a> herunterlädst, "
                                    "aktiviere auf dem ersten Bildschirm „Add python.exe to PATH“.",
                                 fr="Ce doit être l'une de ces versions : <b>{v}</b>. Si vous le téléchargez depuis "
                                    "<a href=\"https://www.python.org/downloads/\">python.org</a>, cochez « Add python.exe "
                                    "to PATH » sur le premier écran.",
                                 ja="次のいずれかのバージョンが必要です: <b>{v}</b>。"
                                    "<a href=\"https://www.python.org/downloads/\">python.org</a>からダウンロードする場合は、"
                                    "最初の画面で「Add python.exe to PATH」にチェックを入れてください。",
                                 pt="Precisa ser uma destas versões: <b>{v}</b>. Se baixar de "
                                    "<a href=\"https://www.python.org/downloads/\">python.org</a>, marque “Add python.exe "
                                    "to PATH” na primeira tela.", v=pv),
                      "cmd": "winget install --id %s -e" % pyid})
        steps.append({"title": tr("Cerrá y volvé a abrir la ventana negra",
                                  en="Close and reopen the black window",
                                  de="Schwarzes Fenster schließen und neu öffnen",
                                  fr="Fermez et rouvrez la fenêtre noire",
                                  ja="黒いウィンドウを閉じて開き直す",
                                  pt="Feche e abra de novo a janela preta"),
                      "text": tr("Así los programas nuevos quedan disponibles.",
                                 en="This makes the newly installed programs available.",
                                 de="So stehen die neu installierten Programme zur Verfügung.",
                                 fr="Ainsi, les nouveaux programmes deviennent disponibles.",
                                 ja="これで、新しくインストールしたプログラムが使えるようになります。",
                                 pt="Assim os programas novos ficam disponíveis."), "cmd": None})
    else:
        steps.append({"title": tr("Instalá Git y Python {v}", en="Install Git and Python {v}",
                                  de="Git und Python {v} installieren", fr="Installez Git et Python {v}",
                                  ja="GitとPython {v}をインストールする", pt="Instale o Git e o Python {v}", v=pv),
                      "text": tr("Con el instalador de paquetes de tu distribución (apt, dnf, pacman...).",
                                 en="With your distribution's package manager (apt, dnf, pacman...).",
                                 de="Mit dem Paketmanager deiner Distribution (apt, dnf, pacman ...).",
                                 fr="Avec le gestionnaire de paquets de votre distribution (apt, dnf, pacman...).",
                                 ja="お使いのディストリビューションのパッケージマネージャー(apt、dnf、pacmanなど)を使います。",
                                 pt="Com o gerenciador de pacotes da sua distribuição (apt, dnf, pacman...)."),
                      "cmd": None})
    return steps


def _guide_clone(app, parent, recursive=False):
    a = APPS[app]
    cmd = "git clone %s%s" % ("--recursive " if recursive else "", a["repo"])
    return {"title": tr("Descargá el programa", en="Download the program", de="Programm herunterladen",
                        fr="Téléchargez le programme", ja="プログラムをダウンロードする",
                        pt="Baixe o programa"),
            "text": tr("Esto baja el código oficial de <a href=\"{u}\">{n}</a> a una carpeta nueva dentro de "
                       "<code>{p}</code>.",
                       en="This downloads the official code from <a href=\"{u}\">{n}</a> into a new folder inside "
                          "<code>{p}</code>.",
                       de="Dadurch wird der offizielle Code von <a href=\"{u}\">{n}</a> in einen neuen Ordner in "
                          "<code>{p}</code> heruntergeladen.",
                       fr="Cela télécharge le code officiel de <a href=\"{u}\">{n}</a> dans un nouveau dossier à "
                          "l'intérieur de <code>{p}</code>.",
                       ja="<a href=\"{u}\">{n}</a>の公式コードを、<code>{p}</code>内の新しいフォルダにダウンロードします。",
                       pt="Isto baixa o código oficial de <a href=\"{u}\">{n}</a> para uma pasta nova dentro de "
                          "<code>{p}</code>.",
                       u=a["official_url"], n=a["official_url"].replace("https://", ""), p=parent),
            "cmd": _join(_cd(parent), cmd)}


def _guide_detect(app, folder_path):
    return {"title": tr("Que AI Launcher lo encuentre", en="Let AI Launcher find it",
                        de="AI Launcher findet es", fr="Faire en sorte qu'AI Launcher le trouve",
                        ja="AI Launcherに見つけてもらう", pt="Fazer o AI Launcher encontrá-lo"),
            "text": tr("Volvé a AI Launcher y tocá <b>Buscar de nuevo</b> (o «Agregar instalación» y elegí la carpeta "
                       "<code>{p}</code>). Desde ahí ya lo podés iniciar con un clic.",
                       en="Go back to AI Launcher and click <b>Search again</b> (or “Add installation” and choose the "
                          "folder <code>{p}</code>). From there you can start it with one click.",
                       de="Wechsle zurück zu AI Launcher und klicke auf <b>Erneut suchen</b> (oder „Installation "
                          "hinzufügen“ und wähle den Ordner <code>{p}</code>). Von dort kannst du es mit einem Klick "
                          "starten.",
                       fr="Revenez dans AI Launcher et cliquez sur <b>Rechercher à nouveau</b> (ou « Ajouter une "
                          "installation » et choisissez le dossier <code>{p}</code>). Vous pourrez ensuite le "
                          "lancer d'un clic.",
                       ja="AI Launcherに戻って<b>もう一度検索</b>をクリックします(または「インストールを追加」で"
                          "フォルダ<code>{p}</code>を選びます)。以降はワンクリックで起動できます。",
                       pt="Volte ao AI Launcher e clique em <b>Buscar de novo</b> (ou em “Adicionar instalação” e "
                          "escolha a pasta <code>{p}</code>). A partir daí você pode iniciá-lo com um clique.",
                       p=folder_path),
            "cmd": None}


def _t_download_installer():
    return tr("Bajá el instalador", en="Download the installer", de="Installer herunterladen",
              fr="Téléchargez l'installateur", ja="インストーラーをダウンロードする",
              pt="Baixe o instalador")


def _t_check_works():
    return tr("Comprobá que anda", en="Check that it works", de="Prüfen, ob es funktioniert",
              fr="Vérifiez que ça marche", ja="動作を確認する", pt="Confira se funciona")


def _t_open_it():
    return tr("Abrilo", en="Open it", de="Öffnen", fr="Ouvrez-le", ja="開く", pt="Abra")


def _t_put_model():
    return tr("Poné un modelo", en="Add a model", de="Ein Modell hinzufügen", fr="Ajoutez un modèle",
              ja="モデルを入れる", pt="Coloque um modelo")


def guide_for(app, parent):
    a = APPS[app]
    dest = os.path.join(parent, a.get("folder") or "")
    if app == "ollama":
        if IS_MAC:
            return [
                {"title": _t_download_installer(),
                 "text": tr("Entrá a <a href=\"https://ollama.com/download\">ollama.com/download</a> y elegí macOS "
                            "(archivo Ollama.dmg).",
                            en="Go to <a href=\"https://ollama.com/download\">ollama.com/download</a> and choose macOS "
                               "(Ollama.dmg file).",
                            de="Gehe zu <a href=\"https://ollama.com/download\">ollama.com/download</a> und wähle macOS "
                               "(Datei Ollama.dmg).",
                            fr="Allez sur <a href=\"https://ollama.com/download\">ollama.com/download</a> et choisissez "
                               "macOS (fichier Ollama.dmg).",
                            ja="<a href=\"https://ollama.com/download\">ollama.com/download</a>にアクセスし、macOS"
                               "(Ollama.dmgファイル)を選びます。",
                            pt="Acesse <a href=\"https://ollama.com/download\">ollama.com/download</a> e escolha macOS "
                               "(arquivo Ollama.dmg)."), "cmd": None},
                {"title": tr("Instalalo", en="Install it", de="Installieren", fr="Installez-le",
                             ja="インストールする", pt="Instale"),
                 "text": tr("Abrí el archivo descargado y arrastrá <b>Ollama</b> a la carpeta <b>Aplicaciones</b>.",
                            en="Open the downloaded file and drag <b>Ollama</b> to the <b>Applications</b> folder.",
                            de="Öffne die heruntergeladene Datei und ziehe <b>Ollama</b> in den Ordner <b>Programme</b>.",
                            fr="Ouvrez le fichier téléchargé et faites glisser <b>Ollama</b> dans le dossier "
                               "<b>Applications</b>.",
                            ja="ダウンロードしたファイルを開き、<b>Ollama</b>を<b>アプリケーション</b>フォルダにドラッグします。",
                            pt="Abra o arquivo baixado e arraste o <b>Ollama</b> para a pasta <b>Aplicativos</b>."),
                 "cmd": None},
                {"title": tr("Abrilo una vez", en="Open it once", de="Einmal öffnen", fr="Ouvrez-le une fois",
                             ja="一度開く", pt="Abra uma vez"),
                 "text": tr("Abrí Ollama desde Aplicaciones: queda un ícono en la barra de menú de arriba.",
                            en="Open Ollama from Applications: an icon stays in the menu bar at the top.",
                            de="Öffne Ollama aus Programme: Oben in der Menüleiste erscheint ein Symbol.",
                            fr="Ouvrez Ollama depuis Applications : une icône reste dans la barre des menus en haut.",
                            ja="アプリケーションからOllamaを開きます。画面上部のメニューバーにアイコンが表示されます。",
                            pt="Abra o Ollama em Aplicativos: um ícone fica na barra de menus, no alto da tela."),
                 "cmd": None},
                {"title": _t_check_works(),
                 "text": tr("En la Terminal, este comando tiene que mostrar un número de versión.",
                            en="In the Terminal, this command must show a version number.",
                            de="Im Terminal muss dieser Befehl eine Versionsnummer anzeigen.",
                            fr="Dans le Terminal, cette commande doit afficher un numéro de version.",
                            ja="ターミナルでこのコマンドを実行すると、バージョン番号が表示されるはずです。",
                            pt="No Terminal, este comando deve mostrar um número de versão."),
                 "cmd": "/Applications/Ollama.app/Contents/Resources/ollama --version"},
                _guide_detect(app, "Ollama.app")]
        if IS_WIN:
            return [
                {"title": _t_download_installer(),
                 "text": tr("Entrá a <a href=\"https://ollama.com/download\">ollama.com/download</a> y bajá "
                            "<b>OllamaSetup.exe</b> para Windows.",
                            en="Go to <a href=\"https://ollama.com/download\">ollama.com/download</a> and download "
                               "<b>OllamaSetup.exe</b> for Windows.",
                            de="Gehe zu <a href=\"https://ollama.com/download\">ollama.com/download</a> und lade "
                               "<b>OllamaSetup.exe</b> für Windows herunter.",
                            fr="Allez sur <a href=\"https://ollama.com/download\">ollama.com/download</a> et "
                               "téléchargez <b>OllamaSetup.exe</b> pour Windows.",
                            ja="<a href=\"https://ollama.com/download\">ollama.com/download</a>にアクセスし、"
                               "Windows用の<b>OllamaSetup.exe</b>をダウンロードします。",
                            pt="Acesse <a href=\"https://ollama.com/download\">ollama.com/download</a> e baixe o "
                               "<b>OllamaSetup.exe</b> para Windows."), "cmd": None},
                {"title": tr("Ejecutalo", en="Run it", de="Ausführen", fr="Exécutez-le",
                             ja="実行する", pt="Execute"),
                 "text": tr("Doble clic en el archivo y seguí los pasos. No pide ser administrador. "
                            "Al terminar queda un ícono en la bandeja (al lado del reloj).",
                            en="Double-click the file and follow the steps. It does not ask for administrator rights. "
                               "When it finishes, an icon stays in the tray (next to the clock).",
                            de="Doppelklicke auf die Datei und folge den Schritten. Administratorrechte sind nicht "
                               "nötig. Danach bleibt ein Symbol im Infobereich (neben der Uhr).",
                            fr="Double-cliquez sur le fichier et suivez les étapes. Les droits d'administrateur ne "
                               "sont pas requis. À la fin, une icône reste dans la zone de notification (à côté de "
                               "l'horloge).",
                            ja="ファイルをダブルクリックして、手順に従います。管理者権限は不要です。"
                               "完了すると、タスクトレイ(時計の横)にアイコンが表示されます。",
                            pt="Clique duas vezes no arquivo e siga os passos. Não pede privilégios de administrador. "
                               "Ao terminar, fica um ícone na bandeja (ao lado do relógio)."), "cmd": None},
                {"title": _t_check_works(),
                 "text": tr("En una ventana negra (cmd), tiene que mostrar un número de versión.",
                            en="In a black window (cmd), it must show a version number.",
                            de="In einem schwarzen Fenster (cmd) muss eine Versionsnummer angezeigt werden.",
                            fr="Dans une fenêtre noire (cmd), il doit afficher un numéro de version.",
                            ja="黒いウィンドウ(cmd)で実行すると、バージョン番号が表示されるはずです。",
                            pt="Numa janela preta (cmd), deve mostrar um número de versão."),
                 "cmd": "ollama --version"},
                _guide_detect(app, "Ollama")]
        return [{"title": tr("Instalá Ollama", en="Install Ollama", de="Ollama installieren",
                             fr="Installez Ollama", ja="Ollamaをインストールする", pt="Instale o Ollama"),
                 "text": tr("Seguí las instrucciones de <a href=\"https://ollama.com/download\">ollama.com/download</a> "
                            "para Linux.",
                            en="Follow the instructions at <a href=\"https://ollama.com/download\">ollama.com/download</a> "
                               "for Linux.",
                            de="Folge den Anweisungen auf <a href=\"https://ollama.com/download\">ollama.com/download</a> "
                               "für Linux.",
                            fr="Suivez les instructions de <a href=\"https://ollama.com/download\">ollama.com/download</a> "
                               "pour Linux.",
                            ja="<a href=\"https://ollama.com/download\">ollama.com/download</a>のLinux向けの手順に従ってください。",
                            pt="Siga as instruções de <a href=\"https://ollama.com/download\">ollama.com/download</a> "
                               "para Linux."),
                 "cmd": "curl -fsSL https://ollama.com/install.sh | sh"}, _guide_detect(app, "Ollama")]

    steps = _guide_prereqs(app)
    if app in ("a1111", "forge"):
        steps.append(_guide_clone(app, parent))
        if IS_MAC:
            steps.append({"title": tr("Decile dónde vive", en="Tell it where it lives",
                                      de="Sag ihm, wo es liegt", fr="Indiquez où il se trouve",
                                      ja="場所を教える", pt="Diga onde ele está"),
                          "text": tr("En Mac, el script de arranque supone que el programa está en tu carpeta personal. "
                                     "Este comando le avisa que está en la carpeta que elegiste.",
                                     en="On Mac, the startup script assumes the program is in your home folder. "
                                        "This command tells it that it is in the folder you chose.",
                                     de="Auf dem Mac geht das Startskript davon aus, dass das Programm in deinem "
                                        "Benutzerordner liegt. Dieser Befehl teilt ihm mit, dass es im gewählten "
                                        "Ordner liegt.",
                                     fr="Sur Mac, le script de démarrage suppose que le programme se trouve dans votre "
                                        "dossier personnel. Cette commande lui indique qu'il est dans le dossier que "
                                        "vous avez choisi.",
                                     ja="Macでは、起動スクリプトはプログラムがホームフォルダにあると想定しています。"
                                        "このコマンドで、選んだフォルダにあることを伝えます。",
                                     pt="No Mac, o script de inicialização supõe que o programa está na sua pasta "
                                        "pessoal. Este comando avisa que ele está na pasta que você escolheu."),
                          "cmd": _join(_cd(dest), "echo 'install_dir=\"%s\"' >> webui-user.sh" % parent,
                                       "echo 'clone_dir=\"%s\"' >> webui-user.sh" % a["folder"])})
            first = _join(_cd(dest), "python_cmd=python3.10 ./webui.sh --exit")
        else:
            first = _join(_cd(dest), "webui.bat --exit")
        steps.append({"title": tr("Primera ejecución (instala todo)", en="First run (installs everything)",
                                  de="Erster Start (installiert alles)",
                                  fr="Premier lancement (installe tout)",
                                  ja="初回実行(すべてインストール)", pt="Primeira execução (instala tudo)"),
                      "text": tr("Crea el entorno de Python y descarga PyTorch y el resto de las piezas, pero sin abrir "
                                 "el programa (por eso <code>--exit</code>). Tarda bastante y baja varios GB: dejalo "
                                 "terminar.",
                                 en="It creates the Python environment and downloads PyTorch and the rest of the "
                                    "pieces, but without opening the program (hence <code>--exit</code>). It takes a "
                                    "long time and downloads several GB: let it finish.",
                                 de="Es erstellt die Python-Umgebung und lädt PyTorch und die übrigen Komponenten "
                                    "herunter, öffnet das Programm aber nicht (daher <code>--exit</code>). Es dauert "
                                    "lange und lädt mehrere GB herunter: lass es fertig laufen.",
                                 fr="Cela crée l'environnement Python et télécharge PyTorch et les autres composants, "
                                    "sans ouvrir le programme (d'où <code>--exit</code>). C'est long et télécharge "
                                    "plusieurs Go : laissez-le terminer.",
                                 ja="Pythonの環境を作成し、PyTorchなどの部品をダウンロードしますが、プログラムは開きません"
                                    "(そのため<code>--exit</code>を付けます)。時間がかかり、数GBをダウンロードします。"
                                    "終わるまで待ってください。",
                                 pt="Cria o ambiente do Python e baixa o PyTorch e o resto das peças, mas sem abrir "
                                    "o programa (por isso o <code>--exit</code>). Demora bastante e baixa vários GB: "
                                    "deixe terminar."),
                      "cmd": first})
        steps.append({"title": _t_put_model(),
                      "text": tr("Para generar imágenes hace falta al menos un modelo (checkpoint, archivo "
                                 ".safetensors). Copialo en <code>{p}</code>.",
                                 en="To generate images you need at least one model (checkpoint, a .safetensors "
                                    "file). Copy it into <code>{p}</code>.",
                                 de="Zum Erzeugen von Bildern braucht es mindestens ein Modell (Checkpoint, eine "
                                    ".safetensors-Datei). Kopiere es nach <code>{p}</code>.",
                                 fr="Pour générer des images, il faut au moins un modèle (checkpoint, fichier "
                                    ".safetensors). Copiez-le dans <code>{p}</code>.",
                                 ja="画像を生成するには、モデル(チェックポイント、.safetensorsファイル)が少なくとも1つ必要です。"
                                    "<code>{p}</code>にコピーしてください。",
                                 pt="Para gerar imagens é preciso pelo menos um modelo (checkpoint, arquivo "
                                    ".safetensors). Copie-o para <code>{p}</code>.",
                                 p=os.path.join(dest, "models", "Stable-diffusion")), "cmd": None})
        steps.append({"title": _t_open_it(),
                      "text": tr("Este comando inicia el programa; cuando diga «Running on local URL» abrí "
                                 "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a> en el navegador.",
                                 en="This command starts the program; when it says “Running on local URL” open "
                                    "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a> in your browser.",
                                 de="Dieser Befehl startet das Programm; sobald „Running on local URL“ erscheint, "
                                    "öffne <a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a> im Browser.",
                                 fr="Cette commande démarre le programme ; quand elle affiche « Running on local URL », "
                                    "ouvrez <a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a> dans le navigateur.",
                                 ja="このコマンドでプログラムが起動します。「Running on local URL」と表示されたら、ブラウザで"
                                    "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>を開いてください。",
                                 pt="Este comando inicia o programa; quando aparecer “Running on local URL”, abra "
                                    "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a> no navegador."),
                      "cmd": _join(_cd(dest), "webui.bat" if IS_WIN else "./webui.sh")})
    elif app == "comfyui":
        steps.append(_guide_clone(app, parent))
        pyc = "py -3.12" if IS_WIN else "python3.12"
        vpy = r"venv\Scripts\python" if IS_WIN else "venv/bin/python"
        steps.append({"title": tr("Creá el entorno de Python", en="Create the Python environment",
                                  de="Python-Umgebung erstellen", fr="Créez l'environnement Python",
                                  ja="Pythonの環境を作成する", pt="Crie o ambiente do Python"),
                      "text": tr("Un «entorno virtual» es una carpeta donde queda todo lo del programa, sin mezclarse "
                                 "con el resto de tu compu.",
                                 en="A “virtual environment” is a folder that holds everything for the program, "
                                    "without mixing with the rest of your computer.",
                                 de="Eine „virtuelle Umgebung“ ist ein Ordner, in dem alles für das Programm liegt, "
                                    "ohne sich mit dem Rest deines Computers zu vermischen.",
                                 fr="Un « environnement virtuel » est un dossier qui contient tout ce qui concerne le "
                                    "programme, sans se mélanger au reste de votre ordinateur.",
                                 ja="「仮想環境」は、このプログラムに必要なものをすべてまとめるフォルダで、"
                                    "パソコンのほかの部分と混ざりません。",
                                 pt="Um “ambiente virtual” é uma pasta onde fica tudo do programa, sem se misturar "
                                    "com o resto do seu computador."),
                      "cmd": _join(_cd(dest), "%s -m venv venv" % pyc)})
        steps.append({"title": tr("Instalá PyTorch", en="Install PyTorch", de="PyTorch installieren",
                                  fr="Installez PyTorch", ja="PyTorchをインストールする",
                                  pt="Instale o PyTorch"),
                      "text": (tr("Si tenés una placa NVIDIA, este comando baja la versión con CUDA.",
                                  en="If you have an NVIDIA card, this command downloads the CUDA version.",
                                  de="Wenn du eine NVIDIA-Grafikkarte hast, lädt dieser Befehl die CUDA-Version herunter.",
                                  fr="Si vous avez une carte NVIDIA, cette commande télécharge la version avec CUDA.",
                                  ja="NVIDIAのグラフィックボードをお持ちの場合、このコマンドでCUDA版がダウンロードされます。",
                                  pt="Se você tem uma placa NVIDIA, este comando baixa a versão com CUDA.") if IS_WIN else
                               tr("PyTorch es el motor de IA; en Mac usa la GPU del chip Apple (MPS).",
                                  en="PyTorch is the AI engine; on Mac it uses the Apple chip's GPU (MPS).",
                                  de="PyTorch ist die KI-Engine; auf dem Mac nutzt es die GPU des Apple-Chips (MPS).",
                                  fr="PyTorch est le moteur d'IA ; sur Mac, il utilise le GPU de la puce Apple (MPS).",
                                  ja="PyTorchはAIのエンジンで、MacではAppleチップのGPU(MPS)を使います。",
                                  pt="O PyTorch é o motor de IA; no Mac usa a GPU do chip Apple (MPS).")),
                      "cmd": _join(_cd(dest), "%s -m pip install --upgrade pip" % vpy,
                                   "%s -m pip install torch torchvision torchaudio%s" %
                                   (vpy, " --extra-index-url https://download.pytorch.org/whl/cu124" if IS_WIN else ""))})
        steps.append({"title": tr("Instalá el resto", en="Install the rest", de="Den Rest installieren",
                                  fr="Installez le reste", ja="残りをインストールする",
                                  pt="Instale o resto"),
                      "text": tr("Las demás piezas de ComfyUI.", en="The remaining pieces of ComfyUI.",
                                 de="Die übrigen Komponenten von ComfyUI.",
                                 fr="Les autres composants de ComfyUI.",
                                 ja="ComfyUIのその他の部品です。", pt="As demais peças do ComfyUI."),
                      "cmd": _join(_cd(dest), "%s -m pip install -r requirements.txt" % vpy)})
        steps.append({"title": _t_put_model(),
                      "text": tr("Copiá un checkpoint (.safetensors) en <code>{p}</code>.",
                                 en="Copy a checkpoint (.safetensors) into <code>{p}</code>.",
                                 de="Kopiere einen Checkpoint (.safetensors) nach <code>{p}</code>.",
                                 fr="Copiez un checkpoint (.safetensors) dans <code>{p}</code>.",
                                 ja="チェックポイント(.safetensors)を<code>{p}</code>にコピーしてください。",
                                 pt="Copie um checkpoint (.safetensors) para <code>{p}</code>.",
                                 p=os.path.join(dest, "models", "checkpoints")), "cmd": None})
        steps.append({"title": _t_open_it(),
                      "text": tr("Cuando diga «To see the GUI go to» abrí <a href=\"http://127.0.0.1:8188\">"
                                 "http://127.0.0.1:8188</a>.",
                                 en="When it says “To see the GUI go to” open <a href=\"http://127.0.0.1:8188\">"
                                    "http://127.0.0.1:8188</a>.",
                                 de="Sobald „To see the GUI go to“ erscheint, öffne <a href=\"http://127.0.0.1:8188\">"
                                    "http://127.0.0.1:8188</a>.",
                                 fr="Quand il affiche « To see the GUI go to », ouvrez <a href=\"http://127.0.0.1:8188\">"
                                    "http://127.0.0.1:8188</a>.",
                                 ja="「To see the GUI go to」と表示されたら、<a href=\"http://127.0.0.1:8188\">"
                                    "http://127.0.0.1:8188</a>を開いてください。",
                                 pt="Quando aparecer “To see the GUI go to”, abra <a href=\"http://127.0.0.1:8188\">"
                                    "http://127.0.0.1:8188</a>."),
                      "cmd": _join(_cd(dest), "%s main.py" % vpy)})
    elif app == "kohya":
        steps.append(_guide_clone(app, parent, recursive=True))
        t_run_installer = tr("Ejecutá el instalador", en="Run the installer", de="Installer ausführen",
                             fr="Exécutez l'installateur", ja="インストーラーを実行する",
                             pt="Execute o instalador")
        t_kohya_open = tr("Cuando diga «Running on local URL» abrí "
                          "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>.",
                          en="When it says “Running on local URL” open "
                             "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>.",
                          de="Sobald „Running on local URL“ erscheint, öffne "
                             "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>.",
                          fr="Quand il affiche « Running on local URL », ouvrez "
                             "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>.",
                          ja="「Running on local URL」と表示されたら、"
                             "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>を開いてください。",
                          pt="Quando aparecer “Running on local URL”, abra "
                             "<a href=\"http://127.0.0.1:7860\">http://127.0.0.1:7860</a>.")
        if IS_MAC:
            steps.append({"title": t_run_installer,
                          "text": tr("Instala Python (con Homebrew), crea el entorno y baja las dependencias. "
                                     "Usa el modo automático: no hace preguntas.",
                                     en="It installs Python (with Homebrew), creates the environment and downloads "
                                        "the dependencies. It uses automatic mode: it asks no questions.",
                                     de="Es installiert Python (mit Homebrew), erstellt die Umgebung und lädt die "
                                        "Abhängigkeiten herunter. Es nutzt den automatischen Modus: Es stellt keine Fragen.",
                                     fr="Il installe Python (avec Homebrew), crée l'environnement et télécharge les "
                                        "dépendances. Il utilise le mode automatique : il ne pose aucune question.",
                                     ja="Python(Homebrew経由)をインストールし、環境を作成して、依存関係をダウンロードします。"
                                        "自動モードを使うので、質問はされません。",
                                     pt="Instala o Python (com o Homebrew), cria o ambiente e baixa as dependências. "
                                        "Usa o modo automático: não faz perguntas."),
                          "cmd": _join(_cd(dest), "./setup.sh -n -u -d %s" % _q(dest))})
            steps.append({"title": _t_open_it(), "text": t_kohya_open,
                          "cmd": _join(_cd(dest), "./gui.sh")})
        else:
            steps.append({"title": t_run_installer,
                          "text": tr("Se abre un menú en la ventana negra: elegí <b>Install kohya_ss gui</b> y andá "
                                     "contestando (para una placa NVIDIA, elegí la opción de Torch 2 con CUDA). Si "
                                     "después pregunta por la configuración de «accelerate», elegí: This machine → "
                                     "No distributed training → CPU/GPU según tu placa → bf16 si tu placa lo permite.",
                                     en="A menu opens in the black window: choose <b>Install kohya_ss gui</b> and keep "
                                        "answering (for an NVIDIA card, choose the Torch 2 with CUDA option). If it "
                                        "then asks about the “accelerate” configuration, choose: This machine → "
                                        "No distributed training → CPU/GPU depending on your card → bf16 if your card "
                                        "supports it.",
                                     de="Im schwarzen Fenster öffnet sich ein Menü: Wähle <b>Install kohya_ss gui</b> und "
                                        "beantworte die Fragen (für eine NVIDIA-Grafikkarte wähle die Option Torch 2 mit "
                                        "CUDA). Falls danach die „accelerate“-Konfiguration abgefragt wird, wähle: "
                                        "This machine → No distributed training → CPU/GPU je nach Karte → bf16, wenn "
                                        "deine Karte es unterstützt.",
                                     fr="Un menu s'ouvre dans la fenêtre noire : choisissez <b>Install kohya_ss gui</b> "
                                        "et répondez aux questions (pour une carte NVIDIA, choisissez l'option Torch 2 "
                                        "avec CUDA). Si on vous demande ensuite la configuration d'« accelerate », "
                                        "choisissez : This machine → No distributed training → CPU/GPU selon votre "
                                        "carte → bf16 si votre carte le permet.",
                                     ja="黒いウィンドウにメニューが開きます。<b>Install kohya_ss gui</b>を選んで、質問に答えていきます"
                                        "(NVIDIAのグラフィックボードの場合は、CUDA対応のTorch 2のオプションを選びます)。"
                                        "その後「accelerate」の設定を聞かれたら、This machine → No distributed training → "
                                        "お使いのボードに応じてCPU/GPU → ボードが対応していればbf16、を選んでください。",
                                     pt="Abre-se um menu na janela preta: escolha <b>Install kohya_ss gui</b> e vá "
                                        "respondendo (para uma placa NVIDIA, escolha a opção Torch 2 com CUDA). Se "
                                        "depois perguntar sobre a configuração do “accelerate”, escolha: This machine → "
                                        "No distributed training → CPU/GPU conforme a sua placa → bf16 se a sua placa "
                                        "permitir."),
                          "cmd": _join(_cd(dest), "setup.bat")})
            steps.append({"title": _t_open_it(), "text": t_kohya_open,
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
        apps.append({"id": aid, "name": a["name"], "purpose": _purpose(aid), "installed": installed,
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
        base = os.path.basename(str(cmd[0])).lower()
        code = "E_NOBREW" if base in ("brew", "brew.exe") else "E_NOGIT" if base in ("git", "git.exe") else None
        raise InstallError(tr("No se encontró el programa «{c}». {e}", en="Program “{c}” not found. {e}",
                              de="Programm „{c}“ nicht gefunden. {e}", fr="Programme « {c} » introuvable. {e}",
                              ja="プログラム「{c}」が見つかりません。{e}", pt="Programa “{c}” não encontrado. {e}",
                              c=cmd[0], e=e), code)
    finally:
        job.proc = None
        logf.close()
    if job.cancelled:
        raise Cancelled()
    if code != 0:
        raise InstallError(tr("El comando terminó con error (código {c}).",
                              en="The command failed (code {c}).",
                              de="Der Befehl ist mit einem Fehler beendet worden (Code {c}).",
                              fr="La commande s'est terminée avec une erreur (code {c}).",
                              ja="コマンドがエラーで終了しました(コード {c})。",
                              pt="O comando terminou com erro (código {c}).", c=code))


def _host_ok(url, allowed):
    """https y host exactamente igual (o subdominio) a alguno de los dominios oficiales permitidos."""
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == d or host.endswith("." + d) for d in allowed)


def _download(job, st, url, dest, allowed_hosts=("ollama.com",)):
    """Descarga con progreso (st['frac'] y st['detail']). Sólo https desde los dominios oficiales dados."""
    _log(job, "Descargando %s" % url)
    if not _host_ok(url, allowed_hosts):
        raise InstallError(tr("Descarga rechazada: la dirección no es https de un dominio oficial ({h}).",
                              en="Download rejected: the address is not https from an official domain ({h}).",
                              de="Download abgelehnt: Die Adresse ist kein https einer offiziellen Domain ({h}).",
                              fr="Téléchargement refusé : l'adresse n'est pas en https sur un domaine officiel ({h}).",
                              ja="ダウンロードを拒否しました。アドレスが公式ドメイン({h})のhttpsではありません。",
                              pt="Download rejeitado: o endereço não é https de um domínio oficial ({h}).",
                              h=", ".join(allowed_hosts)))
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Launcher"})
    part = dest + ".part"
    with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
        if not _host_ok(r.geturl(), allowed_hosts):  # una redirección no puede sacarnos del dominio oficial
            raise InstallError(tr("Descarga rechazada: redirección a un sitio no oficial ({u}).",
                                  en="Download rejected: redirect to an unofficial site ({u}).",
                                  de="Download abgelehnt: Weiterleitung zu einer inoffiziellen Seite ({u}).",
                                  fr="Téléchargement refusé : redirection vers un site non officiel ({u}).",
                                  ja="ダウンロードを拒否しました。非公式サイト({u})へのリダイレクトです。",
                                  pt="Download rejeitado: redirecionamento para um site não oficial ({u}).",
                                  u=r.geturl()))
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
                st["detail"] = tr("{a} de {b}", en="{a} of {b}", de="{a} von {b}", fr="{a} sur {b}",
                                  ja="{a} / {b}", pt="{a} de {b}", a=_be().human_bytes(done),
                                  b=_be().human_bytes(total)) if total else _be().human_bytes(done)
    os.replace(part, dest)
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        for blk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blk)
    _log(job, "SHA-256 de la descarga: %s" % h.hexdigest())
    st["frac"] = 1.0
    st["detail"] = _be().human_bytes(os.path.getsize(dest))


def _sig_error(who):
    return tr("La descarga de Ollama no pasó la verificación de firma de {w}; por seguridad no se instaló. "
              "Probá de nuevo o instalá Ollama desde ollama.com.",
              en="The Ollama download failed the signature check by {w}; for safety it was not installed. "
                 "Try again or install Ollama from ollama.com.",
              de="Der Ollama-Download hat die Signaturprüfung von {w} nicht bestanden; aus Sicherheitsgründen "
                 "wurde er nicht installiert. Versuche es erneut oder installiere Ollama von ollama.com.",
              fr="Le téléchargement d'Ollama n'a pas réussi la vérification de signature de {w} ; par sécurité, "
                 "il n'a pas été installé. Réessayez ou installez Ollama depuis ollama.com.",
              ja="Ollamaのダウンロードが{w}の署名検証に合格しなかったため、安全のためインストールしませんでした。"
                 "もう一度試すか、ollama.comからOllamaをインストールしてください。",
              pt="O download do Ollama não passou na verificação de assinatura de {w}; por segurança, não foi "
                 "instalado. Tente de novo ou instale o Ollama em ollama.com.", w=who)


def _t_sig_ok():
    return tr("Firma válida", en="Valid signature", de="Gültige Signatur", fr="Signature valide",
              ja="署名は有効です", pt="Assinatura válida")


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
        raise InstallError(tr("No se encontró Git. Instalalo y volvé a intentar.",
                              en="Git was not found. Install it and try again.",
                              de="Git wurde nicht gefunden. Installiere es und versuche es erneut.",
                              fr="Git est introuvable. Installez-le et réessayez.",
                              ja="Gitが見つかりません。インストールしてからもう一度お試しください。",
                              pt="O Git não foi encontrado. Instale-o e tente de novo."), "E_NOGIT")
    return path


def _step_clone(job, st, app, dest, recursive=False):
    cmd = [_git_exe(), "clone", "--progress"] + (["--recursive"] if recursive else []) + [APPS[app]["repo"], dest]
    job.cleanup.append(dest)
    _run(job, cmd)
    st["detail"] = dest


def _python_for(app):
    py = find_python(APPS[app]["pyver"])
    if not py:
        raise InstallError(tr("No se encontró Python {v}. Instalalo y volvé a intentar.",
                              en="Python {v} was not found. Install it and try again.",
                              de="Python {v} wurde nicht gefunden. Installiere es und versuche es erneut.",
                              fr="Python {v} est introuvable. Installez-le et réessayez.",
                              ja="Python {v}が見つかりません。インストールしてからもう一度お試しください。",
                              pt="O Python {v} não foi encontrado. Instale-o e tente de novo.",
                              v=" / ".join(APPS[app]["pyver"])), "E_NOPY")
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
        raise InstallError(tr("La primera ejecución terminó pero no se creó el entorno (venv). Mirá el log.",
                              en="The first run finished but the environment (venv) was not created. Check the log.",
                              de="Der erste Start ist beendet, aber die Umgebung (venv) wurde nicht erstellt. Sieh im Log nach.",
                              fr="Le premier lancement s'est terminé mais l'environnement (venv) n'a pas été créé. Consultez le journal.",
                              ja="初回実行は終了しましたが、環境(venv)が作成されませんでした。ログを確認してください。",
                              pt="A primeira execução terminou, mas o ambiente (venv) não foi criado. Veja o log."))


def _step_comfy_venv(job, st, dest):
    py = _python_for("comfyui")
    st["detail"] = "Python " + py["version"]
    # instalación nueva: sin venv todavía, se usa la arquitectura nativa de la máquina
    _run(job, pu.arch_prefix(pu.native_arch()) + [py["exe"], "-m", "venv", os.path.join(dest, "venv")], cwd=dest)


def _step_comfy_pip(job, st, dest, args):
    vpy = pu.venv_python(dest)
    if not vpy:
        raise InstallError(tr("No se encontró el entorno de Python (venv) de ComfyUI.",
                              en="The ComfyUI Python environment (venv) was not found.",
                              de="Die Python-Umgebung (venv) von ComfyUI wurde nicht gefunden.",
                              fr="L'environnement Python (venv) de ComfyUI est introuvable.",
                              ja="ComfyUIのPython環境(venv)が見つかりません。",
                              pt="O ambiente do Python (venv) do ComfyUI não foi encontrado."))
    _run(job, pu.arch_prefix(pu.preferred_arch_for(dest)) + [vpy, "-m", "pip"] + args, cwd=dest)


def _step_kohya_setup(job, st, dest):
    if not IS_MAC:
        raise InstallError(tr("La instalación automática de Kohya_ss no está disponible en este sistema.",
                              en="Automatic installation of Kohya_ss is not available on this system.",
                              de="Die automatische Installation von Kohya_ss ist auf diesem System nicht verfügbar.",
                              fr="L'installation automatique de Kohya_ss n'est pas disponible sur ce système.",
                              ja="このシステムではKohya_ssの自動インストールは利用できません。",
                              pt="A instalação automática do Kohya_ss não está disponível neste sistema."))
    # sin -i: usa la configuración de accelerate por defecto, sin preguntar; -u: no abre la interfaz
    _run(job, pu.shell_script_command(os.path.join(dest, "setup.sh"), ["-n", "-u", "-d", dest]), cwd=dest,
         env=dict(os.environ, PYTHONUNBUFFERED="1"))
    if not (Path(dest, "venv").is_dir()):
        raise InstallError(tr("El instalador terminó pero no se creó el entorno (venv). Mirá el log.",
                              en="The installer finished but the environment (venv) was not created. Check the log.",
                              de="Der Installer ist beendet, aber die Umgebung (venv) wurde nicht erstellt. Sieh im Log nach.",
                              fr="L'installateur s'est terminé mais l'environnement (venv) n'a pas été créé. Consultez le journal.",
                              ja="インストーラーは終了しましたが、環境(venv)が作成されませんでした。ログを確認してください。",
                              pt="O instalador terminou, mas o ambiente (venv) não foi criado. Veja o log."))


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
            raise InstallError(tr("Se instaló, pero no logré encontrarlo. Probá «Buscar de nuevo» en unos segundos.",
                                  en="It was installed, but I could not find it. Try “Search again” in a few seconds.",
                                  de="Es wurde installiert, aber ich konnte es nicht finden. Versuche in ein paar Sekunden „Erneut suchen“.",
                                  fr="Il a été installé, mais je n'ai pas réussi à le trouver. Essayez « Rechercher à nouveau » dans quelques secondes.",
                                  ja="インストールは完了しましたが、見つけられませんでした。数秒後に「もう一度検索」をお試しください。",
                                  pt="Foi instalado, mas não consegui encontrá-lo. Tente “Buscar de novo” em alguns segundos."))
    else:
        if not any(same(i["path"], dest) for i in launcher.installs):
            raise InstallError(tr("Se instaló, pero el launcher no lo reconoce como instalación válida.",
                                  en="It was installed, but the launcher does not recognize it as a valid installation.",
                                  de="Es wurde installiert, aber der Launcher erkennt es nicht als gültige Installation.",
                                  fr="Il a été installé, mais le launcher ne le reconnaît pas comme une installation valide.",
                                  ja="インストールは完了しましたが、ランチャーが有効なインストールとして認識しませんでした。",
                                  pt="Foi instalado, mas o launcher não o reconhece como uma instalação válida."))
        job.result_path = os.path.realpath(dest)


def same(a, b):
    return _be().same_path(os.path.realpath(a), os.path.realpath(b))


# ----------------------------------------------------------------------------
# Hints de falla
# ----------------------------------------------------------------------------

# (patrón, función que da el texto en el idioma activo). Los patrones también reconocen los códigos
# estables E_* de InstallError, así el hint funciona aunque el mensaje esté traducido.
_HINTS_INSTALL = [
    (r"Could not resolve host|Temporary failure in name resolution|getaddrinfo|Network is unreachable|"
     r"Connection (reset|refused|timed out)|Read timed out|URLError|RPC failed|early EOF|SSL|"
     r"Failed to establish a new connection|timed out",
     lambda: tr("Parece un problema de internet. Revisá tu conexión y volvé a intentar.",
                en="This looks like an internet problem. Check your connection and try again.",
                de="Das sieht nach einem Internetproblem aus. Prüfe deine Verbindung und versuche es erneut.",
                fr="Cela ressemble à un problème d'internet. Vérifiez votre connexion et réessayez.",
                ja="インターネットの問題のようです。接続を確認して、もう一度お試しください。",
                pt="Parece um problema de internet. Verifique sua conexão e tente de novo.")),
    (r"No space left on device|ENOSPC|not enough space|espacio en disco|There is not enough space|E_NOSPACE",
     lambda: tr("Te quedaste sin espacio en el disco. Liberá espacio (o elegí otra carpeta u otro disco) y volvé a intentar.",
                en="You ran out of disk space. Free up space (or choose another folder or disk) and try again.",
                de="Dein Speicherplatz ist aufgebraucht. Schaffe Platz (oder wähle einen anderen Ordner oder ein anderes Laufwerk) und versuche es erneut.",
                fr="Vous n'avez plus d'espace disque. Libérez de l'espace (ou choisissez un autre dossier ou un autre disque) et réessayez.",
                ja="ディスクの空き容量がなくなりました。空き容量を確保するか、別のフォルダやディスクを選んで、もう一度お試しください。",
                pt="O espaço em disco acabou. Libere espaço (ou escolha outra pasta ou outro disco) e tente de novo.")),
    (r"git: command not found|No se encontró Git|is not recognized as an internal or external command.*git|"
     r"xcode-select: note|Command Line Tools|E_NOGIT",
     lambda: tr("Falta Git. Instalalo (en Mac: xcode-select --install; en Windows: winget install Git.Git) y volvé a intentar.",
                en="Git is missing. Install it (on Mac: xcode-select --install; on Windows: winget install Git.Git) and try again.",
                de="Git fehlt. Installiere es (auf dem Mac: xcode-select --install; unter Windows: winget install Git.Git) und versuche es erneut.",
                fr="Git est absent. Installez-le (sur Mac : xcode-select --install ; sous Windows : winget install Git.Git) et réessayez.",
                ja="Gitがありません。インストール(Macではxcode-select --install、Windowsではwinget install Git.Git)してから、もう一度お試しください。",
                pt="Falta o Git. Instale-o (no Mac: xcode-select --install; no Windows: winget install Git.Git) e tente de novo.")),
    (r"brew: command not found|No se encontró el programa «brew»|E_NOBREW",
     lambda: tr("Falta Homebrew. Instalalo desde la Terminal (botón «Abrir Terminal») y volvé a intentar.",
                en="Homebrew is missing. Install it from the Terminal (“Open Terminal” button) and try again.",
                de="Homebrew fehlt. Installiere es im Terminal (Schaltfläche „Terminal öffnen“) und versuche es erneut.",
                fr="Homebrew est absent. Installez-le depuis le Terminal (bouton « Ouvrir le Terminal ») et réessayez.",
                ja="Homebrewがありません。ターミナル(「ターミナルを開く」ボタン)からインストールして、もう一度お試しください。",
                pt="Falta o Homebrew. Instale-o pelo Terminal (botão “Abrir Terminal”) e tente de novo.")),
    (r"Permission denied|PermissionError|Access is denied|Operation not permitted",
     lambda: tr("Faltan permisos para escribir en esa carpeta. Elegí una carpeta tuya (por ejemplo dentro de tu carpeta personal) "
                "y volvé a intentar.",
                en="There are no permissions to write to that folder. Choose a folder of your own (for example inside your "
                   "home folder) and try again.",
                de="Es fehlen die Berechtigungen zum Schreiben in diesen Ordner. Wähle einen eigenen Ordner (zum Beispiel "
                   "in deinem Benutzerordner) und versuche es erneut.",
                fr="Les autorisations d'écriture dans ce dossier manquent. Choisissez un dossier à vous (par exemple dans "
                   "votre dossier personnel) et réessayez.",
                ja="このフォルダへの書き込み権限がありません。ご自分のフォルダ(たとえばホームフォルダ内)を選んで、"
                   "もう一度お試しください。",
                pt="Faltam permissões para gravar nessa pasta. Escolha uma pasta sua (por exemplo, dentro da sua pasta "
                   "pessoal) e tente de novo.")),
    (r"No se encontró Python|requires Python|Unsupported Python|Could not find a version that satisfies|"
     r"No matching distribution|E_NOPY",
     lambda: tr("No se encontró la versión de Python que pide el programa. Instalala (la guía te dice cuál) y volvé a intentar.",
                en="The Python version the program requires was not found. Install it (the guide tells you which) and try again.",
                de="Die vom Programm benötigte Python-Version wurde nicht gefunden. Installiere sie (die Anleitung sagt dir, welche) und versuche es erneut.",
                fr="La version de Python requise par le programme est introuvable. Installez-la (le guide vous indique laquelle) et réessayez.",
                ja="プログラムが必要とするバージョンのPythonが見つかりません。インストール(どれかはガイドに記載)してから、もう一度お試しください。",
                pt="A versão do Python exigida pelo programa não foi encontrada. Instale-a (o guia diz qual) e tente de novo.")),
]


def hint_for(tail):
    for pat, text in _HINTS_INSTALL:
        if re.search(pat, tail or "", re.I):
            return text()
    return _be().hint_for(tail)


# ----------------------------------------------------------------------------
# Plan de pasos de cada programa
# ----------------------------------------------------------------------------

def _t_step_clone():
    return tr("Descargar el programa (git clone)", en="Download the program (git clone)",
              de="Programm herunterladen (git clone)", fr="Télécharger le programme (git clone)",
              ja="プログラムをダウンロード(git clone)", pt="Baixar o programa (git clone)")


def _t_step_register():
    return tr("Agregarlo al launcher", en="Add it to the launcher", de="Zum Launcher hinzufügen",
              fr="L'ajouter au launcher", ja="ランチャーに追加", pt="Adicionar ao launcher")


def _plan(launcher, app, parent, prereqs, install_prereqs):
    """Lista de (título, función) para este programa."""
    a = APPS[app]
    dest = str(Path(parent) / a["folder"]) if a.get("folder") else None
    plan = []
    to_install = [p for p in prereqs if not p["ok"] and p.get("_install")]
    if install_prereqs and to_install:
        plan.append((tr("Instalar lo que falta ({n})", en="Install what is missing ({n})",
                        de="Fehlendes installieren ({n})", fr="Installer ce qui manque ({n})",
                        ja="不足しているものをインストール({n})", pt="Instalar o que falta ({n})",
                        n=", ".join(p["name"] for p in to_install)),
                     lambda j, st: _step_prereqs(j, st, to_install)))
    if app in ("a1111", "forge"):
        plan.append((_t_step_clone(), lambda j, st: _step_clone(j, st, app, dest)))
        plan.append((tr("Primera ejecución: instalar todo (puede tardar mucho)",
                        en="First run: install everything (can take a long time)",
                        de="Erster Start: alles installieren (kann lange dauern)",
                        fr="Premier lancement : tout installer (peut être long)",
                        ja="初回実行: すべてインストール(時間がかかることがあります)",
                        pt="Primeira execução: instalar tudo (pode demorar muito)"),
                     lambda j, st: _step_webui_firstrun(j, st, app, dest, parent)))
    elif app == "comfyui":
        win_nvidia = IS_WIN and bool(shutil.which("nvidia-smi"))
        torch = ["install", "torch", "torchvision", "torchaudio"] + \
                (["--extra-index-url", "https://download.pytorch.org/whl/cu124"] if win_nvidia else [])
        plan.append((_t_step_clone(), lambda j, st: _step_clone(j, st, app, dest)))
        plan.append((tr("Crear el entorno de Python", en="Create the Python environment",
                        de="Python-Umgebung erstellen", fr="Créer l'environnement Python",
                        ja="Pythonの環境を作成", pt="Criar o ambiente do Python"),
                     lambda j, st: _step_comfy_venv(j, st, dest)))
        plan.append((tr("Actualizar pip", en="Update pip", de="pip aktualisieren", fr="Mettre à jour pip",
                        ja="pipを更新", pt="Atualizar o pip"),
                     lambda j, st: _step_comfy_pip(j, st, dest, ["install", "--upgrade", "pip"])))
        plan.append((tr("Instalar PyTorch (varios GB)", en="Install PyTorch (several GB)",
                        de="PyTorch installieren (mehrere GB)", fr="Installer PyTorch (plusieurs Go)",
                        ja="PyTorchをインストール(数GB)", pt="Instalar o PyTorch (vários GB)"),
                     lambda j, st: _step_comfy_pip(j, st, dest, torch)))
        plan.append((tr("Instalar el resto de las piezas", en="Install the remaining pieces",
                        de="Übrige Komponenten installieren", fr="Installer les autres composants",
                        ja="残りの部品をインストール", pt="Instalar o resto das peças"),
                     lambda j, st: _step_comfy_pip(j, st, dest, ["install", "-r", "requirements.txt"])))
    elif app == "kohya":
        plan.append((_t_step_clone(), lambda j, st: _step_clone(j, st, app, dest, True)))
        plan.append((tr("Instalar con setup.sh (puede tardar mucho)",
                        en="Install with setup.sh (can take a long time)",
                        de="Mit setup.sh installieren (kann lange dauern)",
                        fr="Installer avec setup.sh (peut être long)",
                        ja="setup.shでインストール(時間がかかることがあります)",
                        pt="Instalar com o setup.sh (pode demorar muito)"),
                     lambda j, st: _step_kohya_setup(j, st, dest)))
    plan.append((_t_step_register(), lambda j, st: _step_register(j, st, launcher, dest)))
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
            current["state"], current["detail"] = "failed", tr("Cancelado", en="Cancelled", de="Abgebrochen",
                                                              fr="Annulé", ja="キャンセルしました",
                                                              pt="Cancelado")
        job.state = "cancelled"
    except Exception as e:  # noqa: BLE001 - cualquier falla se muestra al usuario
        if current:
            current["state"] = "failed"
            current["detail"] = str(e)[:300]
        tail = be._log_tail(job.log)
        job.error = tr("Falló el paso «{t}»: {e}", en="Step “{t}” failed: {e}",
                       de="Der Schritt „{t}“ ist fehlgeschlagen: {e}", fr="L'étape « {t} » a échoué : {e}",
                       ja="手順「{t}」に失敗しました: {e}", pt="Falhou a etapa “{t}”: {e}",
                       t=current["title"] if current else "?", e=e)
        job.hint = hint_for(tail + "\n" + str(e) + " " + (getattr(e, "code", None) or ""))
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
        raise InstallError(_t_unknown_app(app))
    a = APPS[app]
    auto, note = _auto_support(app)
    if not auto:
        raise InstallError(tr("Este programa no se puede instalar automáticamente acá. {n}",
                              en="This program cannot be installed automatically here. {n}",
                              de="Dieses Programm kann hier nicht automatisch installiert werden. {n}",
                              fr="Ce programme ne peut pas être installé automatiquement ici. {n}",
                              ja="このプログラムはここでは自動インストールできません。{n}",
                              pt="Este programa não pode ser instalado automaticamente aqui. {n}", n=note))
    with _jobs_lock:
        for j in JOBS.values():
            if j.state == "running":
                raise InstallError(tr("Ya hay una instalación en curso ({n}). Esperá a que termine o cancelala.",
                                      en="An installation is already in progress ({n}). Wait for it to finish or cancel it.",
                                      de="Es läuft bereits eine Installation ({n}). Warte, bis sie fertig ist, oder brich sie ab.",
                                      fr="Une installation est déjà en cours ({n}). Attendez qu'elle se termine ou annulez-la.",
                                      ja="すでにインストールが進行中です({n})。終了するまで待つか、キャンセルしてください。",
                                      pt="Já há uma instalação em andamento ({n}). Espere terminar ou cancele.",
                                      n=APPS[j.app]["name"]))

    explicit = b.get("parent")
    parent = os.path.expanduser(explicit or default_parent(launcher))
    dest = str(Path(parent) / a["folder"]) if a.get("folder") else None
    if dest and os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise InstallError(tr("La carpeta de destino ya existe y no está vacía: {d}. Elegí otra carpeta.",
                              en="The destination folder already exists and is not empty: {d}. Choose another folder.",
                              de="Der Zielordner existiert bereits und ist nicht leer: {d}. Wähle einen anderen Ordner.",
                              fr="Le dossier de destination existe déjà et n'est pas vide : {d}. Choisissez un autre dossier.",
                              ja="インストール先のフォルダはすでに存在し、空ではありません: {d}。別のフォルダを選んでください。",
                              pt="A pasta de destino já existe e não está vazia: {d}. Escolha outra pasta.", d=dest))
    base = _nearest_existing(parent if dest else (Path("/Applications") if IS_MAC else HOME))
    if dest and not os.access(base, os.W_OK):
        raise InstallError(tr("No tengo permiso para escribir en {d}. Elegí otra carpeta.",
                              en="I do not have permission to write to {d}. Choose another folder.",
                              de="Ich habe keine Berechtigung, in {d} zu schreiben. Wähle einen anderen Ordner.",
                              fr="Je n'ai pas l'autorisation d'écrire dans {d}. Choisissez un autre dossier.",
                              ja="{d}への書き込み権限がありません。別のフォルダを選んでください。",
                              pt="Não tenho permissão para gravar em {d}. Escolha outra pasta.", d=base))
    need = a["requirements"]["disk_gb"]
    free = shutil.disk_usage(base).free / 1024 ** 3
    if free < need:
        raise InstallError(tr("No hay espacio suficiente: hacen falta unos {need} GB libres y hay {free} GB en ese disco.",
                              en="Not enough space: about {need} GB free are needed and there are {free} GB on that disk.",
                              de="Nicht genug Speicherplatz: Es werden etwa {need} GB frei benötigt, auf diesem Laufwerk sind {free} GB frei.",
                              fr="Espace insuffisant : il faut environ {need} Go libres et il y a {free} Go sur ce disque.",
                              ja="空き容量が足りません。約{need}GBの空きが必要ですが、このディスクには{free}GBしかありません。",
                              pt="Não há espaço suficiente: são necessários cerca de {need} GB livres e há {free} GB nesse disco.",
                              need=need, free="%.1f" % free), "E_NOSPACE")

    prereqs = prereqs_for(app)
    for p in prereqs:
        if p["ok"]:
            continue
        if not p["auto"]:
            msg = tr("Falta {n}. {h}", en="{n} is missing. {h}", de="{n} fehlt. {h}", fr="{n} est absent. {h}",
                     ja="{n}がありません。{h}", pt="Falta {n}. {h}", n=p["name"], h=p["how"])
            if p.get("terminal_cmd"):
                msg += tr(" Podés abrir la terminal con el botón correspondiente o correr: {c}",
                          en=" You can open the terminal with the corresponding button or run: {c}",
                          de=" Du kannst das Terminal mit der entsprechenden Schaltfläche öffnen oder ausführen: {c}",
                          fr=" Vous pouvez ouvrir le terminal avec le bouton correspondant ou exécuter : {c}",
                          ja="対応するボタンでターミナルを開くか、次を実行してください: {c}",
                          pt=" Você pode abrir o terminal com o botão correspondente ou executar: {c}",
                          c=p["terminal_cmd"])
            raise InstallError(msg)
        if not b.get("install_prereqs"):
            raise InstallError(tr("Falta {n}. Tildá «instalar lo que falta» para que lo instale yo, o instalalo vos: {h}",
                                  en="{n} is missing. Check “install what is missing” so I install it, or install it yourself: {h}",
                                  de="{n} fehlt. Aktiviere „Fehlendes installieren“, damit ich es installiere, oder installiere es selbst: {h}",
                                  fr="{n} est absent. Cochez « installer ce qui manque » pour que je l'installe, ou installez-le vous-même : {h}",
                                  ja="{n}がありません。「不足しているものをインストール」にチェックを入れて私にインストールさせるか、ご自分でインストールしてください: {h}",
                                  pt="Falta {n}. Marque “instalar o que falta” para eu instalar, ou instale você mesmo: {h}",
                                  n=p["name"], h=p.get("terminal_cmd") or p["how"]))

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


def _t_no_app():
    return tr("El instalador descargado no trae Ollama.app.",
              en="The downloaded installer does not contain Ollama.app.",
              de="Der heruntergeladene Installer enthält keine Ollama.app.",
              fr="L'installateur téléchargé ne contient pas Ollama.app.",
              ja="ダウンロードしたインストーラーにOllama.appが含まれていません。",
              pt="O instalador baixado não contém o Ollama.app.")


def _t_unknown_app(app):
    return tr("Programa desconocido: {a}", en="Unknown program: {a}", de="Unbekanntes Programm: {a}",
              fr="Programme inconnu : {a}", ja="不明なプログラム: {a}", pt="Programa desconhecido: {a}", a=app)


def _ollama_cleanup_ctx(ctx):
    if ctx.get("mounted") and ctx.get("mnt"):
        pu.run(["hdiutil", "detach", ctx["mnt"], "-force"], 60)
        ctx["mounted"] = False
    shutil.rmtree(ctx["tmp"], ignore_errors=True)


def _ollama_plan(ctx, launcher):
    steps = _ollama_steps_ctx(ctx)
    steps.append((_t_step_register(), lambda j, st: _step_register(j, st, launcher, None)))
    return steps


def _t_dl_ollama():
    return tr("Descargar Ollama", en="Download Ollama", de="Ollama herunterladen", fr="Télécharger Ollama",
              ja="Ollamaをダウンロード", pt="Baixar o Ollama")


def _t_verifying():
    return tr("Verificando firma…", en="Verifying signature…", de="Signatur wird geprüft …",
              fr="Vérification de la signature…", ja="署名を検証中…", pt="Verificando assinatura…")


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
                raise InstallError(_t_no_app())
            if not _verify_mac_signature(j, src):
                raise InstallError(_sig_error("Apple"))
            st["detail"] = _t_sig_ok()

        def copy(j, st):
            src = os.path.join(ctx["mnt"], "Ollama.app")
            if not os.path.isdir(src):
                raise InstallError(_t_no_app())
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

        return [(_t_dl_ollama(), download),
                (tr("Montar el instalador", en="Mount the installer", de="Installer einbinden",
                    fr="Monter l'installateur", ja="インストーラーをマウント", pt="Montar o instalador"), mount),
                (_t_verifying(), verify),
                (tr("Copiar Ollama a Aplicaciones", en="Copy Ollama to Applications",
                    de="Ollama nach Programme kopieren", fr="Copier Ollama dans Applications",
                    ja="Ollamaをアプリケーションにコピー", pt="Copiar o Ollama para Aplicativos"), copy),
                (tr("Cerrar el instalador", en="Close the installer", de="Installer schließen",
                    fr="Fermer l'installateur", ja="インストーラーを閉じる", pt="Fechar o instalador"), detach)]

    def download(j, st):
        _download(j, st, "https://ollama.com/download/OllamaSetup.exe", os.path.join(tmp, "OllamaSetup.exe"))

    def verify(j, st):
        if not _verify_win_signature(j, os.path.join(tmp, "OllamaSetup.exe")):
            raise InstallError(_sig_error("Windows (Authenticode)"))
        st["detail"] = _t_sig_ok()

    def run_setup(j, st):
        _run(j, [os.path.join(tmp, "OllamaSetup.exe"), "/VERYSILENT", "/NORESTART"])
        local = os.environ.get("LOCALAPPDATA")
        j.result_path = str(Path(local) / "Programs" / "Ollama") if local else None

    return [(_t_dl_ollama(), download), (_t_verifying(), verify),
            (tr("Instalar Ollama", en="Install Ollama", de="Ollama installieren", fr="Installer Ollama",
                ja="Ollamaをインストール", pt="Instalar o Ollama"), run_setup)]


def status(app):
    job = JOBS.get(app)
    if not job:
        raise InstallError(tr("No hay una instalación de {a} en esta sesión.",
                              en="There is no installation of {a} in this session.",
                              de="In dieser Sitzung gibt es keine Installation von {a}.",
                              fr="Il n'y a pas d'installation de {a} dans cette session.",
                              ja="このセッションには{a}のインストールはありません。",
                              pt="Não há instalação de {a} nesta sessão.", a=app))
    return job.to_dict()


def cancel(app):
    job = JOBS.get(app)
    if not job or job.state != "running":
        raise InstallError(tr("No hay una instalación en curso de ese programa.",
                              en="There is no installation in progress for that program.",
                              de="Für dieses Programm läuft keine Installation.",
                              fr="Aucune installation n'est en cours pour ce programme.",
                              ja="そのプログラムのインストールは進行中ではありません。",
                              pt="Não há instalação em andamento desse programa."))
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
        raise InstallError(_t_unknown_app(app))
    p = next((x for x in prereqs_for(app) if x["id"] == pid), None)
    if not p or not p.get("terminal_cmd"):
        raise InstallError(tr("Ese requisito no tiene un comando para la terminal.",
                              en="That requirement has no terminal command.",
                              de="Für diese Voraussetzung gibt es keinen Terminal-Befehl.",
                              fr="Ce prérequis n'a pas de commande pour le terminal.",
                              ja="この要件にはターミナル用のコマンドがありません。",
                              pt="Esse requisito não tem um comando para o terminal."))
    cmd = p["terminal_cmd"]
    if IS_WIN:
        args = cmd.split(" ")
    else:
        done = tr("Listo. Ya podés cerrar esta ventana y volver a AI Launcher.",
                  en="Done. You can now close this window and go back to AI Launcher.",
                  de="Fertig. Du kannst dieses Fenster jetzt schließen und zu AI Launcher zurückkehren.",
                  fr="Terminé. Vous pouvez fermer cette fenêtre et revenir à AI Launcher.",
                  ja="完了しました。このウィンドウを閉じて、AI Launcherに戻ってください。",
                  pt="Pronto. Você já pode fechar esta janela e voltar ao AI Launcher.")
        args = ["/bin/bash", "-c", cmd + "; echo; echo " + shlex.quote(done)]
    pu.open_terminal(args, "AI Launcher: " + p["name"])
    return {"ok": True}
