# -*- coding: utf-8 -*-
"""入力例に実在の物件を使わない。ネットワーク不要。

住所・価格・面積・築年・駅徒歩がそろった例は、その組み合わせで物件を
特定できてしまう。面積が実測値そのまま（147.07㎡ など）だとなおさら。
形式が伝わればよいので、住所は伏せ、数値は丸める。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp

# かつて入力例に入っていた、実在の物件に由来する値
REAL = ["147.07", "90.47", "城山4-20-18", "鵠沼", "96.77",
        "20,100", "37,550", "7,480", "3,880"]

PAGES = ["/", "/buy", "/mansion", "/pro/diagnose", "/pro/mansion",
         "/pro/finance", "/copy-guide"]


def test_no_real_property_data_on_any_page():
    c = webapp.app.test_client()
    for path in PAGES:
        h = c.get(path).get_data(as_text=True)
        for v in REAL:
            assert v not in h, f"{path} に {v} が残っている"


def test_the_examples_still_teach_the_format():
    """伏せすぎて役に立たなくならないこと。

    住所は都道府県から書く形を残す。ここを省いた住所だと市区町村を
    特定できず、成約データが取れなくなる（src/citycode.py の pref_hint）。
    """
    h = webapp.app.test_client().get("/buy").get_data(as_text=True)
    assert "〇〇県〇〇市" in h, "都道府県から書く形を示す"
    assert "土地面積" in h and "建物面積" in h
    assert "築2010年" in h and "徒歩12分" in h

    m = webapp.app.test_client().get("/mansion").get_data(as_text=True)
    assert "専有面積" in m and "管理費" in m and "修繕積立金" in m


def test_the_landing_sample_is_marked_as_one():
    """LPの結果サンプルに地名を出さない。見本だと明記する。"""
    h = webapp.app.test_client().get("/").get_data(as_text=True)
    assert "見本です" in h
    assert "神奈川県小田原市・築19年" not in h
