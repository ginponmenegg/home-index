# -*- coding: utf-8 -*-
"""診断された物件の記録。

無料で診断を配っても、こちらには何も残っていなかった。地域ごとの傾向は
このサービスにしか無いデータで、改善にも記事にも使える。

ただし、**人の記録にはしない**。利用者を結びつける鍵を持たなければ、
1行は「誰かの行動」ではなく「物件の観測」になる。ここが崩れると、
プライバシーポリシーに書いたことが嘘になる。
"""
import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.observations import KEEP_DAYS, split_address  # noqa: E402

HOUSE = {"address": "神奈川県小田原市城山1-2-3", "price": "3880",
         "land": "147", "building": "90", "byear": "2005",
         "station": "12", "ptype": "chuko_kodate", "income": "600",
         "down": "300", "loan_years": "35"}
FLAT = {"address": "東京都渋谷区神宮前一丁目1番", "price": "7800",
        "area": "70", "byear": "2010", "station": "8", "floor": "5",
        "total_floors": "10", "mfee": "12000", "rfund": "13000",
        "income": "900", "down": "500", "loan_years": "35"}
LAND = {"address": "千葉県船橋市前原西2丁目", "price": "2100", "area": "125",
        "budget": "2600", "household": "4", "road_width": "4.0",
        "road_type": "公道", "frontage": "9.0", "station": "12",
        "income": "800", "down": "500", "loan_years": "35",
        "condition": "none"}
HUMAN = {"User-Agent": "Mozilla/5.0 (iPhone)"}


@pytest.fixture
def env(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(prefix="hi_obs_"), "t.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "obs")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SHINDAN_MOCK", "1")
    monkeypatch.setenv("METRICS_KEY", "ひみつ")
    from src import db, accounts, saved, observations
    import app as webapp
    for m in (db, accounts, saved, observations, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    webapp.observations = observations
    db.init_schema()
    return type("E", (), dict(app=webapp, db=db, obs=observations))


def _diagnose(env, url, data):
    h = env.app.app.test_client().post(url, data=data, headers=HUMAN)
    body = h.get_data(as_text=True)
    assert "スコア内訳" in body, f"{url} が結果画面になっていない"
    return body


# ---- 住所をどこで切るか ---------------------------------------------------

@pytest.mark.parametrize("addr,want", [
    ("神奈川県小田原市城山1-2-3", ("神奈川県", "小田原市", "城山")),
    ("千葉県船橋市前原西2丁目", ("千葉県", "船橋市", "前原西")),
    ("東京都渋谷区神宮前一丁目1番", ("東京都", "渋谷区", "神宮前")),
    # 政令指定都市は区まで
    ("北海道札幌市中央区北一条西2", ("北海道", "札幌市中央区", "北一条西")),
    # 郡は郡ごと。「市川町」を「市」で切らない
    ("兵庫県神崎郡市川町甘地", ("兵庫県", "神崎郡市川町", "甘地")),
    # 先に「村」で切ると「武蔵村」になる
    ("東京都武蔵村山市大南1-1", ("東京都", "武蔵村山市", "大南")),
    # 「市」で終わる名前に「市」が続く
    ("三重県四日市市諏訪町", ("三重県", "四日市市", "諏訪町")),
    # 漢数字は町名にも使う。「丁目」が続くときだけ落とす
    ("東京都新宿区四谷三栄町11-4", ("東京都", "新宿区", "四谷三栄町")),
])
def test_the_address_is_cut_at_the_town_name(addr, want):
    assert split_address(addr) == want


def test_an_address_it_cannot_read_is_left_empty():
    """推測で埋めない。間違った地域に足すより、空のほうがまし。"""
    assert split_address("おかしな住所") == (None, None, None)
    assert split_address("") == (None, None, None)
    assert split_address(None) == (None, None, None)


def test_no_house_number_survives_anywhere():
    for addr in ("神奈川県小田原市城山1-2-3", "東京都渋谷区神宮前一丁目1番",
                 "千葉県船橋市前原西2丁目15-7"):
        _pref, _city, town = split_address(addr)
        assert town and not any(c.isdigit() for c in town), addr
        assert "丁目" not in town and "番" not in town


# ---- 残るもの・残らないもの -----------------------------------------------

def test_a_diagnosis_leaves_a_row(env):
    _diagnose(env, "/diagnose", HOUSE)
    rows = env.obs.rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "kodate"
    assert (r["pref"], r["city"], r["district"]) == ("神奈川県", "小田原市", "城山")
    assert r["price_yen"] == 38_800_000
    assert r["total_score"] and r["grade"]


def test_all_three_diagnoses_are_recorded(env):
    _diagnose(env, "/diagnose", HOUSE)
    _diagnose(env, "/mansion_diagnose", FLAT)
    _diagnose(env, "/land_diagnose", LAND)
    assert sorted(r["kind"] for r in env.obs.rows()) == ["kodate", "mansion",
                                                         "tochi"]


def test_the_money_questions_are_not_kept(env):
    """世帯年収・頭金・他の借入は、外部にも送らず、ここにも残さない。"""
    _diagnose(env, "/diagnose", HOUSE)
    row = dict(env.obs.rows()[0])
    assert "income" not in row and "down" not in row
    # 600万円・300万円がどの列にも紛れ込んでいないこと
    for v in row.values():
        assert str(v) not in ("6000000.0", "3000000.0", "600", "300")


def test_nothing_ties_a_row_to_a_person(env):
    """ユーザーID・セッション・IPを持たない。ここが本体。"""
    for bad in ("user_id", "session", "ip", "ip_address", "email", "uid"):
        assert bad not in env.obs.COLUMNS, bad
    cols = env.db.run("PRAGMA table_info(observations)", (), "all")
    names = {c["name"] for c in cols}
    assert names & {"user_id", "session_id", "ip"} == set()


def test_the_full_address_is_not_kept(env):
    _diagnose(env, "/diagnose", HOUSE)
    row = dict(env.obs.rows()[0])
    assert "1-2-3" not in str(row.values())
    assert row["district"] == "城山"


def test_a_crawler_is_not_recorded(env):
    """巡回のぶんが地域の傾向に混ざると、数字が読めなくなる。"""
    env.app.app.test_client().post("/diagnose", data=HOUSE,
                                   headers={"User-Agent": "Googlebot/2.1"})
    assert env.obs.count() == 0


def test_a_failure_to_record_does_not_break_the_diagnosis(env, monkeypatch):
    """数えるための処理で、診断を巻き添えにしない。"""
    monkeypatch.setattr(env.db, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("だめ")))
    h = env.app.app.test_client().post("/diagnose", data=HOUSE, headers=HUMAN)
    assert h.status_code == 200
    assert "スコア内訳" in h.get_data(as_text=True)


