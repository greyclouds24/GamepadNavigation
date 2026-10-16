"""Bottom-center dual radial keyboard graphic with live sector highlight."""

from __future__ import annotations

import math
import tkinter as tk
from typing import TYPE_CHECKING

from ..radial import items_for
from . import theme

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App
    from ..engine import EngineStatus


class RadialHud:
    def __init__(self, root: tk.Tk, app: "App") -> None:
        self.app = app
        self._visible = False
        self._last_key: tuple = ()

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        self.win.configure(bg=theme.BG)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)

        pad = theme.px(10)
        self.canvas = tk.Canvas(
            self.win, bg=theme.BG, highlightthickness=0,
            width=theme.px(360), height=theme.px(200),
        )
        self.canvas.pack(padx=pad, pady=pad)
        self._hint = self.canvas.create_text(
            theme.px(180), theme.px(188),
            text="", fill=theme.FG_DIM, font=theme.FONT_SMALL,
        )
        self._place()

    def _place(self) -> None:
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, sh - h - theme.px(24))}")

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._visible:
            if visible:
                try:
                    self.win.lift()
                except tk.TclError:
                    pass
            return
        self._visible = visible
        try:
            if visible:
                self._place()
                self.win.deiconify()
                self.win.lift()
            else:
                self.win.withdraw()
        except tk.TclError:
            pass

    def refresh(self, status: "EngineStatus") -> None:
        show = (
            bool(self.app.config.keyboard.show_hud)
            and status.enabled
            and status.input_mode == "keyboard"
            and status.connected
        )
        self.set_visible(show)
        if not show:
            return
        kb = self.app.config.keyboard
        layout = (tuple(kb.left), tuple(kb.left_alt), tuple(kb.right), tuple(kb.right_alt))
        key = (
            status.number_mode,
            status.left_layer,
            status.right_layer,
            status.left_radial_index,
            status.left_radial_glyph,
            status.right_radial_index,
            status.right_radial_glyph,
            status.ctrl_held,
            layout,
            round(status.lx, 2), round(status.ly, 2),
            round(status.rx, 2), round(status.ry, 2),
        )
        if key == self._last_key:
            return
        self._last_key = key
        self._redraw(status)

    def _redraw(self, status: "EngineStatus") -> None:
        c = self.canvas
        c.delete("dial")
        kb = self.app.config.keyboard
        left_items = items_for(
            "left", layer=status.left_layer, number_mode=status.number_mode,
            layers=kb.layers_for("left"),
        )
        right_items = items_for(
            "right", layer=status.right_layer, number_mode=status.number_mode,
            layers=kb.layers_for("right"),
        )
        left_label = "Left" + (" · alt" if status.left_layer else "")
        right_label = "Right" + (" · alt" if status.right_layer else "")
        r = theme.px(68)
        self._draw_dial(
            theme.px(90), theme.px(88), r, left_items,
            highlight=status.left_radial_index if status.left_radial_glyph else None,
            label=left_label,
            stick=(status.lx, status.ly),
        )
        self._draw_dial(
            theme.px(270), theme.px(88), r, right_items,
            highlight=status.right_radial_index if status.right_radial_glyph else None,
            label=right_label,
            stick=(status.rx, status.ry),
        )
        mods = []
        if status.number_mode:
            mods.append("NUM")
        if status.ctrl_held:
            mods.append("CTRL")
        preview_parts = [g for g in (status.left_radial_glyph, status.right_radial_glyph) if g]
        preview = " · ".join(preview_parts) if preview_parts else "aim ≥95% · release to type"
        if mods:
            preview += f"  [{' '.join(mods)}]"
        c.itemconfigure(self._hint, text=preview)

    def _draw_dial(
        self,
        cx: int, cy: int, r: int,
        items: tuple[str, ...],
        *,
        highlight: int | None,
        label: str,
        stick: tuple[float, float],
    ) -> None:
        c = self.canvas
        n = max(1, len(items))
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=theme.FG_DIM, width=2, tags="dial")
        # Aim ring at 95% radius (visual cue for the select threshold).
        aim_r = int(r * 0.95)
        c.create_oval(
            cx - aim_r, cy - aim_r, cx + aim_r, cy + aim_r,
            outline=theme.BORDER, width=1, dash=(2, 3), tags="dial",
        )
        for i, glyph in enumerate(items):
            step = 2.0 * math.pi / n
            a0 = i * step - step / 2.0
            a1 = a0 + step
            on = highlight is not None and i == highlight
            mid = i * step
            tx = cx + math.sin(mid) * (r * 0.62)
            ty = cy - math.cos(mid) * (r * 0.62)
            if on:
                points = [cx, cy]
                steps = 10
                for s in range(steps + 1):
                    a = a0 + (a1 - a0) * (s / steps)
                    points.extend([cx + math.sin(a) * r, cy - math.cos(a) * r])
                c.create_polygon(
                    *points, fill=theme.ACCENT_DARK, outline=theme.ACCENT, width=1, tags="dial",
                )
            c.create_text(
                tx, ty,
                text=glyph,
                fill="#ffffff" if on else theme.FG,
                font=theme.FONT_BOLD if on else theme.FONT,
                tags="dial",
            )
        # Stick tip marker (clamped to the dial).
        sx, sy = stick
        mag = min(1.0, math.hypot(sx, sy))
        if mag > 0.02:
            mx = cx + (sx / mag if mag else 0) * mag * r
            my = cy + (sy / mag if mag else 0) * mag * r
            c.create_oval(mx - 4, my - 4, mx + 4, my + 4, fill=theme.ACCENT, outline="", tags="dial")
        c.create_text(
            cx, cy + r + theme.px(14),
            text=label,
            fill=theme.ACCENT if highlight is not None else theme.FG_DIM,
            font=theme.FONT_SMALL,
            tags="dial",
        )
