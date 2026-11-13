import polars as pl

from gridmap.match import (
    NameIndex,
    base_name,
    cluster_sites,
    name_norm,
    relocate_namesakes,
)


def test_name_norm_strips_noise():
    assert name_norm("Rufford 33 11Kv S Stn") == "rufford"
    assert name_norm("Iron Acton  S.G.P.") == "iron acton"
    assert name_norm("CROYDON GRID 132/33/11 KV") == "croydon"
    assert name_norm("Basingstoke (T1B & T2B) BSP") == "basingstoke"
    assert name_norm("SALE PRY (100136)") == "sale"
    assert name_norm("Guildford 'A' 11kV") == "guildford a"
    assert base_name("finsbury market b") == "finsbury market"
    assert name_norm(None) == ""


def _asset(uid, name, kind, lat, lon, op="UKPN", src="ukpn_grid_primary_sites", bsp=None, gsp=None):
    return {"asset_uid": uid, "operator": op, "licence_area": "EPN", "name": name, "display_name": name,
            "kind": kind, "kv_hv": 33.0, "kv_lv": 11.0, "lat": lat, "lon": lon, "coord_quality": "native_wgs84",
            "source_id": src, "upstream_bsp": bsp, "upstream_gsp": gsp}


def test_cluster_merges_colocated_and_splits_namesakes():
    a = pl.DataFrame([
        _asset("U:1", "BURY GRID 132/33kV", "BSP", 52.240, 0.723),
        _asset("U:2", "BURY GRID 33/11kV", "PRIMARY", 52.2401, 0.7221),
        _asset("U:3", "Bury Grid 33kV", "BSP", 51.633, -0.073),        # Edmonton namesake, 70+ km away
        _asset("U:4", "Earlham West Primary 33kV", "PRIMARY", 52.6301, 1.2501),
        _asset("U:5", "Earlham Grid 33kV", "BSP", 52.6300, 1.2500),   # same plot, similar name
    ])
    site, links = cluster_sites(a)
    sid = dict(links.select("asset_uid", "site_id").iter_rows())
    assert sid["U:1"] == sid["U:2"] != sid["U:3"]
    assert sid["U:4"] == sid["U:5"]
    assert site.height == 3
    assert site["site_id"].n_unique() == 3


def test_relocate_namesake_uses_upstream_feed():
    a = pl.DataFrame([
        _asset("U:1", "BURY GRID 132/33kV", "BSP", 52.240, 0.723),
        _asset("U:9", "BRAMFORD GRID 132kV", "GSP", 52.070, 1.063),
        _asset("U:3", "Bury Grid 33kV", "BSP", 51.633, -0.073, src="ukpn_heatmap", gsp="Bramford"),
    ])
    out = relocate_namesakes(a).filter(pl.col("asset_uid") == "U:3").row(0, named=True)
    assert abs(out["lat"] - 52.240) < 1e-6 and out["coord_quality"] == "relocated_namesake"


def test_name_index_prefers_kind_and_falls_back_to_neso():
    sites = pl.DataFrame([
        {"site_id": "U:bury", "operator": "UKPN", "name": "Bury Grid", "kind": "BSP", "lat": 52.24, "lon": 0.72},
        {"site_id": "NESO:bramford", "operator": "NESO", "name": "Bramford", "kind": "GSP", "lat": 52.07, "lon": 1.06},
    ])
    idx = NameIndex(sites)
    assert idx.resolve("UKPN", "Bury Grid 33", ("BSP",)) == "U:bury"
    assert idx.resolve("UKPN", "Bramford S.G.P.", ("GSP",)) == "NESO:bramford"
    assert idx.resolve("UKPN", "Nowhere", ("GSP",)) is None


# ---------- GSP merge, loose linking, ENW names, fault tiers ----------

from gridmap.match import (
    _enw_names,
    link_faults,
    loose_variants,
    merge_neso_gsps,
)


def _site(sid, op, name, kind, lat, lon, **kw):
    return {"site_id": sid, "operator": op, "licence_area": None, "name": name, "kind": kind, "kinds": kind,
            "kv_max": None, "kv_min": None, "kv_list": "", "lat": lat, "lon": lon, "n_assets": 1,
            "upstream_gsp": None, "upstream_bsp": None, **kw}


def test_merge_neso_gsp_into_dno_bsp_and_keep_alias():
    site = pl.DataFrame([
        _site("NESO:bramford", "NESO", "Bramford", "GSP", 52.070, 1.063),
        _site("UKPN:bramford", "UKPN", "BRAMFORD GRID 132kV", "BSP", 52.0703, 1.0628),
        _site("NESO:clacton bramford", "NESO", "Clacton (Bramford)", "GSP", 51.826, 1.179),
        _site("UKPN:clacton", "UKPN", "Clacton Grid 33kV", "BSP", 51.8261, 1.1791),
        _site("NESO:lonely", "NESO", "Lonely Moor", "GSP", 58.0, -4.0),
        _site("UKPN:bury", "UKPN", "Bury Grid", "BSP", 52.07, 1.07),   # 0.5 km away, different name: not merged
    ])
    links = pl.DataFrame({"asset_uid": ["N:1", "U:1"], "site_id": ["NESO:bramford", "UKPN:bramford"]})
    out, links2, alias = merge_neso_gsps(site, links)
    assert "NESO:bramford" not in out["site_id"].to_list() and "NESO:lonely" in out["site_id"].to_list()
    br = out.filter(pl.col("site_id") == "UKPN:bramford").row(0, named=True)
    assert br["kind"] == "GSP" and br["kinds"] == "GSP,BSP" and br["alias_ids"] == "NESO:bramford"
    assert br["alias_names"] == "Bramford"
    assert out.filter(pl.col("site_id") == "UKPN:bury")["kind"][0] == "BSP"
    assert dict(links2.iter_rows())["N:1"] == "UKPN:bramford"
    assert alias.filter(pl.col("alias_id") == "NESO:clacton bramford")["site_id"][0] == "UKPN:clacton"


