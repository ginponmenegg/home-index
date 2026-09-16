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


# ---- 都道府県だけで判定できるときは、落とさない ----
def test_a_known_prefecture_without_a_city_code_is_still_decided():
    """市区町村コードが取れないだけで、全国どこでも落とすのはやりすぎ。

    制限のある県が6〜9県しかないレイヤでも、住所の解決に失敗しただけで
    北海道でも沖縄でも土砂災害を落としていた。
    """
    from src.enrichment import is_noncommercial
    # 北海道は XKT016（災害危険区域・函館市）だけ。土砂・津波・学区は制限なし
    assert is_noncommercial("XKT029", None, "01") is False
    assert is_noncommercial("XKT028", None, "01") is False
    assert is_noncommercial("XKT004", None, "01") is False
    # 京都府は土砂・津波が県まるごと、学区は宇治市。府内では判定できない
    assert is_noncommercial("XKT029", None, "26") is True
    assert is_noncommercial("XKT028", None, "26") is True
    assert is_noncommercial("XKT004", None, "26") is True


def test_nothing_known_still_stops_at_the_gate():
    from src.enrichment import is_noncommercial
    assert is_noncommercial("XKT029", None, None) is True
    assert is_noncommercial("XKT029", None, "") is True


def test_the_prefecture_of_a_city_only_rule_confines_the_doubt():
    """市区町村で指定されているレイヤは、その県の中でだけ不明にする。"""
    from src.enrichment import is_noncommercial
    # XKT016 の災害危険区域は函館市など6市町。北海道内は不明、沖縄は制限なし
    assert is_noncommercial("XKT016", None, "01") is True
    assert is_noncommercial("XKT016", None, "47") is False


def test_the_layers_that_are_never_restricted_are_never_dropped():
    """洪水・高潮・用途地域・液状化・盛土は、どこでも落とさない。"""
    from src.enrichment import is_noncommercial
    for api in ("XKT026", "XKT027", "XKT002", "XKT001", "XKT025", "XKT020",
                "XKT013", "XKT006", "XKT007", "XKT010", "XKT011", "XKT015",
                "XKT018"):
        for city, pref in (("26100", "26"), (None, "26"), (None, None)):
            assert is_noncommercial(api, city, pref) is False, (api, city, pref)


def test_only_the_listed_layer_is_dropped_in_a_restricted_city():
    """函館市で落ちるのは災害危険区域だけ。土砂も津波も学区も落とさない。"""
    from src.enrichment import is_noncommercial
    assert is_noncommercial("XKT016", "01202") is True
    for api in ("XKT029", "XKT028", "XKT022", "XKT021", "XKT004", "XKT005"):
        assert is_noncommercial(api, "01202") is False, api


def test_the_cap_does_not_soften_just_because_only_one_layer_was_blocked():
    """落ちた数に比例させようとして、やめた。

    洪水・高潮を見て該当なし、土砂だけ取得条件で見られなかった土地を、
    0.9（＝15点中13.5点）にする案を書いてみた。だが土砂災害こそ、
    分からないことがいちばん危ないレイヤ（特別警戒区域は建築制限が
    かかる）。見ていないものを満点近くで通すほうが、間違いが大きい。
    頭打ちは落ちた数によらず 0.7 のままにする。
    """
    from src.scoring import score_risk
    one = score_risk(None, None, HazardResult(
        checked=True, restricted=["土砂災害警戒区域"]))
    two = score_risk(None, None, HazardResult(
        checked=True, restricted=["土砂災害警戒区域", "津波浸水想定"]))
    assert one.raw <= 0.7 and two.raw <= 0.7
    # 差は充足度で出す。何も見ていないときのほうが低い。
    nothing = score_risk(None, None, HazardResult(checked=False))
    assert nothing.sufficiency < one.sufficiency
