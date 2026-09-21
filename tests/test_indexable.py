# -*- coding: utf-8 -*-
"""検索に出す画面と、出さない画面。ネットワーク不要。

Search Console の「ページ」レポートで noindex の除外が出たときに、
それが意図したものかどうかを、ここで見分けられるようにしておく。

見分け方は単純で、**その画面が本人にしか意味を持たないか**で決める。
マイページ・保存・比較・ログインは本人だけのもので、検索から来ても
何も見えない。料金表は誰でも見られる公開ページなので、出す。
"""
import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NOINDEX = '<meta name="robots" content="noindex">'


@pytest.fixture
def env(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(prefix="hi_idx_"), "t.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "idx")
    monkeypatch.setenv("RESEND_API_KEY", "")
    from src import db, accounts, saved
    import app as webapp
    for m in (db, accounts, saved, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    db.init_schema()
    return webapp


def test_the_price_list_is_open_to_search(env):
    """売っているものの値段が検索に出ないのは、意図ではない。"""
    h = env.app.test_client().get("/plan").get_data(as_text=True)
    assert NOINDEX not in h
    assert '<meta name="description"' in h, "検索結果に出す説明文を持つこと"


def test_the_pages_that_only_mean_something_to_their_owner_are_closed(env):
    """検索から来ても何も見えない画面は、出さない。"""
    c = env.app.test_client()
    assert NOINDEX in c.get("/login").get_data(as_text=True)
    # ログインが要る画面はログインへ飛ぶ。飛び先が閉じていればよい。
    for path in ("/mypage", "/compare"):
        assert c.get(path).status_code == 302, path


def test_the_public_pages_are_open(env):
    """診断・記事・規約・特商法は検索に出す。"""
    c = env.app.test_client()
    for path in ("/", "/buy", "/mansion", "/guide", "/about",
                 "/terms", "/privacy", "/tokushoho", "/pro"):
        assert NOINDEX not in c.get(path).get_data(as_text=True), path


def test_every_url_in_the_sitemap_is_indexable(env):
    """サイトマップに載せたものに noindex を付けない。

    載せておいて拒否するのは、クロールの予算を捨てるのと同じ。
    """
    import re
    c = env.app.test_client()
    sm = c.get("/sitemap.xml").get_data(as_text=True)
    paths = [re.sub(r"^https?://[^/]+", "", u)
             for u in re.findall(r"<loc>([^<]+)</loc>", sm)]
    assert len(paths) >= 15
    for p in paths:
        r = c.get(p)
        assert r.status_code == 200, (p, r.status_code)
        assert NOINDEX not in r.get_data(as_text=True), p


# ---- サイトマップのURLは、検索に出す作りになっていること -------------------

def test_every_sitemap_url_has_a_canonical_and_a_description(env):
    """Search Console の「検出 - インデックス未登録」に並んだ8件のうち
    4件（/buy /mansion /land /pro）は、canonical も description も
    持っていなかった。解説記事のほうには両方あった。
    **一番出したいページだけが、検索に出す作りになっていなかった。**
    """
    import re
    import os
    os.environ["SHINDAN_MOCK"] = "1"
    c = env.app.test_client()
    missing = []
    for path in env.SITEMAP_PATHS:
        h = c.get(path).get_data(as_text=True)
        can = re.search(r'<link rel="canonical" href="([^"]+)"', h)
        desc = re.search(r'<meta name="description" content="([^"]{20,})"', h)
        if not can:
            missing.append(f"{path}: canonical なし")
        elif not can.group(1).endswith(path if path != "/" else "/"):
            missing.append(f"{path}: canonical が {can.group(1)}")
        if not desc:
            missing.append(f"{path}: description なし")
    assert not missing, missing


def test_the_sample_does_not_look_like_a_real_listing(env):
    """題が住所のままだと、検索結果に本物の売り物件のように出る。"""
    import re
    import os
    os.environ["SHINDAN_MOCK"] = "1"
    c = env.app.test_client()
    for path, word in (("/sample", "見本の診断結果（中古戸建）"),
                       ("/sample/land", "見本の診断結果（土地")):
        h = c.get(path).get_data(as_text=True)
        title = re.search(r"<title>([^<]*)</title>", h).group(1)
        assert word in title, f"{path}: {title}"


def test_a_real_result_keeps_the_address_in_its_title(env):
    """見本以外は、どの物件の結果か分かるほうがよい（共有されるため）。"""
    import re
    import os
    os.environ["SHINDAN_MOCK"] = "1"
    h = env.app.test_client().post("/diagnose", data={
        "address": "神奈川県小田原市城山1-2-3", "price": "3880",
        "ptype": "chuko_kodate", "land": "147", "building": "90",
        "byear": "2005", "station": "12"}).get_data(as_text=True)
    title = re.search(r"<title>([^<]*)</title>", h).group(1)
    assert "小田原市城山" in title
    assert "見本" not in title
