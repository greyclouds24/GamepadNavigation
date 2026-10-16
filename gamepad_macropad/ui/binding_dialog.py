"""Edit what one controller button does."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

from .. import keys
from ..config import (
    ACTION_TYPE_LABELS,
    ACTION_TYPES,
    BUTTON_LABELS,
    SPECIAL_LABELS,
    SPECIALS,
    Binding,
)
from ..win_input import MOUSE_BUTTONS
from . import theme
from .dialogs import Dialog
from .macro_dialog import MacroDialog

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App


class BindingDialog(Dialog):
    def __init__(self, parent: tk.Misc, app: "App", button_name: str, binding: Binding,
                 on_save: Callable[[Binding], None], *,
                 kinds: tuple[str, ...] | None = None,
                 specials: tuple[str, ...] | None = None) -> None:
        super().__init__(parent, f"Bind {BUTTON_LABELS.get(button_name, button_name)}", width=520, modal=False)
        self.app = app
        self.button_name = button_name
        self.on_save = on_save
        self._capture = None
        self._kinds = kinds or ACTION_TYPES
        self._specials = specials or SPECIALS

        self.type_var = tk.StringVar(value=binding.type)
        self.keys_var = tk.StringVar(value="+".join(binding.keys))
        self.repeat_var = tk.BooleanVar(value=binding.repeat)
        self.mouse_var = tk.StringVar(value=binding.button if binding.button in MOUSE_BUTTONS else "left")
        self.macro_var = tk.StringVar(value=binding.macro)
        self.special_var = tk.StringVar(value=binding.special or SPECIALS[0])
        self._initial_text = binding.text
        self._build()
        self._show_section()

    # -- layout ----------------------------------------------------------------------

    def _build(self) -> None:
        b = self.body
        ttk.Label(b, text=BUTTON_LABELS.get(self.button_name, self.button_name), style="Title.TLabel").pack(anchor="w")
        ttk.Label(b, text="Choose what happens when this button is pressed.", style="Dim.TLabel").pack(
            anchor="w", pady=(0, 10))

        types = ttk.Frame(b)
        types.pack(fill="x")
        if self.type_var.get() not in self._kinds:
            self.type_var.set("none" if "none" in self._kinds else self._kinds[0])
        for t in self._kinds:
            label = "Wheel layer" if t == "special" and self._specials != SPECIALS else ACTION_TYPE_LABELS[t]
            ttk.Radiobutton(types, text=label, value=t, variable=self.type_var,
                            command=self._show_section).pack(anchor="w", pady=1)

        ttk.Separator(b).pack(fill="x", pady=10)
        self.sections = ttk.Frame(b)
        self.sections.pack(fill="both", expand=True)
        self._frames: dict[str, ttk.Frame] = {}

        # key
        f = self._section("key")
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.keys_var, width=28).pack(side="left", fill="x", expand=True)
        self.btn_capture = ttk.Button(row, text="Press keys...", command=self._capture_chord)
        self.btn_capture.pack(side="left", padx=(8, 0))
        self.capture_status = ttk.Label(f, text="Type names separated by + (e.g. ctrl+shift+s), or click "
                                                "Press keys... and press the combination on your keyboard.",
                                        style="Dim.TLabel", wraplength=theme.px(470), justify="left")
        self.capture_status.pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(f, text="Auto-repeat while held (like holding a keyboard key)",
                        variable=self.repeat_var).pack(anchor="w", pady=(8, 0))

        # mouse
        f = self._section("mouse")
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Mouse button").pack(side="left")
        ttk.Combobox(row, textvariable=self.mouse_var, values=list(MOUSE_BUTTONS), state="readonly",
                     width=10).pack(side="left", padx=(8, 0))
        ttk.Label(f, text="Held down while the controller button is held, so you can drag.",
                  style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(f, text="Turbo: click repeatedly while held", variable=self.repeat_var).pack(
            anchor="w", pady=(8, 0))

        # double click
        f = self._section("double_click")
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Mouse button").pack(side="left")
        ttk.Combobox(row, textvariable=self.mouse_var, values=list(MOUSE_BUTTONS), state="readonly",
                     width=10).pack(side="left", padx=(8, 0))

        # text
        f = self._section("text")
        ttk.Label(f, text="Text to type (Unicode is fine):").pack(anchor="w")
        self.text_widget = tk.Text(f, height=4, width=50, bg=theme.BG_RAISED, fg=theme.FG, insertbackground=theme.FG,
                                   relief="flat", font=theme.FONT, wrap="word")
        self.text_widget.insert("1.0", self._initial_text)
        self.text_widget.pack(fill="x", pady=(4, 0))

        # macro
        f = self._section("macro")
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Macro").pack(side="left")
        self.macro_combo = ttk.Combobox(row, textvariable=self.macro_var, values=self._macro_names(),
                                        state="readonly", width=28)
        self.macro_combo.pack(side="left", padx=(8, 0), fill="x", expand=True)
        ttk.Button(row, text="Record new...", command=self._record_macro).pack(side="left", padx=(8, 0))
        self.macro_hint = ttk.Label(f, text="", style="Dim.TLabel", wraplength=theme.px(470), justify="left")
        self.macro_hint.pack(anchor="w", pady=(6, 0))
        self.macro_combo.bind("<<ComboboxSelected>>", lambda e: self._update_macro_hint())
        self._update_macro_hint()

        # special
        f = self._section("special")
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Function").pack(side="left")
        self.special_combo = ttk.Combobox(
            row, values=[SPECIAL_LABELS.get(s, s) for s in self._specials], state="readonly", width=36,
        )
        self.special_combo.pack(side="left", padx=(8, 0))
        current = self.special_var.get()
        self.special_combo.current(self._specials.index(current) if current in self._specials else 0)

        # none
        f = self._section("none")
        ttk.Label(f, text="This button does nothing.", style="Dim.TLabel").pack(anchor="w")

        ttk.Separator(b).pack(fill="x", pady=10)
        bottom = ttk.Frame(b)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(bottom, text="Save", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 8))
        self.error = ttk.Label(bottom, text="", foreground=theme.ERR)
        self.error.pack(side="left")

    def _section(self, name: str) -> ttk.Frame:
        frame = ttk.Frame(self.sections)
        self._frames[name] = frame
        return frame

    def _show_section(self) -> None:
        for name, frame in self._frames.items():
            frame.pack_forget()
        frame = self._frames.get(self.type_var.get())
        if frame is not None:
            frame.pack(fill="both", expand=True)

    # -- key capture ----------------------------------------------------------------

    def _capture_chord(self) -> None:
        if self._capture is not None and self._capture.active:
            self._capture.cancel()
            self._capture = None
            self.btn_capture.configure(text="Press keys...")
            self.capture_status.configure(text="Capture cancelled.")
            return
        from ..win_hooks import ChordCapture

        def done(vks: list[int]) -> None:
            self.after(0, lambda: self._chord_captured(vks))

        try:
            self._capture = ChordCapture(done)
            self._capture.start()
        except OSError as exc:
            self.capture_status.configure(text=f"Key capture unavailable: {exc}")
            self._capture = None
            return
        self.btn_capture.configure(text="Cancel")
        self.capture_status.configure(text="Press the key combination now (keys are not passed to other apps)...")

    def _chord_captured(self, vks: list[int]) -> None:
        self._capture = None
        if not self.winfo_exists():
            return
        self.btn_capture.configure(text="Press keys...")
        tokens = []
        for vk in vks:
            token = keys.vk_to_token(vk)
            if token is None:
                self.capture_status.configure(text=f"Unsupported key (virtual-key {vk}); try another.")
                return
            tokens.append(token)
        self.keys_var.set("+".join(tokens))
        self.capture_status.configure(text=f"Captured {keys.chord_label(tokens)}.")

    # -- macros ----------------------------------------------------------------------

    def _macro_names(self) -> list[str]:
        return sorted(self.app.config.macros)

    def _update_macro_hint(self) -> None:
        from ..macros import summarize
        macro = self.app.config.macros.get(self.macro_var.get())
        self.macro_hint.configure(text=summarize(macro) if macro else "No macro selected.")

    def _record_macro(self) -> None:
        def saved(macro) -> None:
            self.app.add_macro(macro)
            self.macro_combo.configure(values=self._macro_names())
            self.macro_var.set(macro.name)
            self._update_macro_hint()

        MacroDialog(self, self.app, None, saved, existing_names=set(self.app.config.macros)).show()

    # -- finish -------------------------------------------------------------------------

    def _save(self) -> None:
        t = self.type_var.get()
        binding = Binding(type=t)
        if t == "key":
            raw = self.keys_var.get().replace(" ", "+")
            tokens = [keys.normalize_token(x) for x in raw.split("+") if x.strip()]
            if not tokens:
                self.error.configure(text="Enter at least one key.")
                return
            bad = [x for x in tokens if not keys.is_known_token(x)]
            if bad:
                self.error.configure(text=f"Unknown key: {', '.join(bad)}")
                return
            binding.keys = tokens
            binding.repeat = bool(self.repeat_var.get())
        elif t == "mouse":
            binding.button = self.mouse_var.get()
            binding.repeat = bool(self.repeat_var.get())
        elif t == "double_click":
            binding.button = self.mouse_var.get()
        elif t == "text":
            binding.text = self.text_widget.get("1.0", "end-1c")
            if not binding.text:
                self.error.configure(text="Enter some text to type.")
                return
        elif t == "macro":
            binding.macro = self.macro_var.get()
            if binding.macro not in self.app.config.macros:
                self.error.configure(text="Pick or record a macro.")
                return
        elif t == "special":
            binding.special = self._specials[self.special_combo.current()]
        self.on_save(binding)
        self.close()

    def cancel(self) -> None:
        if self._capture is not None and self._capture.active:
            self._capture.cancel()
        self.close()
