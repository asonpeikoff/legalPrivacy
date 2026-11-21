"""Base class for the tabs, with a small "field" system: a control registers itself under a settings key and
the tab then reads, loads and live-saves it by key (see gui_fields.FIELDS). Needs tkinter."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .gui_theme import Card, tip


class Tab(ttk.Frame):
    title = ""
    icon = "dash"

    def __init__(self, nb: ttk.Notebook, app):
        super().__init__(nb)
        self.app = app
        self._fields: dict = {}                      # key -> (get, set)

    # -- tab API (overridden)
    def update_state(self, st: dict) -> None: ...
    def retheme(self) -> None: ...
    def on_show(self) -> None: ...

    # -- fields
    def values(self) -> dict:
        out = {}
        for key, (get, _set) in self._fields.items():
            try:
                out[key] = get()
            except (ValueError, tk.TclError):
                pass                                  # a half-typed number: leave that setting alone for now
        return out

    def load(self, v: dict) -> None:
        for key, (_get, set_) in self._fields.items():
            if key in v:
                try:
                    set_(v[key])
                except (ValueError, tk.TclError):
                    pass

    def _reg(self, key, get, set_) -> None:
        self._fields[key] = (get, set_)

    def _var_changed(self, *_a) -> None:
        self.app.edited()

    # -- field constructors (parent is any widget inside a Card body)
    def f_check(self, card: Card, key: str, text: str, hint: str = "", tooltip: str = "") -> tk.BooleanVar:
        var = tk.BooleanVar()
        var.trace_add("write", self._var_changed)
        card.check(var, text, hint, tooltip=tooltip)
        self._reg(key, var.get, lambda x: var.set(bool(x)))
        return var

    def f_radios(self, card: Card, key: str, options: list[tuple], command=None) -> tk.StringVar:
        var = tk.StringVar()
        var.trace_add("write", self._var_changed)
        for opt in options:
            card.radio(var, opt[0], opt[1], opt[2] if len(opt) > 2 else "", command=command)
        self._reg(key, var.get, lambda x: var.set(str(x)))
        return var

    def f_combo(self, parent, key: str, options: list[tuple], width: int = 28) -> ttk.Combobox:
        """Shows the labels, stores the values."""
        var = tk.StringVar()
        cb = ttk.Combobox(parent, state="readonly", width=width, textvariable=var, values=[lab for _v, lab in options])
        by_label = {lab: val for val, lab in options}
        by_value = {val: lab for val, lab in options}
        cb.bind("<<ComboboxSelected>>", lambda _e: self.app.edited())
        self._reg(key, lambda: by_label.get(var.get(), var.get()), lambda x: var.set(by_value.get(x, str(x))))
        return cb

    def f_spin(self, parent, key: str, lo: float, hi: float, step: float = 1, width: int = 6, kind=int) -> ttk.Spinbox:
        var = tk.StringVar()
        sp = ttk.Spinbox(parent, from_=lo, to=hi, increment=step, width=width, textvariable=var, command=self.app.edited)
        var.trace_add("write", self._var_changed)

        def get():
            x = kind(float(var.get()))
            return min(hi, max(lo, x))
        self._reg(key, get, lambda x: var.set(f"{x:g}" if kind is float else str(int(x))))
        return sp

    def f_entry(self, parent, key: str, width: int = 30, placeholder: str = "") -> ttk.Entry:
        var = tk.StringVar()
        var.trace_add("write", self._var_changed)
        en = ttk.Entry(parent, textvariable=var, width=width)
        tip(en, placeholder)
        self._reg(key, var.get, lambda x: var.set(str(x or "")))
        return en

    def f_scale(self, parent, key: str, lo: float, hi: float, step: float, on_move=None, width=160) -> ttk.Scale:
        var = tk.DoubleVar(value=lo)

        def moved(_v=None):
            snapped = round(var.get() / step) * step
            if abs(snapped - var.get()) > 1e-9:
                var.set(snapped)
            if on_move:
                on_move(snapped)
            self.app.edited()
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", style="Card.Horizontal.TScale",
                       command=moved, length=width)

        def set_(x):
            var.set(float(x))
            if on_move:
                on_move(float(x))
        self._reg(key, lambda: round(var.get() / step) * step, set_)
        return sc

    def f_text(self, parent, key: str, height: int = 5, width: int = 60) -> tk.Text:
        t = tk.Text(parent, height=height, width=width, wrap="none", relief="flat", borderwidth=1, highlightthickness=1,
                    font=self.app.fonts["mono"], undo=True)
        t.bind("<KeyRelease>", lambda _e: self.app.edited())

        def set_(x):
            if t.get("1.0", "end-1c") != str(x or ""):
                t.delete("1.0", "end")
                t.insert("1.0", str(x or ""))
        self._reg(key, lambda: t.get("1.0", "end-1c").strip(), set_)
        self.app.field_texts.append(t)
        return t
