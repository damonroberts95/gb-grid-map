# GB Grid

A live map of the GB and Irish electricity networks, built from public data. It runs on your Mac.

## Install

Mac with Apple Silicon only (M1 or later). Check: Apple menu, About This Mac, "Chip".

### Easiest: one Terminal command

Open Terminal and paste:

```sh
curl -fL -o /tmp/gbgrid.zip https://github.com/damonroberts95/gb-grid-map/releases/download/app-latest/GB-Grid-mac.zip && rm -rf /tmp/gbgrid "/Applications/GB Grid.app" && ditto -x -k /tmp/gbgrid.zip /tmp/gbgrid && mv "/tmp/gbgrid/GB Grid.app" /Applications/ && open "/Applications/GB Grid.app"
```

This downloads the app, puts it in Applications and opens it. macOS does not block apps fetched this way. Run the same command again to reinstall. Your data is kept.

### Or: download the zip

1. Download [GB-Grid-mac.zip](https://github.com/damonroberts95/gb-grid-map/releases/download/app-latest/GB-Grid-mac.zip). Unzip it.
2. Drag "GB Grid.app" to Applications. Double-click it.
3. macOS says it "could not verify" the app. The app is not signed by Apple. Click Done.
4. Open System Settings, Privacy & Security. Scroll down to Security. Click Open Anyway next to "GB Grid was blocked". Enter your password, then click Open.

Right-click, Open no longer works on macOS 15 and later.

Says "damaged", or no Open Anyway button? Paste this in Terminal, then open the app again:

```sh
xattr -dr com.apple.quarantine "/Applications/GB Grid.app"
```

## First run

It downloads the map and data. This takes 10 to 20 minutes and needs internet. Later runs open straight to the map. Updates install themselves.

Add free API keys on the Keys tab for more data. See [docs/KEYS.md](docs/KEYS.md).

## Problems

- Data and logs: `~/Library/Application Support/GB Grid`. Send `logs/app.log` with any report.
- Start over: quit, then delete that folder.
- Uninstall: delete the app and that folder.

## Developers

See [CLAUDE.md](CLAUDE.md), [docs/UPDATES.md](docs/UPDATES.md) and [docs/BUILD_LOG.md](docs/BUILD_LOG.md). Build the app with `make app`. Use `NO_KEYS=1 bash packaging/build_app.sh` for a build without keys.
