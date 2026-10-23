# -*- coding: utf-8 -*-
"""土地の形（旗竿地・不整形地）と、道路との高さ・傾き（2026-10-23）。

旗竿地でも、竿の幅（＝間口）が4m以上なら整形地と同じ扱い。未確認なら
減点しない（情報充足度だけ下げる）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import land_scoring as ls  # noqa: E402
from src.land import site_diagram  # noqa: E402
from src.models import LandSubject  # noqa: E402
from src.scoring import CategoryScore  # noqa: E402


def _cat(name="資産性", weight=10, raw=0.8):
    return CategoryScore(name, weight, raw, weight * raw, 0.9, "基準", [])


def _subj(**kw):
    return LandSubject(address="神奈川県平塚市", **kw)


# ---- 形 --------------------------------------------------------------------

def test_a_flag_lot_with_a_4m_pole_is_a_regular_lot():
    assert ls.effective_shape("flag", 4.0) == "regular"
    assert ls.effective_shape("flag", 3.99) == "flag"
    assert ls.effective_shape("flag", None) == "flag"
    c = ls.apply_shape(_cat(), _subj(land_shape="flag", frontage_m=4.0))
    assert c.raw == 0.8 and "整形地と同じ扱い" in c.reason


def test_shape_takes_the_set_points_off_asset_value():
    c = ls.apply_shape(_cat(), _subj(land_shape="flag", frontage_m=3.0))
    assert round(c.points, 1) == round(8.0 - ls.SHAPE_DEDUCT["flag"], 1)
    c = ls.apply_shape(_cat(), _subj(land_shape="irregular", frontage_m=10))
    assert round(c.points, 1) == round(8.0 - ls.SHAPE_DEDUCT["irregular"], 1)
    assert c.minus


def test_an_unknown_shape_is_not_marked_down():
    base = _cat()
    c = ls.apply_shape(base, _subj())
    assert c.raw == base.raw
    assert c.sufficiency < base.sufficiency
    assert "未確認" in c.reason


# ---- 高さ・傾き --------------------------------------------------------------

def test_level_takes_points_off_risk():
    base = _cat("リスク", 25, 0.8)
    for level, pts in ls.LEVEL_DEDUCT.items():
        c = ls.apply_level(base, _subj(land_level=level))
        assert round(c.points, 1) == round(20.0 - pts, 1), level
    assert ls.apply_level(base, _subj(land_level="flat")).raw == base.raw
    unk = ls.apply_level(base, _subj())
    assert unk.raw == base.raw and unk.sufficiency < base.sufficiency


def test_a_certified_retaining_wall_halves_the_level_deduction():
    base = _cat("リスク", 25, 0.8)
    subj = _subj(land_level="cliff")
    full = ls.apply_level(base, subj)
    back = ls.level_refund(full, subj, "certified")
    assert round(back.points, 1) == round(20.0 - ls.LEVEL_DEDUCT["cliff"] / 2, 1)
    assert ls.level_refund(full, subj, "uncertified").raw == full.raw
    # 敷地の中の傾斜は擁壁の話ではないので戻さない
    s2 = _subj(land_level="slope")
    assert ls.level_refund(ls.apply_level(base, s2), s2, "certified").raw \
        == ls.apply_level(base, s2).raw


def test_a_cliff_is_listed_as_a_risk_and_a_long_pole_as_a_check():
    confirm, risks = ls.shape_level_notes(
        _subj(land_shape="flag", land_level="cliff", pole_length_m=25))
    assert any(r.type == "高低差・がけ" for r in risks)
    assert any("20mを超えると幅3m以上" in c for c in confirm)
    confirm, risks = ls.shape_level_notes(_subj())
    assert not risks and len(confirm) == 2


def test_the_2m_line_is_the_one_in_the_grading_order():
    """宅地造成及び特定盛土等規制法施行令3条：切土で高さ2mを超える崖。"""
    assert ls.CLIFF_M == 2.0
    assert "2m" in dict(ls.LAND_LEVELS)["cliff"]
    assert "2m" in dict(ls.LAND_LEVELS)["step"]


# ---- 図 ----------------------------------------------------------------------

def test_a_flag_lot_is_drawn_with_its_pole_to_scale():
    d = site_diagram(150, 3.0, road_width_m=4, footprint_m2=60,
                     shape="flag", pole_length_m=15)
    assert d.pole is not None and d.pole_length_m == 15
    # 竿の幅と長さは同じ縮尺
    assert abs(d.pole[2] / d.scale - 3.0) < 0.1
    assert abs(d.pole[3] / d.scale - 15.0) < 0.1
    # 建築面積は奥の敷地の中に置く
    assert d.footprint[1] + d.footprint[3] <= d.lot[1] + d.lot[3] + 0.1
    # 竿の長さが無ければ、これまでどおり長方形
    assert site_diagram(150, 3.0, shape="flag").pole is None


# ---- 画面 --------------------------------------------------------------------

def _client():
    os.environ["SHINDAN_MOCK"] = "1"
    import app
    return app.app.test_client()


BASE = dict(address="神奈川県平塚市田村5丁目12-3", price="2500", area="130",
            budget="2200", road_width="5", frontage="8", road_type="公道")


def test_both_land_forms_ask_for_shape_and_level():
    c = _client()
    for path in ("/land", "/pro/land"):
        h = c.get(path).get_data(as_text=True)
        assert 'name="land_shape"' in h and 'name="land_level"' in h, path
        assert 'name="pole_length"' in h and "LAND_SHAPE_FIELDS" not in h


def test_the_result_moves_with_the_shape():
    import re
    c = _client()

    def total(**kw):
        h = c.post("/land_diagnose", data=dict(BASE, **kw)).get_data(as_text=True)
        return int(re.search(r"総合 (\d+)点", h).group(1)), h

    t0, _ = total()
    t_wide, _ = total(land_shape="flag", frontage="4")
    t_narrow, h = total(land_shape="flag", frontage="3", pole_length="15")
    assert t_wide == t0
    assert t_narrow < t0
    assert "竿の長さ" in h


def test_a_bad_value_is_treated_as_unknown():
    import app
    subj, *_ = app._land_subject_from(dict(BASE, land_shape="castle",
                                           land_level="moon", pole_length="-3"))
    assert subj.land_shape == "unknown" and subj.land_level == "unknown"
    assert subj.pole_length_m is None


def test_the_land_comparison_lists_what_land_pro_adds():
    """2026-10-23。土地の無料とPROの比較表を、土地のPROの中身に合わせた。"""
    import re
    import app
    v = app._land_example_v()
    v.update(BASE)
    h = _client().post("/land_diagnose", data=v).get_data(as_text=True)
    i = h.find("無料でここまで／PROでここまで")
    assert i > 0
    card = re.sub(r"<[^>]+>", " ", h[i:h.find("</table>", i)])
    for word in ("現地と書類の答えを点数に反映", "建てられる広さの計算し直し",
                 "条件を揃えた成約の分布", "つなぎ融資の利息", "総額の積み上げ",
                 "手取り・教育費で見た返済", "着工までの手続き", "結果の保存"):
        assert word in card, word
    assert "契約前チェック（PDF）" in card


# ---- 土地の契約前チェック（2026-10-23）-----------------------------------

def _land_pro_html():
    import app
    v = app._land_pro_defaults()
    v.update(BASE, settlement="2027-01-15", completion="2027-08-31",
             bridge_rate="2.0", own_funds="300")
    return _client().post("/pro/land", data=v).get_data(as_text=True)


def test_land_pro_makes_a_pre_contract_check_pdf():
    import io
    import re
    import pytest
    pdfplumber = pytest.importorskip("pdfplumber")
    h = _land_pro_html()
    m = re.search(r'action="/pro/land/check.pdf">\s*<input type="hidden" '
                  r'name="dx" value="([^"]+)"', h)
    assert m, "PROの結果に、作るボタンが無い"
    r = _client().post("/pro/land/check.pdf", data={"dx": m.group(1)})
    assert r.mimetype == "application/pdf"
    with pdfplumber.open(io.BytesIO(r.data)) as pdf:
        t = "\n".join((p.extract_text() or "") for p in pdf.pages)
    for word in ("契約前チェック", "確認すること", "回答・メモ", "総額の積み上げ",
                 "つなぎ融資", "いつ、いくら要るか", "住宅ローン本体"):
        assert word in t, word
    assert "未定" in t


def test_a_tampered_or_foreign_snapshot_makes_no_land_pdf():
    import app
    c = _client()
    r = c.post("/pro/land/check.pdf", data={"dx": "broken"})
    assert r.status_code == 302
    house = app.sign_snapshot({"title": "戸建", "total": 70})
    assert c.post("/pro/land/check.pdf", data={"dx": house}).status_code == 302
