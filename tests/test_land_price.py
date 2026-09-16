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


# ---- 条件を揃えた分布（PRO）----
from src.land_price import (AREA_TOLERANCE, MATCH_FILTERS,
                            analyze_matched_market, road_band)


def _matched_txn(price, area, district="南町", width=6.0, road="公道",
          cov=60, far=200, shape="長方形"):
    return Transaction(
        trade_price=price, type="宅地(土地)", municipality_code="14206",
        district_name=district, land_area_m2=area, building_area_m2=None,
        build_year=None, period_year=2024, period_quarter=1,
        city_planning="第一種住居地域", structure=None, layout=None,
        road_width_m=width, road_type=road, land_shape=shape,
        coverage_ratio=cov, floor_area_ratio=far)


SUBJ = dict(road_type="公道", road_width_m=6.0, coverage_ratio=60,
            floor_area_ratio=200, land_area_m2=120.0)


def test_the_bands_group_widths_that_are_the_same_in_practice():
    """4.0mと4.2mを別物にしても意味がない。帯で合わせる。"""
    assert road_band(3.5) == "4m未満"
    assert road_band(4.0) == road_band(5.9) == "4〜6m"
    assert road_band(6.0) == road_band(12.0) == "6m以上"
    assert road_band(None) is None


def test_matching_narrows_the_pool_to_like_for_like():
    rows = ([_matched_txn(20_000_000, 120.0) for _ in range(6)]
            + [_matched_txn(9_000_000, 120.0, road="私道") for _ in range(6)])
    m = analyze_matched_market(rows, SUBJ, "南町")
    assert m.pool == 12
    assert m.count == 6                      # 私道の6件が落ちる
    assert "道路の種類" in m.applied
    assert m.unit_mid == int(20_000_000 / 120.0)


def test_the_filters_are_dropped_from_the_least_important_end():
    """件数が足りないときは、後ろの条件から外す。

    道路の種類を最後まで残すのは、実測で私道と公道の差が最も大きかった
    ため（船橋市三咲は成約の62%が私道で、坪単価の中央値が36万円。
    前原西の97万円との差は、駅距離だけでは説明がつかない）。
    """
    # 幅員も面積も合わないが、道路の種類だけは合う成約
    rows = [_matched_txn(20_000_000, 300.0, width=3.0) for _ in range(6)]
    m = analyze_matched_market(rows, SUBJ, "南町")
    assert m.applied == ["道路の種類"]
    assert "敷地面積" in m.dropped
    assert "前面道路の幅員" in m.dropped
    assert any("条件は外しています" in n for n in m.notes)


def test_the_most_important_filter_is_first():
    assert MATCH_FILTERS[0] == "road_type"


def test_a_blank_field_is_not_treated_as_a_mismatch():
    """相手の項目が空なら落とさない。

    空欄を不一致として扱うと、項目が埋まっている物件だけが残って
    分布が偏る。
    """
    rows = [_matched_txn(20_000_000, 120.0, road=None) for _ in range(6)]
    m = analyze_matched_market(rows, SUBJ, "南町")
    assert m.count == 6


def test_too_few_matches_means_no_numbers_at_all():
    rows = [_matched_txn(20_000_000, 120.0) for _ in range(3)]
    m = analyze_matched_market(rows, SUBJ, "南町")
    assert m.unit_mid is None
    assert any("足りません" in n or "出していません" in n for n in m.notes)


def test_the_area_tolerance_is_a_ratio_not_an_absolute():
    inside = _matched_txn(20_000_000, 120.0 * (1 + AREA_TOLERANCE - 0.01))
    outside = _matched_txn(20_000_000, 120.0 * (1 + AREA_TOLERANCE + 0.05))
    rows = [inside] * 6 + [outside] * 6
    m = analyze_matched_market(rows, SUBJ, "南町")
    assert m.count == 6
    assert "敷地面積" in m.applied


def test_the_pool_count_is_always_reported():
    """絞ったあとの件数だけ出すと、どれだけ捨てたか分からない。"""
    rows = [_matched_txn(20_000_000, 120.0) for _ in range(6)] + \
           [_matched_txn(9_000_000, 120.0, road="私道") for _ in range(4)]
    m = analyze_matched_market(rows, SUBJ, "南町")
    assert m.pool == 10 and m.count == 6
