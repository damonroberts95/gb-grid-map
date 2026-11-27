"""Estimate of LV monitors installed per operator, with every step written out for the Stats page.

Evidence per operator (EVIDENCE below, researched Nov 2026, each with its source):
  - points: what the operator said it had installed, on a date (a count, or a % of its secondary substations)
  - plan:   what it said it will install, by when ("total": reach N by end; "adds": install N between start and end)
  - base:   monitors installed before the counted period (e.g. SPEN's ED1 monitors, not in its ED2 count)
Plus our own count: LV sites the operator publishes as monitored (extra/lv_monitored.monitored()).

Method (same for every operator):
  1. Confirmed = the larger of our published-list count and the operator's latest stated figure (+ base).
     A % is turned into a count with our own LV substation count for that operator.
  2. Estimate = the latest stated figure carried forward to today along the operator's plan, at a steady rate
     (+ base), never below Confirmed. No plan: Estimate = Confirmed.
  3. Coverage = Estimate / LV substations we hold for that operator.
Assumptions are listed per operator and in ASSUMPTIONS.
"""

from __future__ import annotations

from datetime import date

ASSUMPTIONS = [
    ("Monitors are installed at a steady rate between the last stated figure and the plan's end date. Real rollouts "
    "are often slower at first and faster near the deadline."),
    "One monitor per substation. Some operators count devices, which can be more than one per substation.",
    ("A % of secondary substations is turned into a count using the number of LV substations this app holds for that "
    "operator. The operator's own total may differ."),
    "Plans are assumed to be on track unless the operator says otherwise. Plans can slip.",
    "Published monitor lists only include monitors the operator chose to publish, so they are a lower bound.",
]

D = date.fromisoformat

