# PyInstaller spec for Aulari: one folder (--onedir), windowed, no UPX.
# Build from the project folder with the dev environment active:
#   pyinstaller --clean --noconfirm aulari.spec
# Output: dist/Aulari/Aulari.exe (build/ and dist/ are ignored by git).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# UI texts are data files inside the package.
datas = collect_data_files("aulari", includes=["strings/*.json"])
# The licenses travel with the executable (MIT for Aulari, others for dependencies).
datas += [("LICENSE", "."), ("THIRD_PARTY_LICENSES", ".")]
# keyring picks its backend at run time (Windows Credential Manager).
hiddenimports = collect_submodules("keyring.backends")

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
    version="packaging/version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Aulari", upx=False)
