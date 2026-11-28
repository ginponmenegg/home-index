# -*- coding: utf-8 -*-
"""メールだけで、この物件の契約前チェックを始める（2026-11-28）。ネットワーク不要。

お試し（1物件無料）は、会員登録（別タブでログイン→戻ってもう一度押す）を
しないと始められず、30日で0件だった。結果の画面でメールアドレスを入れる
だけにし、届いたリンクでログイン・お試しの開始・資金計画の入力画面が
一度に開くようにした。

確かめたいこと
・ログインしていない人の結果に、メールの欄と見本の画像がある
・リンクを開くと、この物件がお試しの物件になり、資金計画の画面が開く
・預かる中身に、年収・頭金・家族の入力が入らない（サーバーに残さない約束）
・リンクは1回かぎり。お試しを別の物件で使い終えた人は、ここでは開かない
"""
import importlib
import os
import re
import sys
import tempfile
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"}
_KODATE = dict(address="神奈川県平塚市田村5丁目12-3", price="4500",
               byear="2010", land="120", building="100", station="10",
               loan_years="35", ptype="chuko_kodate", income="800", down="300")


@pytest.fixture
def env(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(prefix="hi_trm_"), "t.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "trm")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SHINDAN_MOCK", "1")
    monkeypatch.setenv("BILLING_ENABLED", "1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PASS_PRICE_ID", "price_dummy")
    from src import db, accounts, saved, metrics
    import app as webapp
    for m in (db, accounts, saved, metrics, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    webapp.metrics = metrics
    db.init_schema()
    yield type("E", (), dict(app=webapp, db=db, accounts=accounts, saved=saved))
    for k in ("BILLING_ENABLED", "STRIPE_SECRET_KEY", "STRIPE_PASS_PRICE_ID"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(webapp)


def _form(h):
    m = re.search(r'<form method="post" action="/trial/mail" class="trialmail">(.*?)</form>',
                  h, re.S)
    assert m, "ログインしていない人の結果に、メールの欄が無い"
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', m.group(1)))


def _devlink(h):
    m = re.search(r'href="(http[^"]*/login/[^"]+)"', h)
    assert m, h[:500]
    return m.group(1).replace("&amp;", "&")


def _path(url):
    u = urllib.parse.urlsplit(url)
    return u.path + ("?" + u.query if u.query else "")


def _send(env, email="buyer@example.com"):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    data = _form(h)
    data["email"] = email
    r = c.post("/trial/mail", data=data, headers=_UA)
    return c, r.get_data(as_text=True), h


def test_a_guest_sees_an_email_box_and_the_sample_pages(env):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    f = _form(h)
    assert "dx" in f
    assert 'name="email"' in h and "リンクを受け取る" in h
    assert "/static/pro_sample_p1.png" in h and "/static/pro_sample_p3.png" in h
    assert "契約が近づいたときに戻ってきて作れます" in h


def test_the_sample_images_exist():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for n in (1, 3):
        assert os.path.exists(os.path.join(root, "static", f"pro_sample_p{n}.png"))


def test_the_link_opens_the_plan_and_starts_the_trial_on_this_property(env):
    c, sent, _h = _send(env)
    assert "契約前チェックのリンクをお送りしました" in sent
    r = c.get(_path(_devlink(sent)), headers=_UA, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "メールのリンクから開きました" in page
    assert 'action="/pro/finance"' in page
    assert 'name="dx" value="' in page
    u = env.accounts.find_by_email("buyer@example.com") \
        if hasattr(env.accounts, "find_by_email") else None
    rows = env.db.run("SELECT trial_key FROM users WHERE email = ?",
                      ("buyer@example.com",), fetch="all")
    assert rows and rows[0]["trial_key"], "お試しの物件が決まっていない"


def test_nothing_about_income_or_down_payment_is_kept(env):
    c, sent, _h = _send(env)
    rows = env.db.run("SELECT snap FROM pending_saves", fetch="all")
    assert rows
    data = env.app._unsign_snapshot(rows[0]["snap"])
    carry = data["carry"]
    for k in ("income", "down", "income_self", "income_spouse", "children_ages"):
        assert k not in carry, k
    assert carry.get("price") == "4500"


def test_the_link_works_once(env):
    c, sent, _h = _send(env)
    path = _path(_devlink(sent))
    c.get(path, headers=_UA, follow_redirects=True)
    c2 = env.app.app.test_client()
    r = c2.get(path, headers=_UA, follow_redirects=True)
    assert "メールのリンクから開きました" not in r.get_data(as_text=True)


def test_a_used_trial_is_not_spent_again_here(env):
    c, sent, _h = _send(env)
    c.get(_path(_devlink(sent)), headers=_UA, follow_redirects=True)
    # 同じ人が、別の物件でもう一度送ってきた
    other = dict(_KODATE, address="神奈川県平塚市見附町1-1")
    c3 = env.app.app.test_client()
    h = c3.post("/diagnose", data=other, headers=_UA).get_data(as_text=True)
    data = _form(h)
    data["email"] = "buyer@example.com"
    sent2 = c3.post("/trial/mail", data=data, headers=_UA).get_data(as_text=True)
    page = c3.get(_path(_devlink(sent2)), headers=_UA,
                  follow_redirects=True).get_data(as_text=True)
    assert "お試しは、すでに別の物件で使いました" in page


def test_a_bad_email_is_refused(env):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    data = _form(h)
    data["email"] = "not-an-email"
    page = c.post("/trial/mail", data=data, headers=_UA).get_data(as_text=True)
    assert "メールアドレスの形式" in page