# ---- 消える ---------------------------------------------------------------

def test_rows_older_than_the_stated_period_are_deleted(env):
    import datetime
    assert KEEP_DAYS == 365 * 3, "プライバシーポリシーに3年と書いてある"
    old = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS + 1))
    env.db.run("INSERT INTO observations (day, kind) VALUES (?, ?)",
               (old.isoformat(), "kodate"))
    keep = (datetime.date.today() - datetime.timedelta(days=10))
    env.db.run("INSERT INTO observations (day, kind) VALUES (?, ?)",
               (keep.isoformat(), "kodate"))
    env.obs.purge()
    days = [r["day"] for r in env.obs.rows()]
    assert days == [keep.isoformat()]


# ---- 書き出し -------------------------------------------------------------

def test_the_csv_needs_the_key(env):
    c = env.app.app.test_client()
    assert c.get("/metrics/data.csv").status_code == 404
    assert c.get("/metrics/data.csv?key=ちがう").status_code == 404


def test_the_csv_comes_out_readable(env):
    _diagnose(env, "/diagnose", HOUSE)
    r = env.app.app.test_client().get("/metrics/data.csv?key=ひみつ")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    text = r.get_data(as_text=True)
    # Excel で開いたときに日本語が化けないよう、先頭に BOM を付ける
    assert text.startswith("﻿")
    head, first = text.lstrip("﻿").splitlines()[:2]
    assert head.split(",")[:4] == ["day", "kind", "pref", "city"]
    assert "小田原市" in first and "城山" in first


# ---- 書いてあることと、していることを合わせる -----------------------------

def test_the_privacy_policy_says_what_is_kept(env):
    """貯めてから書き換える、はできない。先に書いてある状態にする。"""
    h = env.app.app.test_client().get("/privacy").get_data(as_text=True)
    assert "サーバー上での恒久的な保存は行いません" not in h, "古い約束が残っている"
    for word in ("町名まで", "3年間", "世帯年収・頭金・他の借入は残しません",
                 "IPアドレスのいずれも記録しない"):
        assert word in h, word


def test_the_input_screens_say_it_too(env):
    """ポリシーの奥だけに書くのは、書いていないのとあまり変わらない。"""
    c = env.app.app.test_client()
    for url in ("/buy", "/mansion", "/land"):
        h = c.get(url).get_data(as_text=True)
        assert "町名まで" in h, url
