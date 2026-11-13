# Generation & planning sources (extra module `gen_planning`)

- **DESNZ REPD** (`desnz_repd`): CSV from gov.uk "Renewable Energy Planning Database: quarterly extract" (July 2026, 14,657 rows). OGL v3.
  The asset URL is hashed and changes each quarter: resolve via `https://www.gov.uk/api/content/government/publications/renewable-energy-planning-database-quarterly-extract` (`details.attachments`, CSV) and update `sources.d/generation.yaml`.
  X/Y are BNG; Northern Ireland rows (Irish Grid) are dropped. Layer omits refused/withdrawn/abandoned/expired/decommissioned/revised.
- **DNO connection registers** (`ecr_projects`): from `data/norm/*/queue_project.parquet` with coordinates and export/storage MW > 0.
- **NESO TEC register** (`tec`): no coordinates; `Connection Site` name-matched to GSP/BSP/primary sites and SSEN-T substations. About 37% of rows (by MW) match; unmatched are mostly NGET 400 kV substations, offshore platforms and wind-farm collector substations that no open dataset locates.
- **UKPN `ukpn-large-embedded-power-stations`** (`ukpn_large_embedded_power_stations`): fetched (31 rows) but has only a GSP name and gated-contract status, no coordinates; not mapped.
- **NGED `generation-capacity-register`**: already feeds NGED `queue_project`, so it is covered by `ecr_projects`.
