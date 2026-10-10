"""Bottom-center radial keyboard graphic with live sector highlight."""

from __future__ import annotations

import math
import tkinter as tk
from typing import TYPE_CHECKING

from ..radial import RADIAL_LABELS, items_for
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
            self.win.attributes("-alpha", 0.82)
        except tk.TclError:
            pass
        self.win.configure(bg=theme.BG)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)

        pad = theme.px(8)
        self.canvas = tk.Canvas(
            self.win, bg=theme.BG, highlightthickness=0,
            width=theme.px(460), height=theme.px(130),
        )
        self.canvas.pack(padx=pad, pady=pad)
        self._hint = self.canvas.create_text(
            theme.px(230), theme.px(118),
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
        key = (
            status.number_mode,
            status.radial_opener,
            status.radial_index,
            status.radial_glyph,
            status.shift_held,
            status.ctrl_held,
        )
        if key == self._last_key:
            return
        self._last_key = key
        self._redraw(status)

    def _redraw(self, status: "EngineStatus") -> None:
        c = self.canvas
        c.delete("dial")
        number_mode = status.number_mode
        active = status.radial_opener
        # Four dials in a row; enlarge / emphasize the active opener.
        centers = (
            (theme.px(58), theme.px(55), "LT"),
            (theme.px(155), theme.px(55), "LB"),
            (theme.px(305), theme.px(55), "RB"),
            (theme.px(402), theme.px(55), "RT"),
        )
        for cx, cy, opener in centers:
            items = items_for(opener, number_mode=number_mode)
            is_active = opener == active
            r = theme.px(42) if is_active else theme.px(32)
            self._draw_dial(
                cx, cy, r, items,
                highlight=status.radial_index if is_active else None,
                dim=not items,
                label=RADIAL_LABELS.get(opener, opener),
            )
        mods = []
        if status.number_mode:
            mods.append("NUM")
        if status.shift_held:
            mods.append("SHIFT")
        if status.ctrl_held:
            mods.append("CTRL")
        preview = status.radial_glyph or "·"
        hint = f"{preview}" + (f"  [{' '.join(mods)}]" if mods else "")
        c.itemconfigure(self._hint, text=hint)

    def _draw_dial(
        self,
        cx: int, cy: int, r: int,
        items: tuple[str, ...],
        *,
        highlight: int | None,
        dim: bool,
        label: str,
    ) -> None:
        c = self.canvas
        n = max(1, len(items))
        outline = theme.BORDER if dim or not items else theme.FG_DIM
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=outline, width=2, tags="dial")
        if not items:
            c.create_text(cx, cy, text="—", fill=theme.FG_DIM, font=theme.FONT_SMALL, tags="dial")
            return
        for i, glyph in enumerate(items):
            # Sector i centered on angle i * step (0 = up).
            step = 2.0 * math.pi / n
            a0 = i * step - step / 2.0
            a1 = a0 + step
            # Wedge outline (approximate with arc + spokes drawn lightly).
            on = highlight is not None and i == highlight
            mid = i * step
            # Label position along radius.
            tx = cx + math.sin(mid) * (r * 0.62)
            ty = cy - math.cos(mid) * (r * 0.62)
            # Highlight wedge as a thick pie slice via chord polygon.
            if on:
                points = [cx, cy]
                steps = 8
                for s in range(steps + 1):
                    a = a0 + (a1 - a0) * (s / steps)
                    points.extend([cx + math.sin(a) * r, cy - math.cos(a) * r])
                c.create_polygon(
                    *points, fill=theme.ACCENT_DARK, outline=theme.ACCENT, width=1, tags="dial",
                )
            c.create_text(
                tx, ty,
                text=glyph,
                fill="#ffffff" if on else (theme.FG_DIM if dim else theme.FG),
                font=theme.FONT_BOLD if on else theme.FONT_SMALL,
                tags="dial",
            )
        c.create_text(
            cx, cy + r + theme.px(10),
            text=label.replace("Left ", "L").replace("Right ", "R").replace("trigger", "Trig").replace("bumper", "Bump"),
            fill=theme.ACCENT if highlight is not None else theme.FG_DIM,
            font=theme.FONT_SMALL,
            tags="dial",
        )
