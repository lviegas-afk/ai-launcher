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


def _start_sd(launcher, path, **kw):
    """Inicia una app de imagen; si el backend igual devuelve un conflicto, lo muestra como error."""
    r = launcher.start_sd(path, **kw)
    if isinstance(r, dict) and r.get("conflict"):
        raise RuntimeError("No se pudo iniciar: ya hay otra app de imagen abierta o el puerto está ocupado.")
    return r


def _start_item(path, launcher, hb):
    """Ítem "Iniciar": si hay otra app de imagen abierta o el puerto está ocupado, pide confirmación."""
    try:
        plan = launcher.start_plan(path)
    except Exception:
        plan = None
    if not plan or not (plan.get("conflicts") or plan.get("port_taken")):
        return Item("    Iniciar", lambda p=path: _start_sd(launcher, p))
    port = plan.get("suggested_port")
    conflicts = plan.get("conflicts") or []
    if conflicts:
        run = ", ".join("%s (%s de memoria)" % (c["label"], hb(c.get("rss"))) for c in conflicts)
        msg = ("Ahora está abierto: %s.\n\nNo te lo recomiendo: las dos apps compiten por la misma memoria "
               "(en Mac, la RAM y la memoria de la placa de video son la misma), así que todo puede ponerse "
               "muy lento o fallar." % run)
        if any(c.get("training") for c in conflicts):
            msg += ("\n\n⚠️ Hay un entrenamiento de LoRA en curso. Si abrís otra app ahora, "
                    "el entrenamiento podría fallar y perderías el progreso.")
        if port:
            msg += "\n\nSi igual la abrís, va a usar el puerto %d en lugar del de siempre." % port
        title = "Ya hay otra app de imagen abierta"
    else:
        msg = "El puerto habitual está ocupado por otro programa."
        if port:
            msg += " Si seguís, la app va a abrirse en el puerto %d." % port
        title = "El puerto está ocupado"
    action = lambda p=path, n=port: _start_sd(launcher, p, port=n, force=True)
    return Item("    Iniciar (en :%d)" % port if port else "    Iniciar", action,
                confirm=(title, msg), ok_label="Abrir igual")


