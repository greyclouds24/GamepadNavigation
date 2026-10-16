"""Dual always-on radial keyboard layouts and stick → sector selection.

Left and right sticks each drive a 6-sector wheel. Stick clicks toggle each
side between its default and alternate letter layer. Vowels and punctuation
live on discrete buttons (handled by the engine), not on the radials.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SECTORS = 6

# Layer 0 = default, layer 1 = stick-click alternate. Clockwise from top (up).
LEFT_LAYERS: tuple[tuple[str, ...], ...] = (
    ("T", "H", "N", "R", "S", "L"),
    ("G", "Y", "P", "B", "W", "?"),
)

RIGHT_LAYERS: tuple[tuple[str, ...], ...] = (
    ("D", "C", "U", "M", "F", "V"),
    ("K", "J", "X", "Q", "Z", "!"),
)

# Every letter/symbol that must appear exactly once across the four wheels.
REQUIRED_WHEEL_GLYPHS: frozenset[str] = frozenset(
    g for layer in (*LEFT_LAYERS, *RIGHT_LAYERS) for g in layer
)

# Number mode: six glyphs per side (same aim / release rules).
LEFT_NUMBERS: tuple[str, ...] = ("1", "2", "3", "4", "5", ".")
RIGHT_NUMBERS: tuple[str, ...] = ("6", "7", "8", "9", "0", "-")

SIDES = ("left", "right")
LAYER_KEYS = ("left", "left_alt", "right", "right_alt")

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


@dataclass(frozen=True)
class LayoutIssues:
    """Problems with a custom four-wheel letter layout."""

    duplicates: frozenset[str] = frozenset()
    missing: frozenset[str] = frozenset()
    invalid: frozenset[str] = frozenset()  # empty / multi-char / not in required set
    incomplete: bool = False                # a layer has fewer/more than 6 slots

    @property
    def ok(self) -> bool:
        return not (self.duplicates or self.missing or self.invalid or self.incomplete)

    def summary(self) -> str:
        if self.ok:
            return "All letters and symbols present once."
        parts: list[str] = []
        if self.incomplete:
            parts.append("each wheel needs exactly 6 glyphs")
        if self.duplicates:
            parts.append("duplicates: " + " ".join(sorted(self.duplicates)))
        if self.missing:
            parts.append("missing: " + " ".join(sorted(self.missing)))
        if self.invalid:
            parts.append("invalid: " + " ".join(sorted(self.invalid)))
        return " · ".join(parts)


def default_layer_map() -> dict[str, list[str]]:
    return {
        "left": list(LEFT_LAYERS[0]),
        "left_alt": list(LEFT_LAYERS[1]),
        "right": list(RIGHT_LAYERS[0]),
        "right_alt": list(RIGHT_LAYERS[1]),
    }


def normalize_layer(values: object, *, fallback: tuple[str, ...] | list[str]) -> list[str]:
    """Coerce a saved layer to six single-character uppercase glyphs."""
    fb = [str(g) for g in fallback]
    if not isinstance(values, (list, tuple)):
        return list(fb)
    out: list[str] = []
    for i in range(SECTORS):
        if i < len(values):
            raw = str(values[i]).strip()
            glyph = raw[:1].upper() if raw else ""
            if glyph:
                out.append(glyph)
                continue
        out.append(fb[i] if i < len(fb) else "?")
    return out


def layers_tuple(left: list[str], left_alt: list[str]) -> tuple[tuple[str, ...], ...]:
    return (tuple(left), tuple(left_alt))


def collect_glyphs(
    left: list[str],
    left_alt: list[str],
    right: list[str],
    right_alt: list[str],
) -> list[str]:
    return list(left) + list(left_alt) + list(right) + list(right_alt)


def validate_wheel_layout(
    left: list[str],
    left_alt: list[str],
    right: list[str],
    right_alt: list[str],
) -> LayoutIssues:
    layers = (left, left_alt, right, right_alt)
    incomplete = any(len(layer) != SECTORS for layer in layers)
    glyphs = collect_glyphs(left, left_alt, right, right_alt)
    counts: dict[str, int] = {}
    invalid: set[str] = set()
    for g in glyphs:
        if not g or len(g) != 1 or g not in REQUIRED_WHEEL_GLYPHS:
            invalid.add(g if g else "∅")
            continue
        counts[g] = counts.get(g, 0) + 1
    duplicates = frozenset(g for g, n in counts.items() if n > 1)
    present = frozenset(counts)
    missing = frozenset(REQUIRED_WHEEL_GLYPHS - present)
    return LayoutIssues(
        duplicates=duplicates,
        missing=missing,
        invalid=frozenset(invalid),
        incomplete=incomplete,
    )


def items_for(
    side: str,
    *,
    layer: int = 0,
    number_mode: bool = False,
    layers: tuple[tuple[str, ...], ...] | None = None,
) -> tuple[str, ...]:
    if number_mode:
        return LEFT_NUMBERS if side == "left" else RIGHT_NUMBERS
    if layers is None:
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
    layers: tuple[tuple[str, ...], ...] | None = None,
) -> RadialSelection | None:
    items = items_for(side, layer=layer, number_mode=number_mode, layers=layers)
    if not items:
        return None
    idx = stick_angle_sector(x, y, len(items), deadzone=aim_threshold)
    if idx is None:
        return None
    return RadialSelection(side=side, index=idx, glyph=items[idx], layer=layer)
