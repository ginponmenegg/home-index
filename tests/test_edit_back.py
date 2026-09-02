# -*- coding: utf-8 -*-
"""結果から入力へ戻れること。ネットワーク不要。

結果画面から入力へ戻る道は「← 別の物件を診断」だけで、押すと空の
フォームが開いた。価格を一桁間違えた、築年を打ち忘れた、と気づいた
時点で住所から年収まで打ち直しになる。ブラウザの戻るも、POSTの
再送信を聞かれたり、入力が復元されなかったりする。

診断は4種類あり、資金計画も入れると5つ。どれか一つでも戻れないと、
そこで同じことが起きる。全部見る。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SHINDAN_MOCK", "1")

import app as webapp


HOUSE = {"address": "神奈川県小田原市城山1-2-3", "price": "3500",
         "land": "120", "building": "95", "byear": "2010", "station": "8",
         "structure": "wood", "loan_years": "35", "income": "800",
         "down": "500", "ptype": "chuko_kodate"}

FLAT = {"address": "神奈川県小田原市城山1-2-3", "price": "3480", "area": "70",
        "byear": "2010", "station": "8", "floor": "5", "total_floors": "10",
        "direction": "南", "mfee": "12000", "rfund": "13000",
        "loan_years": "35"}

FINANCE = {"price": "3500", "byear": "2010", "land_area": "120",
           "floor_area": "95", "down": "500", "income": "800",
           "loan_years": "35", "newbuild": "0", "quake": "yes",
           "land_ratio": "60"}

KEPT = "入力した内容を残してあります"


def _client():
    return webapp.app.test_client()


def _form(html, action):
    """そのフォームが送る hidden だけを拾う。ブラウザと同じ単位で見る。"""
    m = re.search(r'<form[^>]*action="' + re.escape(action) + r'"[^>]*>(.*?)</form>',
                  html, re.S)
    assert m, f"{action} へ戻るフォームが結果画面に無い"
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', m.group(1)))


def _value(html, name):
    m = re.search(r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else None


@pytest.mark.parametrize("label,result_url,data,edit_url,check", [
    ("戸建", "/diagnose", HOUSE, "/buy/edit",
     {"address": "神奈川県小田原市城山1-2-3", "price": "3500",
      "byear": "2010", "income": "800", "down": "500"}),
    ("マンション", "/mansion_diagnose", FLAT, "/mansion/edit",
     {"area": "70", "mfee": "12000", "rfund": "13000", "floor": "5"}),
    ("PRO戸建", "/pro/diagnose", dict(HOUSE, leak="ok"), "/pro/start",
     {"price": "3500", "land": "120", "byear": "2010"}),
    ("PROマンション", "/pro/mansion", dict(FLAT, plumbing="ok"),
     "/pro/mansion_start", {"area": "70", "rfund": "13000"}),
    ("資金計画", "/pro/finance", FINANCE, "/pro/finance_start",
     {"price": "3500", "down": "500", "income": "800"}),
])
def test_every_result_can_go_back_to_its_input(label, result_url, data,
                                               edit_url, check):
    c = _client()
    html = c.post(result_url, data=data).get_data(as_text=True)
    back = c.post(edit_url, data=_form(html, edit_url)).get_data(as_text=True)
    for name, want in check.items():
        assert _value(back, name) == want, f"{label}: {name} が戻っていない"
    assert KEPT in back, f"{label}: 戻ったことを画面に書いていない"


def test_the_first_visit_is_not_told_its_input_was_kept():
    """引き継ぎと戻りで同じ入口を使う。案内を取り違えないこと。"""
    c = _client()
    for url in ("/buy", "/mansion", "/pro/diagnose", "/pro/finance"):
        assert KEPT not in c.get(url).get_data(as_text=True), url
    # 無料診断からPROへ引き継いだときは、引き継ぎの案内のほう
    html = c.post("/diagnose", data=HOUSE).get_data(as_text=True)
    carried = c.post("/pro/start",
                     data=_form(html, "/pro/start")).get_data(as_text=True)
    assert "引き継ぎました" in carried and KEPT not in carried


def test_going_back_does_not_rerun_the_diagnosis():
    """戻り先は入力画面。点数を出し直すものではない。"""
    c = _client()
    html = c.post("/diagnose", data=HOUSE).get_data(as_text=True)
    back = c.post("/buy/edit",
                  data=_form(html, "/buy/edit")).get_data(as_text=True)
    assert "点 / 100" not in back
    assert 'action="/diagnose"' in back, "直してもう一度出せること"
