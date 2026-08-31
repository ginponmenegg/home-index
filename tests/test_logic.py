# -*- coding: utf-8 -*-
"""オフライン単体テスト（ネットワーク不要）。ロジックの妥当性を検証。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import SubjectProperty
from src.reinfolib import _parse_build_year, _parse_period, normalize_txn
from src.comparable import extract_comparables, _ratio_similarity, _year_similarity
from src.price_analysis import analyze_price, _weighted_percentile, time_adjust
from src.mockdata import sample_transactions
import datetime


def test_parse_build_year():
    assert _parse_build_year("2014年") == 2014
    assert _parse_build_year("戦前") is None
    assert _parse_build_year("") is None
    assert _parse_build_year(None) is None


def test_parse_period():
    assert _parse_period("2024年第4四半期") == (2024, 4)
    assert _parse_period("2023年第1四半期") == (2023, 1)
    assert _parse_period(None) == (None, None)


def test_normalize_txn_real_fields():
    rec = {"Type": "宅地(土地と建物)", "TradePrice": "34800000",
           "Area": "110", "TotalFloorArea": "96", "BuildingYear": "2006年",
           "Period": "2024年第3四半期", "MunicipalityCode": "14206",
           "DistrictName": "南町", "CityPlanning": "第一種住居地域",
           "Structure": "木造", "FloorPlan": "4LDK"}
    t = normalize_txn(rec)
    assert t.trade_price == 34800000
    assert t.land_area_m2 == 110.0
    assert t.building_area_m2 == 96.0
    assert t.build_year == 2006
    assert t.period_year == 2024 and t.period_quarter == 3


def test_ratio_similarity_bounds():
    assert _ratio_similarity(100, 100) == 1.0
    assert 0.0 <= _ratio_similarity(100, 200) <= 1.0
    assert _ratio_similarity(None, 100) == 0.0


def test_year_similarity():
    assert _year_similarity(2006, 2006) == 1.0
    assert _year_similarity(2006, 2006 + 3) == 1.0
    assert _year_similarity(2006, 2006 + 30) == 0.0


def test_weighted_percentile():
    pairs = [(10, 1), (20, 1), (30, 1), (40, 1)]
    assert 10 <= _weighted_percentile(pairs, 50) <= 40


def test_time_adjust_default_no_change():
    price, adjusted = time_adjust(30000000, 2020, 2026, 0.0)
    assert price == 30000000 and adjusted is False
    price2, adjusted2 = time_adjust(30000000, 2020, 2026, 0.02)
    assert price2 > 30000000 and adjusted2 is True


def test_end_to_end_mock():
    subj = SubjectProperty(
        property_type="chuko_kodate", price=34800000,
        address="神奈川県小田原市南町1-1-1", land_area_m2=110,
        building_area_m2=96, build_year=2006, municipality_code="14206",
        district_name="南町")
    txns = sample_transactions()
    year = datetime.date.today().year
    comps = extract_comparables(subj, txns, year)
    assert len(comps) >= 5, "類似が十分抽出されること"
    # マンション類似(建物面積欠損, 本町)の類似度は低いこと
    pa = analyze_price(subj, comps, year)
    assert pa.estimate_low <= pa.estimate_mid <= pa.estimate_high
    assert pa.verdict in ("割安の可能性", "概ね適正", "割高の可能性")
    assert pa.comparable_count >= 5
    print("mock verdict:", pa.verdict, "range:",
          pa.estimate_low, pa.estimate_mid, pa.estimate_high,
          "conf:", pa.confidence)


def test_loan_payment():
    from src.loan import monthly_payment, compute_loan
    # 金利0なら元本/回数
    assert monthly_payment(3600000, 0.0, 30) == 10000
    # 標準ケース：正の返済額
    m = monthly_payment(30000000, 0.0125, 35)
    assert 80000 < m < 100000
    L = compute_loan(38800000, down_payment=8800000, annual_rate=0.0125,
                     years=35, annual_income=8000000)
    assert L.principal == 30000000
    assert L.burden_ratio is not None and L.burden_ratio > 0


def test_scoring_transparent_and_bounded():
    from src.models import SubjectProperty, PriceAnalysis
    from src.scoring import build_diagnosis
    subj = SubjectProperty(property_type="chuko_kodate", price=38800000,
                           address="城山", land_area_m2=147, building_area_m2=90,
                           build_year=2005, station_walk_min=12)
    pa = PriceAnalysis(30000000, 34000000, 37000000, "割高の可能性", 13.5,
                       "mid", 6, "test", [])
    d = build_diagnosis(subj, pa, current_year=2026)
    assert 0 <= d.total_score <= 100
    assert d.grade in ("A", "B", "C", "D", "E")
    assert 0 <= d.data_sufficiency <= 100
    # ハザード未確認は要確認/重大リスクに出る
    assert any("ハザード" in r.type for r in d.critical_risks)
    # 価格乖離+13.5%は価格rawを下げる
    price_cat = [c for c in d.categories if c.name == "価格"][0]
    assert price_cat.raw < 0.85
    print("diagnosis:", d.total_score, d.grade, "suff", d.data_sufficiency)


def test_adjustment_downgrades_confidence_on_spread():
    # レンジ幅が大きいと確信度が下がること（price_analysis側の挙動）
    from src.models import SubjectProperty, Transaction, Comparable
    from src.comparable import similarity
    from src.price_analysis import analyze_price
    subj = SubjectProperty(property_type="chuko_kodate", price=35000000,
                           address="x", land_area_m2=110, building_area_m2=95,
                           build_year=2005, municipality_code="14206",
                           district_name="A")
    recs = [(20000000, 110, 95, 2005), (50000000, 110, 95, 2005),
            (30000000, 110, 95, 2005), (40000000, 110, 95, 2005),
            (25000000, 110, 95, 2005), (45000000, 110, 95, 2005)]
    comps = []
    for p, l, b, by in recs:
        t = Transaction(trade_price=p, type="宅地(土地と建物)",
                        municipality_code="14206", district_name="A",
                        land_area_m2=l, building_area_m2=b, build_year=by,
                        period_year=2023, period_quarter=1, city_planning="住居",
                        structure="木造", layout="4LDK")
        s, parts = similarity(subj, t, 2026)
        comps.append(Comparable(t, s, parts, None, "", False))
    pa = analyze_price(subj, comps, 2026)
    assert pa.confidence in ("low", "mid")  # 大きなばらつき→high не должен


def test_point_in_geometry():
    from src.enrichment import point_in_geometry
    poly = {"type": "Polygon", "coordinates": [[[139.0, 35.0], [139.2, 35.0],
            [139.2, 35.2], [139.0, 35.2], [139.0, 35.0]]]}
    assert point_in_geometry(139.1, 35.1, poly) is True
    assert point_in_geometry(140.0, 35.1, poly) is False


def test_hazard_scoring_with_risk():
    from src.enrichment import HazardResult
    from src.scoring import score_risk
    # 土砂特別警戒＋洪水rank4 → 低スコア・充足度高
    hz = HazardResult(checked=True, flood_rank=4, flood_label="5.0〜10.0m未満",
                      sediment="特別警戒区域")
    cs = score_risk("第一種住居地域", None, hz)
    assert cs.raw <= 0.35 and cs.sufficiency >= 0.8
    # 該当なし → 高スコア
    hz2 = HazardResult(checked=True)
    cs2 = score_risk("第一種住居地域", None, hz2)
    assert cs2.raw >= 0.9
    # 未取得 → 0.7上限・充足度低
    cs3 = score_risk("第一種住居地域", None, None)
    assert cs3.raw <= 0.7 and cs3.sufficiency < 0.5


def test_listing_parser():
    from src.extract import parse_listing_text
    p = parse_listing_text(
        "中古一戸建て 神奈川県小田原市城山1-2-3 価格 3,500万円 "
        "土地面積120.00㎡(36.30坪) 建物面積95.00㎡ 間取り4LDK 築2010年3月 "
        "小田原駅 徒歩20分")
    assert p["price_man"] == 3500
    assert p["city"] == "14206"
    assert p["district"] == "城山"
    assert p["land"] == 120.00 and p["building"] == 95.00
    assert p["layout"] == "4LDK" and p["byear"] == 2010
    assert p["station"] == 20 and p["ptype"] == "chuko_kodate"
    # 坪→㎡換算 と 億 と 和暦
    p2 = parse_listing_text("土地 100坪 平成10年築 1億2000万円")
    assert p2["price_man"] == 12000
    assert abs(p2["land"] - 330.58) < 0.1
    assert p2["byear"] == 1998


def test_radius_gate_and_newbuild_weights():
    from src.models import SubjectProperty, Transaction
    from src.comparable import (extract_comparables, NEWBUILD_WEIGHTS,
                                NEIGHBOR_RADIUS_M)
    subj = SubjectProperty(property_type="chuko_kodate", price=30000000,
                           address="x", land_area_m2=120, building_area_m2=95,
                           build_year=2005, municipality_code="14206",
                           district_name="城山")

    def T(dist, district):
        return Transaction(30000000, "宅地(土地と建物)", "14206", district,
                           120, 95, 2005, 2024, 1, "住居", "木造", "4LDK",
                           distance_m=dist)
    txns = [T(400, "南町"), T(1200, "浜町"), T(3500, "扇町"), T(8000, "遠方町")]
    comps = extract_comparables(subj, txns, 2026, radius_m=NEIGHBOR_RADIUS_M)
    dists = sorted(c.txn.distance_m for c in comps)
    assert dists == [400, 1200]  # 2km超は除外
    # 新築重みは築年を効かせない
    assert NEWBUILD_WEIGHTS["build_year"] == 0.0
    assert abs(sum(NEWBUILD_WEIGHTS.values()) - 1.0) < 1e-9


def test_newbuild_rules_and_bus():
    from src.extract import parse_listing_text
    from src.comparable import is_newbuild_txn
    from src.models import Transaction, SubjectProperty
    from src.scoring import score_location
    import datetime
    cy = datetime.date.today().year
    # 築年無記名の戸建 → 新築判定、築年=現在年で補完
    p = parse_listing_text("戸建 神奈川県小田原市栄町 3200万円 土地120㎡ 建物95㎡ 4LDK 徒歩10分")
    assert p["ptype"] == "shinchiku_kodate"
    assert p["byear"] == cy
    # 中古の明示があれば中古
    p2 = parse_listing_text("中古戸建 小田原市 2500万円 徒歩5分")
    assert p2["ptype"] == "chuko_kodate"
    # バス便抽出
    p3 = parse_listing_text("戸建 小田原駅 バス15分 バス停 徒歩5分 2980万円")
    assert p3["bus"] == 15 and p3["station"] == 5
    # 新築相当の成約判定
    t_new = Transaction(30000000, "宅地(土地と建物)", "14206", "栄町", 120, 95,
                        2024, 2024, 2, "住居", "木造", "4LDK")
    t_old = Transaction(30000000, "宅地(土地と建物)", "14206", "栄町", 120, 95,
                        2005, 2024, 2, "住居", "木造", "4LDK")
    assert is_newbuild_txn(t_new) is True
    assert is_newbuild_txn(t_old) is False
    # バス便は駅近より低スコア
    subj_bus = SubjectProperty(property_type="chuko_kodate", price=1, address="x",
                               station_walk_min=5, bus_min=15)
    cs = score_location(subj_bus, "第一種住居地域", None)
    assert "バス便" in cs.reason and cs.raw <= 0.55


def test_haversine_and_facility_scoring():
    from src.enrichment import haversine_m, FacilityResult
    from src.models import SubjectProperty
    from src.scoring import score_location
    # 約1度緯度≈111km
    assert 110000 < haversine_m(35.0, 139.0, 36.0, 139.0) < 112000
    subj = SubjectProperty(property_type="chuko_kodate", price=1, address="x",
                           station_walk_min=None)
    fac = FacilityResult(checked=True, nearest_station_m=640,
                         nearest_hospital_m=800, nearest_school_m=500,
                         hospital_count_1km=5)
    cs = score_location(subj, "第一種住居地域", fac)
    # 駅640m→徒歩約8分。生活利便のうち医療と教育しか取れていないので、
    # 点は出るが充足度は満点にしない（買い物・公共が未取得のため）。
    assert 0.6 <= cs.sufficiency < 0.85
    assert "病院800m" in cs.reason and "学校500m" in cs.reason
    assert cs.raw > 0.8

    # 買い物と公共まで揃えば充足度が上がる
    from src.osm import ShopResult, Shop
    fac2 = FacilityResult(checked=True, nearest_station_m=640,
                          nearest_hospital_m=800, nearest_school_m=500,
                          hospital_count_1km=5, nearest_preschool_m=400,
                          nearest_library_m=900, nearest_hall_m=700,
                          welfare_count_1km=2)
    shops = ShopResult(checked=True, shops=[Shop("モール", "mall", 800),
                                            Shop("スーパー", "supermarket", 500)])
    cs2 = score_location(subj, "第一種住居地域", fac2, shops)
    assert cs2.sufficiency > cs.sufficiency
    assert "大型商業施設800m" in cs2.reason
    assert "OpenStreetMap" in cs2.sources


def test_location_and_asset_no_longer_track_each_other():
    """立地と資産性が両方とも駅徒歩で動いていた重複を解消したことを固定する。

    駅徒歩だけを変えたとき、資産性は大きく動き、立地は生活利便が主軸なので
    あまり動かない。両方が同じだけ動くようなら分離できていない。
    """
    from src.enrichment import FacilityResult
    from src.models import SubjectProperty
    from src.osm import ShopResult, Shop
    from src.scoring import score_location, score_asset

    fac = FacilityResult(checked=True, nearest_hospital_m=400,
                         hospital_count_1km=6, nearest_school_m=500,
                         nearest_preschool_m=400, nearest_library_m=900,
                         nearest_hall_m=700, welfare_count_1km=3)
    shops = ShopResult(checked=True, shops=[Shop("モール", "mall", 700),
                                            Shop("スーパー", "supermarket", 400)])

    def pair(walk):
        subj = SubjectProperty(property_type="chuko_kodate", price=1, address="x",
                               station_walk_min=walk)
        return (score_location(subj, None, fac, shops).raw,
                score_asset(subj, None, None).raw)

    near_loc, near_asset = pair(5)
    far_loc, far_asset = pair(25)
    assert near_asset - far_asset > near_loc - far_loc


def test_estat_population_parser():
    from src.enrichment import _parse_population_series
    body = {"GET_STATS_DATA": {"STATISTICAL_DATA": {
        "CLASS_INF": {"CLASS_OBJ": [
            {"@id": "cat01", "CLASS": [
                {"@code": "A1101", "@name": "A1101_総人口"},
                {"@code": "A1102", "@name": "A1102_人口増減"}]},
            {"@id": "area", "CLASS": [{"@code": "14206", "@name": "小田原市"}]},
            {"@id": "time", "CLASS": [{"@code": "2015", "@name": "2015年"},
                                       {"@code": "2020", "@name": "2020年"}]}]},
        "DATA_INF": {"VALUE": [
            {"@cat01": "A1101", "@area": "14206", "@time": "2015", "$": "194086"},
            {"@cat01": "A1101", "@area": "14206", "@time": "2020", "$": "188856"},
            {"@cat01": "A1102", "@area": "14206", "@time": "2020", "$": "-5230"},
            {"@cat01": "A1101", "@area": "99999", "@time": "2020", "$": "1"}]}}}}
    series = _parse_population_series(body, "14206")
    assert series == [("2015", 194086), ("2020", 188856)]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"[OK] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
