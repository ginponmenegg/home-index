# -*- coding: utf-8 -*-
"""土地の採点。ネットワーク不要。

配点は 建てられる家25／接道15／リスク25／立地15／資産性10／資金10。
価格は点数に入れない（㎡単価のばらつきが大きく、点にする精度が出ない）。

居住面積水準は住生活基本計画（全国計画）の公表値。出典で確認したもので、
私の感覚ではない。ここを勝手に動かすと、狭い土地に合格点が出る。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import CONFIG
from src.land import build_capacity
from src.land_scoring import (BUILDABLE_PARTS, ROAD_PARTS, SCORE_CAP_REBUILD,
                              SCORE_CAP_URBANIZATION, build_land_diagnosis,
                              floor_area_raw, footprint_raw, frontage_raw,
                              guided_area_m2, minimum_area_m2, score_buildable,
                              score_cap, score_road, use_district_raw)
from src.models import LandSubject


# ---- 配点そのもの ----
def test_the_six_categories_add_up_to_one_hundred():
    assert sum(CONFIG["land_category_weights"].values()) == 100


def test_the_two_new_categories_add_up_to_their_headline():
    assert sum(BUILDABLE_PARTS.values()) == CONFIG["land_category_weights"]["建てられる家"]
    assert sum(ROAD_PARTS.values()) == CONFIG["land_category_weights"]["接道"]
    assert ROAD_PARTS["幅員"] == 7
    assert ROAD_PARTS["接道長さ"] == 4
    assert ROAD_PARTS["道路の種類"] == 4


# ---- 居住面積水準（公表値そのもの）----
def test_the_published_area_standards_for_a_family_of_four():
    assert guided_area_m2(4) == 125          # 一般型 25×4+25
    assert guided_area_m2(4, urban=True) == 95   # 都市居住型 20×4+15
    assert minimum_area_m2(4) == 50          # 最低 10×4+10


def test_a_single_person_has_its_own_figures():
    assert guided_area_m2(1) == 55
    assert guided_area_m2(1, urban=True) == 40
    assert minimum_area_m2(1) == 25


def test_a_household_over_four_gets_five_percent_taken_off():
    # 25×5+25 = 150 の5%控除で 142.5
    assert guided_area_m2(5) == 142.5
    assert guided_area_m2(4) == 125          # 4人ちょうどは控除しない


def test_no_household_size_means_four_people():
    assert guided_area_m2(None) == guided_area_m2(4)


# ---- 延床は段ではなく坂 ----
def test_one_square_metre_does_not_change_the_score_by_a_step():
    a = floor_area_raw(124, 4)
    b = floor_area_raw(125, 4)
    assert b == 1.0
    assert 0.98 <= a < b                     # 崖ではなく坂


def test_the_floor_area_score_follows_the_standards():
    assert floor_area_raw(125, 4) == 1.0     # 誘導水準に届く
    assert floor_area_raw(95, 4) == 0.75     # 都市居住型の水準
    assert floor_area_raw(50, 4) == 0.35     # 最低水準ちょうど
    assert floor_area_raw(25, 4) < 0.2       # 最低水準の半分
    assert floor_area_raw(None) is None


def test_a_bigger_household_needs_a_bigger_house_for_the_same_score():
    assert floor_area_raw(100, 2) == 1.0     # 2人なら75㎡で誘導水準
    assert floor_area_raw(100, 5) < 1.0      # 5人には足りない


# ---- 建築面積・用途地域・間口 ----
def test_a_small_ground_floor_loses_points():
    assert footprint_raw(60) == 1.0
    assert footprint_raw(40) == 0.6
    assert footprint_raw(25) < 0.3
    assert footprint_raw(None) is None


def test_the_use_district_table_reads_the_narrower_name_first():
    # 「近隣商業」は「商業」を含み、「工業専用」は「工業」を含む。
    # 前から順に当てると、近隣商業が商業に、工業専用が工業に化ける。
    assert use_district_raw("近隣商業地域") == 0.5
    assert use_district_raw("商業地域") == 0.35
    assert use_district_raw("工業専用地域") == 0.0
    assert use_district_raw("工業地域") == 0.2
    assert use_district_raw("第一種低層住居専用地域") == 1.0
    assert use_district_raw(None) is None


def test_a_flagpole_lot_is_marked_down_on_frontage():
    assert frontage_raw(6) == 1.0
    assert frontage_raw(2.0) == 0.1
    assert frontage_raw(None) is None


# ---- 建てられる家 ----
def _capacity(width, site=120.0, frontage=8.0):
    """指定建ぺい60%・指定容積200%・第一種中高層住居専用地域の土地。"""
    return build_capacity(site, 60, 200, "第一種中高層住居専用地域",
                          road_width_m=width, frontage_m=frontage)


def test_the_road_eats_into_the_house_you_can_build():
    # 指定200%の土地でも、前面道路が4mなら 4×0.4＝160% までしか使えない。
    wide = _capacity(6.0)
    narrow = _capacity(4.0)
    assert wide.max_total_floor_m2 == 240.0
    assert narrow.max_total_floor_m2 == 192.0
    assert narrow.far_limited_by_road is True

    a = score_buildable(wide, "第一種中高層住居専用地域", 8.0, 4)
    b = score_buildable(narrow, "第一種中高層住居専用地域", 8.0, 4)
    assert a.points == b.points              # どちらも誘導水準125㎡を超える
    assert any("前面道路の制限" in m for m in b.minus)


def test_a_two_point_seven_metre_road_shows_up_as_a_smaller_house():
    tight = _capacity(2.7)
    assert tight.setback_m2 == 5.2
    assert tight.max_total_floor_m2 < 125     # 4人の誘導水準に届かない
    c = score_buildable(tight, "第一種中高層住居専用地域", 8.0, 4)
    assert c.points < score_buildable(_capacity(6.0),
                                      "第一種中高層住居専用地域", 8.0, 4).points
    assert any("セットバック" in m for m in c.minus)


def test_an_unanswered_frontage_costs_confidence_not_points():
    known = score_buildable(_capacity(6.0), "第一種中高層住居専用地域", 8.0, 4)
    blank = score_buildable(_capacity(6.0), "第一種中高層住居専用地域", None, 4)
    narrow = score_buildable(_capacity(6.0), "第一種中高層住居専用地域", 2.0, 4)
    assert blank.sufficiency < known.sufficiency
    # 未入力は「狭い間口」ではない。分からない項目は平均の側に寄せ、
    # 分かっている項目だけで点をつける。
    assert blank.raw > narrow.raw
    assert abs(blank.raw - known.raw) < 0.05


def test_nothing_known_says_what_to_type_instead_of_scoring():
    c = score_buildable(build_capacity(None, None, None))
    assert c.sufficiency == 0.0
    assert "敷地面積" in c.reason


# ---- 接道 ----
def test_a_clean_frontage_takes_the_full_fifteen():
    c = score_road(road_width_m=6.0, road_contact_m=5.0, road_type="公道")
    assert c.points == 15.0
    assert c.sufficiency == 1.0


def test_the_contact_length_is_scored_in_three_bands():
    def pts(m):
        base = score_road(road_width_m=6.0, road_contact_m=m, road_type="公道")
        return base.points - 11.0            # 幅員7＋種類4を引いた残り
    assert pts(5.0) == 4.0                   # 4m以上
    assert pts(2.5) == 2.0                   # 法の2mは満たすが余裕がない
    assert pts(1.5) == 0.0                   # 法43条に足りない


def test_an_unknown_contact_length_lands_in_the_middle_and_is_flagged():
    c = score_road(road_width_m=6.0, road_contact_m=None, road_type="公道")
    assert c.points == 13.0                  # 7 + 2 + 4
    assert c.sufficiency < 1.0
    assert "接道の長さが未確認" in c.reason


def test_a_private_road_keeps_the_point_and_says_what_to_check():
    c = score_road(road_width_m=6.0, road_contact_m=5.0, road_type="私道")
    assert c.points == 13.0                  # 種類が4→2
    assert any("掘削" in m for m in c.minus)


def test_a_narrow_road_is_marked_as_a_setback_not_as_unbuildable():
    c = score_road(road_width_m=3.0, road_contact_m=5.0, road_type="公道")
    assert c.points == 10.5                  # 幅員 7→2.5
    assert any("セットバック" in m for m in c.minus)


# ---- 頭打ち ----
def test_a_narrow_road_on_its_own_does_not_cap_the_score():
    # 幅員4m未満でも、42条2項の道路ならセットバックして建て替えられる。
    assert score_cap(road_width_m=3.0, road_contact_m=5.0,
                     road_type="公道") is None


def test_too_little_frontage_caps_the_whole_score():
    cap = score_cap(road_width_m=6.0, road_contact_m=1.5, road_type="公道")
    assert cap is not None
    assert cap.limit == SCORE_CAP_REBUILD == 30
    assert any("43条" in r for r in cap.reasons)


def test_no_road_at_all_caps_the_whole_score():
    cap = score_cap(road_type="none")
    assert cap.limit == 30


def test_an_industrial_exclusive_zone_caps_the_whole_score():
    cap = score_cap(road_width_m=6.0, road_contact_m=5.0, road_type="公道",
                    use_district="工業専用地域")
    assert cap.limit == 30


def test_an_urbanisation_control_area_caps_it_more_gently():
    # 既存宅地・分家住宅・条例指定区域など、建てられる例外が実際に多い。
    cap = score_cap(road_width_m=6.0, road_contact_m=5.0, road_type="公道",
                    urbanization="市街化調整区域")
    assert cap.limit == SCORE_CAP_URBANIZATION == 50


# ---- 全体 ----
def _subject(**kw):
    base = dict(address="千葉県船橋市前原西1-1-1", price=30_000_000,
                land_area_m2=120.0, building_budget=25_000_000,
                household_size=4, frontage_m=8.0, road_width_m=6.0,
                road_contact_m=8.0, road_type="公道", station_walk_min=8)
    base.update(kw)
    return LandSubject(**base)


def _diagnose(**kw):
    subj = _subject(**kw)
    cap = build_capacity(subj.land_area_m2, 60, 200, "第一種中高層住居専用地域",
                         road_width_m=subj.road_width_m,
                         frontage_m=subj.frontage_m)
    return build_land_diagnosis(subj, cap,
                                use_district="第一種中高層住居専用地域")


def test_the_land_price_never_moves_the_score():
    cheap = _diagnose(price=10_000_000)
    dear = _diagnose(price=90_000_000)
    assert cheap.total_score == dear.total_score
    assert "価格" not in [c.name for c in cheap.categories]
    assert "点数に入れていません" in cheap.comment


def test_the_categories_are_the_six_we_agreed():
    d = _diagnose()
    assert [c.name for c in d.categories] == [
        "建てられる家", "接道", "リスク", "立地", "資産性", "資金"]
    assert [c.weight for c in d.categories] == [25, 15, 25, 15, 10, 10]


def test_an_unbuildable_plot_is_capped_not_merely_docked():
    ok = _diagnose()
    bad = _diagnose(road_contact_m=1.5)
    assert ok.total_score > 30
    assert bad.total_score <= SCORE_CAP_REBUILD
    assert "上限としています" in bad.comment
    assert any(r.type == "建てられない可能性" for r in bad.critical_risks)
    # 43条2項の認定・許可という逃げ道があることを、要確認の先頭で伝える。
    assert "43条2項" in bad.to_confirm[0]


def test_the_control_area_warning_points_at_the_right_desk():
    subj = _subject()
    cap = build_capacity(120.0, 60, 200, "第一種中高層住居専用地域",
                         road_width_m=6.0, frontage_m=8.0)
    d = build_land_diagnosis(subj, cap,
                             use_district="第一種中高層住居専用地域",
                             urbanization="市街化調整区域")
    assert d.total_score <= SCORE_CAP_URBANIZATION
    assert "都市計画課" in d.to_confirm[0]


def test_a_good_plot_still_scores_well():
    d = _diagnose()
    assert d.total_score >= 55
    assert d.grade in ("A", "B", "C")
