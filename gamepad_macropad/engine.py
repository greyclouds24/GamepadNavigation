"""The conversion engine: gamepad state -> mouse motion, scrolling, actions.

One daemon thread owns the SDL reader and runs a fixed-rate loop
(``poll_hz``). Each tick it:

1. polls the pad and derives button press/release edges,
2. dispatches edges to the bound actions (keys, mouse buttons, macros, ...),
3. integrates the left stick into cursor motion and the right stick into
   vertical/horizontal wheel deltas,
4. handles key auto-repeat for held bindings.

The UI thread only ever calls the small thread-safe API (``set_config``,
``set_enabled``, ``status`` ...).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import keys, win_input
from .config import Binding, Config
from .gamepad import GamepadReader, PadState
from .macros import MacroPlayer
from .radial import OPENERS, select_radial

log = logging.getLogger(__name__)

UiEvent = Callable[[str, dict], None]
WHEEL_DELTA = 120
_SMOOTH_SCROLL_INTERVAL = 1.0 / 60.0
# Post-deadzone stick fractions for the three-band response curve:
#   [0, _PRECISION_END)  precision band (scaled by precision_scale + response curve)
#   [_PRECISION_END, _FULL_SPEED_AT)  ramp from precision ceiling up to full speed
#   [_FULL_SPEED_AT, 1]  full speed
_PRECISION_END = 0.90
_FULL_SPEED_AT = 0.98


@dataclass(frozen=True)
class EngineStatus:
    running: bool = False
    connected: bool = False
    device_name: str = ""
    device_guid: str = ""
    is_controller: bool = True
    enabled: bool = True
    input_mode: str = "mouse"
    number_mode: bool = False
    playing_macro: str | None = None
    pressed: frozenset[str] = frozenset()
    radial_opener: str = ""
    radial_index: int = -1
    radial_glyph: str = ""
    shift_held: bool = False
    ctrl_held: bool = False
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    error: str = ""


@dataclass
class _Held:
    """What we actually pressed for a button, so release always matches."""

    binding: Binding
    vks: list[int] = field(default_factory=list)
    mouse_button: str = ""
    pressed_at: float = 0.0
    next_repeat: float = 0.0


def _curve_magnitude(norm: float, accel: float, *, precision_scale: float = 1.0) -> float:
    """Map post-deadzone deflection [0,1] to output strength [0,1].

    - Under 90%: precision band — response curve scaled by ``precision_scale``
      (0 at center → ``precision_scale`` at 90%).
    - 90%–98%: curves from that precision ceiling up to full speed.
    - 98%+: full speed (1.0).
    """
    if norm >= _FULL_SPEED_AT:
        return 1.0
    slow = max(0.01, min(1.0, float(precision_scale)))
    exp = max(0.2, float(accel))
    if norm <= _PRECISION_END:
        t = norm / _PRECISION_END
        return slow * (t ** exp)
    # Ramp 90% → 98%: ease from ``slow`` toward 1.0 (steeper with higher accel).
    u = (norm - _PRECISION_END) / (_FULL_SPEED_AT - _PRECISION_END)
    blend = u ** exp
    return slow + (1.0 - slow) * blend


def shape_radial(
    x: float, y: float, deadzone: float, accel: float, *, precision_scale: float = 1.0,
) -> tuple[float, float, float]:
    """Radial deadzone + power curve. Returns (ux*m, uy*m, m) with m in [0,1]."""
    mag = math.hypot(x, y)
    if mag <= deadzone or mag == 0.0:
        return 0.0, 0.0, 0.0
    norm = min(1.0, (mag - deadzone) / (1.0 - deadzone))
    shaped = _curve_magnitude(norm, accel, precision_scale=precision_scale)
    return x / mag * shaped, y / mag * shaped, shaped


def shape_axis(
    v: float, deadzone: float, accel: float, *, precision_scale: float = 1.0,
) -> float:
    """Per-axis deadzone + power curve, sign preserved."""
    a = abs(v)
    if a <= deadzone:
        return 0.0
    norm = min(1.0, (a - deadzone) / (1.0 - deadzone))
    return math.copysign(_curve_magnitude(norm, accel, precision_scale=precision_scale), v)


class Engine:
    def __init__(self, cfg: Config, *, ui_event: UiEvent | None = None) -> None:
        self._cfg = cfg
        self._ui_event = ui_event or (lambda kind, data: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._enabled = bool(cfg.start_enabled)
        self._status = EngineStatus()
        self._reader = GamepadReader(preferred_guid=cfg.preferred_device)
        self._player = MacroPlayer(on_state=self._on_macro_state)
        self._held: dict[str, _Held] = {}
        self._prev_buttons: frozenset[str] = frozenset()
        self._pressed_now: frozenset[str] = frozenset()
        self._interceptor: Callable[[str], bool] | None = None
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._acc_v = 0.0
        self._acc_h = 0.0
        self._last_scroll_emit = 0.0
        self._rumble_request = 0
        self._devices_cache: list = []
        self._release_requested = False
        self._rescan_requested = False
        self._number_mode = False
        self._radial_held: set[str] = set()
        self._radial_selection: dict[str, str] = {}  # opener → glyph while held
        self._shift_held = False
        self._ctrl_held = False
        self._ctrl_from_dpad = False

    # -- public API (any thread) ---------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    def set_config(self, cfg: Config) -> None:
        self._cfg = cfg
        if cfg.preferred_device != self._reader.preferred_guid:
            self._reader.reselect(cfg.preferred_device)

    @property
    def input_mode(self) -> str:
        return self._cfg.input_mode if self._cfg.input_mode in ("mouse", "keyboard") else "mouse"

    def set_input_mode(self, mode: str) -> None:
        mode = "keyboard" if mode == "keyboard" else "mouse"
        if mode == self._cfg.input_mode:
            return
        self._request_release_all()
        self._cfg.input_mode = mode
        self._number_mode = False
        self._radial_held.clear()
        self._radial_selection.clear()
        self._ui_event("input_mode", {"mode": mode})

    def toggle_input_mode(self) -> None:
        self.set_input_mode("keyboard" if self.input_mode == "mouse" else "mouse")

    def toggle_number_mode(self) -> None:
        if self.input_mode != "keyboard":
            return
        self._number_mode = not self._number_mode
        self._ui_event("number_mode", {"enabled": self._number_mode})

    @property
    def config(self) -> Config:
        return self._cfg

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self._request_release_all()
        self._ui_event("enabled", {"enabled": enabled})

    def toggle_enabled(self) -> None:
        self.set_enabled(not self._enabled)

    def set_button_interceptor(self, fn: Callable[[str], bool] | None) -> None:
        """Route button presses to ``fn`` (returns True to swallow) instead of actions."""
        self._interceptor = fn

    def stop_macros(self) -> None:
        self._player.stop()

    def play_macro(self, macro) -> bool:
        """Play a macro once (used by the UI's test button)."""
        return self._player.play(macro)

    def rumble(self) -> None:
        self._rumble_request += 1

    def status(self) -> EngineStatus:
        with self._lock:
            return self._status

    def list_devices(self):
        """Devices seen by the engine thread (cached)."""
        with self._lock:
            return list(self._devices_cache)

    def request_rescan(self) -> None:
        """Ask the engine thread to re-enumerate gamepads."""
        self._rescan_requested = True

    def _request_release_all(self) -> None:
        self._release_requested = True

    # -- engine thread ----------------------------------------------------------

    def _run(self) -> None:
        error = ""
        try:
            self._reader.open()
        except Exception as exc:  # pragma: no cover - needs SDL failure
            log.exception("gamepad backend failed to start")
            error = f"Gamepad backend failed: {exc}"
            self._publish(error=error)
            return
        win_input.begin_high_res_timer(1)
        win_input.release_all_modifiers()
        last = time.perf_counter()
        next_tick = last
        last_devices = 0.0
        try:
            while not self._stop.is_set():
                cfg = self._cfg
                period = 1.0 / max(30, min(1000, int(cfg.poll_hz)))
                now = time.perf_counter()
                dt = min(0.1, max(1e-4, now - last))
                last = now

                if self._release_requested:
                    self._release_requested = False
                    self._release_everything()

                if self._rescan_requested:
                    self._rescan_requested = False
                    self._reader.request_rescan()
                state = self._reader.poll(cfg.trigger_threshold)
                if now - last_devices > 0.5:
                    last_devices = now
                    devices = self._reader.list_devices()
                    with self._lock:
                        self._devices_cache = devices

                if state is None:
                    if self._prev_buttons or self._held:
                        self._release_everything()
                        self._prev_buttons = frozenset()
                    self._publish(state=None)
                else:
                    self._tick(cfg, state, now, dt)
                    self._publish(state=state)

                if self._rumble_request:
                    self._rumble_request = 0
                    if cfg.haptics:
                        self._reader.rumble(0.25, 0.25, 70)

                next_tick += period
                sleep_for = next_tick - time.perf_counter()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_tick = time.perf_counter()
        except Exception as exc:
            log.exception("engine loop crashed")
            error = f"Engine error: {exc}"
        finally:
            try:
                self._release_everything()
            finally:
                win_input.end_high_res_timer(1)
                self._reader.close()
                self._publish(state=None, error=error, stopped=True)

    def _publish(self, *, state: PadState | None, error: str = "", stopped: bool = False) -> None:
        info = self._reader.info
        status = EngineStatus(
            running=not stopped,
            connected=info is not None,
            device_name=info.name if info else "",
            device_guid=info.guid if info else "",
            is_controller=info.is_controller if info else True,
            enabled=self._enabled,
            input_mode=self.input_mode,
            number_mode=self._number_mode,
            playing_macro=self._player.playing,
            pressed=state.buttons if state else frozenset(),
            radial_opener=self._active_opener(),
            radial_index=self._active_index(),
            radial_glyph=self._active_glyph(),
            shift_held=self._shift_held,
            ctrl_held=self._ctrl_held,
            lx=state.lx if state else 0.0,
            ly=state.ly if state else 0.0,
            rx=state.rx if state else 0.0,
            ry=state.ry if state else 0.0,
            error=error,
        )
        with self._lock:
            self._status = status

    # -- per tick -----------------------------------------------------------------

    def _tick(self, cfg: Config, state: PadState, now: float, dt: float) -> None:
        self._pressed_now = state.buttons
        newly_pressed = state.buttons - self._prev_buttons
        newly_released = self._prev_buttons - state.buttons
        self._prev_buttons = state.buttons
        keyboard = self.input_mode == "keyboard"

        interceptor = self._interceptor
        for name in sorted(newly_pressed):
            if interceptor is not None:
                try:
                    if interceptor(name):
                        continue
                except Exception:
                    log.exception("button interceptor failed")
            binding = cfg.binding(name)
            if not self._enabled:
                if binding.type == "special" and binding.special in (
                    "toggle_enabled", "toggle_input_mode", "toggle_number_mode",
                ):
                    self._special(binding.special, pressed=True)
                continue
            if keyboard:
                if binding.type == "special":
                    self._press(name, binding, now)
                continue
            self._press(name, binding, now)

        for name in sorted(newly_released):
            self._release(name)

        if not self._enabled:
            self._radial_held.clear()
            self._radial_selection.clear()
            return

        if keyboard:
            self._keyboard_tick(cfg, state, newly_pressed, newly_released)
            return

        self._repeat(cfg, now)
        self._pointer(cfg, state, dt)
        self._scroll(cfg, state, dt, now)

    # -- actions --------------------------------------------------------------------

    def _press(self, name: str, binding: Binding, now: float) -> None:
        kind = binding.type
        held = _Held(binding=binding, pressed_at=now)
        try:
            if kind == "key":
                vks = []
                for token in binding.keys:
                    try:
                        vks.append(keys.resolve_vk(token))
                    except KeyError:
                        log.warning("unknown key token %r in binding %s", token, name)
                if vks:
                    win_input.chord_down(vks)
                    held.vks = vks
            elif kind == "mouse":
                button = binding.button if binding.button in win_input.MOUSE_BUTTONS else "left"
                win_input.mouse_down(button)
                held.mouse_button = button
            elif kind == "double_click":
                button = binding.button if binding.button in win_input.MOUSE_BUTTONS else "left"
                win_input.click(button, 2)
            elif kind == "text":
                text = binding.text
                if text:
                    threading.Thread(target=self._type_text, args=(text,), daemon=True).start()
            elif kind == "macro":
                macro = self._cfg.macros.get(binding.macro)
                if macro is None:
                    log.warning("binding %s refers to missing macro %r", name, binding.macro)
                elif self._player.play(macro, held=lambda n=name: n in self._pressed_now):
                    self.rumble()
            elif kind == "special":
                self._special(binding.special, pressed=True)
        except OSError:
            log.exception("action for %s failed", name)
        if binding.repeat and (held.vks or held.mouse_button):
            held.next_repeat = now + max(0.05, self._cfg.repeat_delay_ms / 1000.0)
        self._held[name] = held

    def _release(self, name: str) -> None:
        held = self._held.pop(name, None)
        if held is None:
            return
        try:
            if held.vks:
                win_input.chord_up(held.vks)
            if held.mouse_button:
                win_input.mouse_up(held.mouse_button)
            if held.binding.type == "special":
                self._special(held.binding.special, pressed=False)
        except OSError:
            log.exception("release for %s failed", name)

    def _repeat(self, cfg: Config, now: float) -> None:
        interval = 1.0 / max(1.0, float(cfg.repeat_rate_hz))
        for held in self._held.values():
            if not held.next_repeat or now < held.next_repeat:
                continue
            held.next_repeat = now + interval
            try:
                if held.vks:
                    win_input.key_down(held.vks[-1])
                elif held.mouse_button:
                    win_input.mouse_up(held.mouse_button)
                    win_input.mouse_down(held.mouse_button)
            except OSError:
                log.exception("repeat failed")

    def _special(self, which: str, *, pressed: bool) -> None:
        if not pressed:
            return
        if which == "toggle_enabled":
            # In keyboard mode the pause control toggles number mode instead.
            if self.input_mode == "keyboard":
                self.toggle_number_mode()
            else:
                self.set_enabled(not self._enabled)
        elif which == "toggle_input_mode":
            self.toggle_input_mode()
        elif which == "toggle_number_mode":
            self.toggle_number_mode()
        elif which == "open_settings":
            self._ui_event("open_settings", {})
        elif which == "toggle_overlay":
            self._ui_event("toggle_overlay", {})
        elif which == "stop_macros":
            self._player.stop()

    def _keyboard_tick(
        self,
        cfg: Config,
        state: PadState,
        newly_pressed: frozenset[str],
        newly_released: frozenset[str],
    ) -> None:
        thr = float(cfg.trigger_threshold)
        open_now = {
            "LT": state.lt >= thr or "LT" in state.buttons,
            "RT": state.rt >= thr or "RT" in state.buttons,
            "LB": "LB" in state.buttons,
            "RB": "RB" in state.buttons,
        }

        # Face buttons + d-pad / stick clicks for direct keys & modifiers.
        self._shift_held = "LS" in state.buttons
        self._ctrl_held = "RS" in state.buttons or "DPAD_DOWN" in state.buttons

        for name in newly_pressed:
            if name == "DPAD_UP":
                self._emit_key("space")
            elif name == "DPAD_RIGHT":
                self._emit_key("enter")
            elif name == "DPAD_LEFT":
                self._emit_key("backspace")
            elif name == "Y":
                self._emit_char("j")
            elif name == "X":
                self._emit_char("q")
            elif name == "B":
                self._emit_char(".")
            elif name == "A":
                self._emit_char(",")

        for opener in OPENERS:
            held = open_now[opener]
            if held:
                self._radial_held.add(opener)
                sel = select_radial(
                    opener, state.lx, state.ly, state.rx, state.ry,
                    number_mode=self._number_mode,
                )
                if sel is not None:
                    self._radial_selection[opener] = sel.glyph
                elif opener not in self._radial_selection:
                    self._radial_selection.pop(opener, None)
                # Keep last aimed glyph while stick returns to center (still holding opener).
            elif opener in self._radial_held:
                self._radial_held.discard(opener)
                glyph = self._radial_selection.pop(opener, "")
                if glyph:
                    self._emit_char(glyph)

        # Drop selections for openers no longer held.
        for opener in list(self._radial_selection):
            if opener not in self._radial_held:
                self._radial_selection.pop(opener, None)

    def _active_opener(self) -> str:
        # Prefer the most recently held / left-side first for HUD focus.
        for opener in ("LT", "LB", "RB", "RT"):
            if opener in self._radial_held:
                return opener
        return ""

    def _active_glyph(self) -> str:
        opener = self._active_opener()
        return self._radial_selection.get(opener, "") if opener else ""

    def _active_index(self) -> int:
        opener = self._active_opener()
        glyph = self._radial_selection.get(opener, "")
        if not opener or not glyph:
            return -1
        from .radial import items_for
        items = items_for(opener, number_mode=self._number_mode)
        try:
            return items.index(glyph)
        except ValueError:
            return -1

    def _emit_char(self, glyph: str) -> None:
        ch = glyph
        if len(ch) == 1 and ch.isalpha():
            ch = ch.upper() if self._shift_held else ch.lower()
        if self._ctrl_held and len(ch) == 1 and ch.isalpha():
            try:
                vk = keys.resolve_vk(ch.lower())
                win_input.tap_chord([keys.VK["ctrl"], vk])
            except (KeyError, OSError):
                log.exception("ctrl+%s failed", ch)
            return
        try:
            win_input.type_text(ch)
        except OSError:
            log.exception("typing %r failed", ch)

    def _emit_key(self, token: str) -> None:
        try:
            vk = keys.resolve_vk(token)
            if self._ctrl_held and token not in ("ctrl", "shift"):
                win_input.tap_chord([keys.VK["ctrl"], vk])
            else:
                win_input.tap_key(vk)
        except (KeyError, OSError):
            log.exception("key %s failed", token)

    def _type_text(self, text: str) -> None:
        try:
            win_input.type_text(text)
        except OSError:
            log.exception("typing text failed")

    def _release_everything(self) -> None:
        for name in list(self._held):
            self._release(name)
        self._held.clear()
        self._radial_held.clear()
        self._radial_selection.clear()
        self._shift_held = False
        self._ctrl_held = False
        self._player.stop()
        self._acc_x = self._acc_y = self._acc_v = self._acc_h = 0.0
        win_input.release_all_modifiers()

    def _on_macro_state(self, name: str | None) -> None:
        self._ui_event("macro", {"name": name})

    # -- sticks -----------------------------------------------------------------------

    def _pointer(self, cfg: Config, state: PadState, dt: float) -> None:
        p = cfg.pointer
        ux, uy, mag = shape_radial(
            state.lx, state.ly, p.deadzone, p.acceleration, precision_scale=p.precision_scale,
        )
        if mag == 0.0:
            self._acc_x = self._acc_y = 0.0
            return
        # Sub-99% stick travel stays in the precision band via the response curve.
        speed = p.speed
        if p.invert_y:
            uy = -uy
        self._acc_x += ux * speed * dt
        self._acc_y += uy * speed * dt
        dx = int(self._acc_x)
        dy = int(self._acc_y)
        if dx or dy:
            self._acc_x -= dx
            self._acc_y -= dy
            try:
                win_input.move_relative(dx, dy)
            except OSError:
                log.exception("mouse move failed")

    def _scroll(self, cfg: Config, state: PadState, dt: float, now: float) -> None:
        s = cfg.scroll
        scroll_precision = 0.35
        v = shape_axis(state.ry, s.deadzone, s.acceleration, precision_scale=scroll_precision)
        h = (
            shape_axis(state.rx, s.deadzone, s.acceleration, precision_scale=scroll_precision)
            if s.horizontal_enabled else 0.0
        )
        if v == 0.0 and h == 0.0:
            self._acc_v = self._acc_h = 0.0
            return
        # Stick up (negative SDL y) scrolls up (positive wheel delta).
        v_dir = 1.0 if s.invert_vertical else -1.0
        h_dir = -1.0 if s.invert_horizontal else 1.0
        self._acc_v += v * v_dir * s.speed * WHEEL_DELTA * dt
        self._acc_h += h * h_dir * s.horizontal_speed * WHEEL_DELTA * dt

        if s.smooth:
            if now - self._last_scroll_emit < _SMOOTH_SCROLL_INTERVAL:
                return
            self._last_scroll_emit = now
            dv = int(self._acc_v)
            dh = int(self._acc_h)
        else:
            dv = int(self._acc_v / WHEEL_DELTA) * WHEEL_DELTA
            dh = int(self._acc_h / WHEEL_DELTA) * WHEEL_DELTA
        if dv:
            self._acc_v -= dv
        if dh:
            self._acc_h -= dh
        try:
            if dv:
                win_input.wheel(dv)
            if dh:
                win_input.hwheel(dh)
        except OSError:
            log.exception("scroll failed")
