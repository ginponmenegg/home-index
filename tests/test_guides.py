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
    """法定耐用年数を寿命と読ませない。ここを外すと記事が有害になる。

    言い回しではなく、否定していること自体を見る。
    """
    body = guides.by_slug("keiryo-teppone-taiyo-nensu").body
    assert "寿命" in body
    assert "定めたものではありません" in body


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


# ---- 目次 -----------------------------------------------------------------

def test_the_contents_list_every_heading():
    """目次は本文の見出しから作る。記事側では何も書かない。"""
    g = guides.all_guides()[0]
    h = _client().get(f"/guide/{g.slug}").get_data(as_text=True)
    heads = re.findall(r'<h2 id="(h\d+)">(.*?)</h2>', h, re.S)
    assert len(heads) >= 3
    toc = re.search(r'<nav class="toc">(.*?)</nav>', h, re.S).group(1)
    for hid, text in heads:
        assert f'href="#{hid}"' in toc, hid
        assert re.sub(r"<[^>]+>", "", text).strip() in toc


def test_the_body_carries_no_ids_of_its_own():
    """見出しの id を記事に書かせない。書き忘れると目次だけ外れる。"""
    for g in guides.GUIDES:
        assert "id=" not in g.body, g.slug


def test_short_pieces_get_no_contents_list():
    """見出しが2つ以下なら目次は場所を取るだけ。"""
    toc, body = webapp._with_toc("<h2>あ</h2><p>x</p><h2>い</h2>")
    assert toc == ""
    assert 'id="h1"' in body, "目次を出さなくても id は振る"


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


def test_every_article_sends_the_reader_somewhere_it_can_be_checked():
    """マンションの話を戸建の診断へ送っても、読んだ人は確かめられない。"""
    c = _client()
    for g in guides.GUIDES:
        h = c.get(f"/guide/{g.slug}").get_data(as_text=True)
        assert f'<a href="{g.cta_href}">{g.cta_text}</a>' in h, g.slug
        assert c.get(g.cta_href).status_code == 200, g.cta_href


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


# ---- 記事ごとの数字を、採点そのものに突き合わせる -------------------------
#
# 数字はテストに書き写さない。採点を実際に走らせて出た値が、記事に
# 書いてあるかを見る。採点を変えたらここが落ちる。

from types import SimpleNamespace  # noqa: E402

from src.config import CONFIG  # noqa: E402
from src.enrichment import FLOOD_RANK_LABEL  # noqa: E402
from src.scoring import score_risk  # noqa: E402
from src.mansion_scoring import (NEW_QUAKE_STANDARD_YEAR,  # noqa: E402
                                 is_new_quake_standard)


