@echo off
REM Build dist\GamepadMacropad.exe (single-file, no console window).
setlocal
cd /d "%~dp0"

if exist .venv\Scripts\python.exe (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=py -3
)

%PY% -m pip install -r requirements.txt --quiet || goto :fail
%PY% -m PyInstaller gamepad_macropad.spec --noconfirm --clean || goto :fail

echo.
echo Built: %~dp0dist\GamepadMacropad.exe
exit /b 0

:fail
echo Build failed.
exit /b 1
