"""Bottom-center dual radial keyboard graphic with live sector highlight."""

from __future__ import annotations

import math
import tkinter as tk
from typing import TYPE_CHECKING

from ..config import Binding
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
            width=theme.px(680), height=theme.px(286),
        )
        self.canvas.pack(padx=pad, pady=pad)
        self._hint = self.canvas.create_text(
            theme.px(340), theme.px(272),
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
        layout = (
            tuple(kb.left), tuple(kb.left_alt), tuple(kb.right), tuple(kb.right_alt),
            tuple((name, kb.binding(name).describe()) for name in (
                "A", "B", "X", "Y", "LB", "LT", "RB", "RT", "LS", "RS",
                "BACK", "START", "GUIDE", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
            )),
        )
        key = (
            status.number_mode,
            status.left_layer,
            status.right_layer,
            status.left_radial_index,
            status.left_radial_glyph,
            status.right_radial_index,
            status.right_radial_glyph,
            status.ctrl_held,
            tuple(sorted(status.pressed)),
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
        pressed = status.pressed
        self._draw_shoulders_and_face(pressed, number_mode=status.number_mode, kb=kb)
        left_label = "LS · layer" + (" 2" if status.left_layer else "")
        right_label = "RS · layer" + (" 2" if status.right_layer else "")
        r = theme.px(64)
        self._draw_dial(
            theme.px(250), theme.px(124), r, left_items,
            highlight=status.left_radial_index if status.left_radial_glyph else None,
            label=left_label,
            label_on="LS" in pressed,
            stick=(status.lx, status.ly),
        )
        self._draw_dial(
            theme.px(430), theme.px(124), r, right_items,
            highlight=status.right_radial_index if status.right_radial_glyph else None,
            label=right_label,
            label_on="RS" in pressed,
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
        label_on: bool,
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
            fill=theme.ACCENT if (highlight is not None or label_on) else theme.FG_DIM,
            font=theme.FONT_SMALL,
            tags="dial",
        )

    def _draw_shoulders_and_face(self, pressed: frozenset[str], *, number_mode: bool, kb) -> None:
        """Left-hand buttons sit left of the left stick; right-hand buttons sit right of the right."""
        def glyph(name: str, empty: str = "—") -> str:
            return _short_action(kb.binding(name), empty)

        def unused(name: str) -> bool:
            return kb.binding(name).type == "none" and name in ("X", "Y")

        self._chip(56, 34, glyph("LB", "A"), "LB", on="LB" in pressed, wide=len(glyph("LB", "A")) > 2)
        self._chip(112, 34, glyph("LT", "E"), "LT", on="LT" in pressed, wide=len(glyph("LT", "E")) > 2)
        self._chip(568, 34, glyph("RT", "O"), "RT", on="RT" in pressed, wide=len(glyph("RT", "O")) > 2)
        self._chip(624, 34, glyph("RB", "I"), "RB", on="RB" in pressed, wide=len(glyph("RB", "I")) > 2)
        self._chip(84, 88, glyph("DPAD_UP", "."), "up", on="DPAD_UP" in pressed, wide=len(glyph("DPAD_UP")) > 2)
        self._chip(40, 128, glyph("DPAD_LEFT", ","), "left", on="DPAD_LEFT" in pressed, wide=len(glyph("DPAD_LEFT")) > 1)
        self._chip(128, 128, glyph("DPAD_RIGHT", "↵"), "right", on="DPAD_RIGHT" in pressed, wide=True)
        self._chip(84, 168, glyph("DPAD_DOWN", "Ctrl"), "down", on="DPAD_DOWN" in pressed, wide=True)
        back = glyph("BACK", "Mode")
        self._chip(84, 224, back, "view", on="BACK" in pressed, wide=True)
        self._chip(596, 88, glyph("Y"), "Y", on="Y" in pressed, unused=unused("Y"), wide=len(glyph("Y")) > 1)
        self._chip(552, 128, glyph("X"), "X", on="X" in pressed, unused=unused("X"), wide=len(glyph("X")) > 1)
        self._chip(640, 128, glyph("B", "⌫"), "B", on="B" in pressed, wide=len(glyph("B", "⌫")) > 1)
        self._chip(596, 168, glyph("A", "Spc"), "A", on="A" in pressed, wide=True)
        menu = glyph("START", "Mic")
        self._chip(552, 224, menu, "menu", on="START" in pressed, wide=True)
        guide = glyph("GUIDE", "#")
        self._chip(640, 224, guide, "guide", on=("GUIDE" in pressed) or number_mode, wide=len(guide) > 1)

    def _chip(
        self,
        x: float,
        y: float,
        glyph: str,
        caption: str,
        *,
        on: bool = False,
        unused: bool = False,
        wide: bool = False,
    ) -> None:
        c = self.canvas
        px, py = theme.px(x), theme.px(y)
        w = theme.px(52 if wide else 40)
        h = theme.px(34)
        if on:
            fill, outline, glyph_fill = theme.ACCENT_DARK, theme.ACCENT, "#ffffff"
        elif unused:
            fill, outline, glyph_fill = theme.BG, theme.BORDER, theme.FG_DIM
        else:
            fill, outline, glyph_fill = theme.BG_RAISED, theme.BORDER, theme.FG
        c.create_rectangle(
            px - w // 2, py - h // 2, px + w // 2, py + h // 2,
            fill=fill, outline=outline, width=1, tags="dial",
        )
        font = theme.FONT_SMALL if len(glyph) > 1 else theme.FONT_BOLD
        c.create_text(px, py - theme.px(5), text=glyph, fill=glyph_fill, font=font, tags="dial")
        c.create_text(px, py + theme.px(9), text=caption, fill=theme.FG_DIM, font=("Segoe UI", 7), tags="dial")


_SHORT_KEYS = {
    "space": "Spc", "backspace": "⌫", "enter": "↵", "return": "↵",
    "ctrl": "Ctrl", "control": "Ctrl", "lctrl": "Ctrl", "rctrl": "Ctrl",
    "shift": "Shift", "lshift": "Shift", "rshift": "Shift",
    "alt": "Alt", "period": ".", "comma": ",", "esc": "Esc", "tab": "Tab",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
}


def _short_action(binding: Binding, empty: str = "—") -> str:
    if binding.type == "key" and binding.keys:
        token = binding.keys[-1].lower()
        if token in _SHORT_KEYS:
            return _SHORT_KEYS[token]
        if len(token) == 1:
            return token.upper()
        return token[:5]
    if binding.type == "macro" and binding.macro:
        return binding.macro[:5]
    if binding.type == "special" and "layer" in binding.special:
        return "Layer"
    return empty
