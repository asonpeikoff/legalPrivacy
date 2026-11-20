"""Native desktop app for legalPrivacy: a Tk window that talks to the Controller directly, in this process.

No browser, no web view and no HTTP. Tkinter/ttk only (the standard library); the web dashboard
(legalprivacy/webui.py) stays as the backup front end and can be opened from the File menu.

    legalprivacy-gui            or            python -m legalprivacy.gui

Threads: the Tk main thread never waits on the controller. State is fetched by a worker thread once a
second and slow actions (Start, Stop, checks, imports, Tor, Panic) run in worker threads; their results
come back through a queue that the main thread drains from an `after` poll.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import webbrowser
from dataclasses import asdict

from . import __version__
from . import gui_logic as L

TITLE = "legalPrivacy"
NO_TK_MESSAGE = (
    "The legalPrivacy desktop app needs Tk (Python's built-in GUI toolkit), and this Python does not have it.\n"
    "  Windows / macOS: install Python from python.org (Tk is included).\n"
    "  Debian / Ubuntu: sudo apt install python3-tk\n"
    "  Fedora: sudo dnf install python3-tkinter\n"
    "Opening the web dashboard in your browser instead.")
LOCK_PORT = 47653                                    # loopback port held while the desktop app is open


# ── small drawing helpers ───────────────────────────────────────────────────
def make_logo(tk, size: int, bg: str, fg: str):
    """A simple waveform icon, drawn into a PhotoImage with no image files."""
    img = tk.PhotoImage(width=size, height=size)
    img.put(bg, to=(0, 0, size, size))
    heights = [0.22, 0.45, 0.78, 0.5, 0.92, 0.38, 0.62, 0.3]
    n = len(heights)
    unit = size / (n * 2 + 1)
    for i, h in enumerate(heights):
        x0 = int(round(unit * (2 * i + 1)))
        x1 = max(x0 + 1, int(round(unit * (2 * i + 2))))
        bar = max(2, int(size * 0.8 * h))
        y0 = (size - bar) // 2
        img.put(fg, to=(x0, y0, x1, y0 + bar))
    return img


def claim_instance(port: int = LOCK_PORT):
    """Hold a loopback port while this desktop app is open, so a second copy notices. Returns the socket,
    or None when another copy holds it. (Any other failure is treated as 'no lock available': carry on.)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError as exc:
        s.close()
        import errno
        return None if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1), 10048) else False


def _prefs_path():
    from .paths import app_dir
    return app_dir() / "gui.json"


