# -*- coding: utf-8 -*-
"""記事の文中に、改行由来の隙間が出ないこと。ネットワーク不要。

記事のソースは読みやすさのために文の途中で改行している。ブラウザは改行を
空白1つとして描くので、「制度を 見ています」のように日本語の文の中に隙間が
出ていた（全記事。2026-10-08に気づいた）。表示するときに詰めている。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import guides  # noqa: E402
import app as webapp  # noqa: E402

JA = "぀-ヿ一-鿿　-〿！-｠"


def test_japanese_lines_are_joined_without_a_gap():
    f = webapp._join_ja_lines
    assert f("制度を\n見ています") == "制度を見ています"
    assert f("制度を\r\n見ています") == "制度を見ています"
    assert f("記事は\n<a href=\"/x\">こちら</a>\nに書いた") == \
        "記事は<a href=\"/x\">こちら</a>に書いた"
    assert f("先に<b>太字</b>\nのあと") == "先に<b>太字</b>のあと"


def test_english_words_keep_their_space():
    f = webapp._join_ja_lines
    assert f("No.7108\nand more") == "No.7108\nand more"
    assert f("</p>\n<p>次") == "</p>\n<p>次"


def test_no_rendered_article_has_a_break_inside_a_japanese_sentence():
    c = webapp.app.test_client()
    pat = re.compile(f"[{JA}]\\r?\\n[{JA}]")
    for g in guides.GUIDES:
        h = c.get(f"/guide/{g.slug}").get_data(as_text=True)
        main = h[h.index("<h1>"):]
        for p in re.findall(r"<p[^>]*>(.*?)</p>", main, re.S):
            assert not pat.search(p), f"{g.slug}: {p[:60]}"