def test_name_index_alias_and_no_self_parent():
    site = pl.DataFrame([
        _site("NESO:bramford", "NESO", "Bramford", "GSP", 52.07, 1.063),
        _site("UKPN:bramford", "UKPN", "BRAMFORD GRID 132kV", "GSP", 52.0703, 1.0628),
        _site("UKPN:hadleigh", "UKPN", "Hadleigh Primary", "PRIMARY", 52.04, 0.97),
    ])
    idx = NameIndex(site, alias={"NESO:bramford": "UKPN:bramford"})
    assert idx.resolve("UKPN", "Bramford", ("GSP",), (52.04, 0.97)) == "UKPN:bramford"
    assert idx.resolve("UKPN", "Bramford", ("GSP",), exclude="UKPN:bramford") is None


def test_loose_variants_strip_operator_noise():
    assert "giffnock" in loose_variants("GIFFNOCK GSP A1")
    assert "tynemouth" in loose_variants("Tynemouth 1")
    assert "lytham" in loose_variants("LYTHAM (BASI)")
    assert "west melton" in loose_variants("West Melton 1 & 2")
    assert "penpont" in loose_variants("PENPONT 33kV SWS")


def test_resolve_loose_lists_junk_and_unique_fuzzy():
    site = pl.DataFrame([
        _site("SPEN:giffnock", "SPEN", "GIFFNOCK", "BSP", 55.8, -4.3),
        _site("SPEN:holway", "SPEN", "HOLWAY RD T1", "PRIMARY", 53.3, -3.2),
    ])
    idx = NameIndex(site)
    assert idx.resolve_loose("SPEN", "GIFFNOCK GSP A1") == "SPEN:giffnock"
    assert idx.resolve_loose("SPEN", "Brymbo T1 / Holway Road T1") == "SPEN:holway"   # first name that resolves
    for junk in ("Redacted", "#N/A", "*** Junction near Bold", None):
        assert idx.resolve_loose("SPEN", junk) is None


def test_link_faults_tiers_node_only_for_ukpn():
    site = pl.DataFrame([
        _site("UKPN:sall", "UKPN", "Sall Grid 33kV", "BSP", 52.7, 1.1),
        _site("NGED:west point", "NGED", "West Point", "PRIMARY", 51.0, -3.0),
    ])
    asset_site = pl.DataFrame({"asset_uid": ["UKPN:A1"], "site_id": ["UKPN:sall"]})
    row = {"asset_uid": None, "kv": 33.0, "fl_3ph_make_ka": 1.0, "fl_3ph_break_ka": 1.0, "fl_1ph_make_ka": None,
           "fl_1ph_break_ka": None, "rating_make_ka": None, "rating_break_ka": None, "as_of": None}
    f = pl.DataFrame([
        {**row, "asset_uid": "UKPN:A1", "operator": "UKPN", "ltds_node": "SHER11", "site_name": "SHERINGHAM SALL", "source_id": "a"},
        {**row, "operator": "UKPN", "ltds_node": "SHER11", "site_name": "EPN 0052", "source_id": "b"},   # code name
        {**row, "operator": "NGED", "ltds_node": "WESP3_1", "site_name": "West Point 33kV T 3L3", "source_id": "c"},
        {**row, "operator": "NGED", "ltds_node": "SHER11", "site_name": "Some Solar Farm", "source_id": "c"},
    ])
    out = link_faults(f, asset_site, NameIndex(site)).sort("source_id", "site_name")
    tiers = dict(zip(out["site_name"], out["link_tier"], strict=True))
    assert tiers["SHERINGHAM SALL"] == "asset" and tiers["EPN 0052"] == "node"
    assert tiers["Some Solar Farm"] is None                                # NGED nodes are never a join key
    assert out.filter(pl.col("site_name") == "EPN 0052")["site_id"][0] == "UKPN:sall"


def test_enw_names_sources_and_neso_gsp_and_colocated_bsp():
    q = pl.DataFrame({"operator": ["ENW"], "primary_name": ["SALE PRY (100136)"], "bsp_name": [None],
                      "gsp_name": [None], "connection_point": [None]})
    flex = pl.DataFrame({"number": ["100637", "100136"], "name": ["Frederick Rd", "Wrong Sale"],
                         "source_id": ["f", "f"]})

    def a(uid, name, kind, lat, lon, op="ENW"):
        return {"asset_uid": uid, "operator": op, "name": name, "kind": kind, "lat": lat, "lon": lon}

    asset = pl.DataFrame([
        a("ENW:PRIMARY:100136", "100136", "PRIMARY", 53.40, -2.30),
        a("ENW:PRIMARY:100637", "100637", "PRIMARY", 53.4900, -2.2760),
        a("ENW:BSP:100504", "100504", "BSP", 53.4903, -2.2761),            # 35 m from a named primary
        a("ENW:BSP:100009", "100009", "BSP", 53.60, -2.50),                # nothing near: stays a number
        a("ENW:GSP:305951", "305951", "GSP", 53.6210, -2.2167),
        a("NESO:X", "Rochdale", "GSP", 53.6215, -2.2160, op="NESO"),
    ])
    names = _enw_names(q, flex, asset)
    assert names["100136"] == "Sale Pry"            # ECR first (flex 'Wrong Sale' never overrides)
    assert names["100637"] == "Frederick Rd"
    assert names["100504"] == "Frederick Rd"
    assert "100009" not in names
    assert names["305951"] == "Rochdale"
