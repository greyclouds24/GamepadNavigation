"""Macro recording and playback.

A macro is a list of timestamped steps (see :data:`STEP_KINDS`). Recording
uses global low-level hooks so keys and clicks are captured in whatever app
the user is working in. Playback replays through ``SendInput`` on a worker
thread and always releases anything it pressed when it finishes or is
stopped.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import keys, win_hooks, win_input
from .config import Macro

log = logging.getLogger(__name__)

STEP_KINDS = ("key_down", "key_up", "mouse_down", "mouse_up", "wheel", "hwheel", "move", "text", "delay")

# Keep moves at a sane rate; 60/s is plenty for playback fidelity.
_MOVE_MIN_INTERVAL = 1.0 / 60.0


def describe_step(step: dict[str, Any]) -> str:
    kind = step.get("k", "?")
    t = float(step.get("t", 0.0))
    prefix = f"{t:6.2f}s  "
    if kind in ("key_down", "key_up"):
        name = step.get("key") or f"vk{step.get('vk')}"
        return prefix + ("press " if kind == "key_down" else "release ") + keys.chord_label([name])
    if kind in ("mouse_down", "mouse_up"):
        where = f" @({step['x']},{step['y']})" if "x" in step else ""
        return prefix + ("mouse down " if kind == "mouse_down" else "mouse up ") + str(step.get("button", "left")) + where
    if kind == "wheel":
        d = int(step.get("delta", 0))
        return prefix + f"scroll {'up' if d > 0 else 'down'} {abs(d) / 120:.2f}"
    if kind == "hwheel":
        d = int(step.get("delta", 0))
        return prefix + f"scroll {'right' if d > 0 else 'left'} {abs(d) / 120:.2f}"
    if kind == "move":
        return prefix + f"move to ({step.get('x')},{step.get('y')})"
    if kind == "text":
        return prefix + f'type "{step.get("text", "")}"'
    if kind == "delay":
        return prefix + f"wait {float(step.get('seconds', 0)):.2f}s"
    return prefix + str(kind)


def summarize(macro: Macro) -> str:
    n_keys = sum(1 for s in macro.steps if s.get("k") == "key_down")
    n_clicks = sum(1 for s in macro.steps if s.get("k") == "mouse_down")
    return f"{len(macro.steps)} steps, {n_keys} key presses, {n_clicks} clicks, {macro.duration:.1f}s"


# --- recorder ----------------------------------------------------------------

class MacroRecorder:
    """Record keyboard + mouse activity into macro steps."""

    def __init__(self, *, on_event: Callable[[int], None] | None = None) -> None:
        self._on_event = on_event
        self._lock = threading.Lock()
        self._steps: list[dict[str, Any]] = []
        self._start_t = 0.0
        self._last_move_t = 0.0
        self._held_keys: set[int] = set()
        self._held_mouse: set[str] = set()
        self._record_positions = False
        self._own_pid = win_input.current_pid()
        self._session: win_hooks.HookSession | None = None

    @property
    def active(self) -> bool:
        return self._session is not None and self._session.active

    @property
    def step_count(self) -> int:
        with self._lock:
            return len(self._steps)

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._start_t if self.active else 0.0

    def start(self, *, record_positions: bool = False) -> None:
        if self.active:
            return
        with self._lock:
            self._steps = []
            self._held_keys.clear()
            self._held_mouse.clear()
        self._record_positions = record_positions
        self._start_t = time.perf_counter()
        self._last_move_t = 0.0
        self._session = win_hooks.HookSession(on_key=self._on_key, on_mouse=self._on_mouse, name="macro-recorder")
        self._session.start()

    def stop(self) -> list[dict[str, Any]]:
        session = self._session
        self._session = None
        if session is not None:
            session.stop()
        with self._lock:
            steps = list(self._steps)
            now = self._now()
            # Close anything still held so playback never leaves keys stuck.
            for vk in sorted(self._held_keys):
                steps.append({"t": now, "k": "key_up", "vk": vk, "key": keys.vk_to_token(vk)})
            for button in sorted(self._held_mouse):
                steps.append({"t": now, "k": "mouse_up", "button": button})
            self._held_keys.clear()
            self._held_mouse.clear()
        return _trim(steps)

    def _now(self) -> float:
        return round(time.perf_counter() - self._start_t, 4)

    def _append(self, step: dict[str, Any]) -> None:
        with self._lock:
            self._steps.append(step)
            count = len(self._steps)
        if self._on_event is not None:
            try:
                self._on_event(count)
            except Exception:
                pass

    def _on_key(self, ev: win_hooks.KeyEvent) -> bool:
        if ev.pressed:
            if ev.vk in self._held_keys:
                return False  # auto-repeat; recorded as one hold
            self._held_keys.add(ev.vk)
            kind = "key_down"
        else:
            self._held_keys.discard(ev.vk)
            kind = "key_up"
        self._append({"t": self._now(), "k": kind, "vk": int(ev.vk), "key": keys.vk_to_token(ev.vk)})
        return False

    def _on_mouse(self, ev: win_hooks.MouseEvent) -> bool:
        if ev.kind == "move":
            if not self._record_positions:
                return False
            if ev.t - self._last_move_t < _MOVE_MIN_INTERVAL:
                return False
            self._last_move_t = ev.t
            self._append({"t": self._now(), "k": "move", "x": ev.x, "y": ev.y})
            return False
        if ev.kind in ("down", "up"):
            # Ignore clicks on our own windows (the Stop button, for example).
            if win_input.pid_at_point(ev.x, ev.y) == self._own_pid:
                return False
            step: dict[str, Any] = {"t": self._now(), "k": f"mouse_{ev.kind}", "button": ev.button}
            if self._record_positions:
                step["x"], step["y"] = ev.x, ev.y
            if ev.kind == "down":
                self._held_mouse.add(ev.button)
            else:
                self._held_mouse.discard(ev.button)
            self._append(step)
            return False
        if ev.kind in ("wheel", "hwheel"):
            if win_input.pid_at_point(ev.x, ev.y) == self._own_pid:
                return False
            self._append({"t": self._now(), "k": ev.kind, "delta": int(ev.delta)})
        return False


def _trim(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop leading idle time so playback starts immediately."""
    if not steps:
        return steps
    first = float(steps[0].get("t", 0.0))
    if first <= 0.0:
        return steps
    out = []
    for s in steps:
        s = dict(s)
        s["t"] = round(max(0.0, float(s.get("t", 0.0)) - first), 4)
        out.append(s)
    return out