def _pt(x):
    """点数の書き方をそろえる。7.50ではなく7.5、3.00ではなく3。"""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _hazard(**kw):
    base = dict(checked=True, flood_rank=None, flood_label="", sediment=None,
                tsunami=False, storm_surge=False, liquefaction=None,
                danger_zone=None, steep_slope=False, landslide_zone=False,
                embankment=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _risk_raw(**kw):
    return score_risk(None, None, _hazard(**kw)).raw


def test_the_flood_article_matches_the_risk_scoring():
    body = guides.by_slug("kouzui-shinsui-fukasa").body
    w = CONFIG["category_weights"]["リスク"]
    assert f"リスクは{w}点満点" in body

    # 上限を掛ける扱い（その点以下に抑える）
    for kw in ({"flood_rank": 4}, {"flood_rank": 3},
               {"sediment": "特別警戒区域"}, {"sediment": "警戒区域"},
               {"checked": False}):
        cap = _pt(w * _risk_raw(**kw))
        assert f"{cap}点" in body, f"{kw} → {cap}点 が記事に無い"

    # 引く扱い（満点からの差）
    for kw in ({"flood_rank": 1}, {"flood_rank": 2},
               {"tsunami": True}, {"storm_surge": True}):
        cut = _pt(w * (1.0 - _risk_raw(**kw)))
        assert f"{cut}点" in body, f"{kw} → {cut}点 が記事に無い"


def test_the_flood_article_lists_every_depth_band():
    body = guides.by_slug("kouzui-shinsui-fukasa").body
    for rank, label in FLOOD_RANK_LABEL.items():
        assert label in body, f"ランク{rank}（{label}）が記事に無い"


def test_the_repair_fund_article_matches_the_guideline():
    body = guides.by_slug("shuzen-tsumitatekin-meyasu").body
    g = CONFIG["mansion_repair_fund_guideline"]
    for band in list(g["under_20f_bands"]) + [g["over_20f"]]:
        for k in ("low", "high", "avg"):
            assert str(band[k]) in body, f"{k}={band[k]} が記事に無い"
    # 建築延床面積を聞いていないので、20階未満は4区分を包絡して判定する
    lo = min(b["low"] for b in g["under_20f_bands"])
    hi = max(b["high"] for b in g["under_20f_bands"])
    assert f"{lo}〜{hi}円/㎡" in body

    w = CONFIG["mansion_category_weights"]["管理"]
    assert f"管理は\n{w}点満点" in body or f"管理は{w}点満点" in body
    for raw in (0.85, 0.5, 0.75):     # 範囲内 / 下限割れ / 上回る
        assert f"{_pt(w * raw)}点" in body, f"{raw} → {_pt(w * raw)}点 が記事に無い"


def test_the_repair_fund_example_divides_correctly():
    body = guides.by_slug("shuzen-tsumitatekin-meyasu").body
    assert f"{round(13000 / 70)}円" in body


def test_the_seismic_article_matches_the_cutoff():
    body = guides.by_slug("shin-taishin-kenchiku-kakunin").body
    assert str(NEW_QUAKE_STANDARD_YEAR) in body
    # 記事は「1981年築は旧耐震の側に倒す」と書いている
    assert is_new_quake_standard(NEW_QUAKE_STANDARD_YEAR - 1) is False
    assert is_new_quake_standard(NEW_QUAKE_STANDARD_YEAR) is True
    assert "1981年6月1日" in body, "基準日は建築確認の日"


def test_no_guide_talks_about_the_price_estimate():
    """鑑定評価の法的な整理が済むまで、価格の根拠は記事にしない。"""
    for g in guides.GUIDES:
        text = g.lead + g.body
        for word in ("推定価格", "適正価格", "査定"):
            assert word not in text, f"{g.slug} に「{word}」が出ている"


def _fin_points(rb, income):
    from src.loan import LoanResult
    from src.scoring import score_finance
    lr = LoanResult(0, 0, 0, 0, rb, income)
    return CONFIG["category_weights"]["資金"] * score_finance(lr).raw


def test_the_burden_article_matches_the_finance_scoring():
    body = guides.by_slug("hensai-futanritsu").body
    w = CONFIG["category_weights"]["資金"]
    assert f"資金は{w}点満点" in body
    for rb in (20, 25, 35, 40, 41):        # 年収600万＝上限35%の側
        assert f"{_pt(_fin_points(rb, 6_000_000))}点" in body, f"負担率{rb}%"
    # 年収も頭金も入れなければ、良いとも悪いとも判断しない
    from src.scoring import score_finance
    assert f"{_pt(w * score_finance(None).raw)}点に" in body


def test_the_burden_article_matches_the_income_cutoff():
    """年収400万円で上限が30%と35%に切り替わる（フラット35の要件）。"""
    body = guides.by_slug("hensai-futanritsu").body
    assert "400万円" in body and "30%" in body and "35%" in body
    # 400万円ちょうどは35%側、1円足りなければ30%側
    assert _fin_points(35, 4_000_000) > _fin_points(35, 3_999_999)
    assert _fin_points(30, 3_999_999) == _fin_points(35, 4_000_000)


def test_the_burden_article_matches_the_loan_defaults():
    body = guides.by_slug("hensai-futanritsu").body
    assert f"{CONFIG['loan_rate'] * 100:g}%" in body
    assert f"{CONFIG['loan_years']}年" in body


def test_the_ground_article_matches_the_risk_scoring():
    body = guides.by_slug("jiban-ekijoka-morido").body
    w = CONFIG["category_weights"]["リスク"]
    for kw in ({"steep_slope": True}, {"landslide_zone": True},
               {"danger_zone": "指定あり"}):
        cap = _pt(w * _risk_raw(**kw))
        assert f"{cap}点" in body, f"{kw} → {cap}点 が記事に無い"
    for kw in ({"liquefaction": "液状化しやすい"},
               {"liquefaction": "やや液状化しやすい"},
               {"embankment": "谷埋め型"}):
        cut = _pt(w * (1.0 - _risk_raw(**kw)))
        assert f"{cut}点" in body, f"{kw} → {cut}点 が記事に無い"


def test_ground_that_is_unlikely_to_liquefy_is_not_penalised():
    """記事は「しにくいなら引かない」と書いている。"""
    assert _risk_raw(liquefaction="液状化しにくい") == 1.0
    assert "しにくい" in guides.by_slug("jiban-ekijoka-morido").body


def test_the_population_article_matches_the_adjustment():
    from src.scoring import _future_population_adj
    body = guides.by_slug("shorai-suikei-jinko-mesh").body
    house = CONFIG["category_weights"]["資産性"]
    flat = CONFIG["mansion_category_weights"]["資産性"]
    for pct in (10, 2, -15, -30):          # 0になる帯（-10〜0%）は書きようがない
        adj, _bit = _future_population_adj(pct)
        assert adj, pct
        for w in (house, flat):
            assert f"{_pt(abs(adj) * w)}点" in body, f"{pct}% × {w}点"
    assert _future_population_adj(-5)[0] == 0.0
    assert "動かさない" in body