def build_menu(d, launcher, backend, show_window, quit_app):
    hb = backend.human_bytes
    items = []
    o = d["ollama"]

    # --- Ollama
    if o["up"]:
        items.append(Item("🟢 Ollama · %s" % o["url"].replace("http://", "")))
    elif o["running"]:
        items.append(Item("🟡 Ollama · iniciando…"))
    elif o.get("failure"):
        items.append(Item("🔴 Ollama · no pudo iniciar"))
        items.append(Item("    Ver por qué…", lambda: show_window("ollama"), sync=True))
    elif launcher.ollama.get("found"):
        items.append(Item("⚪️ Ollama · detenido"))
    else:
        items.append(Item("⚪️ Ollama · no instalado", lambda: show_window("ollama")))
    if o["up"] or o["running"]:
        items.append(Item("    Detener Ollama", lambda: launcher.stop_service("ollama"),
                          confirm=("Detener Ollama", "Se liberan de memoria los modelos cargados.")))
    elif launcher.ollama.get("found"):
        items.append(Item("    Iniciar Ollama", launcher.start_ollama))
    if o["up"]:
        loaded_names = {m["name"] for m in o["loaded"]}
        items.append(Item("    En memoria (%d)" % len(o["loaded"]), children=[
            Item("Descargar %s  (%s)" % (m["name"], hb(m.get("size"))),
                 (lambda n=m["name"]: launcher.ollama_unload(n))) for m in o["loaded"]
        ] or [Item("Ningún modelo cargado")]))
        if o["installed"]:
            items.append(Item("    Cargar modelo", children=[
                Item(("✓ " if m["name"] in loaded_names else "") + m["name"],
                     (lambda n=m["name"]: launcher.ollama_load(n))) for m in o["installed"]]))
            items.append(Item("    Chat en terminal", children=[
                Item(m["name"], (lambda n=m["name"]: launcher.ollama_chat_terminal(n))) for m in o["installed"]]))
    items.append(SEP)

    # --- Stable Diffusion
    if not d["sd"]:
        items.append(Item("⚪️ Apps de imagen · no encontradas", lambda: show_window("sd")))
    for s in d["sd"]:
        name = s["label"] if len(d["sd"]) == 1 else "%s (%s)" % (s["label"], s["name"])
        name = name.replace("Stable Diffusion WebUI", "SD WebUI")
        icon = {"kohya": "🏋️", "comfyui": "🧩"}.get(s.get("kind"), "🎨")
        if s["up"]:
            detail = "entrenando" if s.get("training") else ":%d" % s["port"]
            items.append(Item("🟢 %s %s · %s" % (icon, name, detail)))
        elif s["running"]:
            items.append(Item("🟡 %s %s · iniciando…" % (icon, name)))
        elif s.get("failure"):
            items.append(Item("🔴 %s %s · no pudo iniciar" % (icon, name)))
            items.append(Item("    Ver por qué…", lambda: show_window("sd"), sync=True))
        else:
            items.append(Item("⚪️ %s %s · detenido" % (icon, name)))
        if s["running"]:
            if s["up"]:
                items.append(Item("    Abrir en el navegador", (lambda u=s["url"]: pu.open_target(u))))
            if (s.get("api") or {}).get("checkpoint"):
                items.append(Item("    Descargar checkpoint de memoria",
                                  (lambda p=s["path"]: launcher.sd_call(p, "unload"))))
            items.append(Item("    Detener", (lambda p=s["path"]: launcher.stop_service("sd", p)),
                              confirm=("Detener Stable Diffusion", "Si estás generando una imagen, se pierde.")))
        else:
            items.append(_start_item(s["path"], launcher, hb))
    items.append(SEP)

    # --- Procesos y memoria
    procs = d["processes"]
    items.append(Item("Procesos (%d)" % len(procs), children=[
        Item("Detener %s — %s · PID %d" % (p["label"], hb(p["rss"]), p["pid"]),
             (lambda pid=p["pid"]: backend.kill_tree(pid)),
             confirm=("Detener %s" % p["label"], "PID %d · %s de memoria. Se cierran también sus subprocesos."
                      % (p["pid"], hb(p["rss"]))))
        for p in procs] or [Item("Nada corriendo")]))
    mem = d["memory"]
    items.append(Item("Memoria: %s / %s" % (hb(mem["used"]), hb(mem["total"]))))
    for name, pl in d["pulls"].items():
        if not pl.get("done"):
            pct = " %d%%" % (pl["completed"] * 100 // pl["total"]) if pl.get("total") else ""
            items.append(Item("⬇︎ %s%s" % (name, pct)))
    items.append(SEP)
    items.append(Item("Abrir AI Launcher…", lambda: show_window(), sync=True))
    items.append(Item("Salir de AI Launcher", quit_app, sync=True))
    return items


def menu_signature(d):
    """Cambia sólo cuando cambia algo visible en el menú (para no reconstruirlo de más)."""
    return repr([
        d["ollama"]["up"], d["ollama"]["running"], bool(d["ollama"].get("failure")), [m["name"] for m in d["ollama"]["loaded"]],
        [m["name"] for m in d["ollama"]["installed"]],
        [(s["path"], s["running"], s["up"], bool(s.get("failure")), (s.get("api") or {}).get("checkpoint")) for s in d["sd"]],
        [(p["pid"], p["label"], round(p["rss"] / 2 ** 28)) for p in d["processes"]],
        round(d["memory"]["used"] / 2 ** 28),
        [(k, v.get("done"), (v.get("completed", 0) * 20 // v["total"]) if v.get("total") else 0)
         for k, v in d["pulls"].items()],
    ])


def status_text(d):
    on = []
    if d["ollama"]["up"] or d["ollama"]["running"]:
        on.append("Ollama")
    if any(s["running"] for s in d["sd"]):
        on.append("Stable Diffusion")
    return ("Corriendo: " + ", ".join(on)) if on else "Nada corriendo"


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
        return rumps.alert(title, msg, ok=ok_label or "Confirmar", cancel="Cancelar") == 1

    class MenuBar(rumps.App):
        def __init__(self):
            super().__init__(WINDOW_TITLE, title=None if menu_icon else "AI", quit_button=None,
                             icon=str(menu_icon) if menu_icon else None, template=True)
            self.sig = None
            self.menu = [rumps.MenuItem("Cargando…")]
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
                act = (lambda _icon, _item, it=it: run_action(it, lambda t, m, ok=None: pu.native_confirm(t, m, ok=ok or "Aceptar"))) if it.action else None
                out.append(pystray.MenuItem(it.title, act, enabled=it.enabled,
                                            default=(it.title == "Abrir AI Launcher…")))
        return out

    def items():
        d = state["data"]
        if not d:
            return [pystray.MenuItem("Cargando…", None, enabled=False),
                    pystray.MenuItem("Abrir AI Launcher…", lambda *_: show_window(), default=True)]
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
