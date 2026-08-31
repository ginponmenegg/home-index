# -*- coding: utf-8 -*-
"""運営者ページと、中立性の開示。ネットワーク不要。

住宅購入はYMYL（金銭にかかわる話題）で、Googleは書き手が誰かを重く見る。
匿名のままでは記事を書いても評価されにくいので、誰が作っているかを示す。
同時に「業界にいるのに売らない」は、匿名の中立性より説得力がある。
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload(name):
    keep = os.environ.get("OPERATOR_NAME")
    if name is None:
        os.environ["OPERATOR_NAME"] = ""
    else:
        os.environ["OPERATOR_NAME"] = name
    import app as webapp
    importlib.reload(webapp)
    return webapp, keep


def _restore(keep):
    if keep is None:
        os.environ.pop("OPERATOR_NAME", None)
    else:
        os.environ["OPERATOR_NAME"] = keep
    import app as webapp
    importlib.reload(webapp)


def test_hidden_while_the_name_is_a_placeholder():
    """仮の名前のまま運営者ページを出すと、逆効果になる。

    値を消すのではなく空文字にする（app.py が .env を読み直すため）。
    """
    webapp, keep = _reload(None)
    try:
        assert not webapp.operator_named()
        assert webapp.app.test_client().get("/about").status_code == 404
        assert "/about" not in webapp.SITEMAP_PATHS
        assert "運営者について" not in \
            webapp.app.test_client().get("/buy").get_data(as_text=True)
    finally:
        _restore(keep)


def test_the_page_says_who_and_discloses_the_conflict():
    webapp, keep = _reload("山田 太郎")
    try:
        h = webapp.app.test_client().get("/about").get_data(as_text=True)
        assert "山田 太郎" in h
        assert "宅地建物取引士" in h
        # 年数は書かない。読む人によって十分にも浅くも受け取られるため、
        # 資格と、いま現場にいることを示すにとどめる。
        assert "従事しています" in h
        # 勤務先との関係を隠さない。あとから分かるほうが damage が大きい
        assert "勤務先とは関係のない" in h
        # 中立性の中身
        for k in ["不動産の仲介・媒介は行いません",
                  "仲介手数料・紹介料は一切受け取りません",
                  "事業者間の情報は使用しません"]:
            assert k in h, k
    finally:
        _restore(keep)


def test_the_page_is_discoverable():
    webapp, keep = _reload("山田 太郎")
    try:
        assert "/about" in webapp.SITEMAP_PATHS
        assert "/about" in \
            webapp.app.test_client().get("/sitemap.xml").get_data(as_text=True)
        assert "運営者について" in \
            webapp.app.test_client().get("/buy").get_data(as_text=True)
        # 検索エンジンに書き手を伝える
        h = webapp.app.test_client().get("/about").get_data(as_text=True)
        assert "ProfilePage" in h and "宅地建物取引士" in h
    finally:
        _restore(keep)


def test_the_landing_page_names_the_author():
    """匿名で中立を主張するより、誰が言っているかを示す。"""
    webapp, keep = _reload("山田 太郎")
    try:
        h = webapp.app.test_client().get("/").get_data(as_text=True)
        assert "宅地建物取引士" in h
        assert "物件の仲介は行いません" in h
        assert "6年" not in h, "年数は出さない"
        assert 'href="/about"' in h
    finally:
        _restore(keep)


def test_the_faq_matches_what_the_mansion_diagnosis_actually_does():
    """マンション対応と管理費の評価は、もう実装されている。"""
    webapp, keep = _reload("山田 太郎")
    try:
        h = webapp.app.test_client().get("/").get_data(as_text=True)
        assert "現在は戸建（中古・新築）のみです" not in h, "構造化データが古い"
        assert "公的データから取得できないため評価に含まれていません" not in h, \
            "管理費・修繕積立金は評価に入れている"
    finally:
        _restore(keep)
