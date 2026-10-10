# Gamepad Macropad

Turn any gamepad into a mouse and a macro pad. A small always-on-top overlay
converts controller input for as long as it is open:

| Control | Does |
|---|---|
| **Left stick** | Moves the mouse cursor (dead zone, response curve and speed are tunable) |
| **Right stick** | Scroll wheel: up/down scrolls vertically, left/right scrolls horizontally |
| **Every other button** | A key / hotkey, a mouse button, a double click, typed text, a recorded macro, or an app function |

Works with anything SDL2 recognizes as a game controller (Xbox, PlayStation,
Switch Pro, 8BitDo, most generic USB/Bluetooth pads). Unrecognized joysticks
fall back to an Xbox-style button order. Windows only.

Inspired by [JoyType](inspiration/JoyType-main) (Joy-Con / IINE -> desktop input).

## Default bindings

| Button | Action | Button | Action |
|---|---|---|---|
| A | Left mouse button (hold to drag) | LB | Ctrl (held) |
| B | Right mouse button | RB | Shift (held) |
| X | Middle mouse button | LT | Esc |
| Y | Double click | RT | Enter |
| LS (stick click) | Left click | RS (stick click) | Middle mouse (wheel click) |
| D-pad | Arrow keys (auto-repeat) | Back / View | Switch mouse / keyboard mode |
| Start / Menu | Open settings | Guide | nothing |

**Keyboard mode** (dual always-on radials): Back / View toggles mouse ↔ keyboard.
Both sticks drive a 6-letter wheel at all times — push past **95%** deflection to
aim a sector, then release toward center to type it. Stick clicks swap each side’s
letter layer. Vowels are on the shoulders/triggers (LB=A, LT=E, RB=I, RT=O);
A=space, B=backspace, D-pad ↑=`.`, ←=`,`. Guide / Pause toggles number mode.
See **Settings → Keyboard**.

Change any of these in **Settings -> Buttons**.

## Run from source

Requires Python 3.10+ on Windows.

```bat
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

## Build the executable

```bat
build_exe.bat
```

produces `dist\GamepadMacropad.exe`, a single windowless executable. Double
click it to show the overlay; close the overlay (or right-click -> Quit) to
stop converting input.

## Using the overlay

- The dot shows state: green = converting, yellow = paused, grey = no
  controller, red = recording a macro / error.
- `⏸ / ▶` pause or resume conversion, `⚙` opens settings, `–` collapses to a
  tiny pill, `✕` quits. Drag the overlay anywhere; position, opacity and
  compact state are remembered.
- Right-click the overlay for the same commands.

## Recording macros

1. Open **Settings -> Macros** (or pick *Play macro -> Record new...* when
   editing a button) and press **Record**.
2. After a 3 second countdown, everything you type, click and scroll in any
   application is recorded. Optionally record mouse positions so clicks replay
   at the same screen spots.
3. Press **any controller button** or the **Stop** button to finish. Review the
   steps (delete some, add waits or typed text), set playback speed, and save.
4. Bind the macro to a button in **Settings -> Buttons**.

Macros always release any keys they pressed when they finish or are stopped.
A playing macro is never re-recorded, and recording ignores clicks on the
app's own windows.

## Key / hotkey bindings

Pick *Key / hotkey*, then either type the keys (`ctrl+shift+s`, `f5`,
`volumeup`, `win+d`) or click **Press keys...** and press the combination on
your keyboard. Keys are held for as long as the controller button is held, so
a bumper bound to `shift` works like a real modifier alongside the stick
cursor and face-button clicks. Enable auto-repeat for things like arrows.

## Configuration

Settings are saved automatically to
`%LOCALAPPDATA%\GamepadMacropad\config.json`. If a `config.json` exists next
to the executable it is used instead (portable mode). Logs go to
`%LOCALAPPDATA%\GamepadMacropad\gamepad_macropad.log`.

## Project layout

```
main.py                     entry point (also used by PyInstaller)
gamepad_macropad/
  app.py                    wiring: config + engine + Tk windows
  engine.py                 fixed-rate loop: sticks -> cursor/scroll, buttons -> actions
  gamepad.py                pygame-ce / SDL GameController reader, hotplug, fallback mapping
  macros.py                 macro recorder (LL hooks) and player
  win_input.py              SendInput: keys, mouse, vertical + horizontal wheel, text
  win_hooks.py              WH_KEYBOARD_LL / WH_MOUSE_LL session, hotkey chord capture
  keys.py                   key token <-> virtual-key tables
  config.py                 dataclasses, defaults, JSON persistence
  ui/                       overlay, settings, binding + macro dialogs (tkinter)
tests/                      unit tests for the pure logic
gamepad_macropad.spec       PyInstaller spec
```

## Tests

```bat
.venv\Scripts\python -m pytest tests
```
