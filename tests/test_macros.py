"""Tests for macro step helpers (Windows only: the module binds to user32)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

from gamepad_macropad import macros  # noqa: E402
from gamepad_macropad.config import Macro  # noqa: E402


def test_trim_removes_leading_idle_time():
    steps = [{"t": 1.5, "k": "key_down", "vk": 65}, {"t": 1.75, "k": "key_up", "vk": 65}]
    trimmed = macros._trim(steps)
    assert trimmed[0]["t"] == 0.0
    assert trimmed[1]["t"] == pytest.approx(0.25)
    assert macros._trim([]) == []


def test_describe_step_and_summary():
    m = Macro(name="demo", steps=[
        {"t": 0.0, "k": "key_down", "vk": 0x11, "key": "ctrl"},
        {"t": 0.1, "k": "key_down", "vk": ord("C"), "key": "c"},
        {"t": 0.2, "k": "key_up", "vk": ord("C"), "key": "c"},
        {"t": 0.3, "k": "key_up", "vk": 0x11, "key": "ctrl"},
        {"t": 0.5, "k": "mouse_down", "button": "left", "x": 10, "y": 20},
        {"t": 0.6, "k": "mouse_up", "button": "left", "x": 10, "y": 20},
        {"t": 0.7, "k": "wheel", "delta": -240},
        {"t": 0.8, "k": "text", "text": "hi"},
    ])
    assert "press Ctrl" in macros.describe_step(m.steps[0])
    assert "@(10,20)" in macros.describe_step(m.steps[4])
    assert "scroll down 2.00" in macros.describe_step(m.steps[6])
    assert macros.summarize(m) == "8 steps, 2 key presses, 1 clicks, 0.8s"


def test_player_runs_delay_only_macro_and_reports_state():
    states: list[str | None] = []
    player = macros.MacroPlayer(on_state=states.append)
    m = Macro(name="wait", steps=[{"t": 0.0, "k": "delay", "seconds": 0.1}])
    assert player.play(m) is True
    assert player.play(m) is False  # one at a time
    deadline = time.time() + 2
    while player.playing and time.time() < deadline:
        time.sleep(0.02)
    assert player.playing is None
    assert states == ["wait", None]


def test_player_stop_interrupts_long_macro():
    player = macros.MacroPlayer()
    m = Macro(name="long", steps=[{"t": 0.0, "k": "delay", "seconds": 10}])
    assert player.play(m)
    t0 = time.time()
    player.stop(wait=2)
    assert time.time() - t0 < 1.0
    assert player.playing is None


def test_step_vk_falls_back_to_key_name():
    assert macros._step_vk({"vk": 65}) == 65
    assert macros._step_vk({"key": "enter"}) == 0x0D
    assert macros._step_vk({"key": "nope"}) == 0
