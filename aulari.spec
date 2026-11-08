# PyInstaller spec for Aulari: one folder (--onedir), windowed, no UPX.
# Build from the project folder with the dev environment active:
#   pyinstaller --clean --noconfirm aulari.spec
# Output on Windows: dist/Aulari/Aulari.exe.
# Output on macOS: dist/Aulari.app (Apple Silicon, ad-hoc signed, not notarized).
# build/ and dist/ are ignored by git.
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

from aulari import __version__

IS_MACOS = sys.platform == "darwin"
# Reverse-DNS id of the macOS app. macOS ties the app's Keychain access and
# settings to it, so it should never change once released.
BUNDLE_IDENTIFIER = "io.github.alar89.aulari"

# UI texts and the window icon are data files inside the package.
datas = collect_data_files("aulari", includes=["strings/*.json", "assets/*.ico"])
# The licenses travel with the executable (MIT for Aulari, others for dependencies).
datas += [("LICENSE", "."), ("THIRD_PARTY_LICENSES", ".")]
# keyring picks its backend at run time (Windows Credential Manager, macOS Keychain).
hiddenimports = collect_submodules("keyring.backends")

if IS_MACOS:
    # The version resource and the .ico are Windows only; BUNDLE gets the .icns.
    platform_options = {}
else:
    platform_options = {
        "version": "packaging/version_info.txt",
        # Built from packaging/aulari-logo-1024.png by packaging/make_icon.py.
        "icon": "src/aulari/assets/aulari.ico",
    }

a = Analysis(
    ["src/aulari/__main__.py"],
    pathex=["src"],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Aulari",
    console=False,
    upx=False,
    **platform_options,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Aulari", upx=False)

if IS_MACOS:
    app = BUNDLE(
        coll,
        name="Aulari.app",
        # Built from packaging/aulari-logo-1024.png by packaging/make_icon.py.
        icon="packaging/aulari.icns",
        bundle_identifier=BUNDLE_IDENTIFIER,
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "Aulari",
            "CFBundleVersion": __version__,
            "NSHumanReadableCopyright": "Copyright (c) 2026 ALar89. MIT License.",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.education",
        },
    )
