# griddata.uk vs this project: stats and queue

Theirs: captured 2026-11-12 (stats generated 11 Nov 2026 11:47). Ours: live `/api/stats` and `/api/queue` the same day.

## Headline

| Metric | griddata.uk | Ours | Likely reason |
|---|---|---|---|
| Sites | 138,356 | 5,367 HV (+556,934 LV substations) | They cluster LV/secondary substations into "sites" (their "Include LV" toggle). Our `site` table is GSP/BSP/Primary only. |
| Busbars | 159,694 | 8,642 asset rows | Their "busbar" appears to include LV records: their UKPN 74,823 is about our 2,471 HV assets + 72,750 LV. Our asset = one published HV substation/voltage record. |
| Busbars with coords | 304,952 DNO + 1,843 heatmap | 8,632 assets (+~474,500 LV) | Their coordinate count exceeds their own busbar count, so it uses a different, LV-inclusive base. We have no heatmap-matched coords: all asset coords are native WGS84, 10 missing. |
| Coordinates % | 98.4% | 99.9% HV; 85.4% incl. LV | Excl. LV we are slightly ahead. SPEN LV has no coordinates in our data (0 of 82,427). |
| Generation headroom | 23.3% (+0.6% calculated = 23.9%) | 70.3% | Denominator: they include LV rows with no headroom. We do not calculate headroom. |
| Demand headroom | 46.3% | 72.4% | Same; their figure is lifted by LV-level ENW dist-tx and NPG utilisation rows. |
| Fault levels | 6.3% | 91.6% | Same, plus we link LTDS/heatmap fault rows to the matched site (generous vs a per-busbar join). |
| Switchgear ratings | 5.3% | 89.8% | Same (asset counts if its site has a rating). |
| Constraint data | 3.6% | 67.0% | Same (any non-null `upstream_constraint`). |
| RAG status | 2.5% | 69.6% | Same (operator-native gen or demand RAG present). |
| LV coverage | not reported | 556,934 LV; coords 85.2%; utilisation 19.0% | Only UKPN (100%) and ENW (94.5%) publish LV utilisation; NGED/SSEN/NPG/SPEN 0%. |

The percentages are not comparable until the denominator is aligned: theirs is over LV-inclusive busbars, ours over HV assets. I could not reproduce
their figures without their LV-inclusive base.

## Per DNO (theirs / ours; ours per asset row, excl. LV)

| DNO | Busbars theirs / our HV / our LV | Coords | Gen headroom | Demand headroom | Fault |
|---|---|---|---|---|---|
| UKPN | 74,823 / 2,471 / 72,750 | 99.4 / 100.0 | 1.3 / 39.1 | 2.0 / 39.1 | 2.6 / 97.8 |
| ENW | 36,063 / 467 / 34,736 | 100.0 / 98.3 | 92.0 / 94.2 | 92.0 / 93.4 | 0.0 / 0.4 |
| NPG | 30,807 / 670 / 57,248 | 98.8 / 100.0 | 2.1 / 100.0 | 89.7 / 99.7 | 3.2 / 100.0 |
| NGED | 11,950 / 1,463 / 195,255 | 92.1 / 100.0 | 12.0 / 80.8 | 81.8 / 80.8 | 28.3 / 94.7 |
| SSEN | 3,291 / 1,978 / 114,518 | 90.8 / 100.0 | 27.0 / 90.1 | 27.0 / 90.1 | 63.0 / 99.2 |
| SPEN | 2,144 / 1,220 / 82,427 | 79.2 / 99.8 | 47.9 / 84.9 | 48.7 / 100.0 | 72.2 / 95.8 |

- UKPN and ENW: their counts match our LV tables plus HV, so their busbars are mostly LV. For NGED, SSEN, SPEN, NPG they carry far fewer than our LV lists
  (NGED 11,950 vs 195,255), so there we hold more LV.
- ENW headroom agrees closely (92.0 vs 94.2 gen). Both show ~0% fault levels (ENW publishes no LTDS; see AI_GOTCHAS).
- NPG gen headroom 2.1% (diluted by ~30k LV utilisation rows) vs our 100% on 670 heatmap assets: same data, different base.
- Fault levels: SSEN and SPEN are closest (63.0 vs 99.2, 72.2 vs 95.8); ours is higher because LTDS node-keyed rows are linked to sites by name.
- SPEN HV coordinates: theirs 79.2%, ours 99.8%.

