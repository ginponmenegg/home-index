# -*- coding: utf-8 -*-
"""解説記事の器。ネットワーク不要。

記事の目的は、診断で使っている数字の根拠を開いて見せること。だから
**記事と採点は同じ数字でなければならない**。片方だけ直したら、この
テストが落ちるようにしてある。落ちたときは、どちらが正しいかを決めて
両方直すこと。記事のほうだけ書き換えて通すのは筋が違う。

検索結果に出す前提の作り（meta description・canonical・Article の
構造化データ・パンくず）も、消えたら気づけるように固定する。YMYLでは
書き手が誰かが効くので、著者を /about に紐づけている。
"""
import datetime
import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import guides
from src import structure as st

import app as webapp


def _client():
    return webapp.app.test_client()


def _jsonld(html):
    return [json.loads(m) for m in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


def _node(html, typ):
    for doc in _jsonld(html):
        for n in doc.get("@graph", [doc]):
            if n.get("@type") == typ:
                return n
    return None


# ---- 記事と採点の数字が一致していること -----------------------------------

def test_the_table_matches_the_lifespans_used_for_scoring():
    """記事の耐用年数表が src/structure.py と食い違わないこと。"""
    body = guides.by_slug("keiryo-teppone-taiyo-nensu").body
    for key, years in st.LIFE.items():
        assert f"{years}年" in body, f"{key}（{years}年）が記事に無い"
    # 表に載せている厚みの区分。19年と27年は structure.py の注記の根拠
    for years in (19, 20, 27):
        assert f"{years}年" in body, f"{years}年が記事に無い"


def test_the_conversion_examples_match_the_scoring_function():
    """記事の「築30年なら何年相当」が effective_age と一致すること。

    結果画面は f"（木造換算で築{eff:.0f}年相当）" と出す。記事も同じ
    丸め方で書いてある。
    """
    body = guides.by_slug("keiryo-teppone-taiyo-nensu").body
    for key in ("rc", "heavy_steel", "light_steel"):
        eff = st.effective_age(30, key)
        assert f"{eff:.0f}年相当" in body, f"{key} の換算例が合っていない"


def test_the_formula_matches_the_base_structure():
    body = guides.by_slug("keiryo-teppone-taiyo-nensu").body
    assert f"× {st.BASE_LIFE} ÷" in body, "換算式の基準年数が合っていない"
    assert st.LIFE["wood"] == st.BASE_LIFE, "基準は木造"


def test_the_source_is_named_in_the_body():
    """出典は本文に書く。どの表のどの区分かまで。"""
    body = guides.by_slug("keiryo-teppone-taiyo-nensu").body
    assert "国税庁" in body and "耐用年数表" in body
    assert "nta.go.jp" in body
    assert "店舗用・住宅用" in body


def test_the_article_says_it_is_not_a_lifespan():
    """法定耐用年数を寿命と読ませない。ここを外すと記事が有害になる。"""
    body = guides.by_slug("keiryo-teppone-taiyo-nensu").body
    assert "寿命ではありません" in body or "寿命でも" in body


# ---- 記事そのものの決まりごと ---------------------------------------------

def test_slugs_are_unique_and_url_safe():
    slugs = [g.slug for g in guides.GUIDES]
    assert len(slugs) == len(set(slugs))
    for s in slugs:
        assert re.fullmatch(r"[a-z0-9-]+", s), s


def test_dates_are_real_and_not_in_the_future():
    today = datetime.date.today()
    for g in guides.GUIDES:
        pub = datetime.date.fromisoformat(g.published)
        upd = datetime.date.fromisoformat(g.updated)
        assert pub <= today and upd <= today, g.slug
        assert upd >= pub, g.slug


def test_descriptions_fit_a_search_result():
    """長すぎる説明文は検索結果で切られる。短すぎるとクリックされない。"""
    for g in guides.GUIDES:
        assert 60 <= len(g.description) <= 160, f"{g.slug}: {len(g.description)}字"


# ---- ページとして出ること -------------------------------------------------

def test_every_guide_is_reachable():
    c = _client()
    assert c.get("/guide").status_code == 200
    for g in guides.GUIDES:
        assert c.get(f"/guide/{g.slug}").status_code == 200
    assert c.get("/guide/does-not-exist").status_code == 404


def test_the_index_lists_every_guide():
    h = _client().get("/guide").get_data(as_text=True)
    for g in guides.GUIDES:
        assert f'href="/guide/{g.slug}"' in h


def test_the_sitemap_carries_the_guides():
    h = _client().get("/sitemap.xml").get_data(as_text=True)
    assert "/guide<" in h
    for g in guides.GUIDES:
        assert f"/guide/{g.slug}<" in h


def test_every_page_can_reach_the_guides():
    """フッターに導線を置く。記事だけ孤立させない。"""
    c = _client()
    for url in ("/buy", "/mansion", "/terms"):
        assert 'href="/guide"' in c.get(url).get_data(as_text=True), url


def test_the_head_is_built_for_a_search_result():
    g = guides.all_guides()[0]
    h = _client().get(f"/guide/{g.slug}").get_data(as_text=True)
    assert f'<meta name="description" content="{g.description}">' in h
    assert f'<link rel="canonical" href="http://localhost/guide/{g.slug}">' in h
    assert 'property="og:type" content="article"' in h
    assert f"<title>{g.title}｜HOME INDEX</title>" in h


def test_the_article_is_marked_up_as_an_article():
    g = guides.all_guides()[0]
    h = _client().get(f"/guide/{g.slug}").get_data(as_text=True)
    art = _node(h, "Article")
    assert art is not None
    assert art["headline"] == g.title
    assert art["datePublished"] == g.published
    assert art["dateModified"] == g.updated
    crumbs = _node(h, "BreadcrumbList")
    assert [i["name"] for i in crumbs["itemListElement"]] == \
        ["ホーム", "解説", g.title]


def test_the_article_sends_the_reader_to_the_diagnosis():
    g = guides.all_guides()[0]
    h = _client().get(f"/guide/{g.slug}").get_data(as_text=True)
    assert 'href="/buy"' in h


# ---- 書き手 ---------------------------------------------------------------

def _reload(name):
    keep = os.environ.get("OPERATOR_NAME")
    os.environ["OPERATOR_NAME"] = name or ""
    importlib.reload(webapp)
    return keep


def _restore(keep):
    if keep is None:
        os.environ.pop("OPERATOR_NAME", None)
    else:
        os.environ["OPERATOR_NAME"] = keep
    importlib.reload(webapp)


def test_the_author_is_tied_to_the_operator_page():
    """YMYLでは書き手が効く。Person を /about に紐づける。"""
    keep = _reload("見本 太郎")
    try:
        g = guides.all_guides()[0]
        h = _client().get(f"/guide/{g.slug}").get_data(as_text=True)
        art = _node(h, "Article")
        assert art["author"]["name"] == "見本 太郎"
        assert art["author"]["url"].endswith("/about")
        assert 'href="/about"' in h, "本文からも運営者ページへ行けること"
    finally:
        _restore(keep)


def test_no_author_is_claimed_while_the_name_is_a_placeholder():
    """誰でもない著者を名乗るくらいなら、著者を書かない。"""
    keep = _reload(None)
    try:
        g = guides.all_guides()[0]
        h = _client().get(f"/guide/{g.slug}").get_data(as_text=True)
        assert "author" not in _node(h, "Article")
    finally:
        _restore(keep)
