"""Dual always-on radial keyboard layouts and stick → sector selection.

Left and right sticks each drive a 6-sector wheel. Stick clicks toggle each
side between its default and alternate letter layer. Vowels and punctuation
live on discrete buttons (handled by the engine), not on the radials.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Layer 0 = default, layer 1 = stick-click alternate. Clockwise from top (up).
LEFT_LAYERS: tuple[tuple[str, ...], ...] = (
    ("T", "H", "Y", "D", "X", "?"),
    ("R", "G", "W", "C", "V", "J"),
)

RIGHT_LAYERS: tuple[tuple[str, ...], ...] = (
    ("S", "P", "F", "L", "K", "!"),
    ("N", "M", "B", "U", "Z", "Q"),
)

# Number mode: six glyphs per side (same aim / release rules).
LEFT_NUMBERS: tuple[str, ...] = ("1", "2", "3", "4", "5", ".")
RIGHT_NUMBERS: tuple[str, ...] = ("6", "7", "8", "9", "0", "-")

SIDES = ("left", "right")

# Aim must reach this fraction of full deflection before a sector is selected.
AIM_THRESHOLD = 0.95
# After aiming, releasing below this commits the selected glyph.
RELEASE_THRESHOLD = 0.45

# Back-compat aliases used by the settings legend / older tests.
RADIAL_LETTERS: dict[str, tuple[str, ...]] = {
    "left": LEFT_LAYERS[0],
    "right": RIGHT_LAYERS[0],
    "left_alt": LEFT_LAYERS[1],
    "right_alt": RIGHT_LAYERS[1],
}
RADIAL_NUMBERS: dict[str, tuple[str, ...]] = {
    "left": LEFT_NUMBERS,
    "right": RIGHT_NUMBERS,
}
RADIAL_LABELS: dict[str, str] = {
    "left": "Left stick (default)",
    "left_alt": "Left stick (LS click)",
    "right": "Right stick (default)",
    "right_alt": "Right stick (RS click)",
}


@dataclass(frozen=True)
class RadialSelection:
    side: str
    index: int
    glyph: str
    layer: int


def items_for(side: str, *, layer: int = 0, number_mode: bool = False) -> tuple[str, ...]:
    if number_mode:
        return LEFT_NUMBERS if side == "left" else RIGHT_NUMBERS
    layers = LEFT_LAYERS if side == "left" else RIGHT_LAYERS
    return layers[max(0, min(layer, len(layers) - 1))]


def stick_angle_sector(x: float, y: float, n: int, *, deadzone: float = AIM_THRESHOLD) -> int | None:
    """Map stick (SDL y+ down) to sector index 0..n-1, or None if below threshold.

    Sector 0 is centered on stick-up, then clockwise.
    """
    if n <= 0:
        return None
    mag = math.hypot(x, y)
    if mag < deadzone:
        return None
    # atan2(x, -y): up (0,-1) → 0; right (1,0) → π/2.
    angle = math.atan2(x, -y) % (2.0 * math.pi)
    return int(angle / (2.0 * math.pi / n)) % n


def select_side(
    side: str,
    x: float,
    y: float,
    *,
    layer: int = 0,
    number_mode: bool = False,
    aim_threshold: float = AIM_THRESHOLD,
) -> RadialSelection | None:
    items = items_for(side, layer=layer, number_mode=number_mode)
    if not items:
        return None
    idx = stick_angle_sector(x, y, len(items), deadzone=aim_threshold)
    if idx is None:
        return None
    return RadialSelection(side=side, index=idx, glyph=items[idx], layer=layer)
