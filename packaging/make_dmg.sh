#!/bin/sh
# Package dist/Aulari.app into a compressed, read-only disk image with a
# shortcut to the Applications folder, for the macOS release.
# Run from the project folder after 'pyinstaller --clean --noconfirm aulari.spec':
#   packaging/make_dmg.sh Aulari-vX.Y.Z-macos-arm64.dmg
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: packaging/make_dmg.sh <output.dmg>" >&2
    exit 2
fi
app="dist/Aulari.app"
output="$1"

# PyInstaller signs the app ad hoc; a broken signature makes macOS report it as damaged.
codesign --verify --deep --strict "$app"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
ditto "$app" "$staging/Aulari.app"
ln -s /Applications "$staging/Applications"
hdiutil create -volname "Aulari" -srcfolder "$staging" -fs HFS+ -format UDZO -ov "$output"
