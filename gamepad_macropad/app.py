"""Application wiring: config + engine + Tk overlay/settings on one process."""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import tkinter as tk
from logging.handlers import RotatingFileHandler
from typing import Callable

from . import APP_NAME, __version__
from .config import Config, Macro, load_config, log_path, save_config
from .engine import Engine
from .macros import MacroRecorder
from .ui import theme
from .ui.overlay import Overlay
from .ui.settings import SettingsWindow
from .ui.radial_hud import RadialHud

log = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\GamepadMacropad.SingleInstance"


def _configure_logging() -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if not getattr(sys, "frozen", False):
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)


def _acquire_single_instance():
    """Return a mutex handle, or None if another instance already runs."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def _set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class App:
    def __init__(self) -> None:
        self.config: Config = load_config()
        self._events: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self.root = tk.Tk()
        theme.apply(self.root)
        self.engine = Engine(self.config, ui_event=self._enqueue)
        self.engine.set_button_interceptor(self._intercept)
        self.recorder = MacroRecorder()
        self._recording_cb: Callable[[list[dict]], None] | None = None
        self.settings: SettingsWindow | None = None
        self.overlay = Overlay(self.root, self)
        self.radial_hud = RadialHud(self.root, self)
        self._closing = False
        self._tracked_device_guid = (self.config.active_device or "").lower()
        self.engine.start()
        self.root.after(60, self._poll)

    # -- loop ------------------------------------------------------------------------

    def run(self) -> int:
        log.info("%s %s starting", APP_NAME, __version__)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def _poll(self) -> None:
        if self._closing:
            return
        while True:
            try:
                kind, data = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(kind, data)
        status = self.engine.status()
        self._sync_device_profile(status)
        self.overlay.refresh(status)
        try:
            self.radial_hud.refresh(status)
        except tk.TclError:
            pass
        if self.settings is not None:
            try:
                self.settings.refresh_status(status)
            except tk.TclError:
                self.settings = None
        self.root.after(50, self._poll)

    def _sync_device_profile(self, status) -> None:
        """Save/load per-gamepad settings when the connected pad changes."""
        guid = (status.device_guid or "").lower() if status.connected else ""
        if guid == self._tracked_device_guid:
            return
        changed = self.config.switch_device(guid, name=status.device_name if status.connected else "")
        self._tracked_device_guid = guid
        if changed or guid:
            self.config_changed()
            if self.settings is not None:
                try:
                    self.settings.on_device_profile_changed()
                except tk.TclError:
                    self.settings = None
            self.overlay.refresh(status, force=True)

    def _enqueue(self, kind: str, data: dict) -> None:
        self._events.put((kind, data))

    def _handle_event(self, kind: str, data: dict) -> None:
        if kind == "open_settings":
            self.open_settings()
        elif kind == "toggle_overlay":
            self.overlay.toggle_compact()
        elif kind == "stop_recording":
            self.stop_recording()
        elif kind == "enabled":
            self.overlay.flash("Resumed" if data.get("enabled") else "Paused", 1.2)
        elif kind == "input_mode":
            mode = str(data.get("mode", "mouse"))
            self.config.input_mode = mode
            self.save_config()
            label = {"keyboard": "Keyboard mode", "game": "Game mode"}.get(mode, "Mouse mode")
            self.overlay.flash(label, 1.2)
            self.sync_radial_hud()
        elif kind == "number_mode":
            on = bool(data.get("enabled"))
            self.overlay.flash("Number mode" if on else "Letter mode", 1.0)
            self.sync_radial_hud()
        elif kind == "right_stick_mode":
            mode = str(data.get("mode", "wheel"))
            label = "Right stick: arrows" if mode == "arrows" else "Right stick: scroll"
            self.overlay.flash(label, 1.2)

    # -- engine-thread callback ---------------------------------------------------------

    def _intercept(self, name: str) -> bool:
        """Runs on the engine thread: decide whether a press is swallowed."""
        if self.recording:
            self._enqueue("stop_recording", {"button": name})
            return True
        return False

    # -- recording ------------------------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self.recorder.active

    def start_recording(self, *, record_positions: bool, on_stop: Callable[[list[dict]], None]) -> None:
        if self.recording:
            raise OSError("already recording")
        self._recording_cb = on_stop
        self.recorder.start(record_positions=record_positions)
        self.overlay.flash("Recording...", 3600)
        log.info("macro recording started (positions=%s)", record_positions)

    def stop_recording(self) -> None:
        if not self.recording:
            return
        steps = self.recorder.stop()
        self.overlay.flash(f"Recorded {len(steps)} events", 2.0)
        self.engine.rumble()
        log.info("macro recording stopped with %d steps", len(steps))
        cb, self._recording_cb = self._recording_cb, None
        if cb is not None:
            try:
                cb(steps)
            except tk.TclError:
                pass

    # -- config ------------------------------------------------------------------------------

    def config_changed(self, *, persist: bool = True) -> None:
        guid = self._tracked_device_guid or (self.config.active_device or "").lower()
        if guid:
            self.config.save_active_profile(guid)
        self.engine.set_config(self.config)
        if persist:
            self.save_config()

    def save_config(self) -> None:
        try:
            guid = self._tracked_device_guid or (self.config.active_device or "").lower()
            if guid:
                self.config.save_active_profile(guid)
            save_config(self.config)
        except OSError:
            log.exception("saving config failed")

    def add_macro(self, macro: Macro) -> None:
        self.config.macros[macro.name] = macro
        self.config_changed()

    def replace_macro(self, old_name: str, macro: Macro) -> None:
        if old_name != macro.name:
            self.config.macros.pop(old_name, None)
            for binding in self.config.bindings.values():
                if binding.type == "macro" and binding.macro == old_name:
                    binding.macro = macro.name
        self.config.macros[macro.name] = macro
        self.config_changed()

    def delete_macro(self, name: str) -> None:
        self.config.macros.pop(name, None)
        for binding in self.config.bindings.values():
            if binding.type == "macro" and binding.macro == name:
                binding.type = "none"
                binding.macro = ""
        self.config_changed()

    # -- windows -------------------------------------------------------------------------------

    def open_settings(self) -> None:
        if self.settings is not None:
            try:
                self.settings.deiconify()
                self.settings.lift()
                self.settings.focus_force()
                return
            except tk.TclError:
                self.settings = None
        self.settings = SettingsWindow(self)

    def settings_closed(self) -> None:
        self.settings = None

    def toggle_enabled(self) -> None:
        self.engine.toggle_enabled()

    def toggle_input_mode(self) -> None:
        self.engine.toggle_input_mode()

    def sync_radial_hud(self) -> None:
        try:
            self.radial_hud.refresh(self.engine.status())
        except tk.TclError:
            pass

    def toggle_number_mode(self) -> None:
        self.engine.toggle_number_mode()

    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.root.quit()
        except tk.TclError:
            pass

    def _shutdown(self) -> None:
        self._closing = True
        if self.recording:
            try:
                self.recorder.stop()
            except Exception:
                pass
        self.engine.stop()
        try:
            self.save_config()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        log.info("%s exited", APP_NAME)


def main() -> int:
    if sys.platform != "win32":
        print("Gamepad Macropad currently supports Windows only.", file=sys.stderr)
        return 2
    _configure_logging()
    _set_dpi_aware()
    mutex = _acquire_single_instance()
    if mutex is None:
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} is already running (look for the overlay).", APP_NAME, 0x40)
        return 1
    try:
        app = App()
    except Exception:
        log.exception("startup failed")
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} failed to start. See the log:\n{log_path()}", APP_NAME, 0x10)
        return 1
    return app.run()
