# Canonical schema

Code: `src/gridmap/normalise/schema.py` (source of truth for columns/types). Helpers: `normalise/common.py`.

## Flow
raw snapshot (`data/raw/<source>/<ts>/`) → adapter `normalise/<op>.py::run()` → `data/norm/<OP>/<table>.parquet`
→ loader → DuckDB → match → tiles. Adapters never open DuckDB.

## Tables
- **asset**: one row per substation/busbar *as the operator publishes it* (GSP/BSP/PRIMARY; TRANSMISSION for SSEN-T/SPT).
  `asset_uid = "<OP>:<stable native id>"`. Prefer a native id (FLOC, mRID, NRN, substation number, AssetID);
  if none, use a normalised `name|kv_lv`. Same physical site at several voltages = several rows; the match phase clusters them.
- **capacity**: headroom etc. per asset_uid. **Null = not published. Never coerce to 0. Negative is real.**
  - `demand_headroom_mw` only if source is in MW; MVA sources go to `demand_headroom_mva`.
  - `basis`: short text, e.g. `"thermal, incl. contracted gen"`, `"NGED NCM: min(thermal, fault)"`. Take it from the
    operator methodology/description; if unknown write `"unknown"`.
  - `gen_rag_native`/`demand_rag_native`: operator's own RAG via `common.rag()`. We compute our own RAG later.
- **fault_level**: kA. make = peak, break = RMS. 3ph and 1ph separate. Ratings separate. Join by `asset_uid`
  when the dataset carries the same id, else leave null and fill `ltds_node` + `site_name` for later matching.
- **queue_project**: ECR rows (both parts) and queue datasets. Map status to connected|accepted|offered|scoping|other.
  Record connection point names at every level present (primary/BSP/GSP) for aggregation.
- **lv_site**: secondary substations (11/6.6 kV → LV). Utilisation/customers only if published.

## Conventions
- Coordinates via `common.add_coords/best_coords` only (validates GB bounds, fixes swaps, converts BNG, rejects x10 errors).
- Numbers via `common.num_expr/parse_num` (null tokens, commas, units).
- `as_of` via `common.as_of(meta)` unless the data carries its own date.
- Every adapter asserts non-trivial output (e.g. asset rows > 0, coord coverage reported) and returns `{table: rows}`.