## Transmission

| Operator | Theirs (busbars / coords / gen / fault) | Ours |
|---|---|---|
| SSEN Transmission | 225 / 100% / 0% / 52.4% | Not in `asset`; only the extras layer (ssent_sites_*), no busbar or fault stats |
| National Grid ET | 207 / 97.6% / 0% / 0% | Not held per operator |
| SP Transmission | 185 / 100% / 0% / 0% | Not held per operator |
| NESO GSP records | n/a | 373 rows / 106 sites, 100% coords, 0% headroom, 82.3% fault |

They are ahead here: a per-TO breakdown with busbar counts and SSEN-T fault levels. We hold only NESO GSP interface records.

## Sources and freshness

- They list about 15 named sources per headroom metric and flag some as calculated (`calculated:ukpn-ltds-t3b`; the +0.6% gen headroom is firm
  capacity minus connected gen, an upper bound). We list `capacity.source_id` counts (10 generation, 10 demand, all operator-native; top `nged_ltds_heatmap` 1,182, `ukpn_heatmap` 967).
  We do not calculate headroom. Their biggest sources (ENW dist-tx 32,784; NPG site utilisation 26,958) are LV-level; ours sit in `lv_site` as utilisation.
- Freshness: every operator on their page shows "Last Updated 2 Nov 2026", nine days before their own generation time (looks like one batch date).
  Ours is per-source fetch time from `source_run` (all DNOs fetched 2026-11-12) plus operator `as_of` where published (UKPN 2026-05-29: the underlying data is older than the fetch).

## Queue

| Metric | griddata.uk | Ours (default, with TEC) | Ours, DNO ECR only |
|---|---|---|---|
| Queued, top 100 sites | 80.14 GW | 110.67 GW | 69.28 GW |
| Connected, top 100 sites | 25.90 GW | 26.05 GW | 9.68 GW |
| Projects, top 100 sites | 1,435 | 3,304 | 3,113 |
| Queued, all sites | not shown | 147.1 GW (2,019 sites) | 97.9 GW (3,677 sites) |
| #1 site | Norwich (UKPN BSP) 3.87 GW | NORWICH MAIN 132kV (UKPN GSP) 5.31 GW | Ratcliffe On Soar (NGED GSP) 2.68 GW |

Their totals appear to be over the top-100 table (1,435 projects across 100 sites); the page does not state this, I inferred it.

Likely reasons:
- Scope of "queued": theirs is "accepted/scoping". Ours defaults to DNO accepted+offered plus TEC direct-connection projects under construction, consented or awaiting consent.
  Scoping (DNO `other` ~58 GW; TEC Scoping ~490 GW direct) is excluded unless `?scoping=true`, which gives 269.9 GW for the top 100, so they cannot be including TEC scoping in full.
- TEC: matched by name to sites, direct connections only (embedded TEC rows are already in DNO ECRs). Matching is crude: Eggborough lands on an NPG primary, and Norwich Main's 5.3 GW is mostly TEC.
  Their top sites carry different names and kV, so their matching resolves to different substations.
- Roll-up level: we roll ECR projects up to the matched GSP/BSP/Primary; they appear to rank at connection-point level (including "Customer or generator site" rows), which spreads MW differently.
- Connected: our 26.05 GW matches theirs (25.90) by coincidence; the ECR-only figure is 9.68 GW and the rest is TEC "Built" (e.g. Keadby 1.99 GW).
- Project counts: ours count every project at a site (accepted, connected, scoping, TEC); theirs seem to count only queued ones, and may exclude small (<1 MW) ECR rows.
- TEC register on our side is dated 10 Nov 2026.

## Where they are ahead
- Transmission-owner coverage (busbar, coordinate and fault stats for SSEN-T, NGET, SPT).
- Queue ranking at connection-point granularity, and a calculated headroom estimate (+0.6%).
- A whole-network framing with LV in the denominator, which is less flattering but arguably more honest about overall coverage.

## Where we are ahead
- HV fault level and switchgear coverage; named operator-native headroom sources; LV coverage reported (556,934 substations); per-source and per-operator freshness with fetch time and as-of.
