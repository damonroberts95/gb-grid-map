#!/usr/bin/env bash
# Build "GB Grid.app" (Apple Silicon, unsigned) and dist/GB-Grid-mac.zip.   Run from anywhere:  make app
# Needs: uv, node/npm, tippecanoe + pmtiles in /opt/homebrew/bin (or $TOOLS_DIR), and the repo .env (API keys).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
TOOLS_DIR=${TOOLS_DIR:-/opt/homebrew/bin}

[ "$(uname -m)" = arm64 ] || { echo "build on an Apple Silicon Mac"; exit 1; }
[ -f .env ] || { echo ".env missing: the API keys are bundled from it"; exit 1; }

echo "== web build"
(cd web && npm run build)

echo "== stage native tools + keys"
mkdir -p packaging/bin
for t in tippecanoe pmtiles; do
  cp -L "$TOOLS_DIR/$t" "packaging/bin/$t"; chmod 755 "packaging/bin/$t"
  # must only need system libraries, or the app breaks on a Mac without Homebrew
  if otool -L "packaging/bin/$t" | tail -n +2 | awk '{print $1}' | grep -vE '^(/usr/lib/|/System/Library/)'; then
    echo "$t links to non-system libraries (listed above)"; exit 1
  fi
done
cp .env packaging/keys.env; chmod 600 packaging/keys.env   # gitignored (keys.env); never print it

echo "== pyinstaller"
rm -rf build dist/"GB Grid" dist/"GB Grid.app"
uv run pyinstaller packaging/gbgrid.spec --noconfirm --distpath dist --workpath build

echo "== zip"
cat > dist/README-FIRST.txt <<'TXT'
GB Grid  (Apple Silicon Macs only)
==================================

1. Unzip, then drag "GB Grid.app" into your Applications folder.

2. FIRST OPEN: this app is not signed by Apple, so double-clicking is blocked once.
   Right-click (or Control-click) "GB Grid.app" -> Open -> Open.
   If macOS still refuses (newer macOS: System Settings -> Privacy & Security -> "Open Anyway"), or says the
   app is damaged, run this once in Terminal and try again:
       xattr -dr com.apple.quarantine "/Applications/GB Grid.app"

3. The first run downloads the map and the latest grid data and builds everything on your Mac. That takes
   about 10-20 minutes and needs internet. A progress screen shows what is happening; you can leave it open.
   Later runs open straight to the map and refresh the data in the background.

Good to know
------------
* The API keys for the network operators' open-data portals are inside the app, so please do not share the
  app publicly.
* Your data lives in:  ~/Library/Application Support/GB Grid
  (downloaded data, the map, logs in "logs/app.log", and your copy of the keys in ".env").
* To reset and start again from scratch: quit the app, then delete that folder
  (Finder -> Go -> Go to Folder... -> paste the path above -> drag the folder to the Bin).
  To keep the 1.5 GB map and only refetch the grid data, delete just its "data" folder except "data/basemap".
* To uninstall: delete GB Grid.app and the folder above.
TXT
rm -f dist/GB-Grid-mac.zip
(cd dist && ditto -c -k --sequesterRsrc --keepParent "GB Grid.app" GB-Grid-mac.zip && zip -q -j GB-Grid-mac.zip README-FIRST.txt)
du -sh "dist/GB Grid.app" dist/GB-Grid-mac.zip
