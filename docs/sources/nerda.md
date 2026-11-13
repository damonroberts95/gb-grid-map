# SSEN NeRDA (Network Rich Data Access), verified 2026-11-12

Live LV/HV substation monitoring for SSEN's southern network (SEPD): 4,628 sites, 3,816 secondary substations with
LV feeder current/voltage/power at ~3-10 min cadence. Layer `nerda_sites` (Live group, off by default).

## Terms
NeRDA's terms and conditions allow **internal use**; check them before sharing the map or any derived data publicly.
Credentials: `NERDA_USERNAME` and `NERDA_API_KEY` in `.env` (never commit, log or print).

## API
Base `https://nerda-prod-apis-v2.azurewebsites.net/api/`. Auth is a **GET with a JSON body**
`{"username": ..., "apiKey": ...}` and `Content-Type: application/json` (no auth header; the generic fetcher can't do it).

| Endpoint | Notes |
|---|---|
| `ApiNerdaStatic` | no params: all substations (133 MB, ~60 s). `?substation=<uuid>` for one. Lines, measurement ids, units. |
| `ApiNerdaAfter?measurement=<id>&after=<yyyy-MM-ddTHH:mm:ss.SSSZ>` | time series. Reply: `{aliasName, AnalogValues[{aliasName, value_history[{__ts, value}]}], measurementType, ...}`. |
| `ApiNerdaBetween` | same plus `before`. |
| `POST GenerateApiKey` | same body; rotates the key. |

- One request returns **many channels** (`i1,i2,i3,in`, `_max/_min`, `v1_min`, `p1`, `pf1`, `f1`, `i1_thd`, `*_ang`), each
  updating about every 10 min, so some are empty in a short window. A measurement id can sit under two types and lines are
  listed twice in the static file: dedupe by (id, type) and line name.
- No bulk time-series endpoint: one request per measurement id, hence the sweep below.
- NRN `6100_007_120_02` = primary 6100, HV feeder 007, secondary 120, LV feeder 02. Site-level 4-digit NRN = primary (names
  with GRID/NGTE = BSP, 9 sites). The first 10 digits are the SSEN smart-meter `dataset_id` prefix.

## What the code does
- `extra/nerda_client.py`: httpx client, retries, shared rate limiter (`NERDA_RPS`, default 2 req/s), static fetch, compact
  index (`data/derived/nerda_index.json`, rebuilt per snapshot), parser, key rotation.
- **Static snapshot** `data/raw/ssen_nerda_static/<ts>/static.json` + `meta.json`, 2 kept, registered as `ssen_nerda_static`
  in `sources.d/nerda.yaml` (`large: true`, so `refresh` never fetches it). Refresh trigger: the server's NeRDA sweep thread
  fetches it when missing or older than 7 days; manually `uv run python -I -m gridmap.extra.nerda_client --fetch-static`.
- **Sweep** (`server/nerda.py`, daemon thread started by `nerda.install(app, st)` in `create_app`): cycles LV-monitored
  secondaries, one `LineCurrent` request per LV feeder (~12,800 feeders, 2 req/s: a cycle takes ~1h45m). Writes
  `data/derived/nerda_latest.parquet` (nrn, i_max A, t, nf, fetched) every 10 min and at the end of a cycle, then an
  `only_live` build (waits while a refresh is running). Cycle time is logged (`nerda sweep: cycle done in ...`). Values older than
  3 h are not drawn. Not stored: history.
- **On demand** `GET /api/nerda/{nrn_or_line_uuid}`: per line, the latest value per family (current, voltage, kW, kVAr, kVA,
  PF, THD, frequency) from the last 15 min; cached 3 min; an on-demand call pauses the sweep. A secondary takes ~30 s
  (one request per measurement id). The map popup has a "Live readings" button (`web/src/extras.ts`).
- i_max is the largest latest phase-current channel (i1/i2/i3 and their `_max`) across LV feeders; neutral excluded.
  Feeders have no rated limit in the static data (`limit` = 0), so colour is absolute amps, not % loading.

## Join to SSEN LV sites
SSEN's open `lv_site` has no NRN, so the join is spatial: 85.8% of NeRDA secondaries (3,274 of 3,816) have an SEPD
`lv_site` within 40 m. Smart-meter feeders join on NRN digits (first 10) = `dataset_id` prefix.

## API key (90-day life)
The API doesn't report key age, so `data/derived/nerda_key.json` stores the first time the current key (by fingerprint)
was seen. The sweep warns in the log from day 80. Rotate (not automatic):
`uv run python -I -m gridmap.extra.nerda_client --rotate` (POST GenerateApiKey, rewrites `NERDA_API_KEY` in `.env`
atomically, keeping other lines; the old key stops working). The reply shape is unverified: if no key can be extracted
the `.env` is left alone, so check the log before the old key expires (the first rotation is a risk: do it with the
portal open).
