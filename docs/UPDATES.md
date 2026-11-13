# Updates

The app updates itself from GitHub. You do not need a new .app for most changes.

## What updates automatically

- The whole `gridmap` Python package: every `.py`, `.yaml` and `.json` under `src/gridmap/`.
- The built web UI (`web/dist`).
- The source registry (`sources.yaml`, `sources.d/*.yaml`) and the extra layer modules, because they live in the package.

## What needs a new .app

- The Python interpreter and third-party packages (`pyproject.toml`, `uv.lock`).
- The native tools (`tippecanoe`, `pmtiles`; the list is `packaging/native_tools.txt`).
- The pywebview shell and the tiny launcher `packaging/launch.py`.

When one of these changes, bump `SHELL_VERSION` (repo root) if the launcher or shell changed in a way old apps cannot handle.
Any change to `uv.lock` or `native_tools.txt` changes `deps_hash` on its own. Old apps then show
"A new version of the app is needed" and keep running the version they have. Build the new app with `make app`.

## How to release

Push to `main`. That is all.

1. GitHub Actions (`.github/workflows/release.yml`) builds the web UI and runs the tests.
2. It packs `update-bundle.zip`: `src/gridmap/**`, `web/dist/**` and `manifest.json` (`sha`, `built_at`, `min_shell_version`, `deps_hash`).
   A check fails the build if the zip holds `.env*` or `keys.env`.
3. It publishes the zip, its `.sha256` and `manifest.json` to release `update-<shortsha>`, and moves the rolling release `latest-update`.

Installed apps check `latest-update` at start and every 6 hours. Dev checkouts (a `.git` folder) never update unless `GRIDMAP_UPDATES=1`.

## What the app does

1. Reads the release `manifest.json`. If the sha is new, checks `min_shell_version` and `deps_hash` against the app. If they do not match, it sets "needs new app" and stops.
2. Downloads the zip and checks its sha256. A mismatch is thrown away.
3. Unzips to `<home>/payload/<sha>.tmp` and checks it in a separate process. The check imports the new package, builds the web app without the scheduler, loads the source registry and imports every extra layer. A failure throws the update away.
4. Renames the folder to `<home>/payload/<sha>` and marks it `pending` in `<home>/payload/current.json`.
5. The map shows "Update ready. Restart to apply." The Restart button calls `POST /api/updates/apply`, which restarts the app. Quitting and opening the app also works.

`GET /api/updates` shows `shell_version`, `active_sha`, `available_sha`, `state`, `last_check` and `last_error`.
`state` is `up_to_date`, `downloading`, `ready_to_restart`, `needs_new_app` or `error`.

## Rollback

- The launcher runs the `pending` payload once and marks it "trying".
- Once `/api/manifest` answers, the payload becomes `good`. This must happen within 90 seconds.
- If the app crashes, cannot import the new code or is not healthy in 90 seconds, the next launch marks that payload `bad`.
  It then runs the newest good payload, or the copy bundled in the .app.
- A payload marked `bad` is never downloaded again. The next push to `main` produces a new sha and is tried normally.
- The last 2 good payloads are kept. Older ones are deleted.

## For developers

- Test an update locally: build a bundle with `uv run python scripts/make_bundle.py --sha <40 hex> --out some/dir`.
  Serve that folder with `python -m http.server`, add a `latest-update` JSON file listing the three assets (`name`, `browser_download_url`) and set `GRIDMAP_UPDATE_URL` to it.
- Never hand-edit files in `<home>/payload`.
- Rule for payload code: it may only import packages that are in the app. Adding a dependency means a new .app.
