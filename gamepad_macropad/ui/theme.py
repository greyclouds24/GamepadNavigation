"""Shared colours, fonts and ttk styling for the dark UI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#17181d"
BG_PANEL = "#1f2129"
BG_RAISED = "#2a2d38"
BG_HOVER = "#343846"
FG = "#e9eaf0"
FG_DIM = "#9a9eae"
ACCENT = "#4cc2ff"
ACCENT_DARK = "#1f8fc7"
OK = "#5ad37a"
WARN = "#f2b84b"
ERR = "#ff5d6c"
BORDER = "#3a3e4d"

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 10)

_styled = False
_scale = 1.0


def scale(root: tk.Misc | None = None) -> float:
    """UI scale factor relative to 96 DPI (1.5 on a 150% display)."""
    global _scale
    if root is not None:
        try:
            _scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except tk.TclError:
            pass
    return _scale


def px(value: float) -> int:
    """Scale a 96-DPI pixel measurement for the current display."""
    return int(round(value * _scale))


def apply(root: tk.Misc) -> None:
    """Configure ttk styles once per interpreter."""
    global _styled
    if _styled:
        return
    _styled = True
    scale(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG_PANEL, foreground=FG, font=FONT, bordercolor=BORDER,
                    lightcolor=BG_PANEL, darkcolor=BG_PANEL, troughcolor=BG, fieldbackground=BG_RAISED,
                    selectbackground=ACCENT_DARK, selectforeground=FG, insertcolor=FG)
    style.configure("TFrame", background=BG_PANEL)
    style.configure("TLabel", background=BG_PANEL, foreground=FG)
    style.configure("Dim.TLabel", foreground=FG_DIM, font=FONT_SMALL)
    style.configure("Title.TLabel", font=FONT_TITLE)
    style.configure("TButton", background=BG_RAISED, foreground=FG, padding=(10, 5), borderwidth=0,
                    focusthickness=0)
    style.map("TButton", background=[("active", BG_HOVER), ("pressed", ACCENT_DARK), ("disabled", BG_PANEL)],
              foreground=[("disabled", FG_DIM)])
    style.configure("Accent.TButton", background=ACCENT_DARK, foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", ACCENT), ("pressed", ACCENT_DARK)])
    style.configure("Danger.TButton", background="#7a2b35", foreground="#ffffff")
    style.map("Danger.TButton", background=[("active", ERR)])
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=BG_PANEL, foreground=FG, focuscolor=BG_PANEL,
                        indicatorbackground=BG_RAISED, indicatorforeground="#ffffff", indicatormargin=(0, 0, 6, 0),
                        padding=(2, 3))
        style.map(widget, background=[("active", BG_PANEL)],
                  indicatorbackground=[("selected", ACCENT), ("active", BG_HOVER), ("!selected", BG_RAISED)],
                  indicatorforeground=[("selected", "#ffffff")])
    style.configure("TEntry", fieldbackground=BG_RAISED, foreground=FG, bordercolor=BORDER, padding=4)
    style.configure("TCombobox", fieldbackground=BG_RAISED, foreground=FG, background=BG_RAISED,
                    arrowcolor=FG, bordercolor=BORDER, padding=3)
    style.map("TCombobox", fieldbackground=[("readonly", BG_RAISED)], foreground=[("readonly", FG)],
              selectbackground=[("readonly", BG_RAISED)], selectforeground=[("readonly", FG)])
    style.configure("TSpinbox", fieldbackground=BG_RAISED, foreground=FG, background=BG_RAISED,
                    arrowcolor=FG, bordercolor=BORDER)
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(4, 4, 4, 0))
    style.configure("TNotebook.Tab", background=BG_RAISED, foreground=FG_DIM, padding=(14, 7), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", BG_PANEL)], foreground=[("selected", FG)])
    style.configure("Horizontal.TScale", background=BG_PANEL, troughcolor=BG_RAISED, sliderlength=18)
    style.configure("Treeview", background=BG_RAISED, fieldbackground=BG_RAISED, foreground=FG,
                    rowheight=px(26), borderwidth=0)
    style.configure("Treeview.Heading", background=BG_PANEL, foreground=FG_DIM, borderwidth=0,
                    font=FONT_SMALL)
    style.map("Treeview", background=[("selected", ACCENT_DARK)], foreground=[("selected", "#ffffff")])
    style.configure("TLabelframe", background=BG_PANEL, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG_PANEL, foreground=FG_DIM, font=FONT_SMALL)
    style.configure("TSeparator", background=BORDER)
    style.configure("TScrollbar", background=BG_RAISED, troughcolor=BG, arrowcolor=FG_DIM, borderwidth=0)
    root.option_add("*TCombobox*Listbox.background", BG_RAISED)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DARK)
    root.option_add("*TCombobox*Listbox.font", FONT)


def flat_button(parent: tk.Misc, text: str, command, *, fg: str = FG, font=FONT_BOLD, padx: int = 6) -> tk.Label:
    """A borderless clickable label used on the frameless overlay."""
    lbl = tk.Label(parent, text=text, bg=parent["bg"], fg=fg, font=font, padx=padx, pady=1, cursor="hand2")
    lbl.bind("<Enter>", lambda e: lbl.configure(bg=BG_HOVER))
    lbl.bind("<Leave>", lambda e: lbl.configure(bg=parent["bg"]))
    lbl.bind("<Button-1>", lambda e: command())
    return lbl
