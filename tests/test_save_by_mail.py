# -*- coding: utf-8 -*-
"""登録の理由（2026-11-01）。ネットワーク不要。

10/21からの11日、ログインのメールは1通も送られなかった。保存するには
ログイン画面を別のタブで開き、メールのリンクを押し、元のタブに戻って
もう一度押す必要があった。結果画面でメールアドレスを入れるだけにし、
届いたリンクで保存と会員登録が一度に終わるようにした。

確かめたいこと
・リンクを押すと、ログイン（初めてなら会員登録）と保存が一度に終わる
・リンクを押すまでは、アカウントに何も入らない（他人のアドレス対策）
・メールの本文に所在地を書かない
・前に診断した物件と並べるための要点に、番地や家計の入力が入らない
"""
import datetime
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
    path = os.path.join(tempfile.mkdtemp(prefix="hi_svm_"), "t.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "svm")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SHINDAN_MOCK", "1")
    from src import db, accounts, saved, metrics
    import app as webapp
    for m in (db, accounts, saved, metrics, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    webapp.metrics = metrics
    db.init_schema()
    return type("E", (), dict(app=webapp, db=db, accounts=accounts,
                              saved=saved, metrics=metrics))


def _snap(h):
    m = re.search(r'action="/save/mail".*?name="snap" value="([^"]+)"', h, re.S)
    assert m, "ログインしていない人の結果に、保存の欄が無い"
    return m.group(1)


def _devlink(h):
    m = re.search(r'href="(http[^"]*/login/[^"]+)"', h)
    assert m, h[:400]
    return m.group(1).replace("&amp;", "&")


def _ask(env, email="buyer@example.com"):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    r = c.post("/save/mail", data={"snap": _snap(h), "email": email},
               headers=_UA)
    return c, r.get_data(as_text=True)


def _path(url):
    u = urllib.parse.urlsplit(url)
    return u.path + ("?" + u.query if u.query else "")


# ---- A：保存とメールの確認を1回で ----------------------------------------

def test_a_guest_sees_an_email_box_instead_of_a_second_tab(env):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    assert 'action="/save/mail"' in h
    assert "保存用のリンクを送る" in h
    assert "別のタブで開きます" not in h


def test_the_link_signs_up_and_saves_in_one_go(env):
    c, h = _ask(env)
    assert "保存用のリンクをお送りしました" in h
    assert "24時間" in h
    assert env.db.run("SELECT COUNT(*) AS n FROM users", (), "one")["n"] == 0, \
        "リンクを押すまで、会員は作らない"
    r = c.get(_path(_devlink(h)), headers=_UA)
    assert r.status_code == 302 and "/save/claim/" in r.headers["Location"]
    r = c.get(r.headers["Location"], headers=_UA)
    assert r.headers["Location"].endswith("/mypage?added=1")
    u = env.db.run("SELECT * FROM users WHERE email = ?",
                   ("buyer@example.com",), "one")
    rows = env.db.run("SELECT * FROM saved_diagnoses WHERE user_id = ?",
                      (u["id"],), "all")
    assert len(rows) == 1 and rows[0]["total_score"] is not None
    assert "800" not in rows[0]["payload"] or "income" not in rows[0]["payload"]
    t = env.metrics.totals(1)
    assert t["signup"] == 1 and t["saved"] == 1 and t["save_mail_done"] == 1
    assert env.db.run("SELECT COUNT(*) AS n FROM pending_saves", (),
                      "one")["n"] == 0, "保存したら預かりは消す"


def test_the_link_lasts_a_day_but_only_once(env):
    c, h = _ask(env)
    link = _path(_devlink(h))
    row = env.db.run("SELECT expires_at FROM login_tokens", (), "one")
    left = (datetime.datetime.fromisoformat(row["expires_at"])
            - datetime.datetime.now(datetime.timezone.utc))
    assert datetime.timedelta(hours=23) < left <= datetime.timedelta(hours=24)
    c.get(link, headers=_UA)
    c2 = env.app.app.test_client()
    assert c2.get(link, headers=_UA).status_code == 400


def test_someone_elses_account_cannot_claim_it(env):
    _c, h = _ask(env, "owner@example.com")
    claim = urllib.parse.parse_qs(
        urllib.parse.urlsplit(_devlink(h)).query)["next"][0]
    other = env.app.app.test_client()
    other.get("/login/" + env.accounts.issue_login_token("other@example.com"))
    r = other.get(claim, headers=_UA)
    assert r.status_code == 400
    assert env.db.run("SELECT COUNT(*) AS n FROM saved_diagnoses", (),
                      "one")["n"] == 0
    assert env.db.run("SELECT COUNT(*) AS n FROM pending_saves", (),
                      "one")["n"] == 1, "本人の分は残す"


def test_an_expired_pending_save_is_not_saved(env):
    c, h = _ask(env)
    env.db.run("UPDATE pending_saves SET expires_at = ?",
               ("2000-01-01T00:00:00+00:00",))
    r = c.get(_path(_devlink(h)), headers=_UA)
    r = c.get(r.headers["Location"], headers=_UA)
    assert r.status_code == 400
    assert "保存できませんでした" in r.get_data(as_text=True)


def test_a_bad_address_gets_the_form_back_with_the_result_kept(env):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    snap = _snap(h)
    h = c.post("/save/mail", data={"snap": snap, "email": "nope"},
               headers=_UA).get_data(as_text=True)
    assert "メールアドレスの形式をご確認ください" in h
    assert snap in h


def test_a_forged_result_is_not_accepted(env):
    c = env.app.app.test_client()
    r = c.post("/save/mail", data={"snap": "forged", "email": "a@example.com"},
               headers=_UA)
    assert r.status_code == 302
    assert env.db.run("SELECT COUNT(*) AS n FROM pending_saves", (),
                      "one")["n"] == 0


def test_the_mail_never_carries_the_address(env, monkeypatch):
    sent = {}

    def fake(to, url, summary, ttl_h):
        sent.update(to=to, summary=summary, ttl=ttl_h)
        return True, "id"
    monkeypatch.setattr(env.app.mailer, "send_save_link", fake)
    _ask(env)
    assert sent["ttl"] == 24
    assert "平塚" not in sent["summary"] and "田村" not in sent["summary"]
    assert "中古戸建" in sent["summary"] and "4,500万円" in sent["summary"]
    assert env.metrics.totals(1)["save_mail_sent"] == 1


def test_a_logged_in_person_just_saves(env):
    c = env.app.app.test_client()
    c.get("/login/" + env.accounts.issue_login_token("me@example.com"))
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    m = re.search(r'action="/save".*?name="snap" value="([^"]+)"', h, re.S)
    r = c.post("/save/mail", data={"snap": m.group(1)}, headers=_UA)
    assert r.headers["Location"].endswith("/mypage?added=1")


# ---- B：前に診断した物件と並べる ------------------------------------------

def _recent(h):
    m = re.search(r'id="hi-recent-data">(.*?)</script>', h, re.S)
    return m.group(1) if m else None


def test_a_free_result_carries_its_summary_for_the_browser(env):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    import json
    r = json.loads(_recent(h))
    assert r["k"] == "kodate" and r["p"] == "4,500万円"
    assert r["l"] and "12" not in r["l"], "番地は落とす"
    assert "800" not in _recent(h) and "income" not in _recent(h)
    assert {c[0] for c in r["c"]} >= {"価格", "リスク"}
    assert "この端末の記録を消す" in h


def test_the_sample_does_not_go_into_the_browser_list(env):
    c = env.app.app.test_client()
    h = c.get("/sample", headers=_UA).get_data(as_text=True)
    assert _recent(h) is None


def test_the_browser_tells_us_when_it_showed_a_comparison(env):
    c = env.app.app.test_client()
    c.get("/_h.gif?p=recent_shown&1", headers=_UA)
    assert env.metrics.totals(1)["recent_shown"] == 1


# ---- C：お試しは取っておける ----------------------------------------------

def test_the_trial_is_said_to_keep_until_needed(env):
    c = env.app.app.test_client()
    h = c.post("/diagnose", data=_KODATE, headers=_UA).get_data(as_text=True)
    assert "本命が決まってから選べます" in h
    # ログイン画面と結果のお試しの枠は、課金が有効なときだけ出る
    # （tests/test_billing.py で確かめている）


def test_the_privacy_policy_says_what_the_mail_and_browser_keep(env):
    h = env.app.app.test_client().get("/privacy").get_data(as_text=True)
    assert "所在地は書きません" in h
    assert "localStorage" in h
    assert "最長24時間" in h
