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


# ---- 区域区分（市街化区域／市街化調整区域） -------------------------------
#
# 採点で一番重い減点がここに乗っている。付け方を間違えると、調整区域でも
# ないのにリスク15点満点を3点に落とす（またはその逆をやる）。


def _urbanization_tile():
    """XKT001 の実データの形を写したタイル。

    実際の応答では、外側の「都市計画区域」の面の中に「市街化区域」
    「市街化調整区域」の面が重なって入っていて、ひとつの地点が両方に
    当たる。しかも外側が先に並ぶ。最初に内包したものを採ると、いつでも
    「都市計画区域」を拾って区域区分が取れない。
    """
    def sq(x0, y0, x1, y1):
        return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0],
                                                    [x1, y1], [x0, y1],
                                                    [x0, y0]]]}
    return [
        {"geometry": sq(139.0, 35.0, 139.9, 35.9),
         "properties": {"kubun_id": 21, "area_classification_ja": "都市計画区域",
                        "city_name": "市街化市"}},
        {"geometry": sq(139.1, 35.1, 139.2, 35.2),
         "properties": {"kubun_id": 23,
                        "area_classification_ja": "市街化調整区域"}},
        {"geometry": sq(139.3, 35.3, 139.4, 35.4),
         "properties": {"kubun_id": 22, "area_classification_ja": "市街化区域"}},
    ]


def _with_fake_tile(fn):
    """_reinfolib_tile を差し替えて fn を呼ぶ（ネットワークに出ない）。"""
    from src import enrichment
    real = enrichment._reinfolib_tile
    enrichment._reinfolib_tile = lambda *a, **k: _urbanization_tile()
    try:
        return fn(enrichment)
    finally:
        enrichment._reinfolib_tile = real


def test_urbanization_reads_the_inner_polygon_not_the_outer_one():
    """外側の「都市計画区域」を飛ばして、22/23 を返すこと。"""
    def check(en):
        assert en.fetch_urbanization(35.15, 139.15, "key") == "市街化調整区域"
        assert en.fetch_urbanization(35.35, 139.35, "key") == "市街化区域"
    _with_fake_tile(check)


def test_urbanization_is_not_guessed_from_the_tile():
    """内包判定できなければ None。タイル代表値へは落とさない。

    ひとつのタイルには市街化区域と調整区域が同居する。用途地域と同じ
    ように代表値で埋めると、当てずっぽうで最大の減点を付けることになる。
    """
    def check(en):
        # 都市計画区域の中だが 22/23 のどちらでもない（非線引きなど）
        assert en.fetch_urbanization(35.8, 139.8, "key") is None
        # どの面にも入らない
        assert en.fetch_urbanization(34.0, 138.0, "key") is None
    _with_fake_tile(check)


def test_urbanization_only_reads_the_classification_field():
    """市区町村名などに紛れた「市街化」の字を拾わないこと。"""
    from src.enrichment import _extract_urbanization
    assert _extract_urbanization({"city_name": "市街化調整市"}) is None
    assert _extract_urbanization(
        {"area_classification_ja": "市街化調整区域"}) == "市街化調整区域"
    assert _extract_urbanization({"area_classification_ja": "都市計画区域"}) is None


def test_control_area_caps_the_risk_score():
    """市街化調整区域が渡れば、リスクは0.2上限（15点満点の3点以下）。"""
    from src.enrichment import HazardResult
    from src.scoring import score_risk
    clean = HazardResult(checked=True)     # ハザードの該当は無し
    assert score_risk("第一種住居地域", None, clean).raw >= 0.9
    cs = score_risk("第一種住居地域", "市街化調整区域", clean)
    assert cs.raw <= 0.2
    assert "市街化調整区域の可能性" in cs.minus
    assert "市街化調整区域" in cs.reason


def test_unknown_urbanization_leaves_the_score_as_it_was():
    """未取得なら、区域区分が無かったこれまでと同じ点数のままであること。"""
    from src.enrichment import HazardResult
    from src.scoring import score_risk
    for hz in (HazardResult(checked=True), HazardResult(checked=True,
               flood_rank=2, flood_label="0.5〜3.0m未満"), None):
        base = score_risk("第一種住居地域", None, hz)
        for u in (None, "", "市街化区域"):
            cs = score_risk("第一種住居地域", u, hz)
            assert cs.raw == base.raw, u
            assert cs.minus == base.minus, u
            assert "調整区域" not in cs.reason, u
    # 減点そのものが入っていないこと（0.2上限は掛からない）
    assert score_risk("第一種住居地域", None,
                      HazardResult(checked=True)).raw > 0.2


def test_unknown_urbanization_is_reported_not_filled_in():
    """取れなかったら None のまま。埋めずに「未取得」と言う。"""
    from src import enrichment
    real_tile = enrichment._reinfolib_tile
    real_shops = enrichment.fetch_shops_around
    enrichment._reinfolib_tile = lambda *a, **k: _urbanization_tile()
    enrichment.fetch_shops_around = lambda *a, **k: None
    try:
        # 都市計画区域の中だが区域区分は無い地点
        e = enrichment.enrich(35.8, 139.8, "key")
        assert e.urbanization is None
        assert any(n.startswith("区域区分：未取得") for n in e.notes), e.notes
        # 取れた地点では、未取得とは言わない
        e2 = enrichment.enrich(35.15, 139.15, "key")
        assert e2.urbanization == "市街化調整区域"
        assert not any("区域区分" in n for n in e2.notes), e2.notes
    finally:
        enrichment._reinfolib_tile = real_tile
        enrichment.fetch_shops_around = real_shops


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"[OK] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
