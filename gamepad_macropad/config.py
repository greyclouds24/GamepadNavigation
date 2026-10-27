"""Configuration model, defaults and JSON persistence.

Everything here is plain data. The UI edits a :class:`Config`, saves it, and
the engine hot-swaps to the new instance; nothing in this module touches
Win32 or SDL.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import APP_NAME

log = logging.getLogger(__name__)

CONFIG_VERSION = 6

# Logical controller buttons (SDL GameController naming, Xbox-style labels).
BUTTONS: tuple[str, ...] = (
    "A", "B", "X", "Y",
    "LB", "RB", "LT", "RT",
    "LS", "RS",
    "BACK", "START", "GUIDE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
)

BUTTON_LABELS: dict[str, str] = {
    "A": "A", "B": "B", "X": "X", "Y": "Y",
    "LB": "LB (left bumper)", "RB": "RB (right bumper)",
    "LT": "LT (left trigger)", "RT": "RT (right trigger)",
    "LS": "LS (left stick click)", "RS": "RS (right stick click)",
    "BACK": "Back / View / Select", "START": "Start / Menu",
    "GUIDE": "Guide / Home",
    "DPAD_UP": "D-pad Up", "DPAD_DOWN": "D-pad Down",
    "DPAD_LEFT": "D-pad Left", "DPAD_RIGHT": "D-pad Right",
}

ACTION_TYPES = ("none", "key", "mouse", "double_click", "text", "macro", "special")
ACTION_TYPE_LABELS = {
    "none": "Nothing",
    "key": "Key / hotkey (held while button is held)",
    "mouse": "Mouse button (held while button is held)",
    "double_click": "Double click",
    "text": "Type text",
    "macro": "Play macro",
    "special": "App function",
}

SPECIALS = (
    "toggle_enabled", "toggle_input_mode", "toggle_number_mode",
    "toggle_right_stick_mode",
    "open_settings", "stop_macros", "toggle_overlay",
)
SPECIAL_LABELS = {
    "toggle_enabled": "Pause / resume conversion",
    "toggle_input_mode": "Switch mouse / keyboard",
    "toggle_number_mode": "Toggle number mode (keyboard)",
    "toggle_right_stick_mode": "Switch right stick scroll / arrows",
    "toggle_left_layer": "Switch left wheel layer",
    "toggle_right_layer": "Switch right wheel layer",
    "open_settings": "Open settings window",
    "stop_macros": "Stop any playing macro",
    "toggle_overlay": "Collapse / expand overlay",
}

RIGHT_STICK_MODES = ("wheel", "arrows")

INPUT_MODES = ("mouse", "keyboard", "game")


def next_input_mode(mode: str) -> str:
    """mouse → keyboard → game → mouse."""
    try:
        index = INPUT_MODES.index(mode)
    except ValueError:
        index = 0
    return INPUT_MODES[(index + 1) % len(INPUT_MODES)]


@dataclass
class Binding:
    type: str = "none"
    keys: list[str] = field(default_factory=list)   # type == key
    button: str = "left"                             # type == mouse / double_click
    text: str = ""                                   # type == text
    macro: str = ""                                  # type == macro
    special: str = ""                                # type == special
    repeat: bool = False                             # auto-repeat while held (key/mouse)

    @staticmethod
    def for_key(*tokens: str, repeat: bool = False) -> "Binding":
        return Binding(type="key", keys=list(tokens), repeat=repeat)

    @staticmethod
    def for_mouse(button: str) -> "Binding":
        return Binding(type="mouse", button=button)

    @staticmethod
    def for_special(name: str) -> "Binding":
        return Binding(type="special", special=name)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Binding":
        if not data:
            return Binding()
        b = Binding()
        b.type = str(data.get("type", "none"))
        if b.type not in ACTION_TYPES:
            b.type = "none"
        b.keys = [str(k) for k in data.get("keys", []) if str(k).strip()]
        b.button = str(data.get("button", "left"))
        b.text = str(data.get("text", ""))
        b.macro = str(data.get("macro", ""))
        b.special = str(data.get("special", ""))
        # Retired specials (precision / snap) become middle-mouse click.
        if b.type == "special" and b.special in ("precision", "snap"):
            return Binding.for_mouse("middle")
        b.repeat = bool(data.get("repeat", False))
        return b

    def describe(self) -> str:
        from .keys import chord_label
        if self.type == "none":
            return "-"
        if self.type == "key":
            label = chord_label(self.keys) if self.keys else "(no keys)"
            return label + (" (repeat)" if self.repeat else "")
        if self.type == "mouse":
            return f"Mouse {self.button}"
        if self.type == "double_click":
            return f"Double click ({self.button})"
        if self.type == "text":
            preview = self.text.replace("\n", "\u21b5")
            if len(preview) > 24:
                preview = preview[:21] + "..."
            return f'Type "{preview}"'
        if self.type == "macro":
            return f"Macro: {self.macro or '(none)'}"
        if self.type == "special":
            return SPECIAL_LABELS.get(self.special, self.special)
        return self.type


@dataclass
class Macro:
    name: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    speed: float = 1.0           # playback speed multiplier
    loop_while_held: bool = False

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Macro":
        return Macro(
            name=str(data.get("name", "macro")),
            steps=[dict(s) for s in data.get("steps", []) if isinstance(s, dict)],
            speed=float(data.get("speed", 1.0) or 1.0),
            loop_while_held=bool(data.get("loop_while_held", False)),
        )

    @property
    def duration(self) -> float:
        if not self.steps:
            return 0.0
        return float(self.steps[-1].get("t", 0.0))


@dataclass
class PointerSettings:
    deadzone: float = 0.12
    speed: float = 1500.0        # pixels / second at full deflection
    acceleration: float = 11.0   # response exponent; 1 = linear, higher = quieter center, harder rim
    precision_scale: float = 0.50
    invert_y: bool = False


@dataclass
class ScrollSettings:
    deadzone: float = 0.18
    speed: float = 14.0          # notches / second (vertical) at full deflection
    horizontal_speed: float = 10.0
    acceleration: float = 11.0
    smooth: bool = True          # emit sub-notch wheel deltas (browsers, Office)
    invert_vertical: bool = False
    invert_horizontal: bool = False
    horizontal_enabled: bool = True


@dataclass
class OverlaySettings:
    x: int | None = None
    y: int | None = None
    compact: bool = False
    opacity: float = 0.94


def _default_wheel(key: str) -> list[str]:
    from .radial import default_layer_map
    return default_layer_map()[key]


@dataclass
class KeyboardSettings:
    """Radial keyboard-mode options and editable letter wheels."""

    show_hud: bool = True
    # Six glyphs each, clockwise from stick-up. Must together cover REQUIRED_WHEEL_GLYPHS.
    left: list[str] = field(default_factory=lambda: _default_wheel("left"))
    left_alt: list[str] = field(default_factory=lambda: _default_wheel("left_alt"))
    right: list[str] = field(default_factory=lambda: _default_wheel("right"))
    right_alt: list[str] = field(default_factory=lambda: _default_wheel("right_alt"))

    def layers_for(self, side: str) -> tuple[tuple[str, ...], ...]:
        if side == "left":
            return (tuple(self.left), tuple(self.left_alt))
        return (tuple(self.right), tuple(self.right_alt))

    def reset_wheels(self) -> None:
        from .radial import default_layer_map
        defaults = default_layer_map()
        self.left = defaults["left"]
        self.left_alt = defaults["left_alt"]
        self.right = defaults["right"]
        self.right_alt = defaults["right_alt"]

    def apply_wheels(
        self,
        left: list[str],
        left_alt: list[str],
        right: list[str],
        right_alt: list[str],
    ) -> None:
        self.left = list(left)
        self.left_alt = list(left_alt)
        self.right = list(right)
        self.right_alt = list(right_alt)

    bindings: dict[str, Binding] = field(default_factory=lambda: _default_keyboard_bindings())

    def binding(self, name: str) -> Binding:
        return self.bindings.get(name) or Binding()


def _default_keyboard_bindings() -> dict[str, Binding]:
    """What each button does in keyboard mode. Empty means 'use the mouse-mode special, if any'."""
    return {
        "A": Binding.for_key("space"),
        "B": Binding.for_key("backspace"),
        "X": Binding(),
        "Y": Binding(),
        "LB": Binding.for_key("a"),
        "RB": Binding.for_key("i"),
        "LT": Binding.for_key("e"),
        "RT": Binding.for_key("o"),
        "LS": Binding.for_special("toggle_left_layer"),
        "RS": Binding.for_special("toggle_right_layer"),
        "BACK": Binding(),
        "START": Binding(),
        "GUIDE": Binding(),
        "DPAD_UP": Binding.for_key("period"),
        "DPAD_DOWN": Binding.for_key("ctrl"),
        "DPAD_LEFT": Binding.for_key("comma"),
        "DPAD_RIGHT": Binding.for_key("enter"),
    }


def _keyboard_bindings_from_dict(data: Any) -> dict[str, Binding]:
    out = _default_keyboard_bindings()
    if isinstance(data, dict):
        for name in BUTTONS:
            if name in data and isinstance(data[name], dict):
                out[name] = Binding.from_dict(data[name])
    return out


def _default_bindings() -> dict[str, Binding]:
    return {
        "A": Binding.for_mouse("left"),
        "B": Binding.for_mouse("right"),
        "X": Binding.for_mouse("middle"),
        "Y": Binding(type="double_click", button="left"),
        "LB": Binding.for_key("ctrl"),
        "RB": Binding.for_key("shift"),
        "LT": Binding.for_key("esc"),
        "RT": Binding.for_key("enter"),
        "LS": Binding.for_mouse("left"),
        "RS": Binding.for_special("toggle_right_stick_mode"),
        "BACK": Binding.for_special("toggle_input_mode"),
        "START": Binding.for_special("open_settings"),
        "GUIDE": Binding.for_special("toggle_number_mode"),
        "DPAD_UP": Binding.for_key("up", repeat=True),
        "DPAD_DOWN": Binding.for_key("down", repeat=True),
        "DPAD_LEFT": Binding.for_key("left", repeat=True),
        "DPAD_RIGHT": Binding.for_key("right", repeat=True),
    }


def _fill_dataclass(obj: Any, section: Any) -> None:
    if not isinstance(section, dict):
        return
    for key, value in section.items():
        if hasattr(obj, key):
            current = getattr(obj, key)
            try:
                if isinstance(current, bool):
                    setattr(obj, key, bool(value))
                elif isinstance(current, int) and not isinstance(current, bool):
                    setattr(obj, key, int(value))
                elif isinstance(current, float):
                    setattr(obj, key, float(value))
                elif current is None or isinstance(current, (str, type(None))):
                    setattr(obj, key, value)
            except (TypeError, ValueError):
                pass


def _bindings_from_dict(data: Any) -> dict[str, Binding]:
    out = _default_bindings()
    if isinstance(data, dict):
        for name in BUTTONS:
            if name in data:
                out[name] = Binding.from_dict(data[name])
    return out


def _migrate_rs_middle_to_stick_mode(bindings: dict[str, Binding]) -> None:
    """Old default RS = middle mouse → scroll/arrows toggle (config v5)."""
    rs = bindings.get("RS")
    if rs and rs.type == "mouse" and rs.button == "middle":
        bindings["RS"] = Binding.for_special("toggle_right_stick_mode")


def _migrate_dramatic_stick_curve(cfg: "Config") -> None:
    """v6: louder sub-rim and a steeper curve, when the v5 defaults were left as-is."""
    _bump_stick_curve(cfg.pointer, cfg.scroll)
    for profile in cfg.profiles.values():
        _bump_stick_curve(profile.pointer, profile.scroll)


def _bump_stick_curve(pointer: PointerSettings, scroll: ScrollSettings) -> None:
    if abs(pointer.acceleration - 7.0) < 1e-6:
        pointer.acceleration = 11.0
    if abs(pointer.precision_scale - 0.25) < 1e-6:
        pointer.precision_scale = 0.50
    if abs(scroll.acceleration - 7.0) < 1e-6:
        scroll.acceleration = 11.0


def _load_keyboard(kb: KeyboardSettings, section: Any) -> None:
    from .radial import default_layer_map, normalize_layer, validate_wheel_layout

    if not isinstance(section, dict):
        return
    if "show_hud" in section:
        try:
            kb.show_hud = bool(section["show_hud"])
        except (TypeError, ValueError):
            pass
    defaults = default_layer_map()
    for key in ("left", "left_alt", "right", "right_alt"):
        if key in section:
            setattr(kb, key, normalize_layer(section[key], fallback=defaults[key]))
    issues = validate_wheel_layout(kb.left, kb.left_alt, kb.right, kb.right_alt)
    if not issues.ok:
        kb.reset_wheels()
    if "bindings" in section:
        kb.bindings = _keyboard_bindings_from_dict(section.get("bindings"))


@dataclass
class DeviceProfile:
    """Per-gamepad stick feel, bindings and trigger threshold."""

    name: str = ""
    pointer: PointerSettings = field(default_factory=PointerSettings)
    scroll: ScrollSettings = field(default_factory=ScrollSettings)
    bindings: dict[str, Binding] = field(default_factory=_default_bindings)
    trigger_threshold: float = 0.45

    @staticmethod
    def defaults(name: str = "") -> "DeviceProfile":
        return DeviceProfile(name=name)

    @staticmethod
    def from_dict(data: dict[str, Any] | None, *, name: str = "") -> "DeviceProfile":
        profile = DeviceProfile.defaults(name=name)
        if not isinstance(data, dict):
            return profile
        profile.name = str(data.get("name", name) or name)
        _fill_dataclass(profile.pointer, data.get("pointer"))
        _fill_dataclass(profile.scroll, data.get("scroll"))
        if "trigger_threshold" in data:
            try:
                profile.trigger_threshold = float(data["trigger_threshold"])
            except (TypeError, ValueError):
                pass
        if "bindings" in data:
            profile.bindings = _bindings_from_dict(data.get("bindings"))
        return profile

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pointer": asdict(self.pointer),
            "scroll": asdict(self.scroll),
            "trigger_threshold": self.trigger_threshold,
            "bindings": {name: asdict(b) for name, b in self.bindings.items()},
        }


@dataclass
class Config:
    version: int = CONFIG_VERSION
    pointer: PointerSettings = field(default_factory=PointerSettings)
    scroll: ScrollSettings = field(default_factory=ScrollSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    keyboard: KeyboardSettings = field(default_factory=KeyboardSettings)
    bindings: dict[str, Binding] = field(default_factory=dict)
    macros: dict[str, Macro] = field(default_factory=dict)
    profiles: dict[str, DeviceProfile] = field(default_factory=dict)
    active_device: str = ""       # GUID of the profile currently loaded into pointer/scroll/bindings
    input_mode: str = "mouse"    # mouse | keyboard | game
    trigger_threshold: float = 0.45
    repeat_delay_ms: int = 400
    repeat_rate_hz: float = 25.0
    poll_hz: int = 250
    haptics: bool = True
    start_enabled: bool = True
    preferred_device: str = ""    # SDL GUID; empty = first available

    # -- construction -------------------------------------------------------

    @staticmethod
    def defaults() -> "Config":
        cfg = Config()
        cfg.bindings = _default_bindings()
        return cfg

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Config":
        cfg = Config.defaults()
        if not isinstance(data, dict):
            return cfg

        try:
            old_version = int(data.get("version", 0) or 0)
        except (TypeError, ValueError):
            old_version = 0

        _fill_dataclass(cfg.pointer, data.get("pointer"))
        _fill_dataclass(cfg.scroll, data.get("scroll"))
        _fill_dataclass(cfg.overlay, data.get("overlay"))
        _load_keyboard(cfg.keyboard, data.get("keyboard"))
        for key in ("trigger_threshold", "repeat_delay_ms", "repeat_rate_hz", "poll_hz",
                    "haptics", "start_enabled", "preferred_device", "active_device", "input_mode"):
            if key in data:
                try:
                    current = getattr(cfg, key)
                    value = data[key]
                    if isinstance(current, bool):
                        setattr(cfg, key, bool(value))
                    elif isinstance(current, int):
                        setattr(cfg, key, int(value))
                    elif isinstance(current, float):
                        setattr(cfg, key, float(value))
                    else:
                        setattr(cfg, key, str(value))
                except (TypeError, ValueError):
                    pass
        if cfg.input_mode not in INPUT_MODES:
            cfg.input_mode = "mouse"
        if "bindings" in data:
            cfg.bindings = _bindings_from_dict(data.get("bindings"))
            # Pause (Back) is now the mouse/keyboard mode switch by default.
            back = cfg.bindings.get("BACK")
            if back and back.type == "special" and back.special == "toggle_enabled":
                cfg.bindings["BACK"] = Binding.for_special("toggle_input_mode")
        macros = data.get("macros")
        if isinstance(macros, dict):
            cfg.macros = {}
            for name, m in macros.items():
                if isinstance(m, dict):
                    m = dict(m)
                    m.setdefault("name", name)
                    cfg.macros[str(name)] = Macro.from_dict(m)
        profiles = data.get("profiles")
        if isinstance(profiles, dict):
            for guid, pdata in profiles.items():
                if isinstance(pdata, dict):
                    cfg.profiles[str(guid).lower()] = DeviceProfile.from_dict(pdata)
        # Migrate legacy single-profile configs into the preferred / active device slot.
        if not cfg.profiles:
            legacy_guid = (cfg.active_device or cfg.preferred_device or "").lower()
            if legacy_guid:
                cfg.profiles[legacy_guid] = cfg.snapshot_active(name="")
                cfg.active_device = legacy_guid
        if old_version < 5:
            _migrate_rs_middle_to_stick_mode(cfg.bindings)
            for profile in cfg.profiles.values():
                _migrate_rs_middle_to_stick_mode(profile.bindings)
        if old_version < 6:
            _migrate_dramatic_stick_curve(cfg)
        cfg.version = CONFIG_VERSION
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pointer": asdict(self.pointer),
            "scroll": asdict(self.scroll),
            "overlay": asdict(self.overlay),
            "keyboard": asdict(self.keyboard),
            "trigger_threshold": self.trigger_threshold,
            "repeat_delay_ms": self.repeat_delay_ms,
            "repeat_rate_hz": self.repeat_rate_hz,
            "poll_hz": self.poll_hz,
            "haptics": self.haptics,
            "start_enabled": self.start_enabled,
            "preferred_device": self.preferred_device,
            "active_device": self.active_device,
            "input_mode": self.input_mode,
            "bindings": {name: asdict(b) for name, b in self.bindings.items()},
            "macros": {name: asdict(m) for name, m in self.macros.items()},
            "profiles": {guid: p.to_dict() for guid, p in self.profiles.items()},
        }

    def clone(self) -> "Config":
        return copy.deepcopy(self)

    def binding(self, name: str) -> Binding:
        return self.bindings.get(name) or Binding()

    def snapshot_active(self, *, name: str = "") -> DeviceProfile:
        return DeviceProfile(
            name=name,
            pointer=copy.deepcopy(self.pointer),
            scroll=copy.deepcopy(self.scroll),
            bindings=copy.deepcopy(self.bindings),
            trigger_threshold=self.trigger_threshold,
        )

    def apply_profile(self, profile: DeviceProfile) -> None:
        self.pointer = copy.deepcopy(profile.pointer)
        self.scroll = copy.deepcopy(profile.scroll)
        self.bindings = copy.deepcopy(profile.bindings)
        self.trigger_threshold = float(profile.trigger_threshold)

    def save_active_profile(self, guid: str, *, name: str = "") -> None:
        guid = (guid or "").lower()
        if not guid:
            return
        existing = self.profiles.get(guid)
        display = name or (existing.name if existing else "")
        self.profiles[guid] = self.snapshot_active(name=display)
        self.active_device = guid

    def load_device_profile(self, guid: str, *, name: str = "") -> bool:
        """Load the profile for ``guid`` into the active settings.

        Returns True if the active settings changed.
        """
        guid = (guid or "").lower()
        if not guid:
            return False
        if guid == (self.active_device or "").lower() and guid in self.profiles:
            if name and self.profiles[guid].name != name:
                self.profiles[guid].name = name
            return False
        profile = self.profiles.get(guid)
        if profile is None:
            # First profile ever adopts whatever is currently active (legacy configs).
            # Later new pads start from defaults so each controller stays independent.
            if not self.profiles and not self.active_device:
                profile = self.snapshot_active(name=name)
            else:
                profile = DeviceProfile.defaults(name=name)
            self.profiles[guid] = profile
        elif name and not profile.name:
            profile.name = name
        self.apply_profile(profile)
        self.active_device = guid
        return True

    def switch_device(self, new_guid: str, *, name: str = "") -> bool:
        """Persist the current pad's settings, then load ``new_guid``'s profile."""
        new_guid = (new_guid or "").lower()
        old = (self.active_device or "").lower()
        if old and old != new_guid:
            self.save_active_profile(old)
        if not new_guid:
            self.active_device = ""
            return bool(old)
        return self.load_device_profile(new_guid, name=name) or (old != new_guid)


# --- persistence ---------------------------------------------------------------

def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME.replace(" ", "")


def exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    override = os.environ.get("GAMEPAD_MACROPAD_CONFIG")
    if override:
        return Path(override)
    portable = exe_dir() / "config.json"
    if portable.exists():
        return portable
    return app_data_dir() / "config.json"


def log_path() -> Path:
    return app_data_dir() / "gamepad_macropad.log"


_save_lock = threading.Lock()


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config.defaults()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        log.exception("could not read config %s; using defaults", path)
        return Config.defaults()
    return Config.from_dict(data)


def save_config(cfg: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with _save_lock:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(cfg.to_dict(), fh, indent=2)
        os.replace(tmp, path)
    return path
