# -*- coding: utf-8 -*-
"""土地に何が建てられるか。建築基準法の計算。ネットワーク不要。

条文は e-Gov の法令API（建築基準法 325AC0000000201）で確認したもの。
第52条第2項の原文：

  「前面道路（前面道路が二以上あるときは、その幅員の最大のもの）の幅員が
   十二メートル未満である建築物の容積率は、当該前面道路の幅員のメートルの
   数値に、次の各号に掲げる区分に従い、当該各号に定める数値を乗じたもの
   以下でなければならない」

ここを間違えると、建てられない家を建てられると言うことになる。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.land import (build_capacity, far_multiplier, meets_road_requirement,
                      setback_area_m2, tsubo)

# 用途地域13種。十分の四になるのは住居系の8つ。
FOUR_TENTHS = ("第一種低層住居専用地域", "第二種低層住居専用地域", "田園住居地域",
               "第一種中高層住居専用地域", "第二種中高層住居専用地域",
               "第一種住居地域", "第二種住居地域", "準住居地域")
SIX_TENTHS = ("近隣商業地域", "商業地域", "準工業地域", "工業地域", "工業専用地域")


def test_the_multiplier_follows_the_use_district():
    for ud in FOUR_TENTHS:
        assert far_multiplier(ud) == 0.4, ud
    for ud in SIX_TENTHS:
        assert far_multiplier(ud) == 0.6, ud


def test_a_narrow_road_caps_the_floor_area_ratio():
    """指定容積率200%でも、前面道路4mなら160%までしか使えない。

    広告に出ているのは指定容積率のほう。ここで落ちることを知らない人が多い。
    """
    c = build_capacity(120, 60, 200, "第一種中高層住居専用地域", road_width_m=4.0)
    assert c.road_far == 160
    assert c.effective_far == 160
    assert c.far_limited_by_road
    assert c.max_total_floor_m2 == 192.0      # 120 × 1.60


def test_a_wide_road_does_not_cap_anything():
    c = build_capacity(120, 60, 200, "第一種中高層住居専用地域", road_width_m=6.0)
    assert c.road_far == 240                  # 6.0 × 0.4
    assert c.effective_far == 200             # 指定のほうが小さい
    assert not c.far_limited_by_road


def test_twelve_metres_or_more_has_no_road_limit():
    """第52条第2項は「十二メートル未満である建築物」にかかる。"""
    c = build_capacity(120, 60, 200, "第一種住居地域", road_width_m=12.0)
    assert c.road_far is None
    assert c.effective_far == 200


def test_the_commercial_multiplier_is_six_tenths():
    c = build_capacity(100, 80, 400, "商業地域", road_width_m=6.0)
    assert c.road_far == 360                  # 6.0 × 0.6。切り捨てで359にしない
    assert c.effective_far == 360


def test_a_road_under_four_metres_eats_into_the_site():
    """セットバックした分は敷地として使えない。"""
    lost = setback_area_m2(120, 8.0, 2.7)
    assert lost == round((4.0 - 2.7) / 2 * 8.0, 1) == 5.2
    c = build_capacity(120, 60, 200, "第一種住居地域",
                       road_width_m=2.7, frontage_m=8.0)
    assert c.setback_m2 == 5.2
    # 使えるのは 120 - 5.2 = 114.8㎡ のほう
    assert c.max_footprint_m2 == round(114.8 * 0.6, 1)


def test_setback_is_not_guessed_without_the_frontage():
    """間口が分からなければ、後退面積は出さない。推定で埋めない。"""
    assert setback_area_m2(120, None, 2.7) is None
    c = build_capacity(120, 60, 200, "第一種住居地域", road_width_m=2.7)
    assert c.setback_m2 is None
    assert c.max_footprint_m2 == 72.0         # 敷地全体で計算されている


def test_nothing_is_invented_when_the_inputs_are_missing():
    c = build_capacity(120, None, None, None)
    assert c.max_footprint_m2 is None
    assert c.max_total_floor_m2 is None
    assert c.effective_far is None
    assert c.notes == []


def test_the_road_requirement_is_four_metres():
    assert meets_road_requirement(4.0) is True
    assert meets_road_requirement(3.9) is False
    assert meets_road_requirement(None) is None, "分からないものを偽にしない"


def test_tsubo_is_there_because_meetings_happen_in_tsubo():
    assert tsubo(33.0578) == 10.0
    assert tsubo(None) is None


# ---- 上限を許可と読み替えない ----
def test_a_road_width_on_its_own_never_grants_floor_area():
    """法52条2項は「これを超えてはならない」であって「これだけ建ててよい」
    ではない。

    指定容積率が取れなかった土地で道路の数値だけを採ると、幅員6mの道に
    接しているというだけで「容積率360%・延床432㎡」と出てしまう。
    120㎡の土地に43坪×3の家が建つと言うことになる。
    """
    c = build_capacity(120.0, None, None, None, road_width_m=6.0,
                       frontage_m=8.0)
    assert c.road_far is not None          # 上限そのものは出す
    assert c.effective_far is None         # 使ってよい容積率にはしない
    assert c.max_total_floor_m2 is None    # だから延床も出さない
    assert any("指定容積率が分からない" in n for n in c.notes)


def test_an_unknown_use_district_takes_the_stricter_multiplier():
    # 乗数は上限を決めるもの。大きいほうを当てると、建てられない家を
    # 建てられると言うことになる。分からないときは厳しいほうへ。
    assert far_multiplier(None) == 0.4
    assert far_multiplier("商業地域") == 0.6
    assert far_multiplier("第一種住居地域") == 0.4


def test_a_designated_ratio_still_wins_when_the_road_is_generous():
    # 幅員6m・住居系なら道路の上限は240%。指定200%のほうが小さいので200%。
    c = build_capacity(120.0, 60, 200, "第一種住居地域", road_width_m=6.0)
    assert c.road_far == 240
    assert c.effective_far == 200
    assert c.far_limited_by_road is False


# ---- 建ぺい率の緩和（法53条3項・6項。e-Gov で条文を確認）----
def test_the_corner_relaxation_needs_the_designation_not_just_a_corner():
    """法53条3項2号は「街区の角にある敷地…で**特定行政庁が指定するもの**」。

    角地であることと、指定されていることは別。指定されていない角地に
    10%を足すと、建てられない家を建てられると言うことになる。
    """
    from src.land import coverage_with_relaxations
    assert coverage_with_relaxations(60, corner_designated=True)[0] == 70
    assert coverage_with_relaxations(60, corner_designated=False)[0] == 60
    assert coverage_with_relaxations(60, corner_designated=None)[0] == 60


def test_the_fire_relaxation_adds_ten_as_well():
    from src.land import coverage_with_relaxations
    assert coverage_with_relaxations(60, fire_relaxation=True)[0] == 70


def test_both_relaxations_add_twenty():
    # 「第一号及び第二号に該当する建築物にあつては…十分の二を加えたもの」
    from src.land import coverage_with_relaxations
    got, notes = coverage_with_relaxations(60, corner_designated=True,
                                           fire_relaxation=True)
    assert got == 80
    assert len(notes) == 2


def test_an_eighty_percent_zone_with_fireproofing_has_no_limit():
    # 法53条6項1号。建蔽率の限度が十分の八の地域＋防火地域の耐火建築物等
    from src.land import coverage_with_relaxations
    got, notes = coverage_with_relaxations(80, fire_relaxation=True)
    assert got == 100
    assert any("53条6項" in n for n in notes)


def test_the_relaxation_shows_up_in_the_buildable_area():
    plain = build_capacity(120.0, 60, 200, "第一種住居地域", road_width_m=6.0)
    corner = build_capacity(120.0, 60, 200, "第一種住居地域", road_width_m=6.0,
                            corner_designated=True)
    assert plain.max_footprint_m2 == 72.0
    assert corner.max_footprint_m2 == 84.0
    # 指定の値も残す。緩和後だけ出すと、何%の土地なのか分からなくなる。
    assert corner.designated_coverage == 60
    assert corner.coverage_ratio == 70
    assert corner.coverage_relaxed is True
    assert plain.coverage_relaxed is False


# ---- 容積率を使い切るのに要る階数 ----
def test_how_many_floors_the_far_actually_needs():
    """建ぺい60%・容積200%の土地は、4階建てにしないと容積を使い切れない。

    注文住宅は2階建てが多いので、広告の容積率をそのまま「建てられる広さ」
    と読むと、実際よりずっと大きく見える。
    """
    from src.land import floors_to_use_far
    assert floors_to_use_far(240.0, 72.0) == 4       # 200 ÷ 60 = 3.33
    assert floors_to_use_far(120.0, 60.0) == 2       # ちょうど2階
    assert floors_to_use_far(None, 72.0) is None

    c = build_capacity(120.0, 60, 200, "第一種住居地域", road_width_m=6.0)
    assert c.floors_to_use_far == 4
    assert any("4階建てが要ります" in n for n in c.notes)


def test_a_low_rise_zone_carries_the_absolute_height_note():
    # 法55条。第一種・第二種低層住居専用地域と田園住居地域は10mか12m。
    c = build_capacity(120.0, 50, 100, "第一種低層住居専用地域", road_width_m=6.0)
    assert any("10mまたは12m" in n for n in c.notes)
    plain = build_capacity(120.0, 60, 200, "第一種住居地域", road_width_m=6.0)
    assert not any("10mまたは12m" in n for n in plain.notes)


# ---- 坪と㎡ ----
def test_one_tsubo_is_four_hundred_over_one_hundred_twenty_one():
    """計量法の尺貫法の換算そのもの。丸めた値をあちこちに散らさない。"""
    from src.land import M2_PER_TSUBO
    assert M2_PER_TSUBO == 400.0 / 121.0
    assert abs(M2_PER_TSUBO - 3.305785) < 1e-6


def test_converting_to_square_metres():
    from src.land import to_m2
    assert to_m2(120, "m2") == 120
    assert abs(to_m2(50, "tsubo") - 165.289) < 0.01
    assert to_m2(None, "tsubo") is None
    # 知らない単位は㎡として扱う（勝手に大きくしない）
    assert to_m2(120, "でたらめ") == 120


def test_the_round_trip_holds():
    from src.land import to_m2, tsubo
    for t in (30.0, 36.3, 50.0, 100.0):
        assert tsubo(to_m2(t, "tsubo")) == t
