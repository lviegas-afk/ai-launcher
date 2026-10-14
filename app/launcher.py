#!/usr/bin/env python3
"""AI Launcher: ícono en la barra de menú (macOS) / bandeja (Windows) + ventana.

Modos:
  launcher.py               ícono + servidor local (+ abre la ventana)
  launcher.py --background  ícono sin abrir la ventana (inicio de sesión)
  launcher.py --window URL  sólo la ventana (la lanza el proceso del ícono)
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

_SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(_SRC))

import platform_utils as pu  # noqa: E402
from backend_i18n import tr  # noqa: E402

HERE = pu.resource_dir()  # empaquetado: sys._MEIPASS

WINDOW_TITLE = "AI Launcher"


def asset(name):
    """Íconos: junto al código (app instalada) o en build/ (modo desarrollo)."""
    for d in (HERE, HERE.parent / "build"):
        if (d / name).exists():
            return d / name
    return None


def _set_mac_identity(accessory=False):
    """En macOS el proceso es Python: le ponemos nombre e ícono propios."""
    if not pu.IS_MAC:
        return
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = WINDOW_TITLE
        info["CFBundleDisplayName"] = WINDOW_TITLE
        app = NSApplication.sharedApplication()
        if accessory:
            app.setActivationPolicy_(1)  # sin ícono en el Dock
        elif asset("AppIcon.icns"):
            app.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(str(asset("AppIcon.icns"))))
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Ventana
# ----------------------------------------------------------------------------

def run_window(url):
    try:
        import webview
    except ImportError:
        pu.open_target(url)
        return
    _set_mac_identity()
    parent = os.getppid()

    def watch_parent():
        # si se cierra el proceso del ícono, la ventana también
        import psutil
        while True:
            time.sleep(2)
            if os.getppid() != parent or not psutil.pid_exists(parent):
                os._exit(0)
    threading.Thread(target=watch_parent, daemon=True).start()
    icon = asset("AppIcon.ico") if pu.IS_WIN else None
    webview.create_window(WINDOW_TITLE, url, width=1180, height=820, min_size=(820, 560), text_select=True)
    try:
        webview.start(icon=str(icon) if icon else None)
    except TypeError:  # versiones viejas de pywebview sin `icon`
        webview.start()


# ----------------------------------------------------------------------------
# Menú (igual para macOS y Windows)
# ----------------------------------------------------------------------------

class Item:
    """Entrada de menú independiente del sistema."""

    def __init__(self, title, action=None, children=None, confirm=None, enabled=None, sync=False, ok_label=None):
        self.title = title
        self.sync = sync  # ejecutar en el hilo del menú (abrir ventana, salir)
        self.action = action
        self.children = children
        self.confirm = confirm  # (título, mensaje) o None
        self.ok_label = ok_label  # texto del botón de confirmar (None = el de siempre)
        self.enabled = enabled if enabled is not None else bool(action or children)


SEP = None


def _T_START():
    return tr("Iniciar", "Start", "Starten", "Démarrer", "起動", "Iniciar")


def _T_STOP():
    return tr("Detener", "Stop", "Beenden", "Arrêter", "停止", "Parar")


def _T_NOTHING():
    return tr("Nada corriendo", "Nothing running", "Nichts läuft", "Rien en cours",
              "実行中のものはありません", "Nada em execução")


def _T_OPEN():
    return tr("Abrir AI Launcher…", "Open AI Launcher…", "AI Launcher öffnen …", "Ouvrir AI Launcher…",
              "AI Launcher を開く…", "Abrir o AI Launcher…")


def _T_LOADING():
    return tr("Cargando…", "Loading…", "Lädt …", "Chargement…", "読み込み中…", "Carregando…")


def _T_WHY():
    return tr("Ver por qué…", "See why…", "Warum ansehen …", "Voir pourquoi…", "理由を見る…", "Ver por quê…")


def _T_STARTING():
    return tr("iniciando…", "starting…", "startet …", "démarrage…", "起動中…", "iniciando…")


def _T_STOPPED():
    return tr("detenido", "stopped", "gestoppt", "arrêté", "停止中", "parado")


def _T_FAILED():
    return tr("no pudo iniciar", "failed to start", "Start fehlgeschlagen", "échec du démarrage",
              "起動に失敗しました", "não conseguiu iniciar")


def _start_sd(launcher, path, **kw):
    """Inicia una app de imagen; si el backend igual devuelve un conflicto, lo muestra como error."""
    r = launcher.start_sd(path, **kw)
    if isinstance(r, dict) and r.get("conflict"):
        raise RuntimeError(tr(
            "No se pudo iniciar: ya hay otra app de imagen abierta o el puerto está ocupado.",
            "Could not start: another image app is already open or the port is in use.",
            "Start nicht möglich: Es läuft bereits eine andere Bild-App oder der Port ist belegt.",
            "Démarrage impossible : une autre application d'image est déjà ouverte ou le port est occupé.",
            "起動できません。別の画像アプリがすでに開いているか、ポートが使用中です。",
            "Não foi possível iniciar: já há outro app de imagem aberto ou a porta está ocupada."))
    return r


def _start_item(path, launcher, hb):
    """Ítem "Iniciar": si hay otra app de imagen abierta o el puerto está ocupado, pide confirmación."""
    try:
        plan = launcher.start_plan(path)
    except Exception:
        plan = None
    if not plan or not (plan.get("conflicts") or plan.get("port_taken")):
        return Item("    " + _T_START(), lambda p=path: _start_sd(launcher, p))
    port = plan.get("suggested_port")
    conflicts = plan.get("conflicts") or []
    if conflicts:
        run = ", ".join(tr("{label} ({mem} de memoria)", "{label} ({mem} of memory)",
                           "{label} ({mem} Speicher)", "{label} ({mem} de mémoire)",
                           "{label}(メモリ {mem})", "{label} ({mem} de memória)",
                           label=c["label"], mem=hb(c.get("rss"))) for c in conflicts)
        msg = tr(
            "Ahora está abierto: {run}.\n\nNo te lo recomiendo: las dos apps compiten por la misma memoria "
            "(en Mac, la RAM y la memoria de la placa de video son la misma), así que todo puede ponerse "
            "muy lento o fallar.",
            "Currently open: {run}.\n\nI don't recommend it: the two apps compete for the same memory "
            "(on a Mac, RAM and graphics memory are the same), so everything can become very slow or fail.",
            "Derzeit geöffnet: {run}.\n\nDas empfehle ich nicht: Die beiden Apps konkurrieren um denselben "
            "Speicher (beim Mac sind Arbeitsspeicher und Grafikspeicher identisch), sodass alles sehr langsam "
            "werden oder fehlschlagen kann.",
            "Actuellement ouvert : {run}.\n\nJe ne le recommande pas : les deux applications se disputent la "
            "même mémoire (sur Mac, la RAM et la mémoire graphique sont les mêmes), ce qui peut tout ralentir "
            "fortement ou provoquer des échecs.",
            "現在開いているもの: {run}。\n\nおすすめしません。2つのアプリが同じメモリを奪い合うため"
            "(Mac では RAM とグラフィックスメモリは共通です)、動作が非常に遅くなったり失敗したりする"
            "可能性があります。",
            "No momento está aberto: {run}.\n\nNão recomendo: os dois apps competem pela mesma memória "
            "(no Mac, a RAM e a memória de vídeo são a mesma), então tudo pode ficar muito lento ou falhar.",
            run=run)
        if any(c.get("training") for c in conflicts):
            msg += tr(
                "\n\n⚠️ Hay un entrenamiento de LoRA en curso. Si abrís otra app ahora, "
                "el entrenamiento podría fallar y perderías el progreso.",
                "\n\n⚠️ A LoRA training is in progress. If you open another app now, "
                "the training could fail and you would lose the progress.",
                "\n\n⚠️ Ein LoRA-Training läuft gerade. Wenn du jetzt eine weitere App öffnest, "
                "kann das Training fehlschlagen und der Fortschritt geht verloren.",
                "\n\n⚠️ Un entraînement LoRA est en cours. Si tu ouvres une autre application maintenant, "
                "l'entraînement pourrait échouer et tu perdrais la progression.",
                "\n\n⚠️ LoRA の学習が進行中です。今ほかのアプリを開くと、"
                "学習が失敗して進捗が失われる可能性があります。",
                "\n\n⚠️ Há um treinamento de LoRA em andamento. Se você abrir outro app agora, "
                "o treinamento pode falhar e você perderá o progresso.")
        if port:
            msg += tr(
                "\n\nSi igual la abrís, va a usar el puerto {port} en lugar del de siempre.",
                "\n\nIf you open it anyway, it will use port {port} instead of the usual one.",
                "\n\nWenn du sie trotzdem öffnest, verwendet sie Port {port} statt des üblichen.",
                "\n\nSi tu l'ouvres quand même, elle utilisera le port {port} au lieu du port habituel.",
                "\n\nそれでも開く場合は、いつものポートではなくポート {port} を使用します。",
                "\n\nSe você abrir mesmo assim, ele vai usar a porta {port} em vez da de sempre.",
                port=port)
        title = tr("Ya hay otra app de imagen abierta", "Another image app is already open",
                   "Eine andere Bild-App ist bereits geöffnet", "Une autre application d'image est déjà ouverte",
                   "別の画像アプリがすでに開いています", "Já há outro app de imagem aberto")
    else:
        msg = tr("El puerto habitual está ocupado por otro programa.",
                 "The usual port is in use by another program.",
                 "Der übliche Port wird von einem anderen Programm verwendet.",
                 "Le port habituel est occupé par un autre programme.",
                 "いつものポートは別のプログラムが使用しています。",
                 "A porta habitual está ocupada por outro programa.")
        if port:
            msg += tr(" Si seguís, la app va a abrirse en el puerto {port}.",
                      " If you continue, the app will open on port {port}.",
                      " Wenn du fortfährst, öffnet sich die App auf Port {port}.",
                      " Si tu continues, l'application s'ouvrira sur le port {port}.",
                      " 続行すると、アプリはポート {port} で開きます。",
                      " Se você continuar, o app vai abrir na porta {port}.",
                      port=port)
        title = tr("El puerto está ocupado", "The port is in use", "Der Port ist belegt",
                   "Le port est occupé", "ポートが使用中です", "A porta está ocupada")
    action = lambda p=path, n=port: _start_sd(launcher, p, port=n, force=True)
    return Item("    " + (tr("Iniciar (en :{port})", "Start (on :{port})", "Starten (auf :{port})",
                             "Démarrer (sur :{port})", "起動(:{port})", "Iniciar (em :{port})", port=port)
                   if port else _T_START()), action,
                confirm=(title, msg),
                ok_label=tr("Abrir igual", "Open anyway", "Trotzdem öffnen", "Ouvrir quand même",
                            "それでも開く", "Abrir mesmo assim"))


def build_menu(d, launcher, backend, show_window, quit_app):
    hb = backend.human_bytes
    items = []
    o = d["ollama"]

    # --- Ollama
    if o["up"]:
        items.append(Item("🟢 Ollama · %s" % o["url"].replace("http://", "")))
    elif o["running"]:
        items.append(Item("🟡 Ollama · " + _T_STARTING()))
    elif o.get("failure"):
        items.append(Item("🔴 Ollama · " + _T_FAILED()))
        items.append(Item("    " + _T_WHY(), lambda: show_window("ollama"), sync=True))
    elif launcher.ollama.get("found"):
        items.append(Item("⚪️ Ollama · " + _T_STOPPED()))
    else:
        items.append(Item("⚪️ Ollama · " + tr("no instalado", "not installed", "nicht installiert",
                                             "non installé", "未インストール", "não instalado"),
                          lambda: show_window("ollama")))
    if o["up"] or o["running"]:
        _stop_ollama = tr("Detener Ollama", "Stop Ollama", "Ollama beenden", "Arrêter Ollama",
                          "Ollama を停止", "Parar o Ollama")
        items.append(Item("    " + _stop_ollama, lambda: launcher.stop_service("ollama"),
                          confirm=(_stop_ollama,
                                   tr("Se liberan de memoria los modelos cargados.",
                                      "The loaded models are released from memory.",
                                      "Die geladenen Modelle werden aus dem Speicher entfernt.",
                                      "Les modèles chargés sont libérés de la mémoire.",
                                      "読み込まれているモデルがメモリから解放されます。",
                                      "Os modelos carregados são liberados da memória."))))
    elif launcher.ollama.get("found"):
        items.append(Item("    " + tr("Iniciar Ollama", "Start Ollama", "Ollama starten", "Démarrer Ollama",
                                    "Ollama を起動", "Iniciar o Ollama"), launcher.start_ollama))
    if o["up"]:
        loaded_names = {m["name"] for m in o["loaded"]}
        items.append(Item("    " + tr("En memoria ({n})", "In memory ({n})", "Im Speicher ({n})",
                                    "En mémoire ({n})", "メモリ上 ({n})", "Na memória ({n})",
                                    n=len(o["loaded"])), children=[
            Item(tr("Descargar {name}  ({size})", "Unload {name}  ({size})", "{name} entladen  ({size})",
                    "Décharger {name}  ({size})", "{name} をアンロード  ({size})", "Descarregar {name}  ({size})",
                    name=m["name"], size=hb(m.get("size"))),
                 (lambda n=m["name"]: launcher.ollama_unload(n))) for m in o["loaded"]
        ] or [Item(tr("Ningún modelo cargado", "No model loaded", "Kein Modell geladen", "Aucun modèle chargé",
                      "読み込まれたモデルはありません", "Nenhum modelo carregado"))]))
        if o["installed"]:
            items.append(Item("    " + tr("Cargar modelo", "Load model", "Modell laden", "Charger un modèle",
                                        "モデルを読み込む", "Carregar modelo"), children=[
                Item(("✓ " if m["name"] in loaded_names else "") + m["name"],
                     (lambda n=m["name"]: launcher.ollama_load(n))) for m in o["installed"]]))
            items.append(Item("    " + tr("Chat en terminal", "Chat in terminal", "Chat im Terminal",
                                        "Chat dans le terminal", "ターミナルでチャット", "Chat no terminal"),
                              children=[
                Item(m["name"], (lambda n=m["name"]: launcher.ollama_chat_terminal(n))) for m in o["installed"]]))
    items.append(SEP)

    # --- Stable Diffusion
    if not d["sd"]:
        items.append(Item("⚪️ " + tr("Apps de imagen · no encontradas", "Image apps · not found",
                                    "Bild-Apps · nicht gefunden", "Applications d'image · introuvables",
                                    "画像アプリ · 見つかりません", "Apps de imagem · não encontrados"),
                          lambda: show_window("sd")))
    for s in d["sd"]:
        name = s["label"] if len(d["sd"]) == 1 else "%s (%s)" % (s["label"], s["name"])
        name = name.replace("Stable Diffusion WebUI", "SD WebUI")
        icon = {"kohya": "🏋️", "comfyui": "🧩"}.get(s.get("kind"), "🎨")
        if s["up"]:
            detail = tr("entrenando", "training", "Training läuft", "entraînement", "学習中", "treinando") \
                if s.get("training") else ":%d" % s["port"]
            items.append(Item("🟢 %s %s · %s" % (icon, name, detail)))
        elif s["running"]:
            items.append(Item("🟡 %s %s · %s" % (icon, name, _T_STARTING())))
        elif s.get("failure"):
            items.append(Item("🔴 %s %s · %s" % (icon, name, _T_FAILED())))
            items.append(Item("    " + _T_WHY(), lambda: show_window("sd"), sync=True))
        else:
            items.append(Item("⚪️ %s %s · %s" % (icon, name, _T_STOPPED())))
        if s["running"]:
            if s["up"]:
                items.append(Item("    " + tr("Abrir en el navegador", "Open in browser", "Im Browser öffnen",
                                            "Ouvrir dans le navigateur", "ブラウザで開く", "Abrir no navegador"),
                                  (lambda u=s["url"]: pu.open_target(u))))
            if (s.get("api") or {}).get("checkpoint"):
                items.append(Item("    " + tr("Descargar checkpoint de memoria", "Unload checkpoint from memory",
                                            "Checkpoint aus dem Speicher entladen",
                                            "Décharger le checkpoint de la mémoire",
                                            "チェックポイントをメモリからアンロード",
                                            "Descarregar checkpoint da memória"),
                                  (lambda p=s["path"]: launcher.sd_call(p, "unload"))))
            items.append(Item("    " + _T_STOP(), (lambda p=s["path"]: launcher.stop_service("sd", p)),
                              confirm=(tr("Detener Stable Diffusion", "Stop Stable Diffusion",
                                          "Stable Diffusion beenden", "Arrêter Stable Diffusion",
                                          "Stable Diffusion を停止", "Parar o Stable Diffusion"),
                                       tr("Si estás generando una imagen, se pierde.",
                                          "If you are generating an image, it will be lost.",
                                          "Wenn du gerade ein Bild erzeugst, geht es verloren.",
                                          "Si tu es en train de générer une image, elle sera perdue.",
                                          "画像を生成中の場合、その画像は失われます。",
                                          "Se você estiver gerando uma imagem, ela será perdida."))))
        else:
            items.append(_start_item(s["path"], launcher, hb))
    items.append(SEP)

    # --- Procesos y memoria
    procs = d["processes"]
    items.append(Item(tr("Procesos ({n})", "Processes ({n})", "Prozesse ({n})", "Processus ({n})",
                         "プロセス ({n})", "Processos ({n})", n=len(procs)), children=[
        Item(tr("Detener {label} — {mem} · PID {pid}", "Stop {label} — {mem} · PID {pid}",
                "{label} beenden — {mem} · PID {pid}", "Arrêter {label} — {mem} · PID {pid}",
                "{label} を停止 — {mem} · PID {pid}", "Parar {label} — {mem} · PID {pid}",
                label=p["label"], mem=hb(p["rss"]), pid=p["pid"]),
             (lambda pid=p["pid"]: backend.kill_tree(pid)),
             confirm=(tr("Detener {label}", "Stop {label}", "{label} beenden", "Arrêter {label}",
                         "{label} を停止", "Parar {label}", label=p["label"]),
                      tr("PID {pid} · {mem} de memoria. Se cierran también sus subprocesos.",
                         "PID {pid} · {mem} of memory. Its subprocesses are closed too.",
                         "PID {pid} · {mem} Speicher. Auch die Unterprozesse werden beendet.",
                         "PID {pid} · {mem} de mémoire. Ses sous-processus sont également fermés.",
                         "PID {pid} · メモリ {mem}。サブプロセスも終了します。",
                         "PID {pid} · {mem} de memória. Os subprocessos também são encerrados.",
                         pid=p["pid"], mem=hb(p["rss"]))))
        for p in procs] or [Item(_T_NOTHING())]))
    mem = d["memory"]
    items.append(Item(tr("Memoria: {used} / {total}", "Memory: {used} / {total}", "Speicher: {used} / {total}",
                         "Mémoire : {used} / {total}", "メモリ: {used} / {total}", "Memória: {used} / {total}",
                         used=hb(mem["used"]), total=hb(mem["total"]))))
    for name, pl in d["pulls"].items():
        if not pl.get("done"):
            pct = " %d%%" % (pl["completed"] * 100 // pl["total"]) if pl.get("total") else ""
            items.append(Item(tr("⬇︎ {name}{pct}", "⬇︎ {name}{pct}", "⬇︎ {name}{pct}", "⬇︎ {name}{pct}",
                                 "⬇︎ {name}{pct}", "⬇︎ {name}{pct}", name=name, pct=pct)))
    items.append(SEP)
    items.append(Item(_T_OPEN(), lambda: show_window(), sync=True))
    items.append(Item(tr("Salir de AI Launcher", "Quit AI Launcher", "AI Launcher beenden",
                         "Quitter AI Launcher", "AI Launcher を終了", "Sair do AI Launcher"), quit_app, sync=True))
    return items


def menu_signature(d):
    """Cambia sólo cuando cambia algo visible en el menú (para no reconstruirlo de más)."""
    return repr([
        d["ollama"]["up"], d["ollama"]["running"], bool(d["ollama"].get("failure")), [m["name"] for m in d["ollama"]["loaded"]],
        [m["name"] for m in d["ollama"]["installed"]],
        [(s["path"], s["running"], s["up"], bool(s.get("failure")), (s.get("api") or {}).get("checkpoint")) for s in d["sd"]],
        [(p["pid"], p["label"], round(p["rss"] / 2 ** 28)) for p in d["processes"]],
        round(d["memory"]["used"] / 2 ** 28),
        tr("es", "en", "de", "fr", "ja", "pt"),  # idioma: reconstruir el menú al cambiarlo
        [(k, v.get("done"), (v.get("completed", 0) * 20 // v["total"]) if v.get("total") else 0)
         for k, v in d["pulls"].items()],
    ])


def status_text(d):
    on = []
    if d["ollama"]["up"] or d["ollama"]["running"]:
        on.append("Ollama")
    if any(s["running"] for s in d["sd"]):
        on.append("Stable Diffusion")
    return tr("Corriendo: {list}", "Running: {list}", "Läuft: {list}", "En cours : {list}",
              "実行中: {list}", "Em execução: {list}", list=", ".join(on)) if on else _T_NOTHING()


# ----------------------------------------------------------------------------
# Programa principal
# ----------------------------------------------------------------------------

def main():
    background = "--background" in sys.argv

    import backend
    existing = backend.running_instance()
    if existing:
        # ya hay una instancia: pedirle que muestre la ventana y salir
        if not background:
            port, token = existing
            req = urllib.request.Request("http://127.0.0.1:%d/api/show_window" % port, data=b"{}",
                                         headers={"X-Token": token, "Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=3).read()
            except Exception:
                pass
        return

    launcher = backend.Launcher()
    win = {"proc": None}

    def window_url(view=None):
        return "http://127.0.0.1:%d/?t=%s%s" % (port, backend.TOKEN, "#" + view if view else "")

    def show_window(view=None):
        p = win["proc"]
        if p and p.poll() is None:
            pu.focus_window(p.pid, WINDOW_TITLE)
            return
        win["proc"] = subprocess.Popen(pu.self_command("--window", window_url(view)))

    port = backend.start_server(launcher, on_show_window=lambda: show_window())

    def shutdown():
        if backend.CONFIG.get("stop_on_quit"):
            launcher.stop_started()
        p = win["proc"]
        if p and p.poll() is None:
            p.terminate()
        try:
            backend.STATE_FILE.unlink()
        except OSError:
            pass

    state = {"data": None, "error": None}

    def poll_loop():
        while True:
            try:
                state["data"] = launcher.monitor()
            except Exception:
                pass
            time.sleep(3)
    threading.Thread(target=poll_loop, daemon=True).start()

    def run_action(item, confirm_fn):
        """Ejecuta la acción de un ítem del menú en segundo plano (con confirmación si corresponde)."""
        if item.confirm and not confirm_fn(*item.confirm, item.ok_label):
            return
        if item.sync:
            item.action()
            return

        def work():
            try:
                item.action()
            except Exception as e:
                state["error"] = str(e)
            backend._SCAN_CACHE["t"] = 0
            try:
                state["data"] = launcher.monitor()
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    if not background and backend.CONFIG.get("open_window_on_launch", True):
        show_window()

    if pu.IS_MAC:
        ok = run_tray_mac(state, launcher, backend, show_window, shutdown, run_action)
    elif pu.IS_WIN:
        ok = run_tray_windows(state, launcher, backend, show_window, shutdown, run_action)
    else:
        ok = False
    if not ok:
        # sin ícono de bandeja disponible: la ventana manda; al cerrarla se sale
        if not win["proc"]:
            show_window()
        win["proc"].wait()
        shutdown()


def run_tray_mac(state, launcher, backend, show_window, shutdown, run_action):
    try:
        import rumps
    except ImportError:
        return False
    menu_icon = asset("menubar.png")

    def confirm(title, msg, ok_label=None):
        return rumps.alert(
            title, msg,
            ok=ok_label or tr("Confirmar", "Confirm", "Bestätigen", "Confirmer", "確認", "Confirmar"),
            cancel=tr("Cancelar", "Cancel", "Abbrechen", "Annuler", "キャンセル", "Cancelar")) == 1

    class MenuBar(rumps.App):
        def __init__(self):
            super().__init__(WINDOW_TITLE, title=None if menu_icon else "AI", quit_button=None,
                             icon=str(menu_icon) if menu_icon else None, template=True)
            self.sig = None
            self.menu = [rumps.MenuItem(_T_LOADING())]
            self.timer = rumps.Timer(self.refresh, 1.5)
            self.timer.start()

        def to_rumps(self, items):
            out, seen = [], set()
            for it in items:
                if it is SEP:
                    out.append(None)
                    continue
                title = it.title
                while title in seen:  # rumps indexa por título: los repetidos se pisan
                    title += "​"
                seen.add(title)
                cb = (lambda _, it=it: run_action(it, confirm)) if it.action else None
                mi = rumps.MenuItem(title, callback=cb)
                if it.children:
                    for c in self.to_rumps(it.children):
                        mi.add(c if c is not None else rumps.separator)
                out.append(mi)
            return out

        def refresh(self, _=None):
            if state["error"]:
                msg, state["error"] = state["error"], None
                rumps.alert(WINDOW_TITLE, msg)
            d = state["data"]
            if not d:
                return
            sig = menu_signature(d)
            if sig == self.sig:
                return
            self.sig = sig
            icons = ("🦙" if d["ollama"]["up"] or d["ollama"]["running"] else "") + \
                    ("🎨" if any(s["running"] for s in d["sd"]) else "")
            self.title = icons or (None if menu_icon else "AI")
            items = build_menu(d, launcher, backend, show_window, self.quit)
            self.menu.clear()
            self.menu = self.to_rumps(items)

        def quit(self):
            shutdown()
            rumps.quit_application()

    _set_mac_identity(accessory=True)
    MenuBar().run()
    return True


def run_tray_windows(state, launcher, backend, show_window, shutdown, run_action):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    def make_icon(active):
        base = asset("tray.png")
        img = Image.open(base).convert("RGBA").resize((64, 64)) if base else \
            Image.new("RGBA", (64, 64), (99, 102, 241, 255))
        if active:  # puntito verde cuando algo está corriendo
            ImageDraw.Draw(img).ellipse((40, 40, 62, 62), fill=(34, 197, 94, 255),
                                        outline=(255, 255, 255, 255), width=3)
        return img

    icons = {False: make_icon(False), True: make_icon(True)}
    holder = {"icon": None, "sig": None}

    def quit_app():
        shutdown()
        holder["icon"].stop()

    def to_pystray(items):
        out = []
        for it in items:
            if it is SEP:
                out.append(pystray.Menu.SEPARATOR)
            elif it.children:
                out.append(pystray.MenuItem(it.title, pystray.Menu(*to_pystray(it.children))))
            else:
                act = (lambda _icon, _item, it=it: run_action(it, lambda t, m, ok=None: pu.native_confirm(t, m, ok=ok or tr("Aceptar", "OK", "OK", "OK", "OK", "OK")))) if it.action else None
                out.append(pystray.MenuItem(it.title, act, enabled=it.enabled,
                                            default=(it.title == _T_OPEN())))
        return out

    def items():
        d = state["data"]
        if not d:
            return [pystray.MenuItem(_T_LOADING(), None, enabled=False),
                    pystray.MenuItem(_T_OPEN(), lambda *_: show_window(), default=True)]
        return to_pystray(build_menu(d, launcher, backend, show_window, quit_app))

    icon = pystray.Icon("AI Launcher", icons[False], WINDOW_TITLE, menu=pystray.Menu(lambda: items()))
    holder["icon"] = icon

    def updater():
        while True:
            time.sleep(1.5)
            if state["error"]:
                msg, state["error"] = state["error"], None
                threading.Thread(target=pu.native_alert, args=(WINDOW_TITLE, msg), daemon=True).start()
            d = state["data"]
            if not d:
                continue
            sig = menu_signature(d)
            if sig != holder["sig"]:
                holder["sig"] = sig
                active = d["ollama"]["up"] or d["ollama"]["running"] or any(s["running"] for s in d["sd"])
                icon.icon = icons[bool(active)]
                icon.title = "AI Launcher — " + status_text(d)
                try:
                    icon.update_menu()
                except Exception:
                    pass

    def setup(ic):
        ic.visible = True
        threading.Thread(target=updater, daemon=True).start()

    icon.run(setup=setup)
    return True


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--window":
        run_window(sys.argv[2])
    else:
        main()
