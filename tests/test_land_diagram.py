# -*- coding: utf-8 -*-
"""敷地の図。

「前面道路4.0m」「間口9.0m」が、どれくらいなのかは、何度も土地を見た人に
しか浮かばない。買う人はたいてい初めてなので、縮尺を合わせて絵にする。

守ること2つ。
・道路・敷地・建築面積はすべて同じ縮尺。道路だけ別の縮尺にすると、
  4mか6mかを見る意味が無くなる。
・形は分かっていない。面積と間口から割り戻した長方形にすぎないので、
  断定しないための注記を消さない。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
from src.land import BAND_MIN_H, site_diagram  # noqa: E402

FORM = dict(address="神奈川県小田原市南町1-1", price="2100", area="125",
            budget="2600", household="4", road_width="4.0", road_type="公道",
            frontage="9.0", station="12", coverage="60", far="200",
            city="14206", district="南町", income="800", down="500",
            loan_years="35", condition="none")


def _page(**kw):
    os.environ["SHINDAN_MOCK"] = "1"
    c = webapp.app.test_client()
    return c.post("/land_diagnose", data=dict(FORM, **kw)).get_data(as_text=True)


def _svg(html):
    m = re.search(r'<svg class="fig".*?</svg>', html, re.S)
    return m.group(0) if m else None


# ---- 形 -------------------------------------------------------------------

def test_the_depth_is_the_area_divided_by_the_frontage():
    d = site_diagram(125.0, 9.0)
    assert d.depth_m == round(125.0 / 9.0, 1) == 13.9


def test_nothing_is_drawn_without_an_area_or_a_frontage():
    """無いものを線で埋めると、「分からない」が「こういう形」に化ける。"""
    assert site_diagram(None, 9.0) is None
    assert site_diagram(125.0, None) is None
    assert site_diagram(125.0, 0) is None
    assert site_diagram(0, 9.0) is None


def test_the_lot_keeps_its_proportions():
    d = site_diagram(125.0, 9.0)
    _x, _y, w, h = d.lot
    assert abs((w / h) - (9.0 / d.depth_m)) < 0.01


# ---- 縮尺 -----------------------------------------------------------------

def test_the_road_is_drawn_at_the_same_scale_as_the_lot():
    """道路だけ別の縮尺で描くと、4mか6mかを見る意味が無くなる。"""
    for road_m in (3.0, 4.0, 6.0, 9.0):
        d = site_diagram(125.0, 9.0, road_width_m=road_m)
        assert abs(d.road[3] - road_m * d.scale) < 0.2, road_m


def test_a_wider_road_is_drawn_wider():
    a = site_diagram(125.0, 9.0, road_width_m=4.0)
    b = site_diagram(125.0, 9.0, road_width_m=6.0)
    assert b.road[3] > a.road[3]
    assert abs(b.road[3] / a.road[3] - 6.0 / 4.0) < 0.01


def test_the_building_box_holds_the_right_share_of_the_lot():
    d = site_diagram(125.0, 9.0, footprint_m2=75.0)
    lot_px = d.lot[2] * d.lot[3]
    box_px = d.footprint[2] * d.footprint[3]
    assert abs(box_px / lot_px - 75.0 / 125.0) < 0.01


def test_the_setback_band_is_as_deep_as_the_law_makes_it():
    """4m未満の道は、道の中心から2mまで下がる。0.5m下がるなら0.5m分の帯。"""
    d = site_diagram(125.0, 9.0, road_width_m=3.0, setback_m2=4.5)
    assert d.setback_depth_m == 0.5
    assert abs(d.setback[3] - 0.5 * d.scale) < 0.2
    # 帯は敷地の道路側（下端）に置く
    assert abs((d.setback[1] + d.setback[3]) - (d.lot[1] + d.lot[3])) < 0.2


def test_a_very_thin_band_is_still_visible():
    d = site_diagram(400.0, 20.0, road_width_m=3.9, setback_m2=1.0)
    assert d.setback[3] >= BAND_MIN_H


# ---- 描かないもの ---------------------------------------------------------

def test_the_road_is_left_out_when_its_width_is_unknown():
    d = site_diagram(125.0, 9.0)
    assert d.road is None
    assert d.height < site_diagram(125.0, 9.0, road_width_m=6.0).height


def test_the_building_box_is_left_out_without_a_coverage_ratio():
    assert site_diagram(125.0, 9.0, footprint_m2=None).footprint is None


def test_a_footprint_bigger_than_the_lot_is_refused():
    """建ぺい率100%超の入力で、敷地からはみ出した箱を描かない。"""
    assert site_diagram(125.0, 9.0, footprint_m2=200.0).footprint is None


def test_everything_fits_inside_the_canvas():
    for kw in (dict(), dict(road_width_m=12.0),
               dict(road_width_m=3.0, setback_m2=4.5, footprint_m2=70.0)):
        d = site_diagram(125.0, 9.0, **kw)
        bottom = d.road[1] + d.road[3] if d.road else d.lot[1] + d.lot[3]
        assert bottom <= d.height, kw
        assert d.lot[0] >= 0 and d.lot[0] + d.lot[2] <= d.width


# ---- 画面 -----------------------------------------------------------------

def test_the_diagram_reaches_the_land_result():
    svg = _svg(_page())
    assert svg, "図が出ていない"
    assert svg.count("<rect") == 3, "道路・敷地・建築面積の3つ"
    assert "{{" not in svg, "テンプレートが展開されていない"


def test_the_setback_shows_up_as_a_band():
    svg = _svg(_page(road_width="3.0"))
    assert "fg-lost" in svg
    assert "道の中心から 2m" in svg


def test_the_page_says_the_shape_is_not_known():
    """縮尺は本物でも、形は割り戻した長方形にすぎない。"""
    html = _page()
    assert "土地の形は分かっていません" in html
    assert "実際の形・向き・建物の置き場所は違います" in html
    assert "どこに建てるかではありません" in html


def test_the_diagram_disappears_when_the_frontage_is_missing():
    assert _svg(_page(frontage="")) is None


def test_the_diagram_can_be_read_without_seeing_it():
    svg = _svg(_page(road_width="3.0"))
    m = re.search(r'aria-label="([^"]+)"', svg)
    assert m, "読み上げ用の説明が無い"
    label = m.group(1)
    for word in ("間口", "奥行き", "前面道路", "形は分かっていない"):
        assert word in label, word


def test_the_setback_label_cannot_run_off_the_edge():
    """スマホでは字だけ大きくするので、左から置くと枠からはみ出す。

    右端に寄せて、そこから左へ伸ばす。
    """
    svg = _svg(_page(road_width="3.0"))
    i = svg.index("道の中心から 2m")
    assert 'text-anchor="end"' in svg[max(0, i - 200):i]


def test_the_phone_gets_bigger_letters_in_the_diagram():
    """viewBox ごと縮むので、スマホでは字が7pxくらいになってしまう。"""
    css = webapp.LAND_RESULT
    i = css.index(".fg-warn{")
    assert ".fg-t,.fg-n,.fg-bn{font-size:20px}" in css[i:i + 400]


def test_a_narrow_lot_moves_the_number_out_of_the_box():
    """間口が狭いと箱も細い。中に数字を置くと、左の「奥行き 31.2 m」と
    隣り合って一つの数に見える。
    """
    assert site_diagram(125.0, 9.0, footprint_m2=75.0).footprint_number_inside
    narrow = site_diagram(125.0, 4.0, footprint_m2=75.0)
    assert not narrow.footprint_number_inside

    svg = _svg(_page(frontage="4.0"))
    assert "■ 建築面積の上限 " in svg, "右上の札に数字を出す"
    assert 'class="fg-bn"' not in svg, "箱の中には置かない"
