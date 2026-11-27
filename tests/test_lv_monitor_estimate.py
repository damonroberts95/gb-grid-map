from datetime import date

from gridmap import lv_monitor_estimate as lme


def test_total_plan_steady_rate_and_base():
    r = lme.estimate("SPEN", published=0, lv_sites=50000, today=date(2027, 3, 31))
    # 11,768 on 2026-03-31 -> 15,000 on 2028-03-31: half way after a year, plus 2,438 ED1 base
    assert abs(r["estimate"] - (2438 + 11768 + (15000 - 11768) * 0.5)) < 20
    assert r["confirmed"] == 2438 + 11768
    assert any("x 0.50" in s for s in r["steps"])


def test_published_list_raises_confirmed_and_floor():
    r = lme.estimate("SPEN", published=19515, lv_sites=50000, today=date(2026, 11, 27))
    assert r["confirmed"] == 19515 and r["estimate"] == 19515


def test_pct_uses_our_site_count_and_no_plan_holds():
    r = lme.estimate("ENW", published=2444, lv_sites=34734, today=date(2026, 11, 27))
    assert r["confirmed"] == round(0.25 * 34734) and r["estimate"] == r["confirmed"] and not r["has_plan"]


def test_assumed_points_are_not_confirmed_and_ended_plan_is_met():
    r = lme.estimate("ESB", published=0, lv_sites=45868, today=date(2026, 11, 27))
    assert r["confirmed"] == 0 and r["estimate"] == 10660
    assert any("Assumed met" in s for s in r["steps"])


def test_unknown_operator_falls_back_to_published():
    r = lme.estimate("NGED", published=299, lv_sites=195078, today=date(2026, 11, 27))
    assert r["confirmed"] == r["estimate"] == 299 and r["sources"] == []


def test_coverage_hidden_when_site_list_incomplete():
    r = lme.estimate("SPEN", published=19515, lv_sites=19429, today=date(2026, 11, 27))
    assert r["coverage_pct"] is None and any("incomplete" in s for s in r["steps"])
