"""Entry point used by PyInstaller and ``python main.py``."""

import sys

from gamepad_macropad.app import main

if __name__ == "__main__":
    sys.exit(main())