def load_prefs() -> dict:
    try:
        return json.loads(_prefs_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_prefs(p: dict) -> None:
    try:
        _prefs_path().write_text(json.dumps(p), encoding="utf-8")
    except Exception:
        pass


def build_app_class():
    """Import tkinter lazily so `import legalprivacy.gui` works (and is testable) without Tk."""
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk

    from .gui_tabs import TABS

    class App:
        def __init__(self, root: tk.Tk, ctl, web_port: int = 8765):
            self.root, self.ctl, self.web_port = root, ctl, web_port
            self.q = L.UiQueue()
            self.web = None
            self.state: dict = {}
            self.loading = False
            self.closing = False
            self._state_busy = False
            self._apply_text, self._apply_tone, self._applying, self._apply_err = "", "off", False, None
            prefs = load_prefs()
            self.theme_name = "dark" if prefs.get("theme") == "dark" else "light"
            self.pal = L.PALETTES[self.theme_name]
            self._init_fonts()
            self._style = ttk.Style(root)
            try:
                self._style.theme_use("clam")
            except tk.TclError:
                pass
            root.title(TITLE)
            root.minsize(900, 620)
            root.geometry("1020x720")
            self.logo_big = make_logo(tk, 64, "#1d2733", "#5b9dff")
            self.logo_small = make_logo(tk, 28, "#1d2733", "#5b9dff")
            try:
                root.iconphoto(True, self.logo_big)
            except tk.TclError:
                pass
            self.live = L.LiveApply(ctl, self._collect, self.q.post, self._report, root.after, root.after_cancel)
            self._build()
            self.apply_theme()
            self._bind_keys()
            self.loading = True
            vals = L.profile_values(ctl.profile, ctl.app)
            for t in self.tabs:
                t.load(vals)
            self.loading = False
            self._loaded_vals = vals
            root.protocol("WM_DELETE_WINDOW", self.quit_app)
            self.pump()
            self.tick()

        # -- fonts / theme
        def _init_fonts(self) -> None:
            base = tkfont.nametofont("TkDefaultFont")
            if abs(base.cget("size")) < 10:                # readable by default; follows the OS setting above that
                base.configure(size=10)
            self.base_font = base
            self.bold_font = base.copy()
            self.bold_font.configure(weight="bold")
            self.big_font = base.copy()
            self.big_font.configure(size=int(abs(base.cget("size")) * 1.6) * (1 if base.cget("size") > 0 else -1),
                                    weight="bold")
            self.mono_font = tkfont.nametofont("TkFixedFont")
            self.mono_font.configure(size=max(9, abs(self.mono_font.cget("size"))))

        def apply_theme(self) -> None:
            p, s, r = self.pal, self._style, self.root
            r.configure(bg=p["bg"])
            s.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                        bordercolor=p["border"], lightcolor=p["panel"], darkcolor=p["panel"],
                        troughcolor=p["grid"], focuscolor=p["accent"], font=self.base_font)
            s.configure("TFrame", background=p["bg"])
            s.configure("Card.TFrame", background=p["panel"], relief="solid", borderwidth=1)
            s.configure("TLabel", background=p["bg"], foreground=p["fg"])
            s.configure("Card.TLabel", background=p["panel"], foreground=p["fg"])
            s.configure("CardMuted.TLabel", background=p["panel"], foreground=p["muted"])
            s.configure("Muted.TLabel", foreground=p["muted"])
            s.configure("Strong.TLabel", font=self.bold_font)
            s.configure("Big.TLabel", font=self.big_font)
            s.configure("Header.TFrame", background=p["panel"])
            s.configure("Header.TLabel", background=p["panel"], foreground=p["fg"])
            s.configure("HeaderBig.TLabel", background=p["panel"], foreground=p["fg"], font=self.big_font)
            for tone, col in (("ok", p["ok"]), ("warn", p["warn"]), ("bad", p["bad"]), ("off", p["muted"])):
                s.configure(f"Chip{tone}.TLabel", background=p["panel"], foreground=col, padding=(8, 3),
                            relief="solid", borderwidth=1, font=self.base_font)
            s.configure("TButton", background=p["panel"], foreground=p["fg"], padding=(12, 5), borderwidth=1)
            s.map("TButton", background=[("disabled", p["bg"]), ("active", p["select"])],
                  foreground=[("disabled", p["muted"])])
            s.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"], font=self.bold_font)
            s.map("Accent.TButton", background=[("disabled", p["bg"]), ("active", p["accent"])],
                  foreground=[("disabled", p["muted"])])
            s.configure("Danger.TButton", foreground=p["bad"])
            for w in ("TCheckbutton", "TRadiobutton"):
                s.configure(w, background=p["bg"], foreground=p["fg"], indicatorcolor=p["field"])
                s.map(w, background=[("active", p["bg"])], indicatorcolor=[("selected", p["accent"])])
            s.configure("TNotebook", background=p["bg"], borderwidth=0)
            s.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"], padding=(16, 7))
            s.map("TNotebook.Tab", background=[("selected", p["panel"])], foreground=[("selected", p["fg"])])
            s.configure("TLabelframe", background=p["bg"], bordercolor=p["border"])
            s.configure("TLabelframe.Label", background=p["bg"], foreground=p["muted"], font=self.bold_font)
            s.configure("Treeview", background=p["panel"], fieldbackground=p["panel"], foreground=p["fg"],
                        rowheight=int(abs(self.base_font.cget("size")) * 2.2) + 2 if self.base_font.cget("size") > 0 else 24)
            s.configure("Treeview.Heading", background=p["bg"], foreground=p["fg"], font=self.bold_font)
            s.map("Treeview", background=[("selected", p["select"])], foreground=[("selected", p["fg"])])
            s.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"])
            s.configure("TSpinbox", fieldbackground=p["field"], foreground=p["fg"], arrowcolor=p["fg"])
            s.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"], arrowcolor=p["fg"],
                        background=p["panel"])
            s.map("TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["fg"])])
            s.configure("TScrollbar", background=p["panel"], arrowcolor=p["fg"])
            s.configure("TProgressbar", background=p["accent"], troughcolor=p["grid"])
            s.configure("Horizontal.TScale", background=p["bg"])
            r.option_add("*TCombobox*Listbox.background", p["field"])
            r.option_add("*TCombobox*Listbox.foreground", p["fg"])
            r.option_add("*TCombobox*Listbox.selectBackground", p["select"])
            r.option_add("*TCombobox*Listbox.selectForeground", p["fg"])
            for t in getattr(self, "tabs", []):
                t.retheme()
            if hasattr(self, "light"):
                self._paint_light()

        def set_theme(self, name: str) -> None:
            self.theme_name = name
            self.pal = L.PALETTES[name]
            self.apply_theme()
            p = load_prefs()
            p["theme"] = name
            save_prefs(p)

        # -- layout
        def _build(self) -> None:
            r = self.root
            r.columnconfigure(0, weight=1)
            r.rowconfigure(1, weight=1)
            hd = ttk.Frame(r, style="Header.TFrame", padding=(12, 8))
            hd.grid(row=0, column=0, sticky="ew")
            hd.columnconfigure(5, weight=1)
            ttk.Label(hd, image=self.logo_small, style="Header.TLabel").grid(row=0, column=0, padx=(0, 8))
            ttk.Label(hd, text=TITLE, style="HeaderBig.TLabel").grid(row=0, column=1, padx=(0, 18))
            self.b_start = ttk.Button(hd, text="Start  (F5)", style="Accent.TButton", command=self.do_start)
            self.b_pause = ttk.Button(hd, text="Pause  (F6)", command=self.do_pause)
            self.b_stop = ttk.Button(hd, text="Stop  (F7)", command=self.do_stop)
            for i, b in enumerate((self.b_start, self.b_pause, self.b_stop)):
                b.grid(row=0, column=2 + i if i < 3 else 4, padx=(0, 6))
            lf = ttk.Frame(hd, style="Header.TFrame")
            lf.grid(row=0, column=5, sticky="w", padx=(12, 0))
            self.light = tk.Canvas(lf, width=16, height=16, highlightthickness=0, bd=0)
            self.light.pack(side="left")
            self.status_var = tk.StringVar(value="Stopped")
            ttk.Label(lf, textvariable=self.status_var, style="Header.TLabel", font=self.bold_font).pack(
                side="left", padx=(6, 0))
            chips = ttk.Frame(hd, style="Header.TFrame")
            chips.grid(row=0, column=6, sticky="e")
            self.tor_chip = ttk.Label(chips, text="Tor: off", style="Chipoff.TLabel")
            self.tor_chip.pack(side="left", padx=(0, 6))
            self.set_chip = ttk.Label(chips, text="Settings saved", style="Chipoff.TLabel")
            self.set_chip.pack(side="left")

            self.nb = ttk.Notebook(r)
            self.nb.grid(row=1, column=0, sticky="nsew", padx=10, pady=(10, 0))
            self.tabs = []
            for cls in TABS:
                t = cls(self.nb, self)
                self.nb.add(t, text=t.title)
                self.tabs.append(t)
            self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())

            sb = ttk.Frame(r, padding=(12, 6))
            sb.grid(row=2, column=0, sticky="ew")
            sb.columnconfigure(0, weight=1)
            self.msg_var, self.apply_var = tk.StringVar(), tk.StringVar()
            ttk.Label(sb, textvariable=self.msg_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            self.apply_lbl = ttk.Label(sb, textvariable=self.apply_var)
            self.apply_lbl.grid(row=0, column=1, sticky="e")

            menu = tk.Menu(r)
            fm = tk.Menu(menu, tearoff=0)
            fm.add_command(label="Open the web dashboard (backup)", command=self.open_web)
            fm.add_separator()
            fm.add_command(label="Quit", accelerator="Ctrl+Q", command=self.quit_app)
            menu.add_cascade(label="File", menu=fm)
            vm = tk.Menu(menu, tearoff=0)
            self.dark_var = tk.BooleanVar(value=self.theme_name == "dark")
            vm.add_checkbutton(label="Dark mode", variable=self.dark_var,
                               command=lambda: self.set_theme("dark" if self.dark_var.get() else "light"))
            menu.add_cascade(label="View", menu=vm)
            hm = tk.Menu(menu, tearoff=0)
            hm.add_command(label="About legalPrivacy", command=self.about)
            hm.add_command(label="Read the blog we are named after (Legalizing Privacy)",
                           command=lambda: webbrowser.open("https://legalizingprivacy.substack.com"))
            menu.add_cascade(label="Help", menu=hm)
            r.configure(menu=menu)

        def _bind_keys(self) -> None:
            r = self.root
            r.bind("<F5>", lambda _e: self.do_start())
            r.bind("<F6>", lambda _e: self.do_pause())
            r.bind("<F7>", lambda _e: self.do_stop())
            r.bind("<Control-q>", lambda _e: self.quit_app())
            r.bind("<Control-Key-1>", lambda _e: self.nb.select(0))

        def _tab_changed(self) -> None:
            try:
                self.current_tab().on_show()
            except (tk.TclError, IndexError):
                pass

        def current_tab(self):
            return self.tabs[self.nb.index(self.nb.select())]

        # -- threads
        def work(self, fn, done=None, error=None) -> None:
            L.run_in_thread(fn, self.q.post, done, error)

        def pump(self) -> None:
            self.q.drain()
            if not self.closing or not self.q.empty():
                self.root.after(100, self.pump)

        def error(self, what: str, exc) -> None:
            messagebox.showerror(what, str(exc) or exc.__class__.__name__, parent=self.root)

        def flash(self, text: str, ms: int = 5000) -> None:
            self.msg_var.set(text)
            self.root.after(ms, lambda: self.msg_var.set("") if self.msg_var.get() == text else None)

        # -- polling
        def poll_now(self) -> None:
            self._fetch_state()

        def tick(self) -> None:
            if self.closing:
                return
            self._fetch_state()
            if self.web is not None and self.web.quit_event.is_set():       # Quit pressed in the web dashboard
                self.quit_app()
                return
            self.root.after(1000, self.tick)

        def _fetch_state(self) -> None:
            if self._state_busy:
                return
            self._state_busy = True

            def done(st):
                self._state_busy = False
                self.on_state(st)

            def failed(exc):
                self._state_busy = False
                self.flash(f"Could not read the state: {exc}")
            self.work(self.ctl.state, done, failed)

        def on_state(self, st: dict) -> None:
            self.state = st
            kind, text = L.run_status(st)
            self.status_var.set(text)
            self._light_kind = kind
            self._paint_light()
            bs = L.button_states(st)
            for key, b in (("start", self.b_start), ("pause", self.b_pause), ("stop", self.b_stop)):
                b.state(["!disabled"] if bs[key] and not self.closing else ["disabled"])
            self.b_pause.configure(text=f"{L.pause_label(st)}  (F6)")
            ttxt, ttone = L.tor_chip(st.get("tor"))
            self.tor_chip.configure(text=ttxt, style=f"Chip{ttone}.TLabel")
            self._paint_settings_chip()
            for t in self.tabs:
                t.update_state(st)
            self._resync()

        def _paint_light(self) -> None:
            kind = getattr(self, "_light_kind", "stopped")
            col = {"running": self.pal["ok"], "paused": self.pal["warn"], "stopped": self.pal["muted"]}[kind]
            self.light.configure(bg=self.pal["panel"])
            self.light.delete("all")
            self.light.create_oval(2, 2, 14, 14, fill=col, outline=self.pal["border"])

        def _paint_settings_chip(self) -> None:
            text, tone = L.settings_chip(self.state.get("settings"), self._applying, self._apply_err)
            self.set_chip.configure(text=text, style=f"Chip{tone}.TLabel")
            self.apply_var.set(self._apply_text)
            self.apply_lbl.configure(foreground=self.pal.get({"ok": "ok", "warn": "warn", "bad": "bad"}.get(
                self._apply_tone, "muted"), self.pal["muted"]))

        # -- live apply
        def edited(self) -> None:
            if not self.loading:
                self.live.changed()

        def _collect(self) -> dict:
            v: dict = {}
            for t in self.tabs:
                v.update(t.values())
            self._last_edit = v
            return {"config": L.build_config_payload(v, asdict(self.ctl.profile.rate)), "app": L.app_flags(v)}

        def _report(self, kind: str, text: str, tone: str) -> None:
            self._applying = kind == "applying"
            self._apply_err = text if kind == "error" else None
            self._apply_text, self._apply_tone = ("" if kind == "error" else text), tone
            if kind == "error":
                self.flash("Not saved: " + text, 9000)
            elif kind == "done":                                   # let the message fade after a while
                shown = self._apply_text
                self.root.after(12000, lambda: self._fade(shown))
            self._paint_settings_chip()

        def _fade(self, shown: str) -> None:
            if self._apply_text == shown and not self._applying:
                self._apply_text = ""
                self._paint_settings_chip()

        def _resync(self) -> None:
            """If settings changed somewhere else (the backup web dashboard), show them here too."""
            if self.live.debounce.pending or self.live.busy:
                return
            vals = L.profile_values(self.ctl.profile, self.ctl.app)
            if vals != self._loaded_vals:
                self._loaded_vals = vals
                self.loading = True
                try:
                    for t in self.tabs:
                        t.load(vals)
                finally:
                    self.loading = False

        # -- run control
        def do_start(self) -> None:
            if str(self.b_start.cget("state")) == "disabled":
                return
            self.b_start.state(["disabled"])
            self.work(self.ctl.start, done=lambda _r: self.poll_now(), error=self._start_failed)

        def _start_failed(self, exc) -> None:
            self.poll_now()
            self.error("Could not start", exc)

        def do_pause(self) -> None:
            if str(self.b_pause.cget("state")) == "disabled":
                return
            paused = bool(self.state.get("paused"))
            self.work(lambda: self.ctl.pause(not paused), done=lambda _r: self.poll_now())

        def do_stop(self) -> None:
            if str(self.b_stop.cget("state")) == "disabled":
                return
            self.b_stop.state(["disabled"])
            self.work(self.ctl.stop, done=lambda _r: self.poll_now(), error=lambda e: self.error("Stop", e))

        # -- menu actions
        def open_web(self) -> None:
            def go():
                if self.web is None:
                    from .webui import UIServer
                    srv = UIServer(self.ctl, self.web_port)
                    threading.Thread(target=srv.serve_forever, daemon=True, name="webui-main").start()
                    self.web = srv
                return self.web.url

            def opened(url):
                if not webbrowser.open(url):
                    self.flash("Could not open a browser. The link is: " + url, 20000)
                else:
                    self.flash("Opened the web dashboard in your browser.")
            self.work(go, opened, lambda e: self.error("Web dashboard", e))

        def about(self) -> None:
            messagebox.showinfo(
                "About legalPrivacy",
                f"legalPrivacy {__version__}\n\nPrivacy noise generator with optional Tor/onion mode.\n"
                "This is the native desktop app; the web dashboard is available from the File menu.\n\n"
                "Named in homage to the blog Legalizing Privacy by Amy Peikoff:\nhttps://legalizingprivacy.substack.com\n\n"
                "Everything runs on this computer.", parent=self.root)

        def quit_app(self) -> None:
            if self.closing:
                return
            self.closing = True
            self.msg_var.set("Stopping…")
            for b in (self.b_start, self.b_pause, self.b_stop):
                b.state(["disabled"])
            if self.web is not None:
                self.web.quit_event.set()

            def stop():
                self.ctl.stop(stop_tor=True)
            self.work(stop, done=lambda _r: self._destroy(), error=lambda _e: self._destroy())
            self.root.after(20000, self._destroy)               # never hang on exit

        def _destroy(self) -> None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    return App


def main(profile_path: str | None = None, port: int | None = None) -> int:
    from .webui import run_ui, running_instance, start_services
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        print(NO_TK_MESSAGE, file=sys.stderr)
        return run_ui(profile_path, port)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Could not open a window ({exc}). Opening the web dashboard in your browser instead.",
              file=sys.stderr)
        return run_ui(profile_path, port)
    root.withdraw()
    from .controller import Controller
    ctl = Controller(profile_path)
    want = port if port is not None else int(ctl.app.get("port", 8765))
    other = running_instance(want) if port is None else None
    lock = claim_instance() if not other else None
    if other or lock is None:
        if other and messagebox.askyesno(
                TITLE, "legalPrivacy is already running (a web dashboard is open).\n\nStarting a second copy "
                       "would run two noise generators on the same data.\n\nOpen the running one in your "
                       "browser instead?", parent=root):
            webbrowser.open(other)
        elif not other:
            messagebox.showinfo(TITLE, "legalPrivacy is already open in another window.", parent=root)
        root.destroy()
        return 0
    App = build_app_class()
    app = App(root, ctl, want)
    root.deiconify()
    start_services(ctl)
    try:
        root.mainloop()
    finally:
        try:
            ctl.stop(stop_tor=True)
        except Exception:
            pass
        if lock:
            lock.close()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