# --- player ------------------------------------------------------------------

@dataclass
class _Playback:
    macro: Macro
    thread: threading.Thread
    stop: threading.Event
    held: Callable[[], bool]


class MacroPlayer:
    """Play one macro at a time on a worker thread."""

    def __init__(self, *, on_state: Callable[[str | None], None] | None = None) -> None:
        self._on_state = on_state
        self._lock = threading.Lock()
        self._current: _Playback | None = None

    @property
    def playing(self) -> str | None:
        with self._lock:
            return self._current.macro.name if self._current is not None else None

    def play(self, macro: Macro, *, held: Callable[[], bool] | None = None) -> bool:
        with self._lock:
            if self._current is not None and self._current.thread.is_alive():
                return False
            stop = threading.Event()
            pb = _Playback(macro=macro, stop=stop, held=held or (lambda: False), thread=None)  # type: ignore[arg-type]
            pb.thread = threading.Thread(target=self._run, args=(pb,), name=f"macro:{macro.name}", daemon=True)
            self._current = pb
        self._notify(macro.name)
        pb.thread.start()
        return True

    def stop(self, wait: float = 0.5) -> None:
        with self._lock:
            pb = self._current
        if pb is None:
            return
        pb.stop.set()
        if pb.thread is not threading.current_thread():
            pb.thread.join(wait)

    def _notify(self, name: str | None) -> None:
        if self._on_state is not None:
            try:
                self._on_state(name)
            except Exception:
                pass

    def _run(self, pb: _Playback) -> None:
        held_keys: set[int] = set()
        held_mouse: set[str] = set()
        try:
            while True:
                self._run_once(pb, held_keys, held_mouse)
                if pb.stop.is_set() or not pb.macro.loop_while_held or not pb.held():
                    break
        except Exception:
            log.exception("macro %s failed", pb.macro.name)
        finally:
            for vk in held_keys:
                try:
                    win_input.key_up(vk)
                except OSError:
                    pass
            for button in held_mouse:
                try:
                    win_input.mouse_up(button)
                except OSError:
                    pass
            with self._lock:
                if self._current is pb:
                    self._current = None
            self._notify(None)

    def _run_once(self, pb: _Playback, held_keys: set[int], held_mouse: set[str]) -> None:
        speed = max(0.05, float(pb.macro.speed or 1.0))
        start = time.perf_counter()
        for step in pb.macro.steps:
            target = start + float(step.get("t", 0.0)) / speed
            if not _sleep_until(target, pb.stop):
                return
            kind = step.get("k")
            if kind == "key_down":
                vk = _step_vk(step)
                if vk:
                    win_input.key_down(vk)
                    held_keys.add(vk)
            elif kind == "key_up":
                vk = _step_vk(step)
                if vk:
                    win_input.key_up(vk)
                    held_keys.discard(vk)
            elif kind == "mouse_down":
                _maybe_move(step)
                button = str(step.get("button", "left"))
                win_input.mouse_down(button)
                held_mouse.add(button)
            elif kind == "mouse_up":
                _maybe_move(step)
                button = str(step.get("button", "left"))
                win_input.mouse_up(button)
                held_mouse.discard(button)
            elif kind == "wheel":
                win_input.wheel(int(step.get("delta", 0)))
            elif kind == "hwheel":
                win_input.hwheel(int(step.get("delta", 0)))
            elif kind == "move":
                _maybe_move(step)
            elif kind == "text":
                win_input.type_text(str(step.get("text", "")))
            elif kind == "delay":
                if not _sleep_until(time.perf_counter() + float(step.get("seconds", 0.0)) / speed, pb.stop):
                    return


def _step_vk(step: dict[str, Any]) -> int:
    vk = step.get("vk")
    if vk:
        return int(vk)
    key = step.get("key")
    if key:
        try:
            return keys.resolve_vk(str(key))
        except KeyError:
            return 0
    return 0


def _maybe_move(step: dict[str, Any]) -> None:
    if "x" in step and "y" in step:
        win_input.set_cursor_pos(int(step["x"]), int(step["y"]))


def _sleep_until(target: float, stop: threading.Event) -> bool:
    """Sleep until ``target`` (perf_counter). Returns False if stopped."""
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return not stop.is_set()
        if stop.wait(min(remaining, 0.05)):
            return False
