# -*- coding: utf-8 -*-
"""HOME INDEX 住まいのサービス（広告のページ群）。ネットワーク不要。

本体の中立の約束と対になる決まりを固定する。
- 採点のコードは、広告のモジュールを知らない
- 広告のリンクが無ければ、ページも導線も出ない
- 広告であることを、ページの頭とリンクごとに書く。rel="sponsored"
- /go/ は件数だけ数えて送る。巡回させない
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OFFERS = [
    {"key": "chumon_a", "category": "chumon", "name": "見本の家づくり一括請求",
     "advertiser": "見本株式会社", "note": "間取りと見積もりをまとめて請求できます。",
     "url": "https://example.com/aff?id=1"},
    {"key": "loan_a", "category": "loan", "name": "見本のローン比較",
     "advertiser": "見本ローン株式会社", "note": "", "url": "https://example.com/aff?id=2"},
    {"key": "hoken_bad", "category": "hoken", "name": "http の広告",
     "advertiser": "", "note": "", "url": "http://example.com/insecure"},
]


@pytest.fixture
def on(tmp_path, monkeypatch):
    from src import group
    conf = tmp_path / "offers.json"
    conf.write_text(json.dumps({"offers": OFFERS}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(group, "_CONF", str(conf))
    bumped = []
    import app as webapp
    monkeypatch.setattr(webapp.metrics, "bump", lambda name, n=1: bumped.append(name))
    return webapp, bumped


def _client():
    import app as webapp
    return webapp.app.test_client()


SCORING = ("scoring.py", "pro_scoring.py", "land_scoring.py", "mansion_scoring.py",
           "land_pro_scoring.py", "mansion_pro_scoring.py", "pipeline.py",
           "price_analysis.py", "land_price.py", "mansion_price.py", "comparable.py",
           "finance.py", "land_finance.py", "loan.py", "household.py")


def test_the_scoring_code_does_not_know_the_ads():
    for name in SCORING:
        path = os.path.join(ROOT, "src", name)
        if os.path.exists(path):
            src = open(path, encoding="utf-8").read()
            assert "group" not in src.replace("groups", ""), name


def test_the_repository_ships_with_no_ad():
    from src import group
    with open(os.path.join(ROOT, "group_offers.json"), encoding="utf-8") as f:
        assert json.load(f)["offers"] == []
    assert not group.enabled()


def test_without_ads_nothing_shows():
    c = _client()
    for url in ("/services", "/services/chumon", "/services/loan", "/go/anything"):
        assert c.get(url).status_code == 404, url
    from src import group
    assert group.bridge("land") == "" and group.bridge("result") == ""
    assert "/services" not in c.get("/sitemap.xml").get_data(as_text=True)


def test_an_insecure_link_is_not_shown(on):
    """https でないリンクは出さない。火災保険はこれしか無いので、分野ごと出ない。"""
    c = _client()
    assert c.get("/services/hoken").status_code == 404
    assert c.get("/go/hoken_bad").status_code == 404


def test_the_hub_says_it_has_ads_and_lists_live_categories(on):
    h = _client().get("/services").get_data(as_text=True)
    assert "広告を含みます" in h
    assert "診断の点数に関係しません" in h
    assert 'href="/services/chumon"' in h and 'href="/services/loan"' in h
    assert 'href="/services/hoken"' not in h


def test_each_ad_link_is_marked_and_sponsored(on):
    h = _client().get("/services/chumon").get_data(as_text=True)
    assert "広告を含みます" in h
    assert '<span class="gad">広告</span>見本の家づくり一括請求' in h
    assert 'rel="sponsored nofollow noopener"' in h
    assert 'href="/go/chumon_a?from=page"' in h
    assert "広告主：見本株式会社" in h
    assert "比べる前に確かめること" in h
    assert "特定のハウスメーカーや工務店を勧めません" in h


def test_insurance_and_loans_are_ads_only(on):
    """保険・ローンは広告まで。募集・取り次ぎ・推奨をしないと書く。"""
    from src import group
    assert "保険の募集・比較・推奨をしません" in group.CATEGORIES["hoken"]["stance"]
    assert "申込みの取り次ぎ" in group.CATEGORIES["loan"]["stance"]
    for c in group.CATEGORIES.values():
        text = c["lead"] + c["stance"] + "".join(t + d for t, d, _h, _l in c["checks"])
        for word in ("おすすめ", "No.1", "一番お得", "最安"):
            assert word not in text, word


def test_go_counts_and_redirects(on):
    webapp, bumped = on
    r = _client().get("/go/loan_a?from=page")
    assert r.status_code == 302
    assert r.headers["Location"] == "https://example.com/aff?id=2"
    assert "noindex" in r.headers.get("X-Robots-Tag", "")
    assert bumped == ["go_loan"]


def test_robots_keeps_crawlers_out_of_go():
    assert "Disallow: /go/" in _client().get("/robots.txt").get_data(as_text=True)


def test_bridges_sit_below_the_score_and_say_ads(on):
    import app as webapp
    t = webapp.RESULT
    assert t.index("この診断が見ているもの") < t.index('group_bridge("result")')
    assert t.index('group_bridge("result")') < t.index("<b>免責</b>")
    assert webapp.LAND_RESULT.index('group_bridge("land")') < \
        webapp.LAND_RESULT.index("<b>免責</b>")
    from src import group
    b = group.bridge("land")
    assert "広告を含みます" in b and "点数には関係しません" in b
    assert "/services/chumon?from=land" in b and "/services/loan?from=land" in b
    assert "/services/hoken" not in b
    assert "/services/chumon" not in group.bridge("result")


def test_coming_from_a_result_is_counted(on):
    webapp, bumped = on
    _client().get("/services/loan?from=finance")
    assert "services_from_finance" in bumped


def test_sitemap_lists_live_service_pages(on):
    x = _client().get("/sitemap.xml").get_data(as_text=True)
    assert "/services</loc>" in x and "/services/chumon</loc>" in x
    assert "/services/hoken</loc>" not in x
