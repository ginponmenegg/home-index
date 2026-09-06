# -*- coding: utf-8 -*-
"""保存した物件から、PRO診断と資金計画へ進む導線。ネットワーク不要。

確かめたいのは3つ。
・無料で保存した物件からは、詳細診断にも資金計画にも進めること
・詳細診断で保存した物件は、資金計画にだけ進めること
  （同じ診断をもう一度させても得るものが無い）
・世帯年収と頭金が、引き継ぎの値に混ざらないこと
  プライバシーポリシーで保存しないと明言している。
"""
import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def env(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(prefix="hi_carry_"), "t.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "carry")
    monkeypatch.setenv("RESEND_API_KEY", "")
    from src import db, accounts, saved
    import app as webapp
    for m in (db, accounts, saved, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    db.init_schema()
    c = webapp.app.test_client()
    c.get("/login/" + accounts.issue_login_token("carry@example.com"))
    user = db.run("SELECT * FROM users WHERE email = ?",
                  ("carry@example.com",), "one")
    return type("E", (), dict(app=webapp, db=db, accounts=accounts,
                              saved=saved, c=c, user=dict(user)))


def _payload(**kw):
    base = {"total": 72, "grade": "B", "sufficiency": 80,
            "categories": [{"name": "リスク", "points": 9, "weight": 15,
                            "pct": 60,
                            "reason": "指定ハザード区域に該当なし"}],
            "risks": [], "confirm": [], "spec": {"address": "東京都〇〇"}}
    base.update(kw)
    return base


def _save(env, kind, payload):
    return env.saved.save(env.user, kind, "保存した物件", "東京都〇〇",
                          34_800_000, 72, "B", payload)


def _page(env, sid):
    return env.c.get(f"/saved/{sid}").get_data(as_text=True)


# ---- 無料で保存したもの ---------------------------------------------------

def test_a_free_save_can_go_to_both(env):
    sid = _save(env, "chuko_kodate", _payload(
        kind="chuko_kodate",
        redo={"kind": "kodate", "address": "東京都〇〇", "price": "3480"},
        fin={"price": "3480", "byear": "2010"}))
    h = _page(env, sid)
    assert 'action="/pro/start"' in h, "詳細診断へ進めること"
    assert 'action="/pro/finance_start"' in h, "資金計画へも進めること"
    assert "世帯年収と頭金はお預かりしていない" in h


def test_a_free_mansion_save_goes_to_the_mansion_form(env):
    """戸建のPROフォームへ送ってしまうと、入力が入らない。"""
    sid = _save(env, "chuko_mansion", _payload(
        kind="chuko_mansion",
        redo={"kind": "mansion", "address": "東京都〇〇"},
        fin={"price": "4800"}))
    h = _page(env, sid)
    assert 'action="/pro/mansion_start"' in h
    assert 'action="/pro/start"' not in h


def test_the_saved_inputs_are_actually_carried(env):
    """ボタンの中身が空では意味がない。値が hidden に載っていること。"""
    sid = _save(env, "chuko_kodate", _payload(
        kind="chuko_kodate",
        redo={"kind": "kodate", "address": "東京都〇〇", "price": "3480"},
        fin={"price": "3480", "byear": "2010", "mfee": "12000"}))
    h = _page(env, sid)
    for name, val in (("price", "3480"), ("byear", "2010"),
                      ("mfee", "12000")):
        assert f'name="{name}" value="{val}"' in h, name


# ---- PROで保存したもの ----------------------------------------------------

def test_a_pro_save_only_offers_the_finance_plan(env):
    """PRO診断で保存した物件を、もう一度PRO診断へ送っても得るものが無い。"""
    sid = _save(env, "chuko_mansion", _payload(
        kind="chuko_mansion", sufficiency=92,
        fin={"price": "4800", "mfee": "12000", "rfund": "13000"}))
    h = _page(env, sid)
    assert 'action="/pro/finance_start"' in h
    assert 'action="/pro/start"' not in h
    assert 'action="/pro/mansion_start"' not in h
    assert "次は資金計画です" in h


def test_the_finance_plan_receives_the_saved_diagnosis(env):
    """資金計画のPDFに点数を載せるための署名付きの値が入ること。"""
    sid = _save(env, "chuko_kodate", _payload(
        kind="chuko_kodate", fin={"price": "3480"}))
    h = _page(env, sid)
    assert 'name="dx"' in h
    import re
    m = re.search(r'name="dx" value="([^"]+)"', h)
    got = env.app._unsign_snapshot(m.group(1).replace("&#34;", '"'))
    assert got and got["total"] == 72
    assert got["cats"][0][0] == "リスク"


# ---- 引き継ぐ前に保存されたもの -------------------------------------------

def test_an_older_save_shows_no_broken_button(env):
    sid = _save(env, "chuko_kodate", _payload(kind="chuko_kodate"))
    h = _page(env, sid)
    assert "この物件で先に進む" not in h
    assert 'action="/pro/finance_start"' not in h


# ---- 保存してはいけないもの -----------------------------------------------

def test_income_and_down_payment_never_reach_the_saved_row(env):
    """プライバシーポリシーで保存しないと書いている。fin にも入れない。"""
    from types import SimpleNamespace
    from src.saved import snapshot, NEVER_SAVE
    res = SimpleNamespace(
        diagnosis=SimpleNamespace(
            total_score=70, grade="B", data_sufficiency=80, comment="",
            categories=[], critical_risks=[], strengths=[], weaknesses=[],
            to_confirm=[]),
        price=SimpleNamespace(verdict="判定不可"), loan=None)
    out = snapshot(res, SimpleNamespace(), {}, "chuko_mansion",
                   fin={"price": "4800", "income": "800", "down": "500",
                        "reserve": "100", "mfee": "12000"})
    assert set(out["fin"]) == {"price", "mfee"}
    for k in NEVER_SAVE:
        assert k not in out["fin"]


def test_a_real_save_carries_no_money_of_its_own(env):
    """実際に診断して保存した行にも、年収と頭金が入っていないこと。"""
    sid = _save(env, "chuko_kodate", _payload(
        kind="chuko_kodate", fin={"price": "3480", "loan_years": "35"}))
    row = env.saved.get_one(env.user["id"], sid)
    blob = str(row["payload"])
    for k in env.saved.NEVER_SAVE:
        assert f'"{k}"' not in blob, k
