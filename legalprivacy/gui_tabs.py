"""The six tabs of the native desktop app. Each tab is built from ttk widgets, reads what it shows from the
state dict the app polls (`update(state)`), and reports edits with `app.edited()`; it never talks to the
network or blocks: slow work goes through `app.work(...)`."""
from __future__ import annotations

import json
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import gui_logic as L
from .gui_charts import BaselineChart, IntendedChart, SessionChart

PAD = 10


def _wrap_to(container: tk.Misc, labels: list, margin: int = 24) -> None:
    def fit(_e=None):
        w = max(200, container.winfo_width() - margin)
        for lb in labels:
            try:
                lb.configure(wraplength=w)
            except tk.TclError:
                pass
    container.bind("<Configure>", fit, add="+")


class Tab(ttk.Frame):
    title = ""

    def __init__(self, nb: ttk.Notebook, app):
        super().__init__(nb, padding=PAD)
        self.app = app

    def update_state(self, st: dict) -> None: ...
    def retheme(self) -> None: ...
    def values(self) -> dict: return {}
    def load(self, v: dict) -> None: ...
    def on_show(self) -> None: ...


def labelled_scale(parent, var: tk.DoubleVar, lo, hi, command) -> ttk.Scale:
    sc = ttk.Scale(parent, from_=lo, to=hi, orient="horizontal", variable=var, command=command)
    return sc


