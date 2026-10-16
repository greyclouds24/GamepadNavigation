"""Always-on-top frameless overlay. While it is open the engine runs."""

from __future__ import annotations

import time
import tkinter as tk
from typing import TYPE_CHECKING

from ..engine import EngineStatus
from . import theme

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App


class Overlay:
    def __init__(self, root: tk.Tk, app: "App") -> None:
        self.root = root
        self.app = app
        self._compact = bool(app.config.overlay.compact)
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._flash_until = 0.0
        self._flash_text = ""
        self._last_status = EngineStatus()

        root.title("Gamepad Macropad")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", float(app.config.overlay.opacity))
        root.configure(bg=theme.BORDER)
        root.protocol("WM_DELETE_WINDOW", app.quit)

        self.frame = tk.Frame(root, bg=theme.BG, padx=8, pady=5)
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self._build()
        self._place_initial()
        self._bind_drag(self.frame)
        self.root.after(1500, self._keep_on_top)

    # -- construction ------------------------------------------------------------

    def _build(self) -> None:
        f = self.frame
        top = tk.Frame(f, bg=theme.BG)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=12, height=12, bg=theme.BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 6))
        self._dot_id = self.dot.create_oval(1, 1, 11, 11, fill=theme.FG_DIM, outline="")
        self.title = tk.Label(top, text="Gamepad Macropad", bg=theme.BG, fg=theme.FG, font=theme.FONT_BOLD, anchor="w")
        self.title.pack(side="left", fill="x", expand=True)

        self.btn_close = theme.flat_button(top, "\u2715", self.app.quit, fg=theme.FG_DIM)
        self.btn_close.pack(side="right")
        self.btn_compact = theme.flat_button(top, "\u2013", self.toggle_compact, fg=theme.FG_DIM)
        self.btn_compact.pack(side="right")
        self.btn_settings = theme.flat_button(top, "\u2699", self.app.open_settings, fg=theme.FG_DIM)
        self.btn_settings.pack(side="right")
        self.btn_mode = theme.flat_button(top, "M", self.app.toggle_input_mode, fg=theme.FG_DIM)
        self.btn_mode.pack(side="right")
        self.btn_pause = theme.flat_button(top, "\u23F8", self._pause_or_numbers, fg=theme.FG_DIM)
        self.btn_pause.pack(side="right")

        self.bottom = tk.Frame(f, bg=theme.BG)
        self.bottom.pack(fill="x", pady=(3, 0))
        self.info = tk.Label(self.bottom, text="", bg=theme.BG, fg=theme.FG_DIM, font=theme.FONT_SMALL, anchor="w")
        self.info.pack(side="left", fill="x", expand=True)
        self.pressed = tk.Label(self.bottom, text="", bg=theme.BG, fg=theme.ACCENT, font=theme.FONT_SMALL, anchor="e")
        self.pressed.pack(side="right")

        self.menu = tk.Menu(self.root, tearoff=0, bg=theme.BG_RAISED, fg=theme.FG,
                            activebackground=theme.ACCENT_DARK, activeforeground="#fff", bd=0)
        self.menu.add_command(label="Settings...", command=self.app.open_settings)
        self.menu.add_command(label="Switch mouse / keyboard", command=self.app.toggle_input_mode)
        self.menu.add_command(label="Number mode (keyboard)", command=self.app.toggle_number_mode)
        self.menu.add_command(label="Pause / resume", command=self.app.toggle_enabled)
        self.menu.add_command(label="Compact / expand", command=self.toggle_compact)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.app.quit)
        for w in (self.frame, top, self.title, self.bottom, self.info, self.pressed, self.dot):
            w.bind("<Button-3>", self._popup)
            self._bind_drag(w)
        if self._compact:
            self._apply_compact()

    def _place_initial(self) -> None:
        self.root.update_idletasks()
        ov = self.app.config.overlay
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = max(self.root.winfo_reqwidth(), 280), self.root.winfo_reqheight()
        x = ov.x if ov.x is not None else sw - w - 24
        y = ov.y if ov.y is not None else sh - h - 80
        x = max(0, min(sw - 60, int(x)))
        y = max(0, min(sh - 40, int(y)))
        self.root.geometry(f"+{x}+{y}")

    # -- drag -------------------------------------------------------------------------

    def _bind_drag(self, widget: tk.Misc) -> None:
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<ButtonRelease-1>", self._drag_end)

    def _drag_start(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, event) -> None:
        if self._drag_origin is None:
            return
        ox, oy, wx, wy = self._drag_origin
        self.root.geometry(f"+{wx + event.x_root - ox}+{wy + event.y_root - oy}")

    def _drag_end(self, event) -> None:
        if self._drag_origin is None:
            return
        self._drag_origin = None
        self.app.config.overlay.x = self.root.winfo_x()
        self.app.config.overlay.y = self.root.winfo_y()
        self.app.save_config()

    def _popup(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _keep_on_top(self) -> None:
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            return
        self.root.after(1500, self._keep_on_top)

    # -- modes ---------------------------------------------------------------------

    def toggle_compact(self) -> None:
        self._compact = not self._compact
        self.app.config.overlay.compact = self._compact
        self.app.save_config()
        self._apply_compact()

    def _apply_compact(self) -> None:
        if self._compact:
            self.bottom.pack_forget()
            self.btn_mode.pack_forget()
            self.btn_pause.pack_forget()
            self.btn_close.pack_forget()
            self.btn_compact.configure(text="\u25A1")
            self.title.configure(text="GP", width=3)
        else:
            self.bottom.pack(fill="x", pady=(3, 0))
            self.btn_close.pack(side="right")
            self.btn_mode.pack(side="right", before=self.btn_settings)
            self.btn_pause.pack(side="right", before=self.btn_mode)
            self.btn_compact.configure(text="\u2013")
            self.title.configure(width=0)
        self.refresh(self._last_status, force=True)

    def set_opacity(self, value: float) -> None:
        self.root.attributes("-alpha", max(0.2, min(1.0, float(value))))

    def flash(self, text: str, seconds: float = 2.0) -> None:
        self._flash_text = text
        self._flash_until = time.monotonic() + seconds

    def _pause_or_numbers(self) -> None:
        if self._last_status.input_mode == "keyboard":
            self.app.toggle_number_mode()
        else:
            self.app.toggle_enabled()

    # -- status ------------------------------------------------------------------------

    def refresh(self, status: EngineStatus, *, force: bool = False) -> None:
        self._last_status = status
        if status.error:
            color, title, info = theme.ERR, "Error", status.error
        elif not status.connected:
            color, title, info = theme.FG_DIM, "No controller", "Connect a gamepad (USB or Bluetooth)"
        elif status.dictating:
            color, title, info = theme.ACCENT, status.device_name, "Mic · listening"
        elif not status.enabled:
            color, title, info = theme.WARN, status.device_name, "Paused - press \u25B6 on the overlay to resume"
        elif self.app.recording:
            color, title, info = theme.ERR, "Recording macro", "Press any controller button to stop"
        elif status.playing_macro:
            color, title, info = theme.ACCENT, status.device_name, f"Playing macro: {status.playing_macro}"
        else:
            color, title = theme.OK, status.device_name
            if status.input_mode == "keyboard":
                info = "Keyboard · aim 95% · release to type"
                if status.number_mode:
                    info += " \u00b7 NUM"
                info += " \u00b7 hold Back+Menu 2s = game"
            elif status.input_mode == "game":
                info = "Game \u00b7 hold Back+Menu 2s to leave"
            else:
                stick = "R-stick arrows" if status.right_stick_mode == "arrows" else "R-stick scroll"
                info = f"Mouse \u00b7 {stick} \u00b7 hold Menu = mic \u00b7 Back+Menu 2s = game"
            if not status.is_controller:
                info += " \u00b7 generic mapping"
        if self._flash_text and time.monotonic() < self._flash_until:
            info = self._flash_text
        elif self._flash_text:
            self._flash_text = ""
        self.dot.itemconfigure(self._dot_id, fill=color)
        if not self._compact:
            self.title.configure(text=title[:34] if title else "Gamepad Macropad")
            self.info.configure(text=info)
            if status.input_mode == "keyboard":
                aimed = " ".join(
                    g for g in (status.left_radial_glyph, status.right_radial_glyph) if g
                )
                self.pressed.configure(text=aimed)
            else:
                self.pressed.configure(text=" ".join(sorted(status.pressed))[:28])
        if status.input_mode == "keyboard":
            self.btn_pause.configure(
                text="#",
                fg=theme.ACCENT if status.number_mode else theme.FG_DIM,
            )
        else:
            self.btn_pause.configure(
                text="\u25B6" if not status.enabled else "\u23F8",
                fg=theme.WARN if not status.enabled else theme.FG_DIM,
            )
        mode_mark = {"keyboard": "K", "game": "G"}.get(status.input_mode, "M")
        mode_fg = theme.FG_DIM if status.input_mode == "mouse" else (
            theme.OK if status.input_mode == "game" else theme.ACCENT
        )
        self.btn_mode.configure(text=mode_mark, fg=mode_fg)
