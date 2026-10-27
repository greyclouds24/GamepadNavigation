"""The conversion engine: gamepad state -> mouse motion, scrolling, actions.

One daemon thread owns the SDL reader and runs a fixed-rate loop
(``poll_hz``). Each tick it:

1. polls the pad and derives button press/release edges,
2. dispatches edges to the bound actions (keys, mouse buttons, macros, ...),
3. integrates the left stick into cursor motion and the right stick into
   vertical/horizontal wheel deltas (or arrow keys in arrows mode),
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
from .config import INPUT_MODES, Binding, Config, RIGHT_STICK_MODES
from .dictation import MicDictation
from .gamepad import GamepadReader, PadState
from .macros import MacroPlayer
from .radial import RELEASE_THRESHOLD, SIDES, select_side

log = logging.getLogger(__name__)

UiEvent = Callable[[str, dict], None]
WHEEL_DELTA = 120
_SMOOTH_SCROLL_INTERVAL = 1.0 / 60.0
_GAME_EXIT_HOLD_S = 2.0
_MIC_ARM_S = 0.40
_LAYER_SPECIALS = ("toggle_left_layer", "toggle_right_layer")
# Post-deadzone stick fractions for the three-band response curve:
#   [0, _PRECISION_END)  precision band (scaled by precision_scale + response curve)
#   [_PRECISION_END, _FULL_SPEED_AT)  steep ramp from the precision ceiling to full speed
#   [_FULL_SPEED_AT, 1]  full speed
_PRECISION_END = 0.90
_FULL_SPEED_AT = 0.96
# Extra power on the outer rim so the last push snaps instead of easing in.
_RIM_EXPONENT = 2.4
# Right-stick scroll has no sub-rim slider; this is its precision-band ceiling.
SCROLL_PRECISION = 0.55
_CARDINAL_DIRS = ("up", "right", "down", "left")


@dataclass(frozen=True)
class EngineStatus:
    running: bool = False
    connected: bool = False
    device_name: str = ""
    device_guid: str = ""
    is_controller: bool = True
    enabled: bool = True
    input_mode: str = "mouse"  # mouse | keyboard | game
    number_mode: bool = False
    right_stick_mode: str = "wheel"  # wheel | arrows (mouse mode)
    dictating: bool = False
    playing_macro: str | None = None
    pressed: frozenset[str] = frozenset()
    left_layer: int = 0
    right_layer: int = 0
    left_radial_index: int = -1
    left_radial_glyph: str = ""
    right_radial_index: int = -1
    right_radial_glyph: str = ""
    # Legacy single-focus fields (HUD / overlay still read these).
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
      (0 at center → ``precision_scale`` at 90%). Higher accel stays quieter
      longer, then climbs hard into the sub-rim ceiling.
    - 90%–96%: a much steeper rim than the precision band, from that ceiling
      up to full speed.
    - 96%+: full speed (1.0).
    """
    if norm >= _FULL_SPEED_AT:
        return 1.0
    slow = max(0.01, min(1.0, float(precision_scale)))
    exp = max(0.2, float(accel))
    if norm <= _PRECISION_END:
        t = norm / _PRECISION_END
        return slow * (t ** exp)
    # 90% → 96%: hang near the sub-rim ceiling, then snap to full speed.
    u = (norm - _PRECISION_END) / (_FULL_SPEED_AT - _PRECISION_END)
    blend = u ** (exp * _RIM_EXPONENT)
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


def _is_keyboard_action(binding: Binding) -> bool:
    if binding.type in ("key", "macro"):
        return True
    return binding.type == "special" and binding.special in _LAYER_SPECIALS


def shape_axis(
    v: float, deadzone: float, accel: float, *, precision_scale: float = 1.0,
) -> float:
    """Per-axis deadzone + power curve, sign preserved."""
    a = abs(v)
    if a <= deadzone:
        return 0.0
    norm = min(1.0, (a - deadzone) / (1.0 - deadzone))
    return math.copysign(_curve_magnitude(norm, accel, precision_scale=precision_scale), v)