# ── Dashboard ───────────────────────────────────────────────────────────────
class DashboardTab(Tab):
    title = "Dashboard"

    def __init__(self, nb, app):
        super().__init__(nb, app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        self.state_var, self.uptime_var, self.data_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(top, textvariable=self.state_var, style="Big.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.uptime_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(top, textvariable=self.data_var, style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        self.now_var = tk.StringVar(value=L.now_text(None))
        ttk.Label(self, textvariable=self.now_var, style="Card.TLabel", padding=8).grid(
            row=1, column=0, sticky="ew", pady=(PAD, PAD))

        box = ttk.LabelFrame(self, text="Lanes", padding=6)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        cols = ("lane", "does", "hits", "fails", "budget", "problem")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=4, selectmode="none")
        for c, t, w in (("lane", "Lane", 130), ("does", "What it does", 200), ("hits", "Hits", 60),
                        ("fails", "Failed", 60), ("budget", "Today (done / allowed)", 320), ("problem", "Last problem", 200)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c in ("lane", "does", "budget", "problem") else "e",
                             stretch=c in ("budget", "problem"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        rate = ttk.LabelFrame(self, text="How much noise", padding=PAD)
        rate.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))
        rate.columnconfigure(1, weight=1)
        self.level = tk.DoubleVar(value=5)
        self.rate_mode = tk.StringVar(value="random")
        ttk.Label(rate, text="Quiet").grid(row=0, column=0)
        self.scale = labelled_scale(rate, self.level, 1, 10, self._snap)
        self.scale.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(rate, text="Busy").grid(row=0, column=2)
        self.rate_label = tk.StringVar()
        self.rate_msg = ttk.Label(rate, textvariable=self.rate_label, style="Muted.TLabel", justify="left")
        self.rate_msg.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        _wrap_to(rate, [self.rate_msg])
        rm = ttk.Frame(rate)
        rm.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Radiobutton(rm, text="Random: changes every hour, like a person (recommended)", value="random",
                        variable=self.rate_mode, command=self.app.edited).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(rm, text="Steady: about the same amount every hour", value="steady",
                        variable=self.rate_mode, command=self.app.edited).grid(row=1, column=0, sticky="w")
        self.rate_info: list = []

    def _snap(self, _v=None) -> None:
        v = int(round(self.level.get()))
        if abs(self.level.get() - v) > 1e-6:
            self.level.set(v)
        self.rate_label.set(L.rate_text(v, self.rate_info))
        self.app.edited()

    def values(self) -> dict:
        return {"level": int(round(self.level.get())), "rate_mode": self.rate_mode.get()}

    def load(self, v: dict) -> None:
        self.level.set(v["level"])
        self.rate_mode.set(v["rate_mode"])
        self.rate_label.set(L.rate_text(v["level"], self.rate_info))

    def update_state(self, st: dict) -> None:
        kind, text = L.run_status(st)
        self.state_var.set(text)
        self.uptime_var.set("Running for " + L.fmt_uptime(st.get("uptime")) if st.get("running") else "Not running")
        cap = st.get("data_cap_mb")
        self.data_var.set(f"Data used today: {L.fmt_bytes(st.get('bytes_today'))}"
                          + (f" of {cap:g} MB allowed" if cap else ""))
        self.now_var.set(L.now_text(st.get("current")) if st.get("running") and not st.get("paused")
                         else ("Paused." if st.get("paused") else "Idle: the noise is not running."))
        self.rate_info = st.get("rate_info") or self.rate_info
        self.rate_label.set(L.rate_text(self.level.get(), self.rate_info))
        rows = L.lanes_rows(st)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", values=r)


# ── Activity ────────────────────────────────────────────────────────────────
class ActivityTab(Tab):
    title = "Activity"

    def __init__(self, nb, app):
        super().__init__(nb, app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.count = tk.StringVar(value="No activity yet.")
        ttk.Label(self, text="Recent hits, newest first. Only the site name is shown, never a full page address.",
                  style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        cols = ("time", "lane", "kind", "site", "status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("time", "Time", 80), ("lane", "Lane", 120), ("kind", "Kind", 120),
                        ("site", "Site", 360), ("status", "Status", 140)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=c == "site")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        sb.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        ttk.Label(self, textvariable=self.count, style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.last_seq = 0
        self.retheme()

    def retheme(self) -> None:
        p = self.app.pal
        self.tree.tag_configure("ok", foreground=p["ok"])
        self.tree.tag_configure("bad", foreground=p["bad"])
        self.tree.tag_configure("mute", foreground=p["muted"])

    def update_state(self, st: dict) -> None:
        hits = self.app.ctl.hits(self.last_seq)
        if not hits and self.last_seq and not st.get("running") and not self.app.ctl.runner:
            return
        if hits and hits[0]["seq"] <= self.last_seq:
            hits = []
        for h in hits:                                  # oldest first, each inserted on top
            row = L.hit_row(h)
            self.tree.insert("", 0, iid=f"h{row[0]}", values=row[1:6], tags=(row[6],))
            self.last_seq = max(self.last_seq, h["seq"])
        kids = self.tree.get_children()
        if len(kids) > L.ACTIVITY_MAX_ROWS:
            self.tree.delete(*kids[L.ACTIVITY_MAX_ROWS:])
        n = len(self.tree.get_children())
        self.count.set(f"{n} recent hit{'s' if n != 1 else ''} (up to {L.ACTIVITY_MAX_ROWS} are kept)" if n
                       else "No activity yet. Press Start on the Dashboard.")

    def reset(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.last_seq = 0


# ── Live Log ────────────────────────────────────────────────────────────────
class LogTab(Tab):
    title = "Live Log"

    def __init__(self, nb, app):
        super().__init__(nb, app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(bar, text="Show:").pack(side="left")
        self.level = tk.StringVar(value=L.LOG_LEVEL_LABELS["info"])
        self.level_box = ttk.Combobox(bar, state="readonly", width=22, textvariable=self.level,
                                      values=[L.LOG_LEVEL_LABELS[k] for k in L.LOG_LEVEL_ORDER])
        self.level_box.pack(side="left", padx=6)
        self.level_box.bind("<<ComboboxSelected>>", lambda _e: self.render_all())
        self.hold = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Pause scrolling", variable=self.hold).pack(side="left", padx=10)
        ttk.Button(bar, text="Copy", command=self.copy).pack(side="left")
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left", padx=6)
        self.text = tk.Text(self, wrap="none", height=10, state="disabled", relief="flat", borderwidth=1,
                            font=self.app.mono_font)
        sy = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        sx = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        sy.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        sx.grid(row=2, column=0, sticky="ew")
        self.events: list[dict] = []
        self.last_id = 0
        self.epoch = None
        self.retheme()

    def min_level(self) -> str:
        for k, lab in L.LOG_LEVEL_LABELS.items():
            if lab == self.level.get():
                return k
        return "info"

    def retheme(self) -> None:
        p = self.app.pal
        self.text.configure(bg=p["log_bg"], fg=p["fg"], insertbackground=p["fg"], selectbackground=p["select"],
                            selectforeground=p["fg"], highlightbackground=p["border"])
        for tag, col in (("debug", p["muted"]), ("info", p["fg"]), ("warn", p["warn"]), ("error", p["bad"])):
            self.text.tag_configure(tag, foreground=col)

    def _insert(self, evs: list[dict]) -> None:
        if not evs:
            return
        self.text.configure(state="normal")
        for e in evs:
            self.text.insert("end", L.fmt_log_line(e) + "\n", e["level"] if e["level"] in L.LOG_LEVEL_ORDER else "info")
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > L.LOG_MAX_LINES:
            self.text.delete("1.0", f"{lines - L.LOG_MAX_LINES + 1}.0")
        self.text.configure(state="disabled")
        if not self.hold.get():
            self.text.see("end")

    def render_all(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._insert(L.visible_events(self.events, self.min_level())[-L.LOG_MAX_LINES:])

    def update_state(self, st: dict) -> None:
        ep = st.get("log_epoch")
        if self.epoch is not None and ep != self.epoch:        # the app cleared its log: empty our copy too
            self.events.clear()
            self.render_all()
        self.epoch = ep
        new = self.app.ctl.log.since(self.last_id, "debug", limit=1000)
        if new:
            self.last_id = new[-1]["id"]
            self.events.extend(new)
            del self.events[:-L.LOG_MAX_LINES]
            self._insert(L.visible_events(new, self.min_level()))

    def copy(self) -> None:
        try:
            data = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            data = self.text.get("1.0", "end-1c")
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(data)
        self.app.flash("Log copied to the clipboard.")

    def clear(self) -> None:
        self.app.ctl.log.clear()
        self.events.clear()
        self.render_all()


# ── Tor ─────────────────────────────────────────────────────────────────────
class TorTab(Tab):
    title = "Tor"

    def __init__(self, nb, app):
        super().__init__(nb, app)
        self.columnconfigure(0, weight=1)
        intro = ttk.Label(self, style="Muted.TLabel", justify="left",
                          text="Tor is optional and off by default. When it is on, one lane visits .onion sites. "
                               "Ordinary web noise never goes through Tor.")
        intro.grid(row=0, column=0, sticky="w")
        _wrap_to(self, [intro])
        self.enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Use Tor for onion-site noise", variable=self.enabled,
                        command=self.app.edited).grid(row=1, column=0, sticky="w", pady=(PAD, 0))
        st = ttk.LabelFrame(self, text="Tor right now", padding=PAD)
        st.grid(row=2, column=0, sticky="ew", pady=PAD)
        st.columnconfigure(0, weight=1)
        self.state_var, self.hint_var = tk.StringVar(), tk.StringVar()
        ttk.Label(st, textvariable=self.state_var, style="Strong.TLabel").grid(row=0, column=0, sticky="w")
        self.bar = ttk.Progressbar(st, mode="determinate", maximum=100)
        self.bar.grid(row=1, column=0, sticky="ew", pady=6)
        self.hint = ttk.Label(st, textvariable=self.hint_var, style="Muted.TLabel", justify="left")
        self.hint.grid(row=2, column=0, sticky="w")
        _wrap_to(st, [self.hint])
        btns = ttk.Frame(st)
        btns.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.b_start = ttk.Button(btns, text="Start Tor", command=lambda: self.act("start"))
        self.b_stop = ttk.Button(btns, text="Stop Tor", command=lambda: self.act("stop"))
        self.b_new = ttk.Button(btns, text="New identity", command=lambda: self.act("newnym"))
        for b in (self.b_start, self.b_stop, self.b_new):
            b.pack(side="left", padx=(0, 6))
        br = ttk.LabelFrame(self, text="Bridges (if your network blocks Tor)", padding=PAD)
        br.grid(row=3, column=0, sticky="ew")
        self.bridge_mode = tk.StringVar(value="off")
        for i, (val, lab) in enumerate(L.BRIDGE_CHOICES):
            ttk.Radiobutton(br, text=lab, value=val, variable=self.bridge_mode, command=self.app.edited).grid(
                row=i, column=0, sticky="w")
        self.bridge_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(br, text="If Tor cannot connect directly, try the built-in bridges by itself",
                        variable=self.bridge_auto, command=self.app.edited).grid(row=len(L.BRIDGE_CHOICES), column=0,
                                                                              sticky="w", pady=(6, 0))
        self.tor: dict = {}

    def act(self, action: str) -> None:
        self.app.work(lambda: self.app.ctl.tor_action(action), done=lambda _r: self.app.poll_now(),
                      error=lambda e: self.app.error("Tor", e))

    def values(self) -> dict:
        return {"tor_enabled": self.enabled.get(), "bridge_mode": self.bridge_mode.get(),
                "bridge_auto": self.bridge_auto.get()}

    def load(self, v: dict) -> None:
        self.enabled.set(v["tor_enabled"])
        self.bridge_mode.set(v["bridge_mode"])
        self.bridge_auto.set(v["bridge_auto"])

    def update_state(self, st: dict) -> None:
        tor = st.get("tor") or {}
        state = tor.get("state") or "stopped"
        pct = int(tor.get("bootstrap_pct") or 0)
        want = self.enabled.get()
        if state == "ready":
            line = "Tor is connected." + ("" if want else " (It is switched off in the settings above.)")
        elif state in ("bootstrapping", "starting", "installing"):
            line = f"Tor is connecting… {pct}%" + (f" ({tor['bootstrap_summary']})" if tor.get("bootstrap_summary") else "")
        elif state == "error":
            line = "Tor has a problem."
        else:
            line = "Tor is off." if not want else "Tor is enabled and will start when you press Start."
        self.state_var.set(line)
        self.bar["value"] = 100 if state == "ready" else pct
        self.hint_var.set(tor.get("error") or tor.get("hint") or
                          ("Onion noise can run." if state == "ready" and want else ""))
        self.b_start.state(["!disabled"] if state in ("stopped", "error") else ["disabled"])
        self.b_stop.state(["!disabled"] if state != "stopped" else ["disabled"])
        self.b_new.state(["!disabled"] if state == "ready" else ["disabled"])


# ── Settings ────────────────────────────────────────────────────────────────
class SettingsTab(Tab):
    title = "Settings"

    def __init__(self, nb, app):
        super().__init__(nb, app)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        e = app.edited

        dns = ttk.LabelFrame(self, text="Where DNS lookups go", padding=PAD)
        dns.grid(row=0, column=0, sticky="nsew", padx=(0, PAD // 2))
        self.dns_mode = tk.StringVar(value="mix")
        for val, lab in L.DNS_CHOICES:
            ttk.Radiobutton(dns, text=lab, value=val, variable=self.dns_mode, command=e).pack(anchor="w")
        note = ttk.Label(dns, style="Muted.TLabel", justify="left",
                         text="Fine-tuning the server list is in the web dashboard (File menu).")
        note.pack(anchor="w", pady=(6, 0))
        _wrap_to(dns, [note], 30)

        br = ttk.LabelFrame(self, text="The noise browser", padding=PAD)
        br.grid(row=0, column=1, sticky="nsew", padx=(PAD // 2, 0))
        ttk.Label(br, text="Window").grid(row=0, column=0, sticky="w")
        self.window = tk.StringVar(value="hidden")
        self.window_box = ttk.Combobox(br, state="readonly", width=26, textvariable=self.window,
                                       values=[lab for _v, lab in L.WINDOW_CHOICES])
        self.window_box.grid(row=0, column=1, sticky="w", padx=6)
        self.window_box.bind("<<ComboboxSelected>>", lambda _e: e())
        ttk.Label(br, text="Cookies last").grid(row=1, column=0, sticky="w", pady=(8, 0))
        cf = ttk.Frame(br)
        cf.grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))
        self.cookie_value = tk.StringVar(value="1")
        self.cookie_unit = tk.StringVar(value="hours")
        sp = ttk.Spinbox(cf, from_=0, to=365, increment=1, width=6, textvariable=self.cookie_value, command=e)
        sp.pack(side="left")
        sp.bind("<KeyRelease>", lambda _e: e())
        ub = ttk.Combobox(cf, state="readonly", width=7, textvariable=self.cookie_unit, values=["hours", "days"])
        ub.pack(side="left", padx=6)
        ub.bind("<<ComboboxSelected>>", lambda _e: e())
        ttk.Label(br, text="then the browser starts with fresh cookies (0 = never keep any)",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        gen = ttk.LabelFrame(self, text="General", padding=PAD)
        gen.grid(row=1, column=0, columnspan=2, sticky="ew", pady=PAD)
        self.autostart = tk.BooleanVar()
        self.clear_logs = tk.BooleanVar(value=True)
        self.observe = tk.BooleanVar(value=True)
        ttk.Checkbutton(gen, text="Start the noise by itself when this app opens", variable=self.autostart,
                        command=e).pack(anchor="w")
        ttk.Checkbutton(gen, text="Clear the live log each time the noise restarts", variable=self.clear_logs,
                        command=e).pack(anchor="w")
        ttk.Checkbutton(gen, text="Watch this computer's connection counts for the Proof tab "
                                  "(local only, never leaves this computer)", variable=self.observe,
                        command=e).pack(anchor="w")

        pan = ttk.LabelFrame(self, text="Panic", padding=PAD)
        pan.grid(row=2, column=0, columnspan=2, sticky="ew")
        pan.columnconfigure(0, weight=1)
        msg = ttk.Label(pan, style="Muted.TLabel", justify="left",
                        text="Stop now, and optionally leave nothing behind. Your settings are never deleted.")
        msg.grid(row=0, column=0, columnspan=3, sticky="w")
        _wrap_to(pan, [msg])
        for i, (lvl, label, tip) in enumerate((
                ("soft", "Panic: stop", "Stops the noise."),
                ("medium", "Panic: stop + clear", "Also clears the log and the noise browser's cookies."),
                ("hard", "Panic: stop + erase everything", "Also stops Tor and deletes Proof data and log files."))):
            f = ttk.Frame(pan)
            f.grid(row=1, column=i, sticky="w", pady=(8, 0), padx=(0, 16))
            ttk.Button(f, text=label, style="Danger.TButton", command=lambda lv=lvl: self.panic(lv)).pack(anchor="w")
            ttk.Label(f, text=tip, style="Muted.TLabel", wraplength=230, justify="left").pack(anchor="w")

    def panic(self, level: str) -> None:
        what = {"soft": "Stop the noise now?",
                "medium": "Stop the noise, clear the live log and delete the noise browser's cookies "
                          "and any signed-in account sessions?",
                "hard": "Stop the noise and Tor, and delete everything legalPrivacy recorded "
                        "(Proof data, cookies, logs)? Your settings stay."}[level]
        if not messagebox.askokcancel("Panic", what, icon="warning", default="cancel", parent=self.app.root):
            return
        self.app.work(lambda: self.app.ctl.panic(level),
                      done=lambda r: (self.app.poll_now(), messagebox.showinfo(
                          "Panic done", "\n".join("• " + d for d in r["done"]), parent=self.app.root)),
                      error=lambda e: self.app.error("Panic", e))

    def values(self) -> dict:
        v = {"dns_mode": self.dns_mode.get(), "cookie_unit": self.cookie_unit.get(),
             "autostart": self.autostart.get(), "clear_logs_on_restart": self.clear_logs.get(),
             "observe": self.observe.get()}
        for val, lab in L.WINDOW_CHOICES:
            if lab == self.window.get():
                v["window"] = val
        try:
            v["cookie_value"] = float(self.cookie_value.get())
        except ValueError:
            pass                                    # half-typed number: leave that setting alone for now
        return v

    def load(self, v: dict) -> None:
        self.dns_mode.set(v["dns_mode"])
        self.window.set(dict(L.WINDOW_CHOICES).get(v["window"], v["window"]))
        self.cookie_value.set(f"{v['cookie_value']:g}")
        self.cookie_unit.set(v["cookie_unit"])
        self.autostart.set(v["autostart"])
        self.clear_logs.set(v["clear_logs_on_restart"])
        self.observe.set(v["observe"])


# ── Proof ───────────────────────────────────────────────────────────────────
class ScrollFrame(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner = ttk.Frame(self.canvas, padding=PAD)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e=None) -> None:
        r = self.winfo_toplevel()
        r.bind_all("<MouseWheel>", self._wheel)
        r.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
        r.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))

    def _unbind_wheel(self, _e=None) -> None:
        r = self.winfo_toplevel()
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            r.unbind_all(ev)

    def _wheel(self, e) -> None:
        step = -1 * (e.delta // 120 if sys.platform != "darwin" else e.delta)
        self.canvas.yview_scroll(step * 3 if sys.platform != "darwin" else step, "units")

    def retheme(self) -> None:
        self.canvas.configure(bg=self.app.pal["bg"])


class ProofTab(Tab):
    title = "Proof"
    REFRESH_S = 15

    def __init__(self, nb, app):
        super().__init__(nb, app)
        self.configure(padding=0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.sf = ScrollFrame(self, app)
        self.sf.grid(row=0, column=0, sticky="nsew")
        f = self.sf.inner
        f.columnconfigure(0, weight=1)
        self.wrap_labels: list = []

        head = ttk.Frame(f)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.headline = tk.StringVar(value="Is it working?")
        ttk.Label(head, textvariable=self.headline, style="Big.TLabel").grid(row=0, column=0, sticky="w")
        self.run_btn = ttk.Button(head, text="Run checks", command=self.run_checks)
        self.run_btn.grid(row=0, column=1, sticky="e")
        self.checks = ttk.Frame(f)
        self.checks.grid(row=1, column=0, sticky="ew", pady=(6, PAD))
        self.checks.columnconfigure(0, weight=1)

        self.charts: dict = {}
        self.verdicts: dict = {}
        for i, (key, title, cls) in enumerate((
                ("intended", "1. The pattern your settings are meant to produce", IntendedChart),
                ("session", "2. What the noise actually did this session", SessionChart),
                ("baseline", "3. Your day compared with the noise's day", BaselineChart))):
            box = ttk.LabelFrame(f, text=title, padding=6)
            box.grid(row=2 + i, column=0, sticky="ew", pady=(0, PAD))
            box.columnconfigure(0, weight=1)
            ch = cls(box, app.pal)
            ch.grid(row=0, column=0, sticky="ew")
            v = tk.StringVar()
            lb = ttk.Label(box, textvariable=v, justify="left")
            lb.grid(row=1, column=0, sticky="w", pady=(4, 0))
            self.wrap_labels.append(lb)
            self.charts[key], self.verdicts[key] = ch, v

        base = ttk.LabelFrame(f, text="Where your baseline comes from", padding=PAD)
        base.grid(row=5, column=0, sticky="ew", pady=(0, PAD))
        base.columnconfigure(0, weight=1)
        self.base_var, self.har_var = tk.StringVar(), tk.StringVar(value="No browser recording imported.")
        bl = ttk.Label(base, textvariable=self.base_var, justify="left")
        bl.grid(row=0, column=0, columnspan=3, sticky="w")
        self.wrap_labels.append(bl)
        hl = ttk.Label(base, textvariable=self.har_var, style="Muted.TLabel", justify="left")
        hl.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.wrap_labels.append(hl)
        bt = ttk.Frame(base)
        bt.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Button(bt, text="Import browser recording (.har)…", command=self.import_har).pack(side="left")
        self.forget_btn = ttk.Button(bt, text="Forget it", command=self.forget_har)
        self.forget_btn.pack(side="left", padx=6)
        _wrap_to(f, self.wrap_labels, 60)

        self._last = {"leak": None, "act": None}
        self._fetching = {"leak": False, "act": False}
        self._last_fetch = 0.0
        self._checks_json = ""
        self.retheme()

    def retheme(self) -> None:
        self.sf.retheme()
        for ch in self.charts.values():
            ch.set_palette(self.app.pal)
        self._render_checks()

    # -- data
    def on_show(self) -> None:
        self.fetch(force=True)

    def update_state(self, st: dict) -> None:
        if self.app.current_tab() is not self:
            return
        import time
        leak_running = bool((self._last["leak"] or {}).get("running"))
        if leak_running or time.time() - self._last_fetch > self.REFRESH_S:
            self.fetch(force=False, leak_only=leak_running)

    def fetch(self, force: bool, leak_only: bool = False) -> None:
        import time
        self._last_fetch = time.time()
        ctl = self.app.ctl
        if not self._fetching["leak"]:
            self._fetching["leak"] = True
            self.app.work(lambda: ctl.leak_runner().state(), done=self._got_leak,
                          error=lambda e: self._fail("leak"))
        if not leak_only and not self._fetching["act"]:
            self._fetching["act"] = True
            self.app.work(ctl.activity_state, done=self._got_act, error=lambda e: self._fail("act"))

    def _fail(self, which: str) -> None:
        self._fetching[which] = False

    def _got_leak(self, data: dict) -> None:
        self._fetching["leak"] = False
        self._last["leak"] = data
        self._render_checks()

    def _got_act(self, data: dict) -> None:
        self._fetching["act"] = False
        self._last["act"] = data
        self.charts["intended"].update_data(data["intended"])
        self.charts["session"].update_data(data["session"])
        self.charts["baseline"].update_data(data["baseline"])
        self.verdicts["intended"].set(data["intended"].get("verdict", ""))
        self.verdicts["session"].set(data["session"].get("verdict", ""))
        self.verdicts["baseline"].set(data["baseline"].get("verdict", ""))
        self.base_var.set(L.baseline_sources_text(data["baseline"]))
        har = data.get("har") or {}
        if har.get("present"):
            self.har_var.set(f"Imported recording: {har['entries']:,} requests over {har['span_hours']:g} h, "
                             f"{har['sites']} top sites. Only times and site names were kept.")
            self.forget_btn.state(["!disabled"])
        else:
            self.har_var.set("No browser recording imported. In your browser's developer tools, open the Network "
                             "tab, browse as usual, then Save all as HAR.")
            self.forget_btn.state(["disabled"])

    def _render_checks(self) -> None:
        data = self._last["leak"]
        if not data:
            return
        sig = json.dumps(data, sort_keys=True, default=str) + self.app.theme_name
        if sig == self._checks_json:
            return
        self._checks_json = sig
        p = self.app.pal
        for w in self.checks.winfo_children():
            w.destroy()
        summ = data.get("summary") or {}
        self.headline.set(("Checking…  " if data.get("running") else "") + (summ.get("headline") or "Is it working?")
                          + ("  (not tested over the network yet)" if data.get("preview") else ""))
        self.run_btn.state(["disabled"] if data.get("running") else ["!disabled"])
        if data.get("error"):
            ttk.Label(self.checks, text="The checks could not run: " + data["error"], foreground=p["bad"]).grid(
                row=0, column=0, sticky="w")
        labels = []
        for i, c in enumerate(data.get("checks") or []):
            title, want, saw, hint = L.check_lines(c)
            box = ttk.Frame(self.checks, style="Card.TFrame", padding=8)
            box.grid(row=i, column=0, sticky="ew", pady=2)
            box.columnconfigure(0, weight=1)
            tone = {"ok": p["ok"], "bad": p["bad"], "info": p["info"]}.get(c.get("status"), p["muted"])
            ttk.Label(box, text=title, style="Card.TLabel", foreground=tone, font=self.app.bold_font).grid(
                row=0, column=0, sticky="w")
            for r, (txt, st) in enumerate(((want, "Card.TLabel"), (saw, "Card.TLabel"), (hint, "CardMuted.TLabel")), 1):
                if txt:
                    lb = ttk.Label(box, text=txt, style=st, justify="left")
                    lb.grid(row=r, column=0, sticky="w")
                    labels.append(lb)
        self.wrap_labels.extend(labels)
        del self.wrap_labels[:-80]
        _wrap_to(self.sf.inner, self.wrap_labels, 70)
        self.sf.inner.event_generate("<Configure>")

    # -- actions
    def run_checks(self) -> None:
        self.run_btn.state(["disabled"])
        self.app.work(lambda: self.app.ctl.leak_runner().run(), done=lambda _r: self.fetch(True, leak_only=True),
                      error=lambda e: self.app.error("Checks", e))

    def import_har(self) -> None:
        path = filedialog.askopenfilename(parent=self.app.root, title="Choose a browser recording",
                                          filetypes=[("HAR files", "*.har *.json"), ("All files", "*.*")])
        if not path:
            return
        self.har_var.set("Reading the recording…")

        def go():
            import os
            if os.path.getsize(path) > 52 * 1024 * 1024:
                raise RuntimeError("That file is larger than 50 MB. Record a shorter session and try again.")
            with open(path, "rb") as fh:
                raw = fh.read()
            try:
                text = raw.decode("utf-8-sig")
            finally:
                raw = None
            return self.app.ctl.har_import({"text": text})

        def done(_r):
            self.fetch(True)
            self.app.flash("Recording imported. Only times and site names were kept.")

        def err(e):
            self.fetch(True)
            self.app.error("Import", e)
        self.app.work(go, done=done, error=err)

    def forget_har(self) -> None:
        if not messagebox.askokcancel("Forget recording", "Delete the imported recording's data?",
                                      parent=self.app.root):
            return
        self.app.work(lambda: self.app.ctl.har_forget("all"), done=lambda _r: self.fetch(True),
                      error=lambda e: self.app.error("Forget", e))


TABS = [DashboardTab, ActivityTab, LogTab, TorTab, SettingsTab, ProofTab]
