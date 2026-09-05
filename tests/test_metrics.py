# -*- coding: utf-8 -*-
"""日ごとの件数。ネットワーク不要。

確かめたいのは4つ。
・個人も物件も残らないこと（残るのは日付・名前・件数だけ）
・数える処理が落ちても、利用者の画面を巻き添えにしないこと
・クローラを人の閲覧に混ぜないこと
・見る画面が、鍵を知らない人に開かないこと
"""
import datetime
import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def env(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(prefix="hi_met_"), "t.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "met")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("METRICS_KEY", "ひみつ")
    from src import db, accounts, saved, metrics
    import app as webapp
    for m in (db, accounts, saved, metrics, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    webapp.metrics = metrics
    db.init_schema()
    return type("E", (), dict(app=webapp, db=db, accounts=accounts,
                              metrics=metrics))


# ---- 何が残るか -----------------------------------------------------------

def test_only_a_date_a_name_and_a_count_are_stored(env):
    """個人も物件も残さない。列がそれ以上に増えていないこと。"""
    env.metrics.bump("diag_kodate")
    rows = env.db.run("SELECT * FROM daily_counts", (), "all")
    assert len(rows) == 1
    assert set(rows[0]) == {"day", "name", "n"}
    assert rows[0]["n"] == 1


def test_the_same_event_adds_up_within_a_day(env):
    for _ in range(3):
        env.metrics.bump("diag_kodate")
    assert env.metrics.totals(30)["diag_kodate"] == 3
    assert len(env.db.run("SELECT * FROM daily_counts", (), "all")) == 1, \
        "1日1行にまとまること（人ごとの行を作らない）"


def test_an_unknown_name_is_not_written(env):
    """打ち間違いで表が汚れないように、知らない名前は書かない。"""
    env.metrics.bump("diag_kodateee")
    env.metrics.bump("")
    assert env.db.run("SELECT * FROM daily_counts", (), "all") == []


def test_the_day_is_japan_time(env, monkeypatch):
    """本番はUTC。素直に日付を取ると、朝9時までが前日に付く。"""
    class FixedDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            # 日本時間の 2026-09-09 02:00 ＝ UTCでは 09-08 17:00
            utc = datetime.datetime(2026, 9, 8, 17, 0,
                                    tzinfo=datetime.timezone.utc)
            return utc.astimezone(tz) if tz else utc.replace(tzinfo=None)

    monkeypatch.setattr(env.metrics.datetime, "datetime", FixedDT)
    assert env.metrics.today() == datetime.date(2026, 9, 9)


def test_counting_never_breaks_the_page(env, monkeypatch):
    """数えるのは付随的な処理。DBが詰まっても診断を落とさない。"""
    def boom(*a, **k):
        raise RuntimeError("DBが詰まっている")

    monkeypatch.setattr(env.metrics.db, "run", boom)
    env.metrics.bump("diag_kodate")      # 例外が外へ出ないこと
    c = env.app.app.test_client()
    assert c.get("/buy").status_code == 200


# ---- 実際の経路 -----------------------------------------------------------

def _human(c, path):
    return c.get(path, headers={"User-Agent":
                                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"})


def test_a_visit_is_counted_but_a_crawler_is_not(env):
    c = env.app.app.test_client()
    _human(c, "/buy")
    _human(c, "/buy")
    c.get("/buy", headers={"User-Agent": "Googlebot/2.1"})
    c.get("/buy", headers={"User-Agent": "python-requests/2.31"})
    c.get("/buy")                        # 名乗らないものも人として数えない
    assert env.metrics.totals(30)["view_buy"] == 2


def test_signing_up_is_counted_once_not_on_every_login(env):
    c = env.app.app.test_client()
    for _ in range(3):
        tok = env.accounts.issue_login_token("m@example.com")
        c.get("/login/" + tok)
    t = env.metrics.totals(30)
    assert t["signup"] == 1, "2回目以降はログインであって登録ではない"


# ---- 見る画面 -------------------------------------------------------------

def test_the_page_is_closed_without_the_key(env):
    c = env.app.app.test_client()
    assert c.get("/metrics").status_code == 404
    assert c.get("/metrics?key=ちがう").status_code == 404
    assert c.get("/metrics?key=ひみつ").status_code == 200


def test_the_page_is_absent_when_no_key_is_configured(monkeypatch):
    """鍵を決めていない環境では、画面ごと存在しない。"""
    path = os.path.join(tempfile.mkdtemp(prefix="hi_met2_"), "t.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "met")
    monkeypatch.setenv("METRICS_KEY", "")
    from src import db
    import app as webapp
    importlib.reload(db)
    importlib.reload(webapp)
    db.init_schema()
    assert webapp.app.test_client().get("/metrics?key=").status_code == 404


def test_the_page_shows_what_was_counted(env):
    c = env.app.app.test_client()
    _human(c, "/buy")
    env.metrics.bump("diag_kodate")
    h = c.get("/metrics?key=ひみつ").get_data(as_text=True)
    assert "戸建の診断が出た" in h
    assert "入力画面 → 診断" in h
    assert "個人も物件も記録していません" in h
