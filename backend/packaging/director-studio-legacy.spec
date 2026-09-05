from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


repo_root = Path(SPECPATH).parents[1]
backend_root = repo_root / "backend"

analysis = Analysis(
    [str(backend_root / "packaging" / "entrypoint.py")],
    pathex=[str(backend_root)],
    binaries=[],
    datas=[
        (str(repo_root / "frontend" / "dist"), "frontend/dist"),
        (str(backend_root / "workflows"), "workflows"),
        (
            str(backend_root / "app" / "agents" / "director" / "DIRECTOR_SKILL.md"),
            "app/agents/director",
        ),
        (
            str(backend_root / "app" / "agents" / "director" / "guides"),
            "app/agents/director/guides",
        ),
    ],
    hiddenimports=collect_submodules("app.pipelines"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DirectorStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
