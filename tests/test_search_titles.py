# -*- coding: utf-8 -*-
"""検索結果に出る題と説明文。ネットワーク不要。

「住宅購入 診断ツール」「購入診断」「物件診断」などで探す人に、
入口の4画面がそれと分かる語で出ること。語を入れるのは検索のためだが、
**画面がやっていないことを言わない**ことも同じ場所で守る。

- このサービスは建物の検査（ホームインスペクション）ではない。検索で「診断」が
  検査を指すことが多いので、トップの説明文でそう書いて取り違えを減らす。
- AIで判定するとは言わない（点数は決まった規則で出している）。
- 4画面の題は互いに違うこと（同じ題だと、どれを出すか検索側が迷う）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402

PAGES = {
    "/": ("住宅購入", "診断ツール"),
    "/buy": ("中古戸建", "購入診断"),
    "/mansion": ("中古マンション", "購入診断"),
    "/land": ("土地", "診断"),
}


def _head(path):
    h = webapp.app.test_client().get(path).get_data(as_text=True)
    title = re.search(r"<title>([^<]*)</title>", h).group(1)
    desc = re.search(r'<meta name="description" content="([^"]*)"', h).group(1)
    return title, desc


def test_each_entry_page_carries_the_words_people_search_for():
    for path, words in PAGES.items():
        title, desc = _head(path)
        for w in words:
            assert w in title + desc, f"{path}: {w!r} が題にも説明にも無い"
        assert "HOME INDEX" in title, path


def test_the_four_titles_are_all_different():
    titles = [_head(p)[0] for p in PAGES]
    assert len(set(titles)) == len(titles), titles


def test_the_top_page_says_it_is_not_a_building_inspection():
    _, desc = _head("/")
    assert "建物の検査" in desc and "ではありません" in desc


def test_no_title_or_description_claims_ai():
    for path in PAGES:
        title, desc = _head(path)
        assert "AI" not in title + desc, f"{path}: AI判定とは言っていない"
