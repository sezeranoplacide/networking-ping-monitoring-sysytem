# PyInstaller build for the desktop application.
#
#   pip install pyinstaller
#   pyinstaller --noconfirm networkmonitor.spec
#
# Produces dist/NetworkMonitor/NetworkMonitor.exe. The templates and static assets
# are bundled, so the fonts, icons and Chart.js keep working with no network — the
# same reason they are self-hosted in the first place.

block_cipher = None

a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'ping_monitor.console',
        'ping_monitor.diagnostics',
        'ping_monitor.device_manager',
        'ping_monitor.ping_service',
        'webview.platforms.edgechromium',
    ],
    hookspath=[],
    runtime_hooks=[],
    # The database lives beside the executable at runtime, never inside the bundle.
    excludes=['tkinter', 'pytest'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NetworkMonitor',
    debug=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window behind the app
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='NetworkMonitor',
)
