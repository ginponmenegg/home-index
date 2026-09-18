# -*- coding: utf-8 -*-
"""トップページが取りに行く書体。

日本語の書体は、1つの太さが100個以上の塊に分かれて配られる。太さを1つ
並べるだけで @font-face の定義が百数十件増え、書体を読み込む前に落とす
CSS そのものが膨らむ。宣言した太さは、使っていなくても定義が載る。

ブラウザで document.fonts を数えたところ、トップページが実際に使って
いたのは下の EXPECTED だけで、3つの太さは一度も使われていなかった。
外した結果、@font-face は 1,116件 → 749件、CSSの転送量は
265KB → 177KB になった。
"""
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402

# family → 実際に描画に使われている太さ（2026-09-18 に document.fonts で確認）
EXPECTED = {
    "Jost": {"300", "700"},
    "Zen Kaku Gothic New": {"400", "700", "900"},
    "Zen Old Mincho": {"600"},
    "Noto Sans JP": {"400", "700"},
    "IBM Plex Mono": {"400", "500"},
}


def _requested(link: str):
    """<link> のURLから family → 太さの集合 を取り出す。"""
    # preconnect の href も同じホストなので、css2 のほうを拾う
    url = re.search(r'href="([^"]*fonts\.googleapis\.com/css2\?[^"]*)"', link)
    assert url, "Google Fonts の link が見つからない"
    out = {}
    for key, val in urllib.parse.parse_qsl(
            urllib.parse.urlparse(url.group(1)).query):
        if key != "family":
            continue
        name, _, spec = val.partition(":")
        weights = set()
        if spec.startswith("wght@"):
            weights = set(spec[len("wght@"):].split(";"))
        out[name] = weights
    return out


def test_the_landing_page_asks_only_for_the_weights_it_uses():
    assert _requested(webapp.LP_FONT_LINK) == EXPECTED


def test_every_family_it_asks_for_is_actually_used():
    """CSSから消したのにリンクに残っている、という取り残しを防ぐ。"""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
    for family in _requested(webapp.LP_FONT_LINK):
        # link の中の family= 指定ではなく、font-family として使われた箇所
        uses = [m.start() for m in re.finditer(re.escape(family), src)
                if "googleapis" not in src[max(0, m.start() - 400):m.start()]]
        assert uses, f"{family} を読み込んでいるが、どこにも指定が無い"


def test_the_result_pages_do_not_pull_a_japanese_webfont():
    """結果画面とフォームは端末の書体で出す。

    日本語の書体を足すと、この画面だけで600KBを超えた（実測）。数字用の
    IBM Plex Mono は欧文のみなので、数十KBで済む。
    """
    asked = _requested(webapp.FONT_LINK)
    assert set(asked) == {"Jost", "IBM Plex Mono"}, asked
    for page in (webapp.RESULT, webapp.FORM, webapp.LAND_RESULT):
        assert "Noto Sans JP" not in page
        assert "Zen Kaku Gothic New" not in page


def test_the_landing_page_still_declares_its_own_link():
    c = webapp.app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "fonts.googleapis.com/css2" in html
    assert "Zen+Old+Mincho:wght@600&" in html, "余分な太さが戻っている"
