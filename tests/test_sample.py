# -*- coding: utf-8 -*-
"""見本の物件と、共有の輪。ネットワーク不要。

■なぜ見本が要るか
SNSから来た人は、まだ物件を持っていない。入力欄は全部空なので、
何もできずに帰る。採点の中身と出典の見え方だけでも見せる。

■なぜ URL で開けるようにしたか
POSTだと貼れない。SNSに貼れる形でないと、来る理由にならない。

■過去の不具合
フォームを開いた時点で実在の物件が初期値として入っていて、気づかずに
診断すると他人の物件の結果が出ていた（app._example_v のコメント）。
だから見本は「開いたときだけ」「見本と分かる形で」出す。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp


def _client():
    return webapp.app.test_client()


# ---- 見本 -----------------------------------------------------------------

def test_the_sample_opens_with_a_plain_link(_=None):
    """URLで開けること。POSTだとSNSに貼れない。"""
    r = _client().get("/sample")
    assert r.status_code == 200
    assert "これは見本の物件です" in r.get_data(as_text=True)


def test_the_sample_says_which_numbers_are_made_up():
    """住所は実在、価格や築年は説明のための数字。混同させない。"""
    h = _client().get("/sample").get_data(as_text=True)
    assert "実際に売られている物件ではありません" in h
    assert 'href="/buy"' in h, "自分の物件へ進める導線"


def test_the_ordinary_diagnosis_shows_no_sample_notice():
    """本物の診断に見本の帯を出さない。"""
    c = _client()
    h = c.post("/diagnose", data=dict(
        address="東京都新宿区西新宿2丁目", price="4000",
        byear="2010", land="100", building="90",
        ptype="chuko_kodate", loan_years="35")).get_data(as_text=True)
    assert "これは見本の物件です" not in h


def test_the_forms_are_still_empty_when_opened():
    """見本を足したせいで、入力欄に値が入っていないこと。

    hidden入力で見本を持たせると、本物のフォームと同じ name が二重になる。
    最初その形で作って、既存のテストが落ちた。リンクにした理由がこれ。
    """
    c = _client()
    allowed = {"35", "1.25", "1", "0", "unknown", "不明"}
    for path in ("/", "/buy"):
        h = c.get(path).data.decode("utf-8")
        filled = [(m.group(1), m.group(2))
                  for m in re.finditer(r'name="(\w+)" value="([^"]+)"', h)
                  if m.group(2) not in allowed]
        assert not filled, f"{path} に値が入っている: {filled}"


def test_the_sample_address_stops_at_the_block():
    """番地まで書くと、実在する誰かの家を指す。丁目までにする。"""
    addr = webapp.SAMPLE_HOUSE["address"]
    assert addr.endswith("丁目"), addr
    assert not re.search(r"\d+-\d+", addr), addr


def test_both_entrances_point_at_the_sample():
    c = _client()
    for path in ("/", "/buy"):
        assert 'href="/sample"' in c.get(path).get_data(as_text=True), path


def test_the_sample_is_not_advertised_for_crawling():
    """毎回この画面を採り直させる意味がない。サイトマップには載せない。"""
    assert "/sample" not in webapp.SITEMAP_PATHS


def test_a_thin_result_is_not_frozen_for_six_hours():
    """外部APIが落ちているときの結果を、焼き付けない。

    SNSから来た人が最初に開く画面なので、価格が出ていない結果を6時間
    固定すると、その間ずっと痩せた見本を見せることになる。
    """
    assert webapp._PRICE_FAILED in webapp.RESULT,         "判定に使っている文言が、画面から消えている"
    webapp._SAMPLE_CACHE.update(html=None, at=0.0)
    c = _client()
    h = c.get("/sample").get_data(as_text=True)
    if webapp._PRICE_FAILED in h:
        assert webapp._SAMPLE_CACHE["html"] is None, "痩せた結果を残さない"
    else:
        assert webapp._SAMPLE_CACHE["html"], "揃った結果は残す"


def test_both_forms_ask_for_the_street_number():
    """番地まで入れると座標が正確になり、区域の判定が変わる。

    ハザードも用途地域も、座標がポリゴンの中に入るかで見ている。丁目まで
    だと付近の代表値に落ちるので、そのことを入力欄の横で伝える。
    """
    c = _client()
    for path in ("/buy", "/mansion"):
        h = c.get(path).get_data(as_text=True)
        assert "番地まで入れると、判定が正確になります" in h, path


# ---- 共有の輪 -------------------------------------------------------------

def test_the_shared_image_carries_the_domain():
    """Xでは画像だけが転載される。画像の中にURLが無いと辿り着けない。

    共有シートの text や url は、受け取る先によっては捨てられる。
    画像に焼いてあれば、そこだけは残る。
    """
    h = _client().get("/sample").get_data(as_text=True)
    # #report が画像になる範囲。その閉じ位置は、共有ボタンの入れ物が
    # 始まるところ。ここから外に出したら画像に写らない。
    i = h.find('id="report"')
    j = h.find('<div class="wrap" style="padding-top:0">', i)
    assert i >= 0 and j > i
    assert "homeindex.jp" in h[i:j], "結果カードの中にドメインが無い"


def test_the_share_sheet_carries_a_link_back():
    h = _client().get("/sample").get_data(as_text=True)
    assert "navigator.share(" in h
    call = h[h.find("navigator.share("):][:300]
    assert "url:" in call, "共有シートにURLが入っていない"
    assert "homeindex.jp" in call


def test_the_paste_box_sits_below_the_form_on_both_pages():
    """手で入力する人が先。貼り付けは「もっと楽もできます」の位置。

    戸建だけ組み替えて、マンションは貼り付けがトップに残っていた。
    入口でいちばん目立つ場所に、大半の人が使わない機能が置かれていた。
    """
    c = _client()
    for path, form, paste in (("/buy", "/diagnose", "/parse"),
                              ("/mansion", "/mansion_diagnose", "/mansion_parse")):
        h = c.get(path).get_data(as_text=True)
        i = h.find('action="%s"' % form)
        j = h.find('action="%s"' % paste)
        assert i > 0 and j > 0, path
        assert j > i, f"{path}: 貼り付けが診断フォームより上にある"


def test_the_copy_guide_is_linked_once_per_page():
    """組み替えの途中で、同じ案内が2回出ていた。"""
    c = _client()
    for path in ("/buy", "/mansion"):
        h = c.get(path).get_data(as_text=True)
        assert h.count("/copy-guide") == 1, path


def _unbalanced(html_text):
    """閉じ忘れ・閉じすぎを拾う。ブラウザは黙って直すので気づけない。"""
    from html.parser import HTMLParser
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.errors = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in void:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in void:
                return
            if not self.stack:
                self.errors.append("余分な </%s>" % tag)
                return
            if self.stack[-1] != tag:
                self.errors.append("</%s> のところで <%s> が開いたまま"
                                   % (tag, self.stack[-1]))
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i] == tag:
                        del self.stack[i:]
                        return
                return
            self.stack.pop()

    p = P()
    p.feed(html_text)
    return p.errors + ["閉じ忘れ <%s>" % t for t in p.stack
                       if t not in ("html", "body")]


def test_the_pages_are_not_missing_or_extra_closing_tags():
    """マンションのフォームに </div> が1つ余っていて、フッターが .wrap の
    外に出ていた。幅も余白も他のページと違っていたが、見ただけでは
    気づきにくい。
    """
    c = _client()
    for path in ("/", "/buy", "/mansion", "/plan", "/pro", "/sample/finance"):
        bad = _unbalanced(c.get(path).get_data(as_text=True))
        assert not bad, f"{path}: {bad[:3]}"


def test_the_mansion_fees_are_not_folded_away():
    """管理費と修繕積立金は、折りたたみの外に置く。

    管理はマンションの100点のうち15点。ところが修繕積立金が未入力だと
    raw 0.50・充足度 0.10 の固定値になり、全物件が一律7.5点になる。
    物件間の差が一切つかないうえ、情報充足度をいちばん強く押し下げる。
    畳んでいる限り、その15点は働かない。
    返済負担率にも足しているので、資金の側にも効く。
    """
    import re
    h = _client().get("/mansion").get_data(as_text=True)
    form = h[h.find('action="/mansion_diagnose"'):h.find('action="/mansion_parse"')]
    folded = form[form.find("<details"):form.find("</details>")]
    for name in ("mfee", "rfund"):
        assert f'name="{name}"' in form, name
        assert f'name="{name}"' not in folded, f"{name} が畳まれている"