def nearest_cardinal(x: float, y: float) -> str:
    """Round stick vector to nearest of up / right / down / left (SDL y+ down)."""
    # atan2(x, -y): 0 = up, then clockwise. ±45° boundaries between cardinals.
    angle = math.atan2(x, -y)
    sector = int(round(angle / (math.pi / 2.0))) % 4
    return _CARDINAL_DIRS[sector]


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
        self._right_stick_mode = "wheel"
        self._left_layer = 0
        self._right_layer = 0
        # Per-side aim state: glyph/index while armed, cleared on release-commit.
        self._radial_selection: dict[str, str] = {}
        self._radial_index: dict[str, int] = {}
        self._radial_armed: dict[str, bool] = {"left": False, "right": False}
        self._shift_held = False
        self._ctrl_held = False
        self._arrow_held: str | None = None
        self._arrow_next_repeat = 0.0
        self._mic = MicDictation()
        self._mic_armed = False
        self._pause_since: float | None = None
        self._game_exit_since: float | None = None
        self._ignore_start_until_release = False
        self._chord_held = False
        self._chord_latched = False
        self._mode_before_game = "mouse"

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
        return self._cfg.input_mode if self._cfg.input_mode in INPUT_MODES else "mouse"

    def set_input_mode(self, mode: str) -> None:
        mode = mode if mode in INPUT_MODES else "mouse"
        if mode == self._cfg.input_mode:
            return
        if mode == "game" and self.input_mode in ("mouse", "keyboard"):
            self._mode_before_game = self.input_mode
        self._request_release_all()
        self._stop_mic()
        self._cfg.input_mode = mode
        self._number_mode = False
        self._right_stick_mode = "wheel"
        self._release_stick_arrow()
        self._left_layer = 0
        self._right_layer = 0
        self._clear_radial_state()
        self._ui_event("input_mode", {"mode": mode})

    def toggle_input_mode(self) -> None:
        """Back, and the overlay button: mouse ↔ keyboard. Game mode is the Back+Menu hold."""
        if self.input_mode == "game":
            restore = self._mode_before_game if self._mode_before_game in ("mouse", "keyboard") else "mouse"
            self.set_input_mode(restore)
        else:
            self.set_input_mode("keyboard" if self.input_mode == "mouse" else "mouse")

    def toggle_number_mode(self) -> None:
        if self.input_mode != "keyboard":
            return
        self._number_mode = not self._number_mode
        self._ui_event("number_mode", {"enabled": self._number_mode})

    @property
    def right_stick_mode(self) -> str:
        return self._right_stick_mode if self._right_stick_mode in RIGHT_STICK_MODES else "wheel"

    def set_right_stick_mode(self, mode: str) -> None:
        mode = "arrows" if mode == "arrows" else "wheel"
        if mode == self._right_stick_mode:
            return
        self._release_stick_arrow()
        self._acc_v = self._acc_h = 0.0
        self._right_stick_mode = mode
        self._ui_event("right_stick_mode", {"mode": mode})

    def toggle_right_stick_mode(self) -> None:
        if self.input_mode != "mouse":
            return
        self.set_right_stick_mode("arrows" if self.right_stick_mode == "wheel" else "wheel")
        self.rumble()

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
        self._mic.preload()
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
                    self._stop_mic()
                    self._game_exit_since = None
                    self._pause_since = None
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
                self._mic.shutdown()
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
            right_stick_mode=self.right_stick_mode,
            dictating=self._mic_armed,
            playing_macro=self._player.playing,
            pressed=state.buttons if state else frozenset(),
            left_layer=self._left_layer,
            right_layer=self._right_layer,
            left_radial_index=self._radial_index.get("left", -1),
            left_radial_glyph=self._radial_selection.get("left", ""),
            right_radial_index=self._radial_index.get("right", -1),
            right_radial_glyph=self._radial_selection.get("right", ""),
            radial_opener=self._active_side(),
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
        mode = self.input_mode
        keyboard = mode == "keyboard"
        game = mode == "game"
        self._drain_mic()
        self._tick_chords(cfg, state, now)
        mode = self.input_mode
        keyboard = mode == "keyboard"
        game = mode == "game"

        interceptor = self._interceptor
        for name in sorted(newly_pressed):
            # Menu/Start is hold-to-dictate (short tap still runs its binding).
            if name == "START" and not game:
                continue
            if interceptor is not None and not game:
                try:
                    if interceptor(name):
                        continue
                except Exception:
                    log.exception("button interceptor failed")
            binding = cfg.binding(name)
            if game:
                # Passthrough. Leaving game mode is the Back+Menu hold, not a tap.
                continue
            if not self._enabled:
                if binding.type == "special" and binding.special in (
                    "toggle_enabled", "toggle_input_mode", "toggle_number_mode",
                    "toggle_right_stick_mode",
                ):
                    self._special(binding.special, pressed=True)
                continue
            if keyboard:
                kb_binding = cfg.keyboard.binding(name)
                if _is_keyboard_action(kb_binding):
                    self._press(name, kb_binding, now)
                    continue
                if binding.type == "special":
                    self._press(name, binding, now)
                continue
            self._press(name, binding, now)

        for name in sorted(newly_released):
            self._release(name)

        if game or not self._enabled:
            self._clear_radial_state()
            self._release_stick_arrow()
            return

        if keyboard:
            self._release_stick_arrow()
            self._keyboard_tick(cfg, state, newly_pressed, newly_released, now)
            return

        self._repeat(cfg, now)
        self._pointer(cfg, state, dt)
        if self.right_stick_mode == "arrows":
            self._acc_v = self._acc_h = 0.0
            self._right_stick_arrows(cfg, state, now)
        else:
            self._release_stick_arrow()
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
        elif which == "toggle_right_stick_mode":
            self.toggle_right_stick_mode()
        elif which == "toggle_left_layer":
            if self.input_mode == "keyboard":
                self._left_layer = 1 - self._left_layer
                self._clear_side("left")
                self.rumble()
        elif which == "toggle_right_layer":
            if self.input_mode == "keyboard":
                self._right_layer = 1 - self._right_layer
                self._clear_side("right")
                self.rumble()
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
        now: float,
    ) -> None:
        del newly_pressed, newly_released  # stick release is magnitude-based, not button edges
        self._sync_modifiers()
        self._repeat(cfg, now)

        # Dual always-on wheels: aim at ≥95%, type the aimed glyph on release.
        self._tick_side("left", state.lx, state.ly, self._left_layer)
        self._tick_side("right", state.rx, state.ry, self._right_layer)

    def _tick_side(self, side: str, x: float, y: float, layer: int) -> None:
        mag = math.hypot(x, y)
        sel = select_side(
            side, x, y, layer=layer, number_mode=self._number_mode,
            layers=self._cfg.keyboard.layers_for(side),
        )
        if sel is not None:
            self._radial_selection[side] = sel.glyph
            self._radial_index[side] = sel.index
            self._radial_armed[side] = True
            return
        if mag < RELEASE_THRESHOLD and self._radial_armed.get(side):
            glyph = self._radial_selection.pop(side, "")
            self._radial_index.pop(side, None)
            self._radial_armed[side] = False
            if glyph:
                self._emit_char(glyph)

    def _clear_side(self, side: str) -> None:
        self._radial_selection.pop(side, None)
        self._radial_index.pop(side, None)
        self._radial_armed[side] = False

    def _clear_radial_state(self) -> None:
        self._radial_selection.clear()
        self._radial_index.clear()
        self._radial_armed = {"left": False, "right": False}

    def _active_side(self) -> str:
        """Prefer the side that currently has an aimed glyph (left first)."""
        for side in SIDES:
            if self._radial_armed.get(side) and side in self._radial_selection:
                return side
        return ""

    def _active_glyph(self) -> str:
        side = self._active_side()
        return self._radial_selection.get(side, "") if side else ""

    def _active_index(self) -> int:
        side = self._active_side()
        return self._radial_index.get(side, -1) if side else -1

    def _emit_char(self, glyph: str) -> None:
        ch = glyph
        if len(ch) == 1 and ch.isalpha():
            ch = ch.upper() if self._shift_held else ch.lower()
        if self._ctrl_held and len(ch) == 1 and ch.isalpha():
            try:
                # Ctrl is already held by the controller binding, so the letter alone completes the chord.
                win_input.tap_key(keys.resolve_vk(ch.lower()))
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
        self._clear_radial_state()
        self._release_stick_arrow()
        self._shift_held = False
        self._ctrl_held = False
        self._player.stop()
        self._stop_mic()
        self._acc_x = self._acc_y = self._acc_v = self._acc_h = 0.0
        win_input.release_all_modifiers()

    def _on_macro_state(self, name: str | None) -> None:
        self._ui_event("macro", {"name": name})

    def _tick_chords(self, cfg: Config, state: PadState, now: float) -> None:
        """Back+Menu held 2s toggles game mode. Menu hold dictates in mouse/keyboard."""
        back = "BACK" in state.buttons
        start = "START" in state.buttons
        if self._chord_latched:
            if not (back and start):
                self._chord_latched = False
                self._game_exit_since = None
            else:
                return
        if back and start:
            self._chord_held = True
            self._pause_since = None
            self._stop_mic()
            if self._game_exit_since is None:
                self._game_exit_since = now
            elif now - self._game_exit_since >= _GAME_EXIT_HOLD_S:
                self._game_exit_since = None
                self._chord_latched = True
                self._ignore_start_until_release = True
                if self.input_mode == "game":
                    restore = self._mode_before_game if self._mode_before_game in ("mouse", "keyboard") else "mouse"
                    self.set_input_mode(restore)
                else:
                    self.set_input_mode("game")
                self.rumble()
            return
        if self._chord_held:
            self._chord_held = False
            self._game_exit_since = None
            self._pause_since = None
            if start:
                self._ignore_start_until_release = True
            return
        self._game_exit_since = None
        if self.input_mode == "game":
            self._stop_mic()
            self._pause_since = None
            return
        if self._ignore_start_until_release:
            self._pause_since = None
            if not start:
                self._ignore_start_until_release = False
            return
        if start:
            if self._pause_since is None:
                self._pause_since = now
            elif not self._mic_armed and now - self._pause_since >= _MIC_ARM_S:
                self._mic_armed = True
                self._mic.set_listening(True)
            return
        if self._pause_since is not None and not self._mic_armed:
            self._fire_start_tap(cfg, now)
        self._pause_since = None
        if self._mic_armed:
            self._stop_mic()

    def _sync_modifiers(self) -> None:
        shift = False
        ctrl = False
        for held in self._held.values():
            if held.binding.type != "key":
                continue
            tokens = {token.lower() for token in held.binding.keys}
            if tokens & {"shift", "lshift", "rshift"}:
                shift = True
            if tokens & {"ctrl", "control", "lctrl", "rctrl"}:
                ctrl = True
        self._shift_held = shift
        self._ctrl_held = ctrl

    def _fire_start_tap(self, cfg: Config, now: float) -> None:
        """A short Menu press runs the keyboard binding, or the mouse-mode binding."""
        if self.input_mode == "keyboard":
            kb_binding = cfg.keyboard.binding("START")
            if _is_keyboard_action(kb_binding):
                self._press("START", kb_binding, now)
                self._release("START")
                return
        binding = cfg.binding("START")
        if not self._enabled:
            if binding.type == "special" and binding.special in (
                "toggle_enabled", "toggle_input_mode", "toggle_number_mode",
                "toggle_right_stick_mode",
            ):
                self._special(binding.special, pressed=True)
            return
        if self.input_mode == "keyboard" and binding.type != "special":
            return
        self._press("START", binding, now)
        self._release("START")

    def _stop_mic(self) -> None:
        if not self._mic_armed and not self._mic.listening:
            self._mic_armed = False
            return
        self._mic_armed = False
        self._mic.set_listening(False)

    def _drain_mic(self) -> None:
        for note in self._mic.drain_notes():
            self._ui_event("mic", {"message": note})
        for phrase in self._mic.drain_text():
            spoken = phrase[:1].upper() + phrase[1:]
            self._type_text(spoken + " ")

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
        v = shape_axis(state.ry, s.deadzone, s.acceleration, precision_scale=SCROLL_PRECISION)
        h = (
            shape_axis(state.rx, s.deadzone, s.acceleration, precision_scale=SCROLL_PRECISION)
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

    def _right_stick_arrows(self, cfg: Config, state: PadState, now: float) -> None:
        """Map right stick to arrow keys, snapping to the nearest cardinal."""
        deadzone = cfg.scroll.deadzone
        mag = math.hypot(state.rx, state.ry)
        direction = nearest_cardinal(state.rx, state.ry) if mag > deadzone else None
        if direction != self._arrow_held:
            self._release_stick_arrow()
            if direction is not None:
                self._press_stick_arrow(direction, now)
            return
        if direction is None or now < self._arrow_next_repeat:
            return
        interval = 1.0 / max(1.0, float(cfg.repeat_rate_hz))
        self._arrow_next_repeat = now + interval
        try:
            win_input.key_down(keys.resolve_vk(direction))
        except (KeyError, OSError):
            log.exception("arrow repeat %s failed", direction)

    def _press_stick_arrow(self, direction: str, now: float) -> None:
        try:
            win_input.key_down(keys.resolve_vk(direction))
            self._arrow_held = direction
            self._arrow_next_repeat = now + max(0.05, self._cfg.repeat_delay_ms / 1000.0)
        except (KeyError, OSError):
            log.exception("arrow press %s failed", direction)
            self._arrow_held = None

    def _release_stick_arrow(self) -> None:
        direction = self._arrow_held
        if direction is None:
            return
        self._arrow_held = None
        self._arrow_next_repeat = 0.0
        try:
            win_input.key_up(keys.resolve_vk(direction))
        except (KeyError, OSError):
            log.exception("arrow release %s failed", direction)
