@echo off
REM Build the standalone agent.exe for Tauri sidecar use.
REM Requires: PyInstaller installed (see requirements-dev.txt).
REM Output: pc-agent\dist\agent\agent.exe (+ sibling files)
REM
REM After building, the desktop project's tauri.conf.json `bundle.externalBin`
REM points at the dist folder so `tauri build --bundles msi` packs it into
REM the installer.

setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo .venv not found. Create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements-dev.txt
    exit /b 1
)

echo === Cleaning previous build ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === Running PyInstaller ===
.venv\Scripts\pyinstaller.exe agent.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo PyInstaller failed. Common causes:
    echo   * Missing pythonnet — pip install pythonnet
    echo   * .NET runtime not installed — install .NET 6+ runtime
    echo   * pyinstaller too old — pip install -U pyinstaller
    exit /b 1
)

echo.
echo === Smoke test ===
echo Spawning the bundled agent in --print-local-api-token mode (should print a token and exit cleanly).
dist\agent\agent.exe --print-local-api-token
if errorlevel 1 (
    echo.
    echo Bundled agent failed to start. Inspect output above.
    exit /b 1
)

echo.
echo === Done ===
echo Bundle: %CD%\dist\agent\
echo Run agent: dist\agent\agent.exe
echo Bundle size:
dir dist\agent /s /-c | findstr "File(s)"
