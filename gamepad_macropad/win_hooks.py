"""Low-level Windows keyboard/mouse hooks used for macro recording and
hotkey capture.

A :class:`HookSession` owns a dedicated thread that installs
``WH_KEYBOARD_LL`` / ``WH_MOUSE_LL`` hooks and pumps a message loop (LL hooks
only fire on the installing thread, and only while it pumps messages). Events
injected by this application (tagged with :data:`win_input.INJECT_TAG`) are
ignored so a playing macro is never re-recorded.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from . import win_input

log = logging.getLogger(__name__)

user32 = win_input.user32
kernel32 = win_input.kernel32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
WM_QUIT = 0x0012
PM_REMOVE = 0x0001

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

_KEYDOWN = {WM_KEYDOWN, WM_SYSKEYDOWN}
_KEYUP = {WM_KEYUP, WM_SYSKEYUP}
_MOUSE_BUTTON_MSGS = {
    WM_LBUTTONDOWN: ("left", True), WM_LBUTTONUP: ("left", False),
    WM_RBUTTONDOWN: ("right", True), WM_RBUTTONUP: ("right", False),
    WM_MBUTTONDOWN: ("middle", True), WM_MBUTTONUP: ("middle", False),
}

ULONG_PTR = ctypes.c_size_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
)
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.MsgWaitForMultipleObjects.argtypes = (
    wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD, wintypes.DWORD,
)
user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

QS_ALLINPUT = 0x04FF


@dataclass(frozen=True)
class KeyEvent:
    vk: int
    scan: int
    pressed: bool
    extended: bool
    t: float  # perf_counter seconds


@dataclass(frozen=True)
class MouseEvent:
    kind: str  # move | down | up | wheel | hwheel
    x: int
    y: int
    button: str = ""
    delta: int = 0
    t: float = 0.0


KeyCallback = Callable[[KeyEvent], bool]
MouseCallback = Callable[[MouseEvent], bool]


def _side_specific(vk: int, scan: int, flags: int) -> int:
    if vk == 0x10:
        return 0xA1 if scan == 0x36 else 0xA0
    if vk == 0x11:
        return 0xA3 if flags & LLKHF_EXTENDED else 0xA2
    if vk == 0x12:
        return 0xA5 if flags & LLKHF_EXTENDED else 0xA4
    return vk


class HookSession:
    """Run LL hooks on a private thread and forward events to callbacks.

    Callbacks execute on the hook thread and must be fast. Returning ``True``
    from a callback suppresses the event system-wide.
    """

    def __init__(
        self,
        on_key: KeyCallback | None = None,
        on_mouse: MouseCallback | None = None,
        *,
        name: str = "ll-hooks",
    ) -> None:
        self._on_key = on_key
        self._on_mouse = on_mouse
        self._name = name
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._kb_proc = None
        self._ms_proc = None
        self._kb_handle = None
        self._ms_handle = None
        self._pressed: set[int] = set()

    # -- lifecycle ----------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, timeout: float = 2.0) -> None:
        if self.active:
            return
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise OSError("hook thread did not start")
        if self._error is not None:
            raise OSError(f"installing hooks failed: {self._error}")

    def stop(self, timeout: float = 2.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    # -- hook thread --------------------------------------------------------

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        try:
            if self._on_key is not None:
                self._kb_proc = HOOKPROC(self._keyboard_proc)
                self._kb_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, module, 0)
                if not self._kb_handle:
                    raise OSError(ctypes.get_last_error(), "SetWindowsHookEx(keyboard) failed")
            if self._on_mouse is not None:
                self._ms_proc = HOOKPROC(self._mouse_proc)
                self._ms_handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, module, 0)
                if not self._ms_handle:
                    raise OSError(ctypes.get_last_error(), "SetWindowsHookEx(mouse) failed")
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
            self._uninstall()
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while not self._stop.is_set():
                user32.MsgWaitForMultipleObjects(0, None, False, 50, QS_ALLINPUT)
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_QUIT:
                        self._stop.set()
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._uninstall()
            self._pressed.clear()

    def _uninstall(self) -> None:
        for attr in ("_kb_handle", "_ms_handle"):
            handle = getattr(self, attr)
            if handle:
                user32.UnhookWindowsHookEx(handle)
                setattr(self, attr, None)
        self._kb_proc = None
        self._ms_proc = None

    def _keyboard_proc(self, n_code, w_param, l_param):
        try:
            if n_code >= HC_ACTION and self._on_key is not None:
                data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                msg = int(w_param)
                injected = bool(data.flags & LLKHF_INJECTED) or data.dwExtraInfo == win_input.INJECT_TAG
                if not injected and (msg in _KEYDOWN or msg in _KEYUP):
                    vk = _side_specific(int(data.vkCode), int(data.scanCode), int(data.flags))
                    pressed = msg in _KEYDOWN
                    event = KeyEvent(
                        vk=vk,
                        scan=int(data.scanCode),
                        pressed=pressed,
                        extended=bool(data.flags & LLKHF_EXTENDED),
                        t=time.perf_counter(),
                    )
                    if self._on_key(event):
                        return 1
        except Exception:  # never let an exception escape a ctypes callback
            log.exception("keyboard hook callback failed")
        return user32.CallNextHookEx(self._kb_handle, n_code, w_param, l_param)

    def _mouse_proc(self, n_code, w_param, l_param):
        try:
            if n_code >= HC_ACTION and self._on_mouse is not None:
                data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                injected = bool(data.flags & LLMHF_INJECTED) or data.dwExtraInfo == win_input.INJECT_TAG
                if not injected:
                    event = self._mouse_event(int(w_param), data)
                    if event is not None and self._on_mouse(event):
                        return 1
        except Exception:
            log.exception("mouse hook callback failed")
        return user32.CallNextHookEx(self._ms_handle, n_code, w_param, l_param)

    @staticmethod
    def _mouse_event(msg: int, data: MSLLHOOKSTRUCT) -> MouseEvent | None:
        x, y = int(data.pt.x), int(data.pt.y)
        now = time.perf_counter()
        if msg == WM_MOUSEMOVE:
            return MouseEvent("move", x, y, t=now)
        if msg in _MOUSE_BUTTON_MSGS:
            button, down = _MOUSE_BUTTON_MSGS[msg]
            return MouseEvent("down" if down else "up", x, y, button=button, t=now)
        high = ctypes.c_short((int(data.mouseData) >> 16) & 0xFFFF).value
        if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            button = "x1" if high == 1 else "x2"
            return MouseEvent("down" if msg == WM_XBUTTONDOWN else "up", x, y, button=button, t=now)
        if msg == WM_MOUSEWHEEL:
            return MouseEvent("wheel", x, y, delta=high, t=now)
        if msg == WM_MOUSEHWHEEL:
            return MouseEvent("hwheel", x, y, delta=high, t=now)
        return None


class ChordCapture:
    """Capture one key chord (e.g. Ctrl+Shift+S) from the physical keyboard.

    Keys are suppressed while capturing so Win/Alt do not trigger the shell.
    The chord completes on the first key release after at least one press.
    """

    def __init__(self, on_done: Callable[[list[int]], None]) -> None:
        self._on_done = on_done
        self._order: list[int] = []
        self._held: set[int] = set()
        self._done = False
        self._session = HookSession(on_key=self._on_key, name="chord-capture")

    def start(self) -> None:
        self._order.clear()
        self._held.clear()
        self._done = False
        self._session.start()

    def cancel(self) -> None:
        self._done = True
        self._session.stop()

    @property
    def active(self) -> bool:
        return self._session.active and not self._done

    def _on_key(self, ev: KeyEvent) -> bool:
        if self._done:
            return False
        if ev.pressed:
            if ev.vk not in self._held:
                self._held.add(ev.vk)
                self._order.append(ev.vk)
            return True
        if ev.vk in self._held and self._order:
            self._done = True
            chord = list(self._order)
            # Stop from a helper thread; stop() joins the hook thread so it
            # must not be called from within the hook callback itself.
            threading.Thread(target=self._finish, args=(chord,), daemon=True).start()
        return True

    def _finish(self, chord: list[int]) -> None:
        self._session.stop()
        try:
            self._on_done(chord)
        except Exception:
            log.exception("chord capture callback failed")