EVIDENCE: dict[str, dict] = {
    "UKPN": {
        "points": [{"pct": 14, "date": "2024-03-31", "what": "monitoring at 14% of secondary substations",
                    "source": "UKPN DSO Performance Panel Report 2023/24",
                    "url": "https://d1lf1oz5vvdb9r.cloudfront.net/app/uploads/2024/04/UK-Power-Networks-DSO-Performance-Panel-Report-2023-24-FINAL.pdf"}],
        "plan": {"kind": "adds", "n": 5000, "start": "2023-04-01", "end": "2028-03-31",
                 "what": "install 5,000 LV monitors during ED2", "source": "UKPN Annual Review 2024",
                 "url": "https://annualreview2024.ukpowernetworks.co.uk/operational-performance/network-reliability"},
        "notes": ["UKPN publishes no list of monitored LV substations.",
                  ("The 14% may already include some of the 5,000 ED2 monitors. Only the part of the plan after "
                  "March 2024 is added, to avoid counting twice.")],
    },
    "SSEN": {
        "points": [{"n": 3000, "date": "2024-02-01", "what": "about 3,000 LV monitoring devices installed",
                    "source": "SSEN Network Visibility Strategy, Feb 2024",
                    "url": "https://www.ssen.co.uk/globalassets/about-us/stakeholder-engagement/annual-engagement-plans-and-reports/ssen-network-visibility-strategy-feb-2024.pdf"}],
        "plan": {"kind": "adds", "n": 19000, "start": "2023-04-01", "end": "2028-03-31",
                 "what": "install LV monitors in about 19,000 secondary substations during ED2",
                 "source": "SSEN Network Visibility Strategy, Feb 2024",
                 "url": "https://www.ssen.co.uk/globalassets/about-us/stakeholder-engagement/annual-engagement-plans-and-reports/ssen-network-visibility-strategy-feb-2024.pdf"},
        "notes": ["Our published count is SSEN substations with live NeRDA LV readings. SSEN publishes no full list."],
    },
    "SPEN": {
        "base": {"n": 2438, "what": "monitors from ED1 (2015-2023), not in the ED2 count",
                 "source": "SPEN Network Visibility Strategy for RIIO-ED2",
                 "url": "https://www.spenergynetworks.co.uk/userfiles/file/SPEN_Network_Visibility_Strategy_RIIO-ED2.pdf"},
        "points": [{"n": 11768, "date": "2026-03-31", "what": "11,768 LV monitors installed in ED2 so far",
                    "source": "SPEN DSO Performance Panel Submission 2025/26",
                    "url": "https://www.spenergynetworks.co.uk/userfiles/file/SPEN_DSO_Panel_Submission_2026_AW-ONLINE.pdf"}],
        "plan": {"kind": "total", "n": 15000, "end": "2028-03-31", "what": "15,000 LV monitors by the end of ED2",
                 "source": "SPEN DSO Performance Panel Submission 2025/26",
                 "url": "https://www.spenergynetworks.co.uk/userfiles/file/SPEN_DSO_Panel_Submission_2026_AW-ONLINE.pdf"},
        "notes": [("SPEN's published LV monitoring table lists more transformers than SPEN's stated totals. It may "
                  "count transformers, not substations, or include older monitors. We take the larger figure as "
                  "confirmed.")],
    },
    "NPG": {
        "points": [{"n": 3417, "date": "2025-03-31", "what": "3,417 secondary substations with LV monitors",
                    "source": "Northern Powergrid 2024/25 Business Plan Commitments Report",
                    "url": "https://www.northernpowergrid.com/sites/default/files/assets/SLC50%20Report%202025.pdf"}],
        "plan": {"kind": "total", "n": 10000, "end": "2028-03-31", "what": "10,000 LV monitors by the end of ED2",
                 "source": "Northern Powergrid business plan 2023-28",
                 "url": "https://www.northernpowergrid.com/sites/default/files/2025-03/NPg_Our_business_plan_for_2023_28.pdf"},
        "notes": [("The same report also says 5,390 monitors. That is likely devices, with more than one at some "
                  "substations. We use the substation count.")],
    },
    "ENW": {
        "points": [{"pct": 25, "date": "2025-03-31", "what": "LV monitoring at 25% of secondary substations",
                    "source": "ENW DSO Performance Panel Submission 2024/25",
                    "url": "https://www.enwl.co.uk/globalassets/future-energy/dso/dso-performance-panel-submission/dso-performance-panel-submission-2024-25.pdf"}],
        "notes": ["ENW states an aim of full LV visibility but no number or date, so no plan is projected.",
                  ("A staff post mentions over 6,000 PreSense devices. Not used: it is undated and not an official "
                  "figure.")],
    },
    "NGED": {
        "points": [],
        "notes": ["No installed count or ED2 monitor target found in NGED's public documents.",
                  ("Only the substations in NGED's published LV load monitor data are counted. The real number is "
                  "likely much higher.")],
    },
    "NIE": {
        "points": [{"n": 0, "date": "2025-04-01", "assumed": True,
                    "what": "assumed none at the start of RP7 (trials only)",
                    "source": "NIE Networks RP6 Innovation Report",
                    "url": "https://www.nienetworks.co.uk/getattachment/0d336c7c-3009-4169-9a09-665711dc6363/RP6-Innovation-Report.pdf"}],
        "plan": {"kind": "total", "n": 5458, "end": "2031-03-31",
                 "what": "LV monitors at 3,685 ground-mounted and 1,773 pole-mounted substations in RP7 (2025-2031)",
                 "source": "Utility Regulator RP7 Final Determination, Annex P",
                 "url": "https://www.uregni.gov.uk/files/uregni/documents/2024-10/Annex%20P%20-%20Assessment%20of%20Network%20Investment%20Direct%20Allowances.pdf"},
        "notes": ["No installed count found. Low confidence."],
    },
    "ESB": {
        "points": [{"n": 3500, "date": "2024-12-31", "assumed": True,
                    "what": "3,500 LV monitoring devices: the 2024 milestone, assumed met",
                    "source": "ESB Networks Visibility Multiyear Plan 2024-2028",
                    "url": "https://esbnetworksprdsastd01.blob.core.windows.net/media/docs/default-source/publications/visibility-myp-(2024-2028).pdf?sfvrsn=c36a87f9_0"}],
        "plan": {"kind": "total", "n": 10660, "end": "2025-12-31",
                 "what": "10,660 LV monitoring devices by the end of 2025",
                 "source": "ESB Networks Visibility Multiyear Plan 2024-2028",
                 "url": "https://esbnetworksprdsastd01.blob.core.windows.net/media/docs/default-source/publications/visibility-myp-(2024-2028).pdf?sfvrsn=c36a87f9_0"},
        "notes": ["Targets only. No confirmed installed count found, so the estimate assumes the target was met.",
                  "ESB has about 250,000 MV/LV substations, mostly small pole-mounted units."],
    },
}


def _fmt(n: float) -> str:
    return f"{round(n):,}"


def _months(a: date, b: date) -> float:
    return (b - a).days / 30.44


