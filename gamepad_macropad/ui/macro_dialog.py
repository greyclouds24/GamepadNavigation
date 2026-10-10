"""Record / edit a macro."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

from .. import macros as macro_lib
from ..config import Macro
from . import theme
from .dialogs import Dialog, ask_text

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App


class MacroDialog(Dialog):
    def __init__(self, parent: tk.Misc, app: "App", macro: Macro | None, on_save: Callable[[Macro], None],
                 *, existing_names: set[str] | None = None) -> None:
        super().__init__(parent, "Macro" if macro else "New macro", width=560, modal=False)
        self.app = app
        self.on_save = on_save
        self.existing = set(existing_names or ())
        self.original_name = macro.name if macro else ""
        self.steps = [dict(s) for s in (macro.steps if macro else [])]
        self._countdown_job: str | None = None
        self._poll_job: str | None = None

        self.name_var = tk.StringVar(value=macro.name if macro else self._suggest_name())
        self.positions_var = tk.BooleanVar(value=False)
        self.speed_var = tk.DoubleVar(value=macro.speed if macro else 1.0)
        self.loop_var = tk.BooleanVar(value=macro.loop_while_held if macro else False)
        self._build()
        self._refresh_steps()

    def _suggest_name(self) -> str:
        n = 1
        while f"Macro {n}" in self.existing:
            n += 1
        return f"Macro {n}"

    # -- layout ----------------------------------------------------------------------

    def _build(self) -> None:
        b = self.body
        row = ttk.Frame(b)
        row.pack(fill="x")
        ttk.Label(row, text="Name").pack(side="left")
        ttk.Entry(row, textvariable=self.name_var, width=36).pack(side="left", padx=(8, 0), fill="x", expand=True)

        opts = ttk.Frame(b)
        opts.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(opts, text="Record mouse positions (replay clicks at the same screen spots)",
                        variable=self.positions_var).pack(anchor="w")
        ttk.Checkbutton(opts, text="Loop while the controller button is held",
                        variable=self.loop_var).pack(anchor="w")
        speed_row = ttk.Frame(opts)
        speed_row.pack(fill="x", pady=(4, 0))
        ttk.Label(speed_row, text="Playback speed").pack(side="left")
        ttk.Spinbox(speed_row, from_=0.25, to=8.0, increment=0.25, textvariable=self.speed_var, width=6,
                    format="%.2f").pack(side="left", padx=(8, 0))
        ttk.Label(speed_row, text="x", style="Dim.TLabel").pack(side="left", padx=(3, 0))

        rec = ttk.Frame(b)
        rec.pack(fill="x", pady=(12, 4))
        self.btn_record = ttk.Button(rec, text="\u25CF  Record", style="Accent.TButton", command=self._toggle_record)
        self.btn_record.pack(side="left")
        self.status = ttk.Label(rec, text="Record keys, clicks and scrolling in any app. "
                                          "Press any controller button or Stop to finish.",
                                style="Dim.TLabel", wraplength=theme.px(360), justify="left")
        self.status.pack(side="left", padx=(12, 0))

        list_frame = ttk.Frame(b)
        list_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.listbox = tk.Listbox(list_frame, height=12, bg=theme.BG_RAISED, fg=theme.FG, font=theme.FONT_MONO,
                                  selectbackground=theme.ACCENT_DARK, selectforeground="#fff", relief="flat",
                                  highlightthickness=0, activestyle="none", selectmode="extended")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        tools = ttk.Frame(b)
        tools.pack(fill="x", pady=(6, 0))
        ttk.Button(tools, text="Delete step", command=self._delete_selected).pack(side="left")
        ttk.Button(tools, text="Add wait...", command=self._add_wait).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="Add text...", command=self._add_text).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="Clear", command=self._clear).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="\u25B6 Test", command=self._test).pack(side="right")

        self.summary = ttk.Label(b, text="", style="Dim.TLabel")
        self.summary.pack(anchor="w", pady=(6, 0))

        bottom = ttk.Frame(b)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Cancel", command=self.cancel).pack(side="right")
        self.btn_save = ttk.Button(bottom, text="Save", style="Accent.TButton", command=self._save)
        self.btn_save.pack(side="right", padx=(0, 8))

    # -- steps -------------------------------------------------------------------------

    def _refresh_steps(self) -> None:
        self.listbox.delete(0, "end")
        for step in self.steps:
            self.listbox.insert("end", macro_lib.describe_step(step))
        self.listbox.see("end")
        self.summary.configure(text=macro_lib.summarize(self._macro()))

    def _delete_selected(self) -> None:
        selected = sorted((int(i) for i in self.listbox.curselection()), reverse=True)
        for i in selected:
            if 0 <= i < len(self.steps):
                del self.steps[i]
        self._refresh_steps()

    def _clear(self) -> None:
        self.steps = []
        self._refresh_steps()

    def _insert_after_selection(self, step: dict) -> None:
        sel = self.listbox.curselection()
        index = int(sel[-1]) + 1 if sel else len(self.steps)
        prev_t = float(self.steps[index - 1]["t"]) if index > 0 else 0.0
        step["t"] = round(prev_t, 4)
        self.steps.insert(index, step)
        # Keep timestamps monotonic for anything that follows.
        shift = float(step.get("seconds", 0.0))
        if shift:
            for later in self.steps[index + 1:]:
                later["t"] = round(float(later["t"]) + shift, 4)
        self._refresh_steps()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)

    def _add_wait(self) -> None:
        value = ask_text(self, "Add wait", "Seconds to wait:", "0.5")
        if value is None:
            return
        try:
            seconds = max(0.0, float(value))
        except ValueError:
            return
        self._insert_after_selection({"k": "delay", "seconds": seconds})

    def _add_text(self) -> None:
        value = ask_text(self, "Add text", "Text to type:", "", multiline=True)
        if not value:
            return
        self._insert_after_selection({"k": "text", "text": value})

    def _macro(self) -> Macro:
        try:
            speed = float(self.speed_var.get())
        except (tk.TclError, ValueError):
            speed = 1.0
        return Macro(name=self.name_var.get().strip() or "macro", steps=[dict(s) for s in self.steps],
                     speed=max(0.05, speed), loop_while_held=bool(self.loop_var.get()))

    def _test(self) -> None:
        if not self.steps:
            return
        if not self.app.engine.play_macro(self._macro()):
            self.status.configure(text="A macro is already playing.")

    # -- recording -----------------------------------------------------------------------

    def _toggle_record(self) -> None:
        if self.app.recording:
            self.app.stop_recording()
            return
        if self._countdown_job is not None:
            self.after_cancel(self._countdown_job)
            self._countdown_job = None
            self.btn_record.configure(text="\u25CF  Record", style="Accent.TButton")
            self.status.configure(text="Cancelled.")
            return
        self.btn_record.configure(text="Cancel", style="TButton")
        self.btn_save.state(["disabled"])
        self._countdown(3)

    def _countdown(self, n: int) -> None:
        if n <= 0:
            self._countdown_job = None
            self._start_recording()
            return
        self.status.configure(text=f"Recording starts in {n}... switch to the target app now.")
        self._countdown_job = self.after(1000, lambda: self._countdown(n - 1))

    def _start_recording(self) -> None:
        try:
            self.app.start_recording(record_positions=bool(self.positions_var.get()), on_stop=self._recorded)
        except OSError as exc:
            self.status.configure(text=f"Could not start recording: {exc}")
            self.btn_record.configure(text="\u25CF  Record", style="Accent.TButton")
            self.btn_save.state(["!disabled"])
            return
        self.btn_record.configure(text="\u25A0  Stop", style="Danger.TButton")
        self._poll_recording()

    def _poll_recording(self) -> None:
        if not self.app.recording:
            self._poll_job = None
            return
        rec = self.app.recorder
        self.status.configure(text=f"REC  {rec.elapsed:5.1f}s   {rec.step_count} events   "
                                   "(any controller button or Stop ends recording)")
        self._poll_job = self.after(100, self._poll_recording)

    def _recorded(self, steps: list[dict]) -> None:
        """Called on the UI thread when recording stops."""
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if not self.winfo_exists():
            return
        self.btn_record.configure(text="\u25CF  Record", style="Accent.TButton")
        self.btn_save.state(["!disabled"])
        if steps:
            self.steps = steps
            self.status.configure(text=f"Recorded {len(steps)} events. Review below, then Save.")
        else:
            self.status.configure(text="Nothing was recorded.")
        self._refresh_steps()
        self.lift()

    # -- finish ----------------------------------------------------------------------------

    def _save(self) -> None:
        macro = self._macro()
        name = macro.name
        if name != self.original_name and name in self.existing:
            self.status.configure(text=f'A macro named "{name}" already exists.')
            return
        if not macro.steps:
            self.status.configure(text="Record or add at least one step first.")
            return
        self.on_save(macro)
        self.close()

    def cancel(self) -> None:
        if self._countdown_job is not None:
            self.after_cancel(self._countdown_job)
        if self.app.recording:
            self.app.stop_recording()
        self.close()
