.PHONY: dev serve refresh test lint web app
dev:        ## run server (startup refresh + scheduler) and web dev server
	uv run uvicorn gridmap.server.app:app --host 127.0.0.1 --port 8787 --reload --reload-dir src --timeout-graceful-shutdown 2 & cd web && npm run dev
refresh:    ## fetch + normalise + tiles once
	uv run python -I -m gridmap.refresh
serve:      ## server only (no web dev server)
	uv run uvicorn gridmap.server.app:app --host 127.0.0.1 --port 8787
test:
	uv run pytest -q
lint:
	uv run ruff check src tests
app:        ## build dist/GB Grid.app + dist/GB-Grid-mac.zip (Apple Silicon, unsigned; bundles .env keys)
	packaging/build_app.sh