def estimate(op: str, published: int, lv_sites: int, today: date) -> dict:
    """One operator's figures and the steps that produced them, in plain sentences."""
    ev = EVIDENCE.get(op, {"points": [], "notes": []})
    steps: list[str] = []
    sources: list[dict] = []
    base = ev.get("base")
    b = base["n"] if base else 0
    steps.append(f"Published list: {_fmt(published)} LV substations marked as monitored in the operator's open data.")
    if base:
        steps.append(f"Add {_fmt(b)} {base['what']}.")
        sources.append({"title": base["source"], "url": base["url"]})

    latest = None
    for p in sorted(ev["points"], key=lambda p: p["date"]):
        sources.append({"title": p["source"], "url": p["url"]})
        if "pct" in p:
            n = p["pct"] / 100 * lv_sites
            steps.append(f"{p['date']}: {p['what']}. {p['pct']}% x {_fmt(lv_sites)} LV substations we hold = {_fmt(n)}.")
        else:
            n = p["n"]
            steps.append(f"{p['date']}: {p['what']}.")
        latest = (D(p["date"]), n, p.get("assumed", False))

    stated = (latest[1] + b) if latest else None
    confirmed = max(published, stated or 0) if latest and not latest[2] else published
    if latest and not latest[2]:
        steps.append(f"Confirmed = larger of published list ({_fmt(published)}) and latest stated figure"
                     f"{' + base' if b else ''} ({_fmt(stated)}) = {_fmt(confirmed)}.")
    else:
        steps.append(f"Confirmed = published list = {_fmt(confirmed)}. No confirmed installed figure from the operator.")

    est, plan = confirmed, ev.get("plan")
    if plan and latest:
        sources.append({"title": plan["source"], "url": plan["url"]})
        d0, v0, _ = latest
        end = D(plan["end"])
        t = min(today, end)
        if plan["kind"] == "total":
            frac = 1.0 if end <= d0 else max(0.0, min(1.0, _months(d0, t) / _months(d0, end)))
            proj = v0 + (plan["n"] - v0) * frac
            steps.append(f"Plan: {plan['what']}. From {_fmt(v0)} on {d0} to {_fmt(plan['n'])} on {end} at a steady "
                         f"rate. By {t}: {_fmt(v0)} + ({_fmt(plan['n'])} - {_fmt(v0)}) x {frac:.2f} = {_fmt(proj)}.")
        else:
            start = D(plan["start"])
            per_month = plan["n"] / _months(start, end)
            m = max(0.0, _months(d0, t))
            proj = v0 + per_month * m
            steps.append(f"Plan: {plan['what']}. That is {_fmt(per_month)} a month. {m:.0f} months from {d0} to {t}: "
                         f"{_fmt(v0)} + {_fmt(per_month)} x {m:.0f} = {_fmt(proj)}.")
        if today > end:
            steps.append(f"The plan ended on {end}. Assumed met.")
        est = max(confirmed, proj + b)
        steps.append(f"Estimate = projected{' + base' if b else ''}, never below confirmed = {_fmt(est)}.")
    else:
        steps.append(f"No numbered plan found, so Estimate = Confirmed = {_fmt(est)}.")
    cover = 100 * est / lv_sites if lv_sites and lv_sites > est else None
    if cover is not None:
        steps.append(f"Coverage = {_fmt(est)} / {_fmt(lv_sites)} LV substations = {cover:.1f}%.")
    elif lv_sites:
        steps.append(f"Coverage not shown: we hold only {_fmt(lv_sites)} LV substations for {op}, fewer than the "
                     "estimate, so our site list is incomplete for this operator.")
    seen, uniq = set(), []
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            uniq.append(s)
    return {"operator": op, "published": published, "confirmed": round(confirmed), "estimate": round(est),
            "lv_sites": lv_sites, "coverage_pct": None if cover is None else round(cover, 1),
            "has_plan": bool(plan), "steps": steps, "notes": ev.get("notes", []), "sources": uniq}


def all_estimates(published: dict[str, int], lv_sites: dict[str, int], today: date | None = None) -> dict:
    today = today or date.today()
    ops = [o for o in ("UKPN", "SSEN", "SPEN", "NGED", "NPG", "ENW", "NIE", "ESB") if o in lv_sites or o in published]
    rows = [estimate(o, published.get(o, 0), lv_sites.get(o, 0), today) for o in ops]
    return {"as_of": today.isoformat(), "rows": rows, "assumptions": ASSUMPTIONS,
            "total": {"published": sum(r["published"] for r in rows), "confirmed": sum(r["confirmed"] for r in rows),
                      "estimate": sum(r["estimate"] for r in rows)}}
