"""Radial on-screen keyboard layouts and stick → sector selection."""

from __future__ import annotations

import math
from dataclasses import dataclass

# Six most-common letter groups (frequency order), one radial each.
RADIAL_LETTERS: dict[str, tuple[str, ...]] = {
    "LT": ("E", "A", "R", "I", "O", "T"),
    "RT": ("N", "S", "L", "C", "U", "D"),
    "LB": ("P", "M", "H", "G", "B", "F"),
    "RB": ("Y", "W", "K", "V", "X", "Z"),
}

# Number mode: only triggers; five digits each (sixth sector unused).
RADIAL_NUMBERS: dict[str, tuple[str, ...]] = {
    "LT": ("1", "2", "3", "4", "5"),
    "RT": ("6", "7", "8", "9", "0"),
    "LB": (),
    "RB": (),
}

# Which stick aims which opener.
RADIAL_STICK: dict[str, str] = {
    "LT": "left",
    "LB": "left",
    "RT": "right",
    "RB": "right",
}

RADIAL_LABELS: dict[str, str] = {
    "LT": "Left trigger",
    "RT": "Right trigger",
    "LB": "Left bumper",
    "RB": "Right bumper",
}

OPENERS = ("LT", "LB", "RT", "RB")

# Stick must leave this radius before a sector is active.
AIM_DEADZONE = 0.35


@dataclass(frozen=True)
class RadialSelection:
    opener: str
    index: int
    glyph: str


def items_for(opener: str, *, number_mode: bool) -> tuple[str, ...]:
    table = RADIAL_NUMBERS if number_mode else RADIAL_LETTERS
    return table.get(opener, ())


def sector_count(opener: str, *, number_mode: bool) -> int:
    return len(items_for(opener, number_mode=number_mode))


def stick_angle_sector(x: float, y: float, n: int, *, deadzone: float = AIM_DEADZONE) -> int | None:
    """Map stick (SDL y+ down) to sector index 0..n-1, or None if centered.

    Sector 0 is centered on stick-up, then clockwise.
    """
    if n <= 0:
        return None
    mag = math.hypot(x, y)
    if mag < deadzone:
        return None
    # Mathematical angle: 0 = up, increasing clockwise, in [0, 2π).
    # atan2(x, -y): up (0,-1) → 0; right (1,0) → π/2.
    angle = math.atan2(x, -y) % (2.0 * math.pi)
    sector = int(angle / (2.0 * math.pi / n)) % n
    return sector


def select_radial(
    opener: str,
    lx: float, ly: float, rx: float, ry: float,
    *,
    number_mode: bool,
    deadzone: float = AIM_DEADZONE,
) -> RadialSelection | None:
    items = items_for(opener, number_mode=number_mode)
    if not items:
        return None
    stick = RADIAL_STICK.get(opener, "left")
    x, y = (lx, ly) if stick == "left" else (rx, ry)
    idx = stick_angle_sector(x, y, len(items), deadzone=deadzone)
    if idx is None:
        return None
    return RadialSelection(opener=opener, index=idx, glyph=items[idx])
