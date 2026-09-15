# -*- coding: utf-8 -*-
"""近隣の土地取引の分布。ネットワーク不要。

■点数を付けない
土地の㎡単価はばらつきが大きい。実測（2026-09-15）で四分位の幅が中央値の
±40〜55%。戸建でさえ価格は100点中20点にとどめている。土地でそれ以上の精度は
出ないので、分布を見せて判断は読む人に返す。

■近隣の傾向は、対象地の話ではない
「この町の成約の20%が私道接道だった」は、その土地が私道だという意味では
ない。混同されると事実と違うことを伝えることになるので、割合の計算と、
画面に出す言葉の両方で気をつける。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.land_price import (MIN_SAMPLES, add_neighbourhood_traits,
                            analyze_land_market, is_land_txn)
from src.models import Transaction


def _land(price, area, district="前原西", road=6.0, rtype="市道",
          shape="ほぼ長方形", type_="宅地(土地)"):
    return Transaction(trade_price=price, type=type_, municipality_code="12204",
                       district_name=district, land_area_m2=area,
                       building_area_m2=None, build_year=None,
                       period_year=2024, period_quarter=2, city_planning=None,
                       structure=None, layout=None, road_width_m=road,
                       road_type=rtype, land_shape=shape)


def test_only_land_transactions_are_used():
    house = _land(30_000_000, 120, type_="宅地(土地と建物)")
    assert not is_land_txn(house)
    m = analyze_land_market([house] * 10, "前原西")
    assert m.count == 0
    assert "見つかりません" in m.notes[0]


def test_too_few_transactions_produce_no_numbers():
    """3件の中央値は、真ん中の1件そのもの。分布とは呼べない。"""
    rows = [_land(20_000_000 + i * 10**6, 120) for i in range(MIN_SAMPLES - 1)]
    m = analyze_land_market(rows, "前原西")
    assert m.unit_mid is None
    assert "分布を出していません" in " ".join(m.notes)


def test_the_distribution_comes_out_when_there_is_enough():
    rows = [_land(int(120 * u), 120) for u in
            (100_000, 120_000, 140_000, 160_000, 180_000, 200_000)]
    m = analyze_land_market(rows, "前原西", subject_price=18_000_000,
                            subject_area_m2=120)
    assert m.count == 6
    assert m.unit_low < m.unit_mid < m.unit_high
    assert m.subject_unit == 150_000
    assert m.spread_pct and m.spread_pct > 0


def test_it_widens_to_the_whole_municipality_when_the_district_is_thin():
    rows = ([_land(120 * 150_000, 120, district="前原西")] * 2
            + [_land(120 * 150_000, 120, district="別の町")] * 8)
    m = analyze_land_market(rows, "前原西")
    assert m.district is None, "町名で出したことになっている"
    assert m.count == 10
    assert "市区町村全体で見ています" in " ".join(m.notes)


def test_the_neighbourhood_traits_are_shares_not_facts_about_the_site():
    rows = ([_land(120 * 150_000, 120, rtype="私道")] * 2
            + [_land(120 * 150_000, 120, rtype="市道")] * 8)
    m = add_neighbourhood_traits(analyze_land_market(rows, "前原西"),
                                 rows, "前原西")
    assert m.private_road_pct == 20
    # 対象地そのものの値は、この関数では一切触らない
    assert m.subject_unit is None


def test_a_trait_is_not_reported_when_the_field_is_mostly_empty():
    """項目が埋まっていない取引ばかりのとき、割合を出さない。"""
    rows = [_land(120 * 150_000, 120, road=None, rtype=None, shape=None)
            for _ in range(10)]
    m = add_neighbourhood_traits(analyze_land_market(rows, "前原西"),
                                 rows, "前原西")
    assert m.private_road_pct is None
    assert m.narrow_road_pct is None
    assert m.irregular_pct is None
