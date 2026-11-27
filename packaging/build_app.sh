#!/usr/bin/env bash
# Build "GB Grid.app" (Apple Silicon, unsigned) and dist/GB-Grid-mac.zip.   Run from anywhere:  make app
# Needs: uv, node/npm, tippecanoe + pmtiles in /opt/homebrew/bin (or $TOOLS_DIR), and the repo .env (API keys).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
TOOLS_DIR=${TOOLS_DIR:-/opt/homebrew/bin}

[ "$(uname -m)" = arm64 ] || { echo "build on an Apple Silicon Mac"; exit 1; }
# NO_KEYS=1: public build with no API keys (users add their own on the Keys page)
if [ "${NO_KEYS:-0}" != 1 ]; then
  [ -f .env ] || { echo ".env missing: the API keys are bundled from it (or set NO_KEYS=1)"; exit 1; }
fi

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
rm -f packaging/keys.env   # never let a stale keys file slip into a build
if [ "${NO_KEYS:-0}" != 1 ]; then
  cp .env packaging/keys.env; chmod 600 packaging/keys.env   # gitignored (keys.env); never print it
fi

echo "== build info (shell version, deps hash, commit): the updater compares these with each update"
printf '{"deps_hash": "%s", "sha": "%s", "commit_time": "%s"}\n' "$(uv run python scripts/make_bundle.py --deps-hash)" "$(git rev-parse HEAD)" "$(git show -s --format=%cI HEAD)" > packaging/build_info.json

echo "== pyinstaller"
rm -rf build dist/"GB Grid" dist/"GB Grid.app"
uv run pyinstaller packaging/gbgrid.spec --noconfirm --distpath dist --workpath build

echo "== zip"
cat > dist/README-FIRST.txt <<'TXT'
GB Grid (Mac with Apple Silicon: M1, M2, M3, M4 or later)
==========================================================

Check your Mac: Apple menu, About This Mac. "Chip" must say Apple M-something.
Intel Macs are not supported.

The app is not signed by Apple, so macOS blocks it the first time.
Right-click, Open no longer works on macOS 15 and later. Do this instead.

Install
-------
1. Unzip. Drag "GB Grid.app" to Applications.
2. Double-click it. macOS says it "could not verify" the app. Click Done.
3. Open System Settings, Privacy & Security. Scroll down to Security.
   Next to "GB Grid was blocked", click Open Anyway. Enter your password.
4. Click Open in the last prompt. You only do this once.

Says "damaged", or no Open Anyway button? Open Terminal and paste:
    xattr -dr com.apple.quarantine "/Applications/GB Grid.app"
Then double-click the app again.

First run
---------
It downloads the map and data. This takes 10-20 minutes and needs internet.
Later runs open straight to the map. Updates install themselves.

Notes
-----
* API keys: add free keys on the Keys tab for more data.
  (Private builds may already include keys. Do not share those publicly.)
* Data folder: ~/Library/Application Support/GB Grid
  (data, map, logs/app.log, keys in ".env").
* Problems: send logs/app.log from that folder.
* Start over: quit, then delete that folder.
  To keep the 1.5 GB map, delete "data" except "data/basemap".
* Uninstall: delete the app and that folder.
TXT
rm -f dist/GB-Grid-mac.zip
(cd dist && ditto -c -k --sequesterRsrc --keepParent "GB Grid.app" GB-Grid-mac.zip && zip -q -j GB-Grid-mac.zip README-FIRST.txt)
du -sh "dist/GB Grid.app" dist/GB-Grid-mac.zip
