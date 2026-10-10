"""Gamepad reader built on pygame-ce / SDL2's GameController API.

SDL's GameController layer normalizes Xbox, PlayStation, Switch Pro and most
generic pads to one logical layout, which is what makes "any gamepad" work.
Devices SDL does not recognize fall back to a best-effort raw joystick
mapping (Xbox-style button order).

All pygame/SDL calls must happen on the thread that called :meth:`open`;
the engine owns a dedicated thread for exactly that.
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from dataclasses import dataclass

log = logging.getLogger(__name__)

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_XINPUT_ENABLED", "1")
# SDL's RawInput backend needs a real (helper) window pumping WM_INPUT; in this
# windowless process it enumerates Xbox pads but never delivers data. The
# XInput/DirectInput path works everywhere, so disable RawInput.
os.environ.setdefault("SDL_JOYSTICK_RAWINPUT", "0")

AXIS_MAX = 32767.0


@dataclass(frozen=True)
class PadState:
    """One normalized controller snapshot.

    Stick axes are in [-1, 1]; ``y`` is positive *down* (SDL convention, which
    is also screen convention for cursor motion). Triggers are in [0, 1].
    """

    buttons: frozenset[str] = frozenset()
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    lt: float = 0.0
    rt: float = 0.0


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    guid: str
    name: str
    is_controller: bool


class GamepadReader:
    """Poll-based reader. Call :meth:`poll` at a steady rate from one thread."""

    RESCAN_INTERVAL_S = 1.0

    def __init__(self, *, preferred_guid: str = "") -> None:
        self.preferred_guid = preferred_guid
        self._pygame = None
        self._controller_mod = None
        self._controller = None
        self._joystick = None
        self._instance_id: int | None = None
        self._info: DeviceInfo | None = None
        self._last_scan = 0.0
        self._removed_event = None
        self._added_event = None
        self._devices: list[DeviceInfo] = []
        self._scan_needed = True

    # -- lifecycle ----------------------------------------------------------

    def open(self) -> None:
        import pygame
        from pygame._sdl2 import controller as controller_mod

        self._pygame = pygame
        self._controller_mod = controller_mod
        pygame.display.init()
        pygame.joystick.init()
        controller_mod.init()
        self._removed_event = getattr(pygame, "JOYDEVICEREMOVED", None)
        self._added_event = getattr(pygame, "JOYDEVICEADDED", None)
        self._try_connect(force=True)

    def close(self) -> None:
        self._disconnect()
        if self._pygame is not None:
            try:
                self._controller_mod.quit()
                self._pygame.joystick.quit()
                self._pygame.display.quit()
            except Exception:
                log.debug("pygame shutdown failed", exc_info=True)
        self._pygame = None

    # -- device management --------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._info is not None

    @property
    def info(self) -> DeviceInfo | None:
        return self._info

    def list_devices(self) -> list[DeviceInfo]:
        """Cached device list (refreshed on hotplug events or :meth:`request_rescan`)."""
        return list(self._devices)

    def request_rescan(self) -> None:
        self._scan_needed = True

    def _scan_devices(self) -> list[DeviceInfo]:
        """Enumerate joysticks. Never opens/closes the index we already hold:
        pygame shares one SDL handle per index, so quitting a temporary
        ``Joystick`` for it would close the live controller too."""
        pg = self._pygame
        if pg is None:
            return []
        own_index = None
        if self._joystick is not None:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    own_index = int(self._joystick.get_id())
            except Exception:
                own_index = None
        out: list[DeviceInfo] = []
        for index in range(pg.joystick.get_count()):
            if index == own_index and self._info is not None:
                out.append(DeviceInfo(index=index, guid=self._info.guid, name=self._info.name,
                                      is_controller=self._info.is_controller))
                continue
            try:
                joy = pg.joystick.Joystick(index)
            except Exception:
                continue
            try:
                out.append(DeviceInfo(
                    index=index,
                    guid=str(joy.get_guid()).lower(),
                    name=str(joy.get_name()),
                    is_controller=bool(self._controller_mod.is_controller(index)),
                ))
            finally:
                try:
                    joy.quit()
                except Exception:
                    pass
        self._devices = out
        self._scan_needed = False
        return out

    def _pick_device(self, devices: list[DeviceInfo]) -> DeviceInfo | None:
        if not devices:
            return None
        if self.preferred_guid:
            for d in devices:
                if d.guid == self.preferred_guid:
                    return d
        # Prefer recognized controllers over raw joysticks.
        for d in devices:
            if d.is_controller:
                return d
        return devices[0]

    def _try_connect(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_scan < self.RESCAN_INTERVAL_S:
            return
        self._last_scan = now
        device = self._pick_device(self._scan_devices())
        if device is None:
            return
        pg = self._pygame
        try:
            if device.is_controller:
                ctl = self._controller_mod.Controller(device.index)
                self._controller = ctl
                self._joystick = ctl.as_joystick()
            else:
                self._controller = None
                self._joystick = pg.joystick.Joystick(device.index)
            self._instance_id = int(self._joystick.get_instance_id())
            self._info = device
            log.info("gamepad connected: %s (%s, controller=%s)", device.name, device.guid, device.is_controller)
        except Exception:
            log.exception("opening gamepad %s failed", device.name)
            self._disconnect()

    def _disconnect(self) -> None:
        if self._info is not None:
            log.info("gamepad disconnected: %s", self._info.name)
        for obj in (self._controller, self._joystick):
            if obj is not None:
                try:
                    obj.quit()
                except Exception:
                    pass
        self._controller = None
        self._joystick = None
        self._instance_id = None
        self._info = None
        self._scan_needed = True

    def reselect(self, preferred_guid: str) -> None:
        """Switch to another device on the next poll."""
        self.preferred_guid = preferred_guid
        if self._info is not None and preferred_guid and self._info.guid != preferred_guid:
            self._disconnect()
        self._last_scan = 0.0

    # -- polling --------------------------------------------------------------

    def poll(self, trigger_threshold: float = 0.45) -> PadState | None:
        pg = self._pygame
        if pg is None:
            return None
        pg.event.pump()
        hotplug = [e for e in (self._added_event, self._removed_event) if e is not None]
        if hotplug:
            for event in pg.event.get(hotplug):
                self._scan_needed = True
                if event.type == self._removed_event and self._instance_id is not None:
                    removed = getattr(event, "instance_id", getattr(event, "instanceid", None))
                    if removed is not None and int(removed) == self._instance_id:
                        self._disconnect()
        pg.event.clear()
        if self._info is None:
            self._try_connect()
            return None
        if self._scan_needed:
            self._scan_devices()
        try:
            if self._controller is not None:
                if not self._controller.attached():
                    self._disconnect()
                    return None
                return self._read_controller(trigger_threshold)
            if self._joystick is not None:
                if hasattr(self._joystick, "get_attached") and not self._joystick.get_attached():
                    self._disconnect()
                    return None
                return self._read_joystick(trigger_threshold)
        except Exception:
            log.exception("reading gamepad failed; dropping device")
            self._disconnect()
        return None

    def _read_controller(self, trigger_threshold: float) -> PadState:
        pg = self._pygame
        c = self._controller
        pressed = set()
        for name, const in (
            ("A", pg.CONTROLLER_BUTTON_A), ("B", pg.CONTROLLER_BUTTON_B),
            ("X", pg.CONTROLLER_BUTTON_X), ("Y", pg.CONTROLLER_BUTTON_Y),
            ("LB", pg.CONTROLLER_BUTTON_LEFTSHOULDER), ("RB", pg.CONTROLLER_BUTTON_RIGHTSHOULDER),
            ("LS", pg.CONTROLLER_BUTTON_LEFTSTICK), ("RS", pg.CONTROLLER_BUTTON_RIGHTSTICK),
            ("BACK", pg.CONTROLLER_BUTTON_BACK), ("START", pg.CONTROLLER_BUTTON_START),
            ("GUIDE", pg.CONTROLLER_BUTTON_GUIDE),
            ("DPAD_UP", pg.CONTROLLER_BUTTON_DPAD_UP), ("DPAD_DOWN", pg.CONTROLLER_BUTTON_DPAD_DOWN),
            ("DPAD_LEFT", pg.CONTROLLER_BUTTON_DPAD_LEFT), ("DPAD_RIGHT", pg.CONTROLLER_BUTTON_DPAD_RIGHT),
        ):
            if c.get_button(const):
                pressed.add(name)
        lx = c.get_axis(pg.CONTROLLER_AXIS_LEFTX) / AXIS_MAX
        ly = c.get_axis(pg.CONTROLLER_AXIS_LEFTY) / AXIS_MAX
        rx = c.get_axis(pg.CONTROLLER_AXIS_RIGHTX) / AXIS_MAX
        ry = c.get_axis(pg.CONTROLLER_AXIS_RIGHTY) / AXIS_MAX
        lt = max(0.0, c.get_axis(pg.CONTROLLER_AXIS_TRIGGERLEFT) / AXIS_MAX)
        rt = max(0.0, c.get_axis(pg.CONTROLLER_AXIS_TRIGGERRIGHT) / AXIS_MAX)
        if lt >= trigger_threshold:
            pressed.add("LT")
        if rt >= trigger_threshold:
            pressed.add("RT")
        return PadState(frozenset(pressed), _clamp(lx), _clamp(ly), _clamp(rx), _clamp(ry), lt, rt)

    # Xbox-style raw order used by most generic pads SDL has no mapping for.
    _RAW_BUTTONS = ("A", "B", "X", "Y", "LB", "RB", "BACK", "START", "LS", "RS", "GUIDE")

    def _read_joystick(self, trigger_threshold: float) -> PadState:
        j = self._joystick
        pressed = set()
        nb = j.get_numbuttons()
        for idx, name in enumerate(self._RAW_BUTTONS):
            if idx < nb and j.get_button(idx):
                pressed.add(name)
        if j.get_numhats() > 0:
            hx, hy = j.get_hat(0)
            if hy > 0:
                pressed.add("DPAD_UP")
            elif hy < 0:
                pressed.add("DPAD_DOWN")
            if hx < 0:
                pressed.add("DPAD_LEFT")
            elif hx > 0:
                pressed.add("DPAD_RIGHT")
        na = j.get_numaxes()

        def axis(i: int) -> float:
            return float(j.get_axis(i)) if i < na else 0.0

        lx, ly = axis(0), axis(1)
        rx, ry = axis(2), axis(3)
        lt = rt = 0.0
        if na >= 6:
            # Triggers usually report -1..1 (rest = -1) on raw joysticks.
            lt = (axis(4) + 1.0) / 2.0
            rt = (axis(5) + 1.0) / 2.0
        elif na == 5:
            combined = axis(4)
            lt, rt = max(0.0, combined), max(0.0, -combined)
        if lt >= trigger_threshold:
            pressed.add("LT")
        if rt >= trigger_threshold:
            pressed.add("RT")
        return PadState(frozenset(pressed), _clamp(lx), _clamp(ly), _clamp(rx), _clamp(ry), lt, rt)

    # -- feedback -------------------------------------------------------------

    def rumble(self, low: float = 0.3, high: float = 0.3, duration_ms: int = 80) -> None:
        target = self._controller or self._joystick
        if target is None:
            return
        try:
            target.rumble(low, high, duration_ms)
        except Exception:
            pass


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))
