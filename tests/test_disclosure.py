# -*- coding: utf-8 -*-
"""重要事項説明書の読み合わせシート。ネットワーク不要。

このシートは宅地建物取引士が作っているという前提で読まれる。だから
守るべきことが3つある。

・**条文の番号が正しいこと。** 現場で「そんな欄はありません」と言われたら、
  そこで信用が終わる。番号は e-Gov 法令検索で確かめたものだけを載せる
・**重説に書かれないものを、書かれるものとして並べないこと。** 滞納額は
  重要事項説明の記載事項ではない
・**良し悪しを書かないこと。** ここが出すのは「どこに何が書かれるか」まで。
  買ってよいかは書かない
"""
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import disclosure
from src.models import MansionSubject, SubjectProperty


def _enr(**kw):
    hz = SimpleNamespace(sediment=None, flood_rank=None, tsunami=False,
                         storm_surge=False, steep_slope=False,
                         landslide_zone=False, danger_zone=None,
                         embankment=None)
    for k, v in kw.items():
        setattr(hz, k, v)
    return SimpleNamespace(hazard=hz, urbanization=None)


def _house(**kw):
    base = dict(property_type="chuko_kodate", address="東京都〇〇",
                price=34_800_000, build_year=2010)
    base.update(kw)
    return SubjectProperty(**base)


def _condo(**kw):
    base = dict(address="東京都〇〇", price=48_000_000, build_year=2010,
                exclusive_area_m2=70.0)
    base.update(kw)
    return MansionSubject(**base)


def _laws(points):
    return " / ".join(p.law for p in points)


# ---- 条文の番号 -----------------------------------------------------------

def test_every_citation_is_shaped_like_a_real_reference():
    """条・項・号まで書く。担当者に見せてそのまま通じる形にする。"""
    ok = re.compile(r"^(法第\d+条第\d+項第\d+号(の\d+)?|規則第16条の(2|4の3)第"
                    r"\d+号(の\d+)?|重要事項調査報告書)$")
    for p in disclosure.sheet(_condo(), _enr(), "chuko_mansion"):
        assert ok.match(p.law), p.law


def test_the_hazard_items_cite_the_ordinance_not_the_act():
    """区域内である旨は、法ではなく規則第16条の4の3が定めている。"""
    pts = disclosure.sheet(
        _house(), _enr(sediment="特別警戒区域", flood_rank=3, tsunami=True),
        "chuko_kodate")
    laws = _laws(pts)
    assert "規則第16条の4の3第2号" in laws, "土砂災害警戒区域"
    assert "規則第16条の4の3第3号の2" in laws, "水害ハザードマップ"
    assert "規則第16条の4の3第3号" in laws, "津波災害警戒区域"


def test_the_condo_items_cite_the_ordinance_for_article_six():
    """区分所有建物の中身は、法第35条第1項第6号を受けた規則第16条の2。"""
    laws = _laws(disclosure.sheet(_condo(), _enr(), "chuko_mansion"))
    for n in ("第1号", "第3号", "第6号", "第7号", "第10号"):
        assert f"規則第16条の2{n}" in laws, n


# ---- 出す・出さないの切り分け ---------------------------------------------

def test_nothing_is_listed_for_a_zone_the_property_is_not_in():
    """該当していない区域の欄を並べない。絞った意味が消える。"""
    pts = disclosure.sheet(_house(), _enr(), "chuko_kodate")
    joined = " ".join(p.where for p in pts)
    for word in ("土砂災害", "津波", "水害ハザードマップ", "急傾斜地"):
        assert word not in joined, word


def test_a_house_gets_no_condominium_items():
    joined = " ".join(p.where for p in disclosure.sheet(
        _house(), _enr(), "chuko_kodate"))
    assert "修繕積立金" not in joined
    assert "専有部分" not in joined


def test_a_new_build_is_not_asked_for_an_inspection_of_an_existing_home():
    """建物状況調査は既存建物の欄。新築には出さない。"""
    joined = " ".join(p.where for p in disclosure.sheet(
        _house(build_year=2026), _enr(), "shinchiku_kodate"))
    assert "建物状況調査" not in joined


