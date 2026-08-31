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

# かつて入力例に入っていた、実在の物件に由来する値。
#
# 地名そのもの（城山・鵠沼）は対象にしない。市区町村コードの解決や
# 町名の抽出を検証するのに、実在する地名でないとテストが意味をなさない。
# 物件が特定できるのは地名と番地・金額・面積の組み合わせなので、
# 番地から先と、実測値のほうを禁じる。
REAL = ["147.07", "90.47", "44.48",
        "城山4-20-18", "鵠沼桜が岡3丁目", "ブリリア",
        "96.77", "20,100", "37,550", "7,480", "3,880"]

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


def test_no_real_property_data_anywhere_in_the_repository():
    """コードとドキュメントのどこにも残さない。

    リポジトリは公開されているので、画面から外しただけでは足りない。
    テストのフィクスチャやCLIの初期値にも入っていた。

    このファイル自身は、禁止する値そのものを並べているので対象外。
    """
    import glob
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    me = os.path.abspath(__file__)
    targets = (glob.glob(os.path.join(root, "*.py"))
               + glob.glob(os.path.join(root, "src", "*.py"))
               + glob.glob(os.path.join(root, "tests", "*.py"))
               + glob.glob(os.path.join(root, "tools", "*.py"))
               + [os.path.join(root, "README.md")])
    found = []
    for path in targets:
        if os.path.abspath(path) == me or not os.path.exists(path):
            continue
        text = io_read(path)
        for v in REAL:
            if v in text:
                found.append(f"{os.path.relpath(path, root)}: {v}")
    assert not found, "実在の物件に由来する値が残っている: " + ", ".join(found)


def io_read(path):
    import io
    return io.open(path, encoding="utf-8", errors="ignore").read()
