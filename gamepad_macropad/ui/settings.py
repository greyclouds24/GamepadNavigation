"""Settings window: buttons, sticks, macros, general."""

from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from .. import __version__
from ..config import BUTTON_LABELS, BUTTONS, Binding, Config, Macro, config_path, log_path
from ..engine import EngineStatus, shape_axis, shape_radial
from ..macros import summarize
from ..radial import LEFT_LAYERS, LEFT_NUMBERS, RIGHT_LAYERS, RIGHT_NUMBERS
from . import theme
from .binding_dialog import BindingDialog
from .dialogs import confirm, labeled_scale
from .macro_dialog import MacroDialog

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App


class SettingsWindow(tk.Toplevel):
    def __init__(self, app: "App") -> None:
        super().__init__(app.root)
        self.app = app
        theme.apply(self)
        self.title("Gamepad Macropad - Settings")
        self.configure(bg=theme.BG)
        self.attributes("-topmost", True)
        self.minsize(theme.px(640), theme.px(560))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._save_job: str | None = None
        self._last_pressed: frozenset[str] = frozenset()
        self._last_stick_sample: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._loading_vars = False

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_buttons = ttk.Frame(self.nb, padding=12)
        self.tab_sticks = ttk.Frame(self.nb, padding=12)
        self.tab_keyboard = ttk.Frame(self.nb, padding=12)
        self.tab_macros = ttk.Frame(self.nb, padding=12)
        self.tab_general = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_buttons, text="  Buttons  ")
        self.nb.add(self.tab_sticks, text="  Sticks  ")
        self.nb.add(self.tab_keyboard, text="  Keyboard  ")
        self.nb.add(self.tab_macros, text="  Macros  ")
        self.nb.add(self.tab_general, text="  General  ")
        self._build_buttons()
        self._build_sticks()
        self._build_keyboard()
        self._build_macros()
        self._build_general()
        self._place()

    def _place(self) -> None:
        self.update_idletasks()
        w, h = theme.px(780), theme.px(660)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(w, sw - 40), min(h, sh - 80)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        self.lift()
        self.focus_force()

    @property
    def cfg(self) -> Config:
        return self.app.config

    def close(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)
            self._save_job = None
            self.app.save_config()
        self.app.settings_closed()
        self.destroy()

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(350, self._flush_save)

    def _flush_save(self) -> None:
        self._save_job = None
        self.app.save_config()

    # ======================================================================
    # Buttons tab
    # ======================================================================

    def _build_buttons(self) -> None:
        t = self.tab_buttons
        ttk.Label(t, text="Button bindings", style="Title.TLabel").pack(anchor="w")
        ttk.Label(t, text="Double-click a row (or select it and press Edit) to change its binding. "
                          "Controller mouse and buttons keep working while this window is open.",
                  style="Dim.TLabel", wraplength=theme.px(700), justify="left").pack(anchor="w", pady=(0, 8))
        frame = ttk.Frame(t)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("button", "action"), show="headings", selectmode="browse")
        self.tree.heading("button", text="Controller button")
        self.tree.heading("action", text="Action")
        self.tree.column("button", width=200, anchor="w")
        self.tree.column("action", width=420, anchor="w")
        self.tree.tag_configure("pressed", background="#2b4a5c")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._edit_selected())
        self.tree.bind("<Return>", lambda e: self._edit_selected())
        for name in BUTTONS:
            self.tree.insert("", "end", iid=name, values=(BUTTON_LABELS.get(name, name), ""))
        row = ttk.Frame(t)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Edit...", style="Accent.TButton", command=self._edit_selected).pack(side="left")
        ttk.Button(row, text="Clear", command=self._clear_selected).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Reset all to defaults", command=self._reset_bindings).pack(side="right")
        self._refresh_bindings()

    def _refresh_bindings(self) -> None:
        for name in BUTTONS:
            self.tree.item(name, values=(BUTTON_LABELS.get(name, name), self.cfg.binding(name).describe()))

    def _selected_button(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _edit_selected(self) -> None:
        name = self._selected_button()
        if name is None:
            return

        def saved(binding: Binding) -> None:
            self.cfg.bindings[name] = binding
            self.app.config_changed()
            self._refresh_bindings()

        BindingDialog(self, self.app, name, self.cfg.binding(name), saved).show()

    def _clear_selected(self) -> None:
        name = self._selected_button()
        if name is None:
            return
        self.cfg.bindings[name] = Binding()
        self.app.config_changed()
        self._refresh_bindings()

    def _reset_bindings(self) -> None:
        if not confirm(self, "Reset bindings", "Replace every button binding with the defaults?"):
            return
        self.cfg.bindings = Config.defaults().bindings
        self.app.config_changed()
        self._refresh_bindings()

    # ======================================================================
    # Sticks tab
    # ======================================================================

    def _build_sticks(self) -> None:
        t = self.tab_sticks
        p, s = self.cfg.pointer, self.cfg.scroll
        top = ttk.Frame(t)
        top.pack(fill="x")
        left = ttk.Frame(top)
        left.pack(side="left", fill="both", expand=True)
        viz = ttk.Frame(top)
        viz.pack(side="right", padx=(16, 0))

        pf = ttk.LabelFrame(left, text="Left stick - cursor", padding=10)
        pf.pack(fill="x")
        self.v_speed = tk.DoubleVar(value=p.speed)
        self.v_accel = tk.DoubleVar(value=p.acceleration)
        self.v_dz = tk.DoubleVar(value=p.deadzone)
        self.v_prec = tk.DoubleVar(value=p.precision_scale)
        self.v_inv_y = tk.BooleanVar(value=p.invert_y)
        labeled_scale(pf, "Speed (px/s)", self.v_speed, 200, 5000, fmt="{:.0f}", row=0, command=self._sticks_changed)
        labeled_scale(pf, "Response curve", self.v_accel, 1.0, 14.0, row=1, command=self._sticks_changed)
        labeled_scale(pf, "Dead zone", self.v_dz, 0.0, 0.5, row=2, command=self._sticks_changed)
        labeled_scale(pf, "Sub-rim speed (precision band)", self.v_prec, 0.05, 1.0, row=3,
                      command=self._sticks_changed)
        ttk.Checkbutton(pf, text="Invert vertical", variable=self.v_inv_y, command=self._sticks_changed).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        sf = ttk.LabelFrame(left, text="Right stick - scroll wheel", padding=10)
        sf.pack(fill="x", pady=(10, 0))
        self.v_sspeed = tk.DoubleVar(value=s.speed)
        self.v_hspeed = tk.DoubleVar(value=s.horizontal_speed)
        self.v_sdz = tk.DoubleVar(value=s.deadzone)
        self.v_saccel = tk.DoubleVar(value=s.acceleration)
        self.v_smooth = tk.BooleanVar(value=s.smooth)
        self.v_horiz = tk.BooleanVar(value=s.horizontal_enabled)
        self.v_inv_v = tk.BooleanVar(value=s.invert_vertical)
        self.v_inv_h = tk.BooleanVar(value=s.invert_horizontal)
        labeled_scale(sf, "Vertical speed (notches/s)", self.v_sspeed, 1, 60, fmt="{:.0f}", row=0,
                      command=self._sticks_changed)
        labeled_scale(sf, "Horizontal speed", self.v_hspeed, 1, 60, fmt="{:.0f}", row=1, command=self._sticks_changed)
        labeled_scale(sf, "Dead zone", self.v_sdz, 0.0, 0.6, row=2, command=self._sticks_changed)
        labeled_scale(sf, "Response curve", self.v_saccel, 1.0, 14.0, row=3, command=self._sticks_changed)
        checks = ttk.Frame(sf)
        checks.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(checks, text="Smooth scrolling", variable=self.v_smooth, command=self._sticks_changed).grid(
            row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Checkbutton(checks, text="Horizontal scroll (left/right)", variable=self.v_horiz,
                        command=self._sticks_changed).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(checks, text="Invert vertical", variable=self.v_inv_v, command=self._sticks_changed).grid(
            row=1, column=0, sticky="w", padx=(0, 12))
        ttk.Checkbutton(checks, text="Invert horizontal", variable=self.v_inv_h, command=self._sticks_changed).grid(
            row=1, column=1, sticky="w")

        ttk.Label(viz, text="Live", style="Dim.TLabel").pack()
        px = theme.px
        self._viz_cx, self._viz_r = px(75), px(55)
        self._viz_cy = (px(75), px(220))
        self.canvas = tk.Canvas(viz, width=px(150), height=px(300), bg=theme.BG_PANEL, highlightthickness=0)
        self.canvas.pack()
        self._viz_items = {}
        cx, r = self._viz_cx, self._viz_r
        for i, label in enumerate(("Left", "Right")):
            cy = self._viz_cy[i]
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=theme.BORDER, width=2)
            self._viz_items[f"dz{i}"] = self.canvas.create_oval(cx - px(10), cy - px(10), cx + px(10), cy + px(10),
                                                                 outline=theme.FG_DIM, dash=(2, 2))
            self._viz_items[f"raw{i}"] = self.canvas.create_oval(cx - px(4), cy - px(4), cx + px(4), cy + px(4),
                                                                  fill=theme.FG_DIM, outline="")
            self._viz_items[f"out{i}"] = self.canvas.create_oval(cx - px(6), cy - px(6), cx + px(6), cy + px(6),
                                                                  fill=theme.ACCENT, outline="")
            self.canvas.create_text(cx, cy + r + px(11), text=label, fill=theme.FG_DIM, font=theme.FONT_SMALL)
        ttk.Button(t, text="Reset stick settings", command=self._reset_sticks).pack(anchor="e", pady=(10, 0))

    def _load_stick_vars(self) -> None:
        """Push current config stick settings into the UI controls."""
        p, s = self.cfg.pointer, self.cfg.scroll
        self._loading_vars = True
        try:
            for var, value in (
                (self.v_speed, p.speed), (self.v_accel, p.acceleration), (self.v_dz, p.deadzone),
                (self.v_prec, p.precision_scale), (self.v_inv_y, p.invert_y),
                (self.v_sspeed, s.speed), (self.v_hspeed, s.horizontal_speed), (self.v_sdz, s.deadzone),
                (self.v_saccel, s.acceleration), (self.v_smooth, s.smooth),
                (self.v_horiz, s.horizontal_enabled), (self.v_inv_v, s.invert_vertical),
                (self.v_inv_h, s.invert_horizontal),
            ):
                var.set(value)
        finally:
            self._loading_vars = False
        self._redraw_stick_viz(*self._last_stick_sample)

    def _sticks_changed(self) -> None:
        if self._loading_vars:
            return
        p, s = self.cfg.pointer, self.cfg.scroll
        p.speed = float(self.v_speed.get())
        p.acceleration = float(self.v_accel.get())
        p.deadzone = float(self.v_dz.get())
        p.precision_scale = float(self.v_prec.get())
        p.invert_y = bool(self.v_inv_y.get())
        s.speed = float(self.v_sspeed.get())
        s.horizontal_speed = float(self.v_hspeed.get())
        s.deadzone = float(self.v_sdz.get())
        s.acceleration = float(self.v_saccel.get())
        s.smooth = bool(self.v_smooth.get())
        s.horizontal_enabled = bool(self.v_horiz.get())
        s.invert_vertical = bool(self.v_inv_v.get())
        s.invert_horizontal = bool(self.v_inv_h.get())
        self.app.config_changed(persist=False)
        self._schedule_save()
        self._redraw_stick_viz(*self._last_stick_sample)

    def _reset_sticks(self) -> None:
        d = Config.defaults()
        self.cfg.pointer = d.pointer
        self.cfg.scroll = d.scroll
        self._load_stick_vars()
        self.app.config_changed()

    def _redraw_stick_viz(self, lx: float, ly: float, rx: float, ry: float) -> None:
        p, s = self.cfg.pointer, self.cfg.scroll
        ox, oy, _ = shape_radial(lx, ly, p.deadzone, p.acceleration, precision_scale=p.precision_scale)
        self._draw_stick(0, (lx, ly), (ox, oy), p.deadzone)
        scroll_precision = 0.35
        sx = (
            shape_axis(rx, s.deadzone, s.acceleration, precision_scale=scroll_precision)
            if s.horizontal_enabled else 0.0
        )
        sy = shape_axis(ry, s.deadzone, s.acceleration, precision_scale=scroll_precision)
        self._draw_stick(1, (rx, ry), (sx, sy), s.deadzone)

    def _draw_stick(self, index: int, raw: tuple[float, float], out: tuple[float, float], dz: float) -> None:
        cx, r, cy = self._viz_cx, self._viz_r, self._viz_cy[index]
        c, px = self.canvas, theme.px
        c.coords(self._viz_items[f"dz{index}"], cx - r * dz, cy - r * dz, cx + r * dz, cy + r * dz)
        rx, ry = cx + raw[0] * r, cy + raw[1] * r
        c.coords(self._viz_items[f"raw{index}"], rx - px(4), ry - px(4), rx + px(4), ry + px(4))
        ox, oy = cx + out[0] * r, cy + out[1] * r
        c.coords(self._viz_items[f"out{index}"], ox - px(6), oy - px(6), ox + px(6), oy + px(6))

    # ======================================================================
    # Keyboard (radial) tab
    # ======================================================================

    def _build_keyboard(self) -> None:
        t = self.tab_keyboard
        ttk.Label(t, text="Keyboard mode (dual radials)", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            t,
            text="Back / View switches mouse ↔ keyboard. Both stick wheels are always live: "
                 "push a stick past 95% to aim a letter, then release toward center to type it. "
                 "Guide / Pause toggles number mode.",
            style="Dim.TLabel", wraplength=theme.px(700), justify="left",
        ).pack(anchor="w", pady=(0, 8))

        opts = ttk.Frame(t)
        opts.pack(fill="x", pady=(0, 8))
        self.v_hud = tk.BooleanVar(value=self.cfg.keyboard.show_hud)
        ttk.Checkbutton(
            opts, text="Show radial graphic (bottom center) while in keyboard mode",
            variable=self.v_hud, command=self._keyboard_opts_changed,
        ).pack(anchor="w")

        frame = ttk.Frame(t)
        frame.pack(fill="both", expand=True)
        self.radial_tree = ttk.Treeview(
            frame, columns=("wheel", "layer0", "layer1", "numbers"), show="headings", selectmode="browse",
        )
        self.radial_tree.heading("wheel", text="Wheel")
        self.radial_tree.heading("layer0", text="Default (clockwise from up)")
        self.radial_tree.heading("layer1", text="Alt (stick click)")
        self.radial_tree.heading("numbers", text="Number mode")
        self.radial_tree.column("wheel", width=100, anchor="w")
        self.radial_tree.column("layer0", width=220, anchor="w")
        self.radial_tree.column("layer1", width=220, anchor="w")
        self.radial_tree.column("numbers", width=140, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.radial_tree.yview)
        self.radial_tree.configure(yscrollcommand=sb.set)
        self.radial_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for side in ("left", "right"):
            self.radial_tree.insert("", "end", iid=side, values=("", "", "", ""))

        extras = ttk.LabelFrame(t, text="Also in keyboard mode", padding=10)
        extras.pack(fill="x", pady=(10, 0))
        ttk.Label(
            extras,
            text="Vowels: LB → A · LT → E · RB → I · RT → O\n"
                 "A → space · B → backspace · D-pad ↑ . · ← , · → enter · ↓ ctrl\n"
                 "LS click swaps left wheel layer · RS click swaps right wheel layer",
            style="Dim.TLabel", justify="left",
        ).pack(anchor="w")
        self._refresh_radial_legend()

    def _refresh_radial_legend(self) -> None:
        self.radial_tree.item("left", values=(
            "Left stick",
            " ".join(LEFT_LAYERS[0]),
            " ".join(LEFT_LAYERS[1]),
            " ".join(LEFT_NUMBERS),
        ))
        self.radial_tree.item("right", values=(
            "Right stick",
            " ".join(RIGHT_LAYERS[0]),
            " ".join(RIGHT_LAYERS[1]),
            " ".join(RIGHT_NUMBERS),
        ))

    def _keyboard_opts_changed(self) -> None:
        self.cfg.keyboard.show_hud = bool(self.v_hud.get())
        self.app.config_changed()
        self.app.sync_radial_hud()

    # ======================================================================
    # Macros tab
    # ======================================================================

    def _build_macros(self) -> None:
        t = self.tab_macros
        ttk.Label(t, text="Macros", style="Title.TLabel").pack(anchor="w")
        ttk.Label(t, text="Recorded key/mouse sequences. Assign one to a button from the Buttons tab.",
                  style="Dim.TLabel").pack(anchor="w", pady=(0, 8))
        frame = ttk.Frame(t)
        frame.pack(fill="both", expand=True)
        self.macro_tree = ttk.Treeview(frame, columns=("name", "summary", "used"), show="headings", selectmode="browse")
        self.macro_tree.heading("name", text="Name")
        self.macro_tree.heading("summary", text="Contents")
        self.macro_tree.heading("used", text="Bound to")
        self.macro_tree.column("name", width=180)
        self.macro_tree.column("summary", width=320)
        self.macro_tree.column("used", width=120)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.macro_tree.yview)
        self.macro_tree.configure(yscrollcommand=sb.set)
        self.macro_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.macro_tree.bind("<Double-1>", lambda e: self._edit_macro())
        row = ttk.Frame(t)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="\u25CF Record new...", style="Accent.TButton", command=self._new_macro).pack(side="left")
        ttk.Button(row, text="Edit...", command=self._edit_macro).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="\u25B6 Play", command=self._play_macro).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Delete", style="Danger.TButton", command=self._delete_macro).pack(side="right")
        self._refresh_macros()

    def _refresh_macros(self) -> None:
        self.macro_tree.delete(*self.macro_tree.get_children())
        used: dict[str, list[str]] = {}
        for btn, binding in self.cfg.bindings.items():
            if binding.type == "macro" and binding.macro:
                used.setdefault(binding.macro, []).append(btn)
        for name in sorted(self.cfg.macros):
            macro = self.cfg.macros[name]
            self.macro_tree.insert("", "end", iid=name, values=(name, summarize(macro), ", ".join(used.get(name, []))))

    def _selected_macro(self) -> Macro | None:
        sel = self.macro_tree.selection()
        return self.cfg.macros.get(sel[0]) if sel else None

    def _new_macro(self) -> None:
        def saved(macro: Macro) -> None:
            self.app.add_macro(macro)
            self._refresh_macros()

        MacroDialog(self, self.app, None, saved, existing_names=set(self.cfg.macros)).show()

    def _edit_macro(self) -> None:
        macro = self._selected_macro()
        if macro is None:
            return
        old_name = macro.name

        def saved(updated: Macro) -> None:
            self.app.replace_macro(old_name, updated)
            self._refresh_macros()
            self._refresh_bindings()

        MacroDialog(self, self.app, macro, saved, existing_names=set(self.cfg.macros)).show()

    def _play_macro(self) -> None:
        macro = self._selected_macro()
        if macro is not None:
            self.app.engine.play_macro(macro)

    def _delete_macro(self) -> None:
        macro = self._selected_macro()
        if macro is None:
            return
        if not confirm(self, "Delete macro", f'Delete macro "{macro.name}"? Buttons using it will be cleared.'):
            return
        self.app.delete_macro(macro.name)
        self._refresh_macros()
        self._refresh_bindings()

    # ======================================================================
    # General tab
    # ======================================================================

    def _build_general(self) -> None:
        t = self.tab_general
        c = self.cfg
        df = ttk.LabelFrame(t, text="Controller", padding=10)
        df.pack(fill="x")
        row = ttk.Frame(df)
        row.pack(fill="x")
        ttk.Label(row, text="Device").pack(side="left")
        self.device_combo = ttk.Combobox(row, state="readonly", width=44)
        self.device_combo.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self.device_combo.bind("<<ComboboxSelected>>", self._device_selected)
        ttk.Button(row, text="Refresh", command=self._rescan_devices).pack(side="left", padx=(8, 0))
        self.device_note = ttk.Label(df, text="", style="Dim.TLabel", wraplength=theme.px(640), justify="left")
        self.device_note.pack(anchor="w", pady=(6, 0))
        grid = ttk.Frame(df)
        grid.pack(fill="x", pady=(8, 0))
        self.v_trigger = tk.DoubleVar(value=c.trigger_threshold)
        labeled_scale(grid, "Trigger press threshold", self.v_trigger, 0.1, 0.95, row=0, command=self._general_changed)
        self.v_haptics = tk.BooleanVar(value=c.haptics)
        ttk.Checkbutton(grid, text="Rumble when a macro starts / recording stops", variable=self.v_haptics,
                        command=self._general_changed).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        kf = ttk.LabelFrame(t, text="Key auto-repeat", padding=10)
        kf.pack(fill="x", pady=(10, 0))
        self.v_rdelay = tk.DoubleVar(value=c.repeat_delay_ms)
        self.v_rrate = tk.DoubleVar(value=c.repeat_rate_hz)
        labeled_scale(kf, "Delay before repeat (ms)", self.v_rdelay, 100, 1000, fmt="{:.0f}", row=0,
                      command=self._general_changed)
        labeled_scale(kf, "Repeat rate (per second)", self.v_rrate, 2, 60, fmt="{:.0f}", row=1,
                      command=self._general_changed)

        of = ttk.LabelFrame(t, text="Overlay & app", padding=10)
        of.pack(fill="x", pady=(10, 0))
        self.v_opacity = tk.DoubleVar(value=c.overlay.opacity)
        labeled_scale(of, "Overlay opacity", self.v_opacity, 0.3, 1.0, row=0, command=self._general_changed)
        self.v_poll = tk.DoubleVar(value=c.poll_hz)
        labeled_scale(of, "Polling rate (Hz)", self.v_poll, 60, 500, fmt="{:.0f}", row=1, command=self._general_changed)
        self.v_start_enabled = tk.BooleanVar(value=c.start_enabled)
        ttk.Checkbutton(of, text="Start converting input immediately on launch", variable=self.v_start_enabled,
                        command=self._general_changed).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        info = ttk.Frame(t)
        info.pack(fill="x", pady=(14, 0))
        ttk.Label(info, text=f"Config: {config_path()}", style="Dim.TLabel").pack(anchor="w")
        ttk.Label(info, text=f"Log: {log_path()}", style="Dim.TLabel").pack(anchor="w")
        btns = ttk.Frame(info)
        btns.pack(anchor="w", pady=(6, 0))
        ttk.Button(btns, text="Open config folder", command=self._open_config_folder).pack(side="left")
        ttk.Label(info, text=f"Gamepad Macropad {__version__}", style="Dim.TLabel").pack(anchor="w", pady=(10, 0))
        self._refresh_devices()

    def _rescan_devices(self) -> None:
        self.app.engine.request_rescan()
        self.after(700, self._refresh_devices)

    def _refresh_devices(self) -> None:
        if not self.winfo_exists():
            return
        devices = self.app.engine.list_devices()
        self._devices = devices
        values = ["Auto (first recognized controller)"] + [
            f"{d.name}  [{d.guid[:8]}]{'' if d.is_controller else '  (generic)'}" for d in devices
        ]
        self.device_combo.configure(values=values)
        index = 0
        for i, d in enumerate(devices):
            if d.guid == self.cfg.preferred_device:
                index = i + 1
        self.device_combo.current(index)
        if not devices:
            self.device_note.configure(text="No gamepads detected. Connect one via USB or Bluetooth; it is picked up automatically.")
        elif any(not d.is_controller for d in devices):
            self.device_note.configure(text="Devices marked (generic) have no SDL controller mapping; an Xbox-style button order is assumed.")
        else:
            self.device_note.configure(text=f"{len(devices)} device(s) detected.")

    def _device_selected(self, _event=None) -> None:
        index = self.device_combo.current()
        self.cfg.preferred_device = "" if index <= 0 else self._devices[index - 1].guid
        self.app.config_changed()
        # Engine will open the preferred pad; profile swap happens when status.guid changes.

    def _load_general_vars(self) -> None:
        c = self.cfg
        self._loading_vars = True
        try:
            self.v_trigger.set(c.trigger_threshold)
            self.v_haptics.set(c.haptics)
            self.v_rdelay.set(c.repeat_delay_ms)
            self.v_rrate.set(c.repeat_rate_hz)
            self.v_opacity.set(c.overlay.opacity)
            self.v_poll.set(c.poll_hz)
            self.v_start_enabled.set(c.start_enabled)
        finally:
            self._loading_vars = False

    def _general_changed(self) -> None:
        if self._loading_vars:
            return
        c = self.cfg
        c.trigger_threshold = float(self.v_trigger.get())
        c.haptics = bool(self.v_haptics.get())
        c.repeat_delay_ms = int(self.v_rdelay.get())
        c.repeat_rate_hz = float(self.v_rrate.get())
        c.poll_hz = int(self.v_poll.get())
        c.start_enabled = bool(self.v_start_enabled.get())
        c.overlay.opacity = float(self.v_opacity.get())
        self.app.overlay.set_opacity(c.overlay.opacity)
        self.app.config_changed(persist=False)
        self._schedule_save()

    def on_device_profile_changed(self) -> None:
        """Active pad changed: reload per-device controls and lists."""
        self._load_stick_vars()
        self._load_general_vars()
        self._refresh_bindings()
        self._refresh_radial_legend()
        self._refresh_macros()
        self._refresh_devices()
        self.v_hud.set(self.cfg.keyboard.show_hud)

    def _open_config_folder(self) -> None:
        folder = config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            subprocess.Popen(["explorer", str(folder)])

    # ======================================================================
    # live status
    # ======================================================================

    def refresh_status(self, status: EngineStatus) -> None:
        if status.pressed != self._last_pressed:
            for name in self._last_pressed - status.pressed:
                if self.tree.exists(name):
                    self.tree.item(name, tags=())
            for name in status.pressed:
                if self.tree.exists(name):
                    self.tree.item(name, tags=("pressed",))
            self._last_pressed = status.pressed
        self._last_stick_sample = (status.lx, status.ly, status.rx, status.ry)
        try:
            tab = self.nb.index(self.nb.select())
        except tk.TclError:
            return
        if tab == 1:
            self._redraw_stick_viz(status.lx, status.ly, status.rx, status.ry)
