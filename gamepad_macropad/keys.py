"""Key token names <-> Win32 virtual-key codes.

Config files and the UI use lowercase logical tokens (``enter``, ``ctrl``,
``f5``, ``a``). This module is pure data + lookups and has no Win32 calls so
it is importable (and testable) on any platform.
"""

from __future__ import annotations

# --- Named keys -------------------------------------------------------------
VK: dict[str, int] = {
    # Modifiers (generic = left side when injected)
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "lshift": 0xA0, "rshift": 0xA1,
    "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "apps": 0x5D, "menu": 0x5D,
    # Whitespace / editing
    "enter": 0x0D, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "esc": 0x1B,
    # Navigation
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    # Locks / system
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "printscreen": 0x2C, "pause": 0x13,
    # Punctuation (US layout names)
    "semicolon": 0xBA, "equals": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "backtick": 0xC0,
    "lbracket": 0xDB, "backslash": 0xDC, "rbracket": 0xDD, "quote": 0xDE,
    # Numpad
    **{f"num{i}": 0x60 + i for i in range(10)},
    "nummultiply": 0x6A, "numadd": 0x6B, "numsubtract": 0x6D,
    "numdecimal": 0x6E, "numdivide": 0x6F,
    # Function keys
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    # Media / browser
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "nexttrack": 0xB0, "prevtrack": 0xB1, "stopmedia": 0xB2, "playpause": 0xB3,
    "browserback": 0xA6, "browserforward": 0xA7, "browserrefresh": 0xA8,
    "browserhome": 0xAC,
    "launchmail": 0xB4, "launchmedia": 0xB5,
    "sleep": 0x5F,
}

# Aliases accepted on input but never produced on output.
_ALIASES = {
    "control": "ctrl", "return": "enter", "escape": "esc", "del": "delete",
    "ins": "insert", "pgup": "pageup", "pgdn": "pagedown", "back": "backspace",
    "super": "win", "meta": "win", "cmd": "win", "lcontrol": "lctrl",
    "rcontrol": "rctrl", "lmenu": "lalt", "rmenu": "ralt", "spacebar": "space",
    "plus": "equals", "grave": "backtick", "tilde": "backtick",
    "dot": "period", "mute": "volumemute",
    "prtsc": "printscreen", "prtscr": "printscreen",
}

# Single-character punctuation -> token
_CHAR_TOKENS = {
    ";": "semicolon", "=": "equals", ",": "comma", "-": "minus",
    ".": "period", "/": "slash", "`": "backtick", "[": "lbracket",
    "\\": "backslash", "]": "rbracket", "'": "quote", " ": "space",
}

MODIFIER_TOKENS = frozenset({
    "shift", "ctrl", "alt", "win",
    "lshift", "rshift", "lctrl", "rctrl", "lalt", "ralt", "lwin", "rwin",
})
MODIFIER_VKS = frozenset(VK[t] for t in MODIFIER_TOKENS)

# VKs whose scancodes need the EXTENDEDKEY flag.
EXTENDED_VKS = frozenset({
    0xA1, 0xA3, 0xA5, 0x5B, 0x5C, 0x5D,
    0x2E, 0x2D, 0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22,
    0x6F, 0x90, 0x2C, 0x13,
    0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3, 0xA6, 0xA7, 0xA8, 0xAC,
    0xB4, 0xB5, 0x5F,
})

# Canonical reverse map. Build so that preferred names win over aliases that
# share a VK (e.g. "win" over "lwin", "apps" over "menu").
_PREFERRED_ORDER = (
    "shift", "ctrl", "alt", "win", "apps", "esc", "enter",
)
_VK_TO_TOKEN: dict[int, str] = {}
for _name in _PREFERRED_ORDER:
    _VK_TO_TOKEN.setdefault(VK[_name], _name)
for _name, _code in VK.items():
    _VK_TO_TOKEN.setdefault(_code, _name)
# When we see side-specific VKs from a hook, keep them side-specific.
for _name in ("lshift", "rshift", "lctrl", "rctrl", "lalt", "ralt", "rwin"):
    _VK_TO_TOKEN[VK[_name]] = _name


def normalize_token(token: str) -> str:
    t = token.strip().lower()
    return _ALIASES.get(t, t)


def resolve_vk(token: str) -> int:
    """Map a token to a virtual-key code. Raises ``KeyError`` if unknown."""
    t = normalize_token(token)
    if t in VK:
        return VK[t]
    if len(t) == 1:
        if t in _CHAR_TOKENS:
            return VK[_CHAR_TOKENS[t]]
        if t.isalnum() and t.isascii():
            return ord(t.upper())
    raise KeyError(f"Unknown key '{token}'")


def is_known_token(token: str) -> bool:
    try:
        resolve_vk(token)
    except KeyError:
        return False
    return True


def vk_to_token(vk: int) -> str | None:
    """Canonical token for a VK, or ``None`` when we have no name for it."""
    if vk in _VK_TO_TOKEN:
        return _VK_TO_TOKEN[vk]
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    return None


def generic_modifier(vk: int) -> int:
    """Collapse a side-specific modifier VK to its generic VK (for display)."""
    return {
        0xA0: 0x10, 0xA1: 0x10,
        0xA2: 0x11, 0xA3: 0x11,
        0xA4: 0x12, 0xA5: 0x12,
        0x5C: 0x5B,
    }.get(vk, vk)


def chord_label(tokens: list[str] | tuple[str, ...]) -> str:
    """Human-friendly label like ``Ctrl+Shift+S``."""
    pretty = {
        "ctrl": "Ctrl", "lctrl": "LCtrl", "rctrl": "RCtrl",
        "shift": "Shift", "lshift": "LShift", "rshift": "RShift",
        "alt": "Alt", "lalt": "LAlt", "ralt": "RAlt",
        "win": "Win", "lwin": "Win", "rwin": "RWin",
        "esc": "Esc", "enter": "Enter", "space": "Space", "tab": "Tab",
        "backspace": "Backspace", "delete": "Del", "pageup": "PgUp",
        "pagedown": "PgDn", "printscreen": "PrtSc",
    }
    out = []
    for tok in tokens:
        t = normalize_token(tok)
        if t in pretty:
            out.append(pretty[t])
        elif len(t) == 1 or (t[0] == "f" and t[1:].isdigit()):
            out.append(t.upper())
        else:
            out.append(t.capitalize())
    return "+".join(out)
