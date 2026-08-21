# -*- coding: utf-8 -*-
"""マンション診断のオフライン単体テスト（ネットワーク不要）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import MansionSubject, Transaction
from src.mansion_price import (analyze_mansion_price, extract_mansion_comparables,
                               is_mansion_txn, txn_area_m2)

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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"[OK] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
