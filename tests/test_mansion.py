# -*- coding: utf-8 -*-
"""マンション診断のオフライン単体テスト（ネットワーク不要）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import MansionSubject, Transaction
from src.mansion_price import (analyze_mansion_price, extract_mansion_comparables,
                               is_mansion_txn, txn_area_m2)
from src.mansion_scoring import (is_new_quake_standard, score_mansion_asset,
                                 score_mansion_management, repair_fund_band,
                                 build_mansion_diagnosis)
from src.loan import compute_loan

CURRENT_YEAR = 2026


def _subject(price=None, area=70.0, build_year=2005, walk=8, **kw):
    return MansionSubject(address="神奈川県小田原市栄町1-1-1", price=price,
                          build_year=build_year, station_walk_min=walk,
                          exclusive_area_m2=area, municipality_code="14206",
                          district_name="栄町", **kw)


def _txn(price, area, build_year=2005, year=2025, district="栄町",
         type_="中古マンション等"):
    return Transaction(trade_price=price, type=type_, municipality_code="14206",
                       district_name=district, land_area_m2=area,
                       building_area_m2=None, build_year=build_year,
                       period_year=year, period_quarter=2, city_planning=None,
                       structure="ＲＣ", layout="3LDK")


def _market(unit_price=500_000, n=8):
    """㎡単価が unit_price 前後の成約をn件つくる。"""
    out = []
    for i in range(n):
        area = 65.0 + i          # 65〜72㎡
        unit = unit_price + (i - n // 2) * 4000   # 単価を少しばらつかせる
        out.append(_txn(int(unit * area), area, build_year=2004 + (i % 3),
                        year=2025 - (i % 3)))
    return out


def test_is_mansion_txn():
    assert is_mansion_txn(_txn(30_000_000, 70.0)) is True
    assert is_mansion_txn(_txn(30_000_000, 70.0, type_="宅地(土地と建物)")) is False
    assert is_mansion_txn(_txn(30_000_000, 70.0, type_=None)) is False


def test_txn_area_prefers_whichever_field_carries_it():
    # XIT001 のマンションは面積が Area（正規化後は land_area_m2）に入る
    t = _txn(30_000_000, 70.0)
    assert txn_area_m2(t) == 70.0
    # 建物面積側に入っていた場合も拾う
    t2 = Transaction(trade_price=30_000_000, type="中古マンション等",
                     municipality_code="14206", district_name="栄町",
                     land_area_m2=None, building_area_m2=68.0, build_year=2005,
                     period_year=2025, period_quarter=1, city_planning=None,
                     structure=None, layout=None)
    assert txn_area_m2(t2) == 68.0
    # どちらも無ければ None（勝手に埋めない）
    t3 = Transaction(trade_price=30_000_000, type="中古マンション等",
                     municipality_code="14206", district_name="栄町",
                     land_area_m2=None, building_area_m2=None, build_year=2005,
                     period_year=2025, period_quarter=1, city_planning=None,
                     structure=None, layout=None)
    assert txn_area_m2(t3) is None


def test_unit_price_median_and_estimate():
    """㎡単価50万円の市場・専有70㎡なら、推定中央値は3,500万円前後になる。"""
    subj = _subject(price=35_000_000)
    comps = extract_mansion_comparables(subj, _market(500_000), CURRENT_YEAR)
    pa = analyze_mansion_price(subj, comps, CURRENT_YEAR)
    assert pa.comparable_count >= 3
    assert 480_000 <= pa.unit_building_median <= 520_000
    assert 33_000_000 <= pa.estimate_mid <= 37_000_000
    assert pa.estimate_low <= pa.estimate_mid <= pa.estimate_high


def test_verdict_fair_high_low():
    market = _market(500_000)
    subj = _subject(price=35_000_000)
    mid = analyze_mansion_price(subj, extract_mansion_comparables(
        subj, market, CURRENT_YEAR), CURRENT_YEAR)
    assert mid.verdict == "概ね適正"

    over = _subject(price=48_000_000)
    pa_over = analyze_mansion_price(over, extract_mansion_comparables(
        over, market, CURRENT_YEAR), CURRENT_YEAR)
    assert pa_over.verdict == "割高の可能性"
    assert pa_over.deviation_pct > 0

    under = _subject(price=24_000_000)
    pa_under = analyze_mansion_price(under, extract_mansion_comparables(
        under, market, CURRENT_YEAR), CURRENT_YEAR)
    assert pa_under.verdict == "割安の可能性"
    assert pa_under.deviation_pct < 0


def test_no_comparables_is_undecidable():
    """事例が無ければ価格を断定しない（第14・41章）。"""
    subj = _subject(price=35_000_000)
    pa = analyze_mansion_price(subj, [], CURRENT_YEAR)
    assert pa.verdict == "判定不可"
    assert pa.confidence == "low"
    assert pa.estimate_mid is None


def test_detached_transactions_are_ignored():
    """戸建の成約が混ざっていても、マンションの価格には使わない。"""
    subj = _subject(price=35_000_000)
    detached = [_txn(40_000_000, 120.0, type_="宅地(土地と建物)") for _ in range(6)]
    comps = extract_mansion_comparables(subj, detached, CURRENT_YEAR)
    assert comps == []
    assert analyze_mansion_price(subj, comps, CURRENT_YEAR).verdict == "判定不可"


def test_missing_area_is_undecidable():
    subj = _subject(price=35_000_000, area=None)
    comps = extract_mansion_comparables(subj, _market(500_000), CURRENT_YEAR)
    pa = analyze_mansion_price(subj, comps, CURRENT_YEAR)
    assert pa.verdict == "判定不可"
    assert "専有面積" in pa.note


# ---- スコアリング ----
def test_new_quake_standard_boundary():
    """1981年築は旧耐震側、1982年築から新耐震（年単位運用・安全側）。"""
    assert is_new_quake_standard(1981) is False
    assert is_new_quake_standard(1982) is True
    assert is_new_quake_standard(None) is None


def test_old_quake_standard_is_penalised_and_flagged():
    old = _subject(price=20_000_000, build_year=1978)
    new = _subject(price=20_000_000, build_year=1990)
    assert (score_mansion_asset(old, CURRENT_YEAR).raw
            < score_mansion_asset(new, CURRENT_YEAR).raw)
    d = build_mansion_diagnosis(old, None)
    assert any(r.type == "旧耐震基準" for r in d.critical_risks)


def test_floor_and_direction_are_small_adjustments():
    base = _subject(price=35_000_000)
    plain = score_mansion_asset(base, CURRENT_YEAR).raw
    south = score_mansion_asset(_subject(price=35_000_000, direction="南"),
                                CURRENT_YEAR).raw
    north = score_mansion_asset(_subject(price=35_000_000, direction="北"),
                                CURRENT_YEAR).raw
    first = score_mansion_asset(_subject(price=35_000_000, floor=1),
                                CURRENT_YEAR).raw
    assert south > plain > north
    assert abs(south - plain) <= 0.05 and abs(north - plain) <= 0.05
    assert first < plain and abs(plain - first) <= 0.06


def test_missing_inputs_lower_sufficiency_not_the_score():
    """入力が無い項目は評価に反映せず、充足度を下げる（第14章）。"""
    bare = MansionSubject(address="東京都渋谷区1-1-1")
    cat = score_mansion_asset(bare, CURRENT_YEAR)
    assert cat.sufficiency == 0.0
    full = _subject(price=35_000_000, floor=5, direction="南", total_floors=10)
    assert score_mansion_asset(full, CURRENT_YEAR).sufficiency == 1.0


def test_diagnosis_uses_mansion_weights():
    subj = _subject(price=35_000_000, floor=5, total_floors=10, direction="南")
    comps = extract_mansion_comparables(subj, _market(500_000), CURRENT_YEAR)
    pa = analyze_mansion_price(subj, comps, CURRENT_YEAR)
    d = build_mansion_diagnosis(subj, pa, compute_loan(35_000_000, 3_000_000,
                                                       0.0125, 35, 6_000_000))
    names = [c.name for c in d.categories]
    assert names == ["価格", "資産性", "管理", "立地", "リスク", "資金"]
    assert sum(c.weight for c in d.categories) == 100
    assert "物件" not in names          # 建物内部はMVPで評価しない
    assert 0 <= d.total_score <= 100
    assert d.grade in ("A", "B", "C", "D", "E")


def test_balance_and_repair_history_stay_flagged():
    """額が分かっても、残高と修繕履歴は見えていない。そこは必ず残す。"""
    subj = _subject(price=35_000_000, management_fee=15_000, repair_fund=13_000)
    d = build_mansion_diagnosis(subj, None)
    assert any(r.type == "積立金残高と修繕履歴が未確認"
               for r in d.critical_risks)
    assert "管理" in d.comment


def test_management_unscored_without_the_numbers():
    """修繕積立金が無ければ水準を判定しない（勝手に埋めない）。"""
    c = score_mansion_management(_subject(price=35_000_000))
    assert c.sufficiency <= 0.2
    assert "未入力" in c.reason


def test_repair_fund_within_guideline_scores_well():
    # 70㎡ で月13,000円 = 186円/㎡。20階未満の目安 170〜430円の範囲内
    c = score_mansion_management(
        _subject(price=35_000_000, repair_fund=13_000, management_fee=15_000,
                 total_floors=10))
    assert c.raw >= 0.8
    assert "範囲" in c.reason


def test_repair_fund_far_below_guideline_is_a_critical_risk():
    # 70㎡ で月5,000円 = 71円/㎡。目安下限170円の6割(102円)すら下回る
    subj = _subject(price=35_000_000, repair_fund=5_000, management_fee=8_000,
                   total_floors=10)
    c = score_mansion_management(subj)
    assert c.raw <= 0.3
    d = build_mansion_diagnosis(subj, None)
    risk = [r for r in d.critical_risks
            if r.type == "修繕積立金が目安を大きく下回る"]
    assert len(risk) == 1 and risk[0].severity == "high"


def test_tall_buildings_use_the_higher_band():
    assert repair_fund_band(25)["low"] > repair_fund_band(10)["low"]
    assert repair_fund_band(None) == repair_fund_band(10)   # 不明は低層側


def test_monthly_charges_raise_the_burden_ratio():
    """管理費・修繕積立金は住み続ける限り出ていくので負担率に入れる。"""
    bare = compute_loan(35_000_000, 3_000_000, 0.0125, 35, 6_000_000)
    with_fees = compute_loan(35_000_000, 3_000_000, 0.0125, 35, 6_000_000,
                             monthly_extra=28_000)
    assert with_fees.monthly_payment == bare.monthly_payment
    assert with_fees.burden_ratio > bare.burden_ratio
    assert with_fees.monthly_extra == 28_000


def test_detached_loan_is_unchanged():
    """戸建は monthly_extra を渡さないので、これまでと同じ数字になる。"""
    loan = compute_loan(35_000_000, 3_000_000, 0.0125, 35, 6_000_000)
    assert loan.monthly_extra == 0
    assert loan.burden_ratio == round(loan.annual_payment / 6_000_000 * 100, 1)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"[OK] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
