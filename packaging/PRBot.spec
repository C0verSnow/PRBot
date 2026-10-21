# Run only in remote macOS CI. The app embeds Python; it never uses user site-packages.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
analysis = Analysis(
    [str(root / 'packaging/entry.py')],
    pathex=[str(root / 'src')],
    binaries=[],
    datas=collect_data_files('certifi'),
    hiddenimports=collect_submodules('keyring.backends.macOS'),
    hookspath=[], runtime_hooks=[], excludes=['tkinter', 'pytest'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name='PRBot',
    debug=False, strip=False, upx=False, console=False,
    argv_emulation=False, target_arch='arm64',
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, name='PRBot', upx=False)
app = BUNDLE(
    collection, name='PRBot.app', bundle_identifier='org.prbot.app',
    version='0.1.0',
    info_plist={
        'CFBundleShortVersionString': '0.1.0',
        'LSMinimumSystemVersion': '14.0',
        'NSHighResolutionCapable': True,
        'LSUIElement': True,
    },
)
