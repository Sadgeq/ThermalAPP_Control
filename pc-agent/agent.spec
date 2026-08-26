# PyInstaller spec for the ThermalControl agent
# ==============================================
# Bundles Backend/agent.py + its Python deps + the LibreHardwareMonitor DLLs
# into a single agent.exe that Tauri can launch as a sidecar (see
# pc-agent/desktop/src-tauri/tauri.conf.json bundle.externalBin).
#
# Build:
#   cd pc-agent
#   .venv/Scripts/activate           # or activate however
#   pip install pyinstaller>=6.0
#   pyinstaller agent.spec --clean
#   # Output: pc-agent/dist/agent/agent.exe (onedir mode — faster cold start)
#
# Why onedir, not onefile:
#   * Faster cold start (no temp extraction on every launch).
#   * pythonnet + LibreHardwareMonitor have hairy .NET runtime resolution
#     that breaks more often when extracted to a temp directory.
#   * Tauri's `bundle.externalBin` accepts onedir folders just as easily.
#   * Trade-off: distribution is a folder, not a file. We pack it inside
#     the MSI so the user never sees the difference.
#
# Re-run after adding any new pip dependency. PyInstaller analyses the import
# graph at build time, so deps added without re-running silently won't ship.

# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

# This file lives at pc-agent/agent.spec; SPECPATH points to pc-agent/.
BACKEND = Path(SPECPATH) / "Backend"
LHM_LIB = BACKEND / "lib"

block_cipher = None

# All the Backend/*.py modules need to be reachable to the entry point.
# Adding the directory to pathex lets PyInstaller pick them up.
analysis = Analysis(
    [str(BACKEND / "agent.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[
        # Bundle the entire LHM directory so hardware._try_load_lhm() finds
        # LibreHardwareMonitorLib.dll and its sibling .dlls at runtime.
        # Layout inside the bundle: <appdir>/lib/LibreHardwareMonitorLib.dll, ...
        # This matches the path agent uses today: _BASE_DIR / "lib".
        (str(LHM_LIB), "lib"),
    ],
    hiddenimports=[
        # PyInstaller's static analyser misses these because they're loaded
        # dynamically via dotted strings or runtime metaclasses.
        "clr_loader",
        "pythonnet",
        "wmi",
        "win32com",
        "win32com.client",
        "pywintypes",
        "pythoncom",
        # supabase-py loads transports lazily.
        "httpx",
        "httpcore",
        "h11",
        # gotrue / postgrest / storage3 / realtime are loaded via supabase
        # facade, but PyInstaller doesn't always trace through.
        "gotrue",
        "postgrest",
        "storage3",
        "realtime",
        "realtime._async",
        "realtime._sync",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Drop modules we definitely don't need to keep the bundle small.
        "tkinter",
        "matplotlib",
        "PIL",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "pytest",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,           # onedir mode → exe references sibling files
    name="agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                        # UPX often breaks pythonnet/.NET loaders
    console=True,                     # console window so logs are visible if launched directly; Tauri spawns it hidden
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# COLLECT bundles the exe + its sidecar DLLs/data into pc-agent/dist/agent/.
# This whole folder is what Tauri's externalBin will reference.
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="agent",
)
