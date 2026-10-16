"""Unit tests for the platform-independent logic (config, keys, stick math)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gamepad_macropad import keys  # noqa: E402
from gamepad_macropad.config import (  # noqa: E402
    BUTTONS,
    Binding,
    Config,
    Macro,
    load_config,
    next_input_mode,
    save_config,
)
from gamepad_macropad.engine import nearest_cardinal, shape_axis, shape_radial  # noqa: E402
from gamepad_macropad.radial import (  # noqa: E402
    AIM_THRESHOLD,
    LEFT_LAYERS,
    REQUIRED_WHEEL_GLYPHS,
    RIGHT_LAYERS,
    default_layer_map,
    select_side,
    stick_angle_sector,
    validate_wheel_layout,
)


# --- keys ------------------------------------------------------------------------------

def test_resolve_named_and_single_char_keys():
    assert keys.resolve_vk("enter") == 0x0D
    assert keys.resolve_vk("Return") == 0x0D
    assert keys.resolve_vk("a") == ord("A")
    assert keys.resolve_vk("5") == ord("5")
    assert keys.resolve_vk(";") == keys.VK["semicolon"]
    assert keys.resolve_vk("F12") == 0x7B
    assert keys.resolve_vk("ctrl") == 0x11 and keys.resolve_vk("control") == 0x11


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        keys.resolve_vk("notakey")
    assert not keys.is_known_token("??")


def test_vk_roundtrip_prefers_canonical_names():
    assert keys.vk_to_token(keys.resolve_vk("win")) == "win"
    assert keys.vk_to_token(0xA0) == "lshift"
    assert keys.vk_to_token(ord("Q")) == "q"
    assert keys.vk_to_token(0x30) == "0"
    assert keys.vk_to_token(0xFF) is None
    for name in ("enter", "tab", "esc", "pageup", "f5", "numadd", "volumeup"):
        assert keys.vk_to_token(keys.resolve_vk(name)) == name


def test_chord_label():
    assert keys.chord_label(["ctrl", "shift", "s"]) == "Ctrl+Shift+S"
    assert keys.chord_label(["lalt", "f4"]) == "LAlt+F4"


# --- config -----------------------------------------------------------------------------

def test_defaults_cover_every_button():
    cfg = Config.defaults()
    assert set(cfg.bindings) == set(BUTTONS)
    assert cfg.binding("A").type == "mouse"
    assert cfg.binding("DPAD_UP").repeat is True
    assert cfg.binding("GUIDE").special == "toggle_number_mode"
    assert cfg.binding("RS").special == "toggle_right_stick_mode"


def test_input_mode_cycles_mouse_keyboard_game():
    assert next_input_mode("mouse") == "keyboard"
    assert next_input_mode("keyboard") == "game"
    assert next_input_mode("game") == "mouse"
    assert next_input_mode("nope") == "keyboard"


def test_config_roundtrip(tmp_path):
    cfg = Config.defaults()
    cfg.pointer.speed = 999
    cfg.scroll.horizontal_enabled = False
    cfg.bindings["A"] = Binding.for_key("ctrl", "c")
    cfg.macros["hello"] = Macro(name="hello", steps=[{"t": 0.0, "k": "text", "text": "hi"}], speed=2.0)
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.pointer.speed == 999
    assert loaded.scroll.horizontal_enabled is False
    assert loaded.bindings["A"].keys == ["ctrl", "c"]
    assert loaded.macros["hello"].speed == 2.0
    assert loaded.macros["hello"].steps[0]["text"] == "hi"


def test_config_tolerates_garbage(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "pointer": {"speed": "fast", "deadzone": 0.3, "bogus": 1},
        "bindings": {"A": {"type": "nonsense"}, "ZZ": {"type": "key"}},
        "macros": {"m": {"steps": "nope"}},
    }), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.pointer.speed == Config.defaults().pointer.speed
    assert cfg.pointer.deadzone == 0.3
    assert cfg.bindings["A"].type == "none"
    assert "ZZ" not in cfg.bindings
    assert cfg.macros["m"].steps == []
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert load_config(corrupt).binding("B").type == "mouse"


def test_device_profiles_are_independent(tmp_path):
    cfg = Config.defaults()
    cfg.pointer.speed = 2000
    cfg.bindings["A"] = Binding.for_key("a")
    assert cfg.switch_device("guid-a", name="Pad A")
    assert cfg.profiles["guid-a"].pointer.speed == 2000

    assert cfg.switch_device("guid-b", name="Pad B")
    assert cfg.pointer.speed == Config.defaults().pointer.speed
    assert cfg.binding("A").type == "mouse"

    cfg.pointer.speed = 400
    cfg.bindings["A"] = Binding.for_key("b")
    cfg.save_active_profile("guid-b", name="Pad B")

    cfg.switch_device("guid-a")
    assert cfg.pointer.speed == 2000
    assert cfg.binding("A").keys == ["a"]

    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert "guid-a" in loaded.profiles and "guid-b" in loaded.profiles
    loaded.switch_device("guid-b")
    assert loaded.pointer.speed == 400
    assert loaded.binding("A").keys == ["b"]


def test_legacy_config_migrates_into_preferred_device(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "version": 1,
        "preferred_device": "abc123",
        "pointer": {"speed": 1234},
        "bindings": {"A": {"type": "key", "keys": ["x"]}},
    }), encoding="utf-8")
    cfg = load_config(path)
    assert "abc123" in cfg.profiles
    assert cfg.profiles["abc123"].pointer.speed == 1234
    assert cfg.profiles["abc123"].bindings["A"].keys == ["x"]


def test_rs_middle_mouse_migrates_to_stick_mode_toggle(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "version": 4,
        "bindings": {"RS": {"type": "mouse", "button": "middle"}},
    }), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.binding("RS").special == "toggle_right_stick_mode"
    # Already on v5: intentional middle-mouse RS is preserved.
    path.write_text(json.dumps({
        "version": 5,
        "bindings": {"RS": {"type": "mouse", "button": "middle"}},
    }), encoding="utf-8")
    assert load_config(path).binding("RS").button == "middle"


def test_binding_describe():
    assert Binding().describe() == "-"
    assert Binding.for_key("ctrl", "z").describe() == "Ctrl+Z"
    assert Binding.for_key("up", repeat=True).describe() == "Up (repeat)"
    assert Binding.for_mouse("right").describe() == "Mouse right"
    assert Binding(type="macro", macro="x").describe() == "Macro: x"
    assert "Pause" in Binding.for_special("toggle_enabled").describe()


# --- stick shaping ---------------------------------------------------------------------------

def test_shape_radial_deadzone_and_full_deflection():
    assert shape_radial(0.05, 0.05, 0.12, 2.0) == (0.0, 0.0, 0.0)
    x, y, m = shape_radial(1.0, 0.0, 0.12, 2.0)
    assert math.isclose(m, 1.0) and math.isclose(x, 1.0) and y == 0.0
    # Diagonal full push is not clipped into a square.
    x, y, m = shape_radial(0.8, 0.8, 0.1, 1.0)
    assert math.isclose(m, 1.0)
    assert math.isclose(x, y)
    # Curve keeps small moves small.
    _, _, lin = shape_radial(0.5, 0.0, 0.0, 1.0)
    _, _, sq = shape_radial(0.5, 0.0, 0.0, 2.0)
    assert sq < lin


def test_curve_precision_band_ramp_and_full_speed():
    accel = 2.0
    scale = 0.25
    _, _, m50 = shape_radial(0.5, 0.0, 0.0, accel, precision_scale=scale)
    _, _, m90 = shape_radial(0.90, 0.0, 0.0, accel, precision_scale=scale)
    _, _, m94 = shape_radial(0.94, 0.0, 0.0, accel, precision_scale=scale)
    _, _, m98 = shape_radial(0.98, 0.0, 0.0, accel, precision_scale=scale)
    assert m98 == 1.0
    assert math.isclose(m90, scale)
    assert 0 < m50 < scale
    assert scale < m94 < 1.0
    # Higher precision scale → faster under 90%.
    _, _, slow = shape_radial(0.5, 0.0, 0.0, accel, precision_scale=0.2)
    _, _, fast = shape_radial(0.5, 0.0, 0.0, accel, precision_scale=0.5)
    assert slow < fast


def test_shape_axis_sign_and_deadzone():
    assert shape_axis(0.1, 0.2, 2.0) == 0.0
    assert shape_axis(-1.0, 0.2, 2.0) == -1.0
    assert 0 < shape_axis(0.6, 0.2, 1.0) < 1
    assert shape_axis(-0.6, 0.2, 1.0) == -shape_axis(0.6, 0.2, 1.0)


def test_nearest_cardinal_snaps_to_arrows():
    assert nearest_cardinal(0.0, -1.0) == "up"
    assert nearest_cardinal(0.0, 1.0) == "down"
    assert nearest_cardinal(-1.0, 0.0) == "left"
    assert nearest_cardinal(1.0, 0.0) == "right"
    # Off-axis vectors round to the nearest of the four.
    assert nearest_cardinal(0.9, -0.2) == "right"
    assert nearest_cardinal(0.2, -0.9) == "up"
    assert nearest_cardinal(-0.8, 0.3) == "left"
    assert nearest_cardinal(0.1, 0.95) == "down"


def test_retired_snap_special_becomes_middle_mouse():
    assert Binding.from_dict({"type": "special", "special": "snap"}).type == "mouse"
    assert Binding.from_dict({"type": "special", "special": "snap"}).button == "middle"
    assert Binding.from_dict({"type": "special", "special": "precision"}).button == "middle"


def test_radial_sector_selection():
    # Stick up at full deflection → sector 0 (T on left default).
    assert stick_angle_sector(0.0, -1.0, 6) == 0
    assert stick_angle_sector(0.0, 0.0, 6) is None
    # Below 95% is ignored for selection.
    assert stick_angle_sector(0.0, -0.90, 6, deadzone=AIM_THRESHOLD) is None
    assert stick_angle_sector(0.0, -0.96, 6, deadzone=AIM_THRESHOLD) == 0

    sel = select_side("left", 0.0, -1.0, layer=0)
    assert sel is not None and sel.glyph == "T" and sel.index == 0
    assert LEFT_LAYERS[0] == ("T", "H", "N", "R", "S", "L")
    assert RIGHT_LAYERS[0] == ("D", "C", "U", "M", "F", "V")
    assert LEFT_LAYERS[1] == ("G", "Y", "P", "B", "W", "?")
    assert RIGHT_LAYERS[1] == ("K", "J", "X", "Q", "Z", "!")

    # Alt layers via stick click.
    sel_alt = select_side("left", 0.0, -1.0, layer=1)
    assert sel_alt is not None and sel_alt.glyph == "G"
    sel_right_alt = select_side("right", 0.0, -1.0, layer=1)
    assert sel_right_alt is not None and sel_right_alt.glyph == "K"

    # Clockwise from up: next sector is to the right.
    # Angle near 60° (π/3) → sector 1.
    angle = math.pi / 3
    x, y = math.sin(angle), -math.cos(angle)
    sel_h = select_side("left", x, y, layer=0)
    assert sel_h is not None and sel_h.glyph == "H"

    sel_num = select_side("left", 0.0, -1.0, number_mode=True)
    assert sel_num is not None and sel_num.glyph == "1"
    sel_rt = select_side("right", 0.0, -1.0, layer=0)
    assert sel_rt is not None and sel_rt.glyph == "D"


def test_wheel_layout_validation_requires_full_unique_set():
    d = default_layer_map()
    ok = validate_wheel_layout(d["left"], d["left_alt"], d["right"], d["right_alt"])
    assert ok.ok
    assert REQUIRED_WHEEL_GLYPHS == frozenset(
        d["left"] + d["left_alt"] + d["right"] + d["right_alt"]
    )

    bad_left = list(d["left"])
    bad_left[0] = "H"  # duplicates H, drops T
    issues = validate_wheel_layout(bad_left, d["left_alt"], d["right"], d["right_alt"])
    assert not issues.ok
    assert "H" in issues.duplicates
    assert "T" in issues.missing

    cfg = Config.defaults()
    assert cfg.keyboard.left == list(LEFT_LAYERS[0])
    assert cfg.keyboard.right_alt == list(RIGHT_LAYERS[1])