def test_an_old_building_is_asked_about_the_seismic_assessment():
    joined = " ".join(p.where for p in disclosure.sheet(
        _condo(build_year=1975), _enr(), "chuko_mansion"))
    assert "耐震診断" in joined
    assert "石綿" in joined, "2006年以前は石綿の調査結果も対象"


def test_a_recent_building_is_not_asked_about_asbestos():
    joined = " ".join(p.where for p in disclosure.sheet(
        _house(build_year=2015), _enr(), "chuko_kodate"))
    assert "石綿" not in joined
    assert "耐震診断" not in joined


def test_an_urbanization_control_area_points_at_the_restrictions_column():
    pts = disclosure.sheet(_house(), _enr(), "chuko_kodate",
                           urbanization="市街化調整区域")
    hit = [p for p in pts if "区域区分" in p.where]
    assert hit and hit[0].law == "法第35条第1項第2号"


# ---- 書いてはいけないこと -------------------------------------------------

def test_arrears_are_marked_as_a_different_document():
    """滞納額は重要事項説明の記載事項ではない。混ぜると嘘になる。"""
    pts = disclosure.sheet(_condo(), _enr(), "chuko_mansion")
    hit = [p for p in pts if "滞納" in p.where]
    assert hit, "滞納の話そのものは載せる"
    assert hit[0].law == "重要事項調査報告書"
    assert "記載事項ではありません" in hit[0].why


def test_the_sheet_does_not_judge_the_property():
    """良し悪しは書かない。買ってよいかを言い出した時点で別の商品になる。"""
    banned = ("買うべき", "買ってはいけない", "おすすめ", "避けたほうが",
              "危険です", "安全です", "お得")
    for p in disclosure.sheet(_condo(build_year=1975),
                              _enr(sediment="特別警戒区域", flood_rank=4),
                              "chuko_mansion", urbanization="市街化調整区域"):
        for w in banned:
            assert w not in p.why, (w, p.where)


def test_the_asbestos_note_does_not_read_as_all_clear():
    """記載が無い＝使われていない、ではない。そこを書いておく。"""
    pts = disclosure.sheet(_house(build_year=1990), _enr(), "chuko_kodate")
    hit = [p for p in pts if "石綿" in p.where][0]
    assert "「使われていない」という意味にはなりません" in hit.why


def test_every_place_that_describes_pro_mentions_the_sheet():
    """PROの中身は5か所に書いてある。1か所だけ直すと他が古くなる。

    特定商取引法の最終確認画面は、実際に提供するものを正確に書く義務が
    あるので、ここが漏れると表示の不備になる。だからまとめて見る。
    """
    import app as webapp
    for name in ("PLAN_PAGE", "PLAN_CONFIRM", "_PRO_HUB_BODY",
                 "_PRO_LOCKED_BODY"):
        text = getattr(webapp, name)
        assert "重要事項説明書" in text, name


def test_the_plan_table_lists_it_as_a_pro_only_row():
    """比較表では、無料は「—」でPROが「○」。"""
    import app as webapp
    row = [ln for ln in webapp.PLAN_PAGE.splitlines()
           if "重要事項説明書で確認すること" in ln]
    assert len(row) == 1, row
    assert "<td>—</td><td>○</td>" in row[0]


def test_the_pdf_description_lists_what_the_pdf_holds():
    """PDFに入るものを数え上げている箇所。増えたら足す。"""
    import app as webapp
    assert "重要事項説明書で確認すること" in webapp.PRO_FINANCE_RESULT
    assert "重要事項説明書で確認すること" in webapp.SAVED_DETAIL


# ---- 画面 -----------------------------------------------------------------

def test_the_free_diagnosis_does_not_show_the_sheet():
    """PROの機能。無料の結果画面には出さない。"""
    import app as webapp
    c = webapp.app.test_client()
    h = c.get("/buy").get_data(as_text=True)
    assert "重要事項説明書の、どこを見るか" not in h
