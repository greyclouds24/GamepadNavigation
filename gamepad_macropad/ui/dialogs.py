"""Small dialog helpers shared by the settings UI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class Dialog(tk.Toplevel):
    """Themed, always-on-top dialog positioned next to its parent."""

    def __init__(self, parent: tk.Misc, title: str, *, width: int = 480, height: int | None = None,
                 modal: bool = True) -> None:
        super().__init__(parent)
        self.withdraw()
        theme.apply(self)
        self.title(title)
        self.configure(bg=theme.BG_PANEL)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        self._width = width
        self._height = height
        self._modal = modal
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())

    def show(self) -> None:
        self.update_idletasks()
        w = max(theme.px(self._width), self.winfo_reqwidth())
        h = theme.px(self._height) if self._height else self.winfo_reqheight()
        parent = self.master.winfo_toplevel()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(sw - w, px + (pw - w) // 2))
        y = max(0, min(sh - h, py + (ph - h) // 2))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.focus_force()
        if self._modal:
            try:
                self.grab_set()
            except tk.TclError:
                pass

    def cancel(self) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def labeled_scale(parent: tk.Misc, text: str, var: tk.DoubleVar, lo: float, hi: float, *, fmt: str = "{:.2f}",
                  row: int = 0, command=None) -> ttk.Scale:
    """Label + horizontal slider + live value readout on one grid row."""
    ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", pady=3)
    value_lbl = ttk.Label(parent, text=fmt.format(var.get()), width=7, anchor="e", style="Dim.TLabel")
    value_lbl.grid(row=row, column=2, sticky="e", padx=(8, 0))

    def _update_label(*_args) -> None:
        try:
            value_lbl.configure(text=fmt.format(var.get()))
        except tk.TclError:
            pass

    def _changed(_v=None):
        _update_label()
        if command is not None:
            command()

    scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", command=_changed)
    scale.grid(row=row, column=1, sticky="ew", padx=(10, 0))
    parent.columnconfigure(1, weight=1)
    # Programmatic var.set(...) (reset / profile switch) does not fire Scale.command.
    var.trace_add("write", _update_label)
    return scale


def ask_text(parent: tk.Misc, title: str, prompt: str, initial: str = "", *, multiline: bool = False) -> str | None:
    """Minimal themed text prompt. Returns None when cancelled."""
    result: dict[str, str | None] = {"value": None}
    dlg = Dialog(parent, title, width=420)
    ttk.Label(dlg.body, text=prompt).pack(anchor="w")
    if multiline:
        widget = tk.Text(dlg.body, height=5, width=46, bg=theme.BG_RAISED, fg=theme.FG, insertbackground=theme.FG,
                         relief="flat", font=theme.FONT, wrap="word")
        widget.insert("1.0", initial)
        widget.pack(fill="x", pady=8)

        def get() -> str:
            return widget.get("1.0", "end-1c")
    else:
        var = tk.StringVar(value=initial)
        entry = ttk.Entry(dlg.body, textvariable=var, width=46)
        entry.pack(fill="x", pady=8)
        entry.select_range(0, "end")

        def get() -> str:
            return var.get()

        widget = entry

    def ok(_e=None):
        result["value"] = get()
        dlg.close()

    row = ttk.Frame(dlg.body)
    row.pack(fill="x")
    ttk.Button(row, text="Cancel", command=dlg.close).pack(side="right")
    ttk.Button(row, text="OK", style="Accent.TButton", command=ok).pack(side="right", padx=(0, 8))
    if not multiline:
        widget.bind("<Return>", ok)
    dlg.show()
    widget.focus_set()
    parent.wait_window(dlg)
    return result["value"]


def confirm(parent: tk.Misc, title: str, message: str) -> bool:
    answer = {"ok": False}
    dlg = Dialog(parent, title, width=380)
    ttk.Label(dlg.body, text=message, wraplength=theme.px(340)).pack(anchor="w", pady=(0, 12))
    row = ttk.Frame(dlg.body)
    row.pack(fill="x")

    def yes():
        answer["ok"] = True
        dlg.close()

    ttk.Button(row, text="Cancel", command=dlg.close).pack(side="right")
    ttk.Button(row, text="OK", style="Danger.TButton", command=yes).pack(side="right", padx=(0, 8))
    dlg.show()
    parent.wait_window(dlg)
    return answer["ok"]
