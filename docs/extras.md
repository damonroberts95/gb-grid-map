# Adding a public dataset as an extra layer

1. Register sources in `src/gridmap/sources.d/<module>.yaml` (same schema as `src/gridmap/sources.yaml`; one file per module).
2. Fetch: `uv run python -I -m gridmap.fetch --only id1,id2` (raw snapshot → `data/raw/<id>/<ts>/`).
3. Write `src/gridmap/extra/<module>.py` with `SOURCES`, `LAYERS: list[LayerMeta]`, `build(ctx) -> {layer_id: path}`.
   Reference: `src/gridmap/extra/gsp_regions.py`. Contract + styling hints: `src/gridmap/extra/__init__.py`.
4. Check: `uv run python -I -m gridmap.extra._try <module>` (scratch build, size/feature report; never touches the manifest).
5. Tests: `tests/test_extra_<module>.py` with tiny real fixtures in `tests/fixtures/extra/<module>/`.
The server rebuilds changed extras automatically; the web app renders them from `LayerMeta` (no frontend code).
