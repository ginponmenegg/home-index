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
