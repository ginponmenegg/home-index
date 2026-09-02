# -*- coding: utf-8 -*-
"""PROの入口と、診断から資金計画への引き継ぎ。ネットワーク不要。

PROは長らく3つのフォームがフッターに並んでいるだけで、/pro は404だった。
そのうえ診断から資金計画へ入力が渡らず、同じ物件の価格・築年・借入条件を
2回打ち直す必要があった。月額を払う人にさせることではない。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import SubjectProperty, MansionSubject

import app as webapp


def _client():
    return webapp.app.test_client()


# ---- 入口 -----------------------------------------------------------------

def test_the_hub_exists_and_reaches_every_tool():
    c = _client()
    h = c.get("/pro").get_data(as_text=True)
    for url in ("/pro/diagnose", "/pro/mansion", "/pro/finance"):
        assert f'href="{url}"' in h, url
        assert c.get(url).status_code == 200, url


def test_the_hub_repeats_that_pro_does_not_move_the_price():
    """PROの入力は物件評価とリスクにだけ渡す。ここを曖昧にしない。"""
    h = _client().get("/pro").get_data(as_text=True)
    assert "推定価格" in h
    assert "無料版と同じ" in h or "同じ数字" in h


def test_every_page_can_reach_the_hub():
    c = _client()
    for url in ("/buy", "/mansion", "/terms"):
        assert 'href="/pro"' in c.get(url).get_data(as_text=True), url


def test_the_hub_is_in_the_sitemap():
    h = _client().get("/sitemap.xml").get_data(as_text=True)
    assert "<loc>http://localhost/pro</loc>" in h


# ---- 引き継ぎ -------------------------------------------------------------

def test_the_carry_converts_yen_into_the_units_the_form_wants():
    """資金計画の金額欄は万円。面積は㎡。ここを間違えると桁が変わる。"""
    subj = SubjectProperty(property_type="chuko_kodate", price=34_800_000,
                           address="x", land_area_m2=120.0,
                           building_area_m2=95.0, build_year=2010)
    v = webapp._finance_carry(subj, 5_000_000, 35, 8_000_000)
    assert v["price"] == "3480"
    assert v["down"] == "500"
    assert v["income"] == "800"
    assert v["land_area"] == "120.0" and v["floor_area"] == "95.0"
    assert v["byear"] == "2010" and v["loan_years"] == "35"
    assert v["newbuild"] == "0"


def test_the_carry_reads_a_flat_exclusive_area():
    """マンションは building_area_m2 ではなく exclusive_area_m2 を持つ。"""
    flat = MansionSubject(address="x", price=34_800_000, build_year=2010,
                          exclusive_area_m2=70.0)
    v = webapp._finance_carry(flat, 0, 35)
    assert v["floor_area"] == "70.0"
    assert v["down"] == "" and v["income"] == ""


def test_a_new_build_is_carried_as_a_new_build():
    """新築だけ表題登記・保存登記を計上するので、種別を取り違えない。"""
    subj = SubjectProperty(property_type="shinchiku_kodate", price=1,
                           address="x")
    assert webapp._finance_carry(subj, 0, 35)["newbuild"] == "1"


def test_a_building_on_the_seismic_boundary_is_not_claimed_as_compliant():
    """1982年前後は建築確認の日で決まる。築年だけで適合と言わない。

    /guide/shin-taishin-kenchiku-kakunin に書いたとおり。
    """
    def q(year):
        subj = SubjectProperty(property_type="chuko_kodate", price=1,
                               address="x", build_year=year)
        return webapp._finance_carry(subj, 0, 35)["quake"]

    assert q(2010) == "yes"
    assert q(1982) == "unknown", "1982年築は建築確認の日で分かれる"
    assert q(1980) == "unknown"
    assert q(None) == "unknown"


def test_the_finance_form_opens_with_the_carried_values():
    h = _client().post("/pro/finance_start", data={
        "price": "3480", "byear": "2010", "land_area": "120",
        "floor_area": "95", "down": "500", "income": "800",
        "loan_years": "35", "newbuild": "0", "quake": "yes",
    }).get_data(as_text=True)
    for name, val in (("price", "3480"), ("byear", "2010"),
                      ("land_area", "120"), ("floor_area", "95"),
                      ("down", "500"), ("income", "800"),
                      ("loan_years", "35")):
        assert f'name="{name}" value="{val}"' in h \
            or f'value="{val}"' in h, f"{name}={val} が入っていない"
    assert "引き継ぎました" in h, "引き継いだことを画面に書く"


def test_the_finance_form_still_opens_empty_on_its_own():
    h = _client().get("/pro/finance").get_data(as_text=True)
    assert "引き継ぎました" not in h
    assert 'name="price"' in h
