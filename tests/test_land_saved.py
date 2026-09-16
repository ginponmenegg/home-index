# -*- coding: utf-8 -*-
"""土地診断の保存・マイページ・再診断。sqliteを繋いで実際の画面で通す。

土地だけ結果を残せなかった。保存できないので、マイページにも出ず、
比較もできず、あとから見返すこともできなかった。
"""
import importlib
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FORM = dict(address="神奈川県小田原市南町1-1", price="1800", area="120",
            budget="2500", household="4", road_width="6", road_type="公道",
            frontage="8", station="12", coverage="60", far="200",
            city="14206", district="南町", income="800", down="500",
            loan_years="35", condition="none")


@pytest.fixture
def env():
    """sqliteを繋いだ状態の app / src 一式。test_accounts と同じ作り。"""
    keep = {k: os.environ.get(k)
            for k in ("DATABASE_URL", "SECRET_KEY", "RESEND_API_KEY",
                      "SHINDAN_MOCK", "BILLING_ENABLED")}
    path = os.path.join(tempfile.mkdtemp(prefix="hi_land_"), "t.db")
    os.environ["DATABASE_URL"] = "sqlite:///" + path.replace("\\", "/")
    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ["RESEND_API_KEY"] = ""
    os.environ["SHINDAN_MOCK"] = "1"
    os.environ["BILLING_ENABLED"] = ""

    from src import accounts, db, saved
    import app as webapp
    for m in (db, accounts, saved, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    db.init_schema()

    yield type("E", (), dict(app=webapp, db=db, accounts=accounts,
                             saved=saved))

    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in (db, accounts, saved, webapp):
        importlib.reload(m)


def _login(client, env, email="land@example.com"):
    token = env.accounts.issue_login_token(email)
    user = env.accounts.consume_login_token(token)
    with client.session_transaction() as sess:
        sess["uid"] = user["id"]
    return user


def _diagnose_and_save(client, env, **kw):
    h = client.post("/land_diagnose", data=dict(FORM, **kw)).get_data(as_text=True)
    m = re.search(r'name="snap" value="([^"]+)"', h)
    assert m, "保存のトークンが出ていない"
    assert client.post("/save", data={"snap": m.group(1)}).status_code == 302
    return h


# ---- 保存できる ----
def test_the_savebar_appears_on_a_land_result(env):
    c = env.app.app.test_client()
    _login(c, env)
    h = c.post("/land_diagnose", data=FORM).get_data(as_text=True)
    assert "この結果を保存する" in h


def test_a_visitor_is_asked_to_log_in_first(env):
    c = env.app.app.test_client()
    h = c.post("/land_diagnose", data=FORM).get_data(as_text=True)
    assert "ログインして保存する" in h
    assert "この結果を保存する" not in h


def test_a_land_result_can_be_saved_and_read_back(env):
    c = env.app.app.test_client()
    u = _login(c, env)
    _diagnose_and_save(c, env)

    rows = env.saved.listing(u["id"])
    assert len(rows) == 1
    assert rows[0]["kind"] == env.app.LAND_KIND
    assert "小田原市南町" in rows[0]["title"]
    assert rows[0]["total_score"] > 0


def test_the_kind_has_a_japanese_name(env):
    assert env.app._kindja(env.app.LAND_KIND) == "土地（注文住宅）"
    # 「物件」に落ちていないこと。落ちるとマイページで種別が読めない。
    assert env.app._kindja(env.app.LAND_KIND) != "物件"


def test_it_shows_up_on_the_my_page(env):
    c = env.app.app.test_client()
    _login(c, env)
    _diagnose_and_save(c, env)
    page = c.get("/mypage").get_data(as_text=True)
    assert "土地（注文住宅）" in page
    assert "小田原市南町" in page


def test_the_saved_detail_keeps_the_score_breakdown(env):
    c = env.app.app.test_client()
    u = _login(c, env)
    _diagnose_and_save(c, env)
    sid = env.saved.listing(u["id"])[0]["id"]
    page = c.get(f"/saved/{sid}").get_data(as_text=True)
    for name in ("建てられる家", "接道", "リスク", "立地", "資産性", "資金"):
        assert name in page, name


# ---- 再診断 ----
def test_redo_puts_the_answers_back_in_the_form(env):
    c = env.app.app.test_client()
    u = _login(c, env)
    _diagnose_and_save(c, env)
    sid = env.saved.listing(u["id"])[0]["id"]
    page = c.get(f"/saved/{sid}/redo").get_data(as_text=True)
    assert 'value="神奈川県小田原市南町1-1"' in page
    assert 'value="120"' in page
    assert 'value="1800"' in page
    # 世帯年収と頭金は預かっていないので、空で戻る
    assert 'value="800"' not in page


def test_redo_does_not_fall_through_to_the_house_form(env):
    """種別の分岐が無いと、土地の保存から戸建のフォームが開いていた。"""
    c = env.app.app.test_client()
    u = _login(c, env)
    _diagnose_and_save(c, env)
    sid = env.saved.listing(u["id"])[0]["id"]
    page = c.get(f"/saved/{sid}/redo").get_data(as_text=True)
    assert "/land_diagnose" in page or "/pro/land" in page
    assert 'action="/diagnose"' not in page


def test_two_plots_can_be_compared(env):
    c = env.app.app.test_client()
    u = _login(c, env)
    _diagnose_and_save(c, env)
    _diagnose_and_save(c, env, address="神奈川県小田原市栄町2-2",
                       district="栄町", price="2400")
    rows = env.saved.listing(u["id"])
    assert len(rows) == 2
    page = c.get("/mypage").get_data(as_text=True)
    assert "小田原市南町" in page and "小田原市栄町" in page
