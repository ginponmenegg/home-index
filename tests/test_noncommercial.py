# -*- coding: utf-8 -*-
"""非商用に指定されたデータを、有料サービスで出さない。ネットワーク不要。

■なぜ要るか
不動産情報ライブラリのデータには、コンテンツごと・自治体ごとに「一部非商用」
のものがある。土砂災害警戒区域・津波浸水想定・地すべり防止地区・急傾斜地
崩壊危険区域・災害危険区域・小中学校区の6系統。
このサービスは有料プラン（PRO）を持つので、該当地域では取りに行かない。

■取ってから捨てるのではなく、取りに行かない
条件のあるデータを一度でも受け取って画面に出せば、出したことに変わりはない。

■見ていないものを「該当なし」に数えない
ここがいちばん危ない。土砂を確認していないのに「指定ハザード区域に該当なし」
と出して満点を付けたら、買主はその土地を安全だと思う。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.enrichment import HazardResult, NONCOMMERCIAL, is_noncommercial
from src.scoring import score_risk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_list_is_there_and_says_where_it_came_from():
    """出どころと確認日が無いと、次に見直すとき何を照合すればよいか分からない。"""
    with io.open(os.path.join(ROOT, "noncommercial.json"), encoding="utf-8") as f:
        d = json.load(f)
    assert d["_source"].startswith("https://www.reinfolib.mlit.go.jp/")
    assert d["_checked_at"]
    assert set(d["layers"]) == {"XKT029", "XKT028", "XKT022", "XKT021",
                                "XKT016", "XKT004", "XKT005"}
    for api, rule in d["layers"].items():
        assert rule.get("name"), api
        assert rule.get("prefectures") or rule.get("cities"), api


def test_a_restricted_prefecture_is_skipped():
    """県単位の指定。土砂は神奈川、津波は千葉が非商用。"""
    assert is_noncommercial("XKT029", "14100")      # 横浜市
    assert not is_noncommercial("XKT029", "12204")  # 船橋市
    assert is_noncommercial("XKT028", "12204")      # 津波は千葉が対象
    assert not is_noncommercial("XKT028", "14100")


def test_a_restricted_municipality_is_skipped():
    """市区町村単位の指定。学区は25市区町村だけ。県ごと落とさない。"""
    assert is_noncommercial("XKT004", "11202")      # 熊谷市
    assert not is_noncommercial("XKT004", "11203")  # 同じ埼玉の川口市


def test_the_all_except_one_form_works():
    """災害危険区域の長野県は「中野市以外の全市町村」。"""
    assert not is_noncommercial("XKT016", "20211")  # 中野市だけ商用可
    assert is_noncommercial("XKT016", "20201")      # 長野市


def test_an_unrestricted_layer_is_untouched():
    """洪水（XKT026）には条件が付いていない。巻き添えにしない。"""
    assert not is_noncommercial("XKT026", "14100")


def test_an_unknown_municipality_is_treated_as_restricted():
    """どの地域か分からないまま、条件のあるデータを出さない。"""
    for code in (None, "", "123"):
        assert is_noncommercial("XKT029", code), code


def test_what_we_did_not_look_at_is_never_called_safe():
    """見ていないレイヤがあるとき「該当なし」と言わない。"""
    seen = score_risk(None, None, HazardResult(checked=True))
    assert "該当なし" in seen.reason
    assert seen.raw == 1.0

    blocked = score_risk(None, None, HazardResult(
        checked=True, restricted=["土砂災害警戒区域"]))
    assert "該当なし" not in blocked.reason
    assert "確認していません" in blocked.reason
    assert blocked.raw <= 0.7, "見ていないのに満点に近い"
    assert blocked.sufficiency <= 0.5
    assert not blocked.plus, "見ていないのに強みとして出している"


def test_a_real_hit_still_outranks_the_restriction():
    """制限があっても、見えている被害想定は普通に減点する。"""
    h = HazardResult(checked=True, restricted=["津波浸水想定"])
    h.sediment = "特別警戒区域"
    c = score_risk(None, None, h)
    assert c.raw <= 0.3, "土砂の特別警戒区域が効いていない"
