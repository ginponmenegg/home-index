# -*- coding: utf-8 -*-
"""カードの形を1か所（CARD_CSS）にまとめた。

結果画面だけ直したので、保存・マイページ・比較・資金計画へ進んだ瞬間に
枠や文字の大きさが変わっていた。同じ指定をテンプレートごとに書いていた
のが原因。共通のぶんを出して、各テンプレートは自分のぶんだけ持つ。
"""
import importlib
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402

HOUSE = {"address": "神奈川県小田原市城山1-2-3", "price": "3880",
         "land": "147", "building": "90", "byear": "2005",
         "station": "12", "ptype": "chuko_kodate", "income": "600",
         "down": "300", "loan_years": "35"}

# CARD_CSS にあって、どの画面にも要る指定
MARKS = ("h2::before", "border-radius:18px", "--line:#e8ebef")


# ---- 共通CSSが配られている -------------------------------------------------

@pytest.mark.parametrize("name", ["FORM", "RESULT", "LAND_RESULT",
                                  "PRO_FINANCE_RESULT"])
def test_every_template_gets_the_shared_card(name):
    t = getattr(webapp, name)
    assert "CARD_CSS_PLACEHOLDER" not in t, "差し込み忘れ"
    for mark in MARKS:
        assert mark in t, f"{name} に {mark} が無い"


def test_the_account_pages_get_it_too():
    """保存・マイページ・比較は _account_page が着せる。"""
    html = webapp._account_page("見出し", "<div class='card'>本文</div>")
    for mark in MARKS:
        assert mark in html, mark


def test_the_shared_rules_are_not_written_twice():
    """同じ指定を2か所に置くと、片方だけ直した状態に戻る。"""
    for name in ("FORM", "RESULT", "PRO_FINANCE_RESULT"):
        css = getattr(webapp, name)
        assert css.count("h2::before") == 1, name
        assert css.count(".card{background:var(--card)") == 1, name
        assert css.count(":root{--bg:") == 1, name


# ---- 定義が無いまま使われていた class ---------------------------------------

def test_the_risk_band_is_defined_where_it_is_used():
    """保存した診断の詳細が rsk と hz を使っているのに、そこへ配られる
    CSSに定義が無く、ただの文字として出ていた。
    """
    assert 'class="rsk"' in webapp.SAVED_DETAIL
    assert 'class="hz ' in webapp.SAVED_DETAIL
    css = webapp._account_page("x", "")
    assert ".rsk{" in css and ".hz{" in css
    # あとから足した同名の指定が、共通側を黙って上書きしないこと。
    # _ACCOUNT_CSS は _FORM_CSS のうしろに置かれるので、ここに .rsk を
    # 書くと共通側が負ける（実際、古い形のまま残っていた）。
    assert css.count(".rsk{") == 1, "リスクの帯が2回定義されている"


# ---- 保存した診断の詳細 ----------------------------------------------------

@pytest.fixture
def env():
    keep = {k: os.environ.get(k)
            for k in ("DATABASE_URL", "SECRET_KEY", "RESEND_API_KEY",
                      "SHINDAN_MOCK", "BILLING_ENABLED")}
    path = os.path.join(tempfile.mkdtemp(prefix="hi_look_"), "t.db")
    os.environ["DATABASE_URL"] = "sqlite:///" + path.replace("\\", "/")
    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ["RESEND_API_KEY"] = ""
    os.environ["SHINDAN_MOCK"] = "1"
    os.environ["BILLING_ENABLED"] = ""

    from src import accounts, db, saved
    import app as w
    for m in (db, accounts, saved, w):
        importlib.reload(m)
    w.db, w.accounts, w.saved = db, accounts, saved
    db.init_schema()
    yield type("E", (), dict(app=w, db=db, accounts=accounts, saved=saved))

    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in (db, accounts, saved, w):
        importlib.reload(m)


def _saved_page(env):
    c = env.app.app.test_client()
    user = env.accounts.consume_login_token(
        env.accounts.issue_login_token("look@example.com"))
    with c.session_transaction() as sess:
        sess["uid"] = user["id"]
    h = c.post("/diagnose", data=HOUSE).get_data(as_text=True)
    token = re.search(r'name="snap" value="([^"]+)"', h)
    assert token, "保存のトークンが出ていない"
    assert c.post("/save", data={"snap": token.group(1)}).status_code == 302
    sid = env.saved.listing(user["id"])[0]["id"]
    return c.get(f"/saved/{sid}").get_data(as_text=True)


def test_the_saved_detail_does_not_print_english_keys(env):
    """保存済みの行には英語が入っている。出すときに直す。

    保存し直さなくても読めること。作り直させるのは筋が悪い。
    """
    page = _saved_page(env)
    for bad in ("[medium]", "[high]", "[low]", "（unknown）", "（confirmed）"):
        assert bad not in page, bad


def test_the_saved_detail_keeps_the_same_colours(env):
    page = _saved_page(env)
    assert 'color:#0ea5e9">強み' in page, "強みは青"
    assert 'color:#dc2626">弱み' in page, "弱みは赤"
    # 見出しだけでなく、箇条書きの色も結果画面と合わせる
    assert '<ul class="strong">' in page and '<ul class="weak">' in page


def test_the_saved_detail_looks_like_the_result(env):
    page = _saved_page(env)
    for mark in MARKS:
        assert mark in page, mark
