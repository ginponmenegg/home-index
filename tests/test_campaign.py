# -*- coding: utf-8 -*-
"""ローンチ時のキャンペーン価格。ネットワーク不要。

確かめたいのは3つ。
・期間の内と外で、表示と請求が同時に切り替わること
・境目が日本時間であること（本番はUTCなので、ここを外すと9時間ずれる）
・据え置きの約束が、特商法の最終確認画面に書いてあること
"""
import datetime
import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import campaign  # noqa: E402

FROM, UNTIL = "2026-09-08", "2026-12-07"

_ENV = {"BILLING_ENABLED": "1", "OPERATOR_NAME": "山田 太郎",
        "OPERATOR_ADDRESS": "神奈川県小田原市栄町1-1-1",
        "OPERATOR_TEL": "0465-00-0000",
        "CONTACT_EMAIL": "support@example.jp",
        "STRIPE_SECRET_KEY": "sk_test_dummy",
        "STRIPE_PRICE_ID": "price_normal",
        "STRIPE_WEBHOOK_SECRET": "whsec_dummy",
        "CAMPAIGN_PRICE_ID": "price_campaign",
        "CAMPAIGN_PRICE_YEN": "1980",
        "CAMPAIGN_FROM": FROM,
        "CAMPAIGN_UNTIL": UNTIL}


def _d(s):
    return datetime.date.fromisoformat(s)


@pytest.fixture
def env(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    yield


# ---- 期間の判定 -----------------------------------------------------------

@pytest.mark.parametrize("day,expect", [
    ("2026-09-07", False),   # 前日
    ("2026-09-08", True),    # 初日は含む
    ("2026-10-20", True),
    ("2026-12-07", True),    # 最終日も含む
    ("2026-12-08", False),   # 翌日
])
def test_the_window_includes_both_ends(env, day, expect):
    assert campaign.active(_d(day)) is expect, day


def test_the_boundary_is_japan_time(env, monkeypatch):
    """本番はUTC。素直に今日を取ると9時間ずれる。

    12月8日の午前0時（日本時間）に終わってほしい。UTCで見ていると、
    その瞬間はまだ12月7日15時なので、9時間ぶん余計に安く売れてしまう。
    """
    class FixedDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            # 日本時間の 2026-12-08 00:30 ＝ UTCでは 12-07 15:30
            utc = datetime.datetime(2026, 12, 7, 15, 30,
                                    tzinfo=datetime.timezone.utc)
            return utc.astimezone(tz) if tz else utc.replace(tzinfo=None)

    monkeypatch.setattr(campaign.datetime, "datetime", FixedDT)
    assert campaign.today() == _d("2026-12-08"), "日本時間で見ていない"
    assert not campaign.active(), "日本時間ではもう終わっている"


def test_a_missing_price_turns_the_campaign_off(env, monkeypatch):
    """日付だけ設定されていても、請求先のPriceが無ければ出さない。"""
    monkeypatch.setenv("CAMPAIGN_PRICE_ID", "")
    assert not campaign.active(_d("2026-10-01"))


def test_a_broken_date_falls_back_to_the_normal_price(env, monkeypatch):
    """設定の書き損じで、うっかり安く売り続けない。"""
    monkeypatch.setenv("CAMPAIGN_UNTIL", "2026/12/07")
    assert not campaign.active(_d("2026-10-01"))


def test_no_settings_means_no_campaign(monkeypatch):
    for k in ("CAMPAIGN_PRICE_ID", "CAMPAIGN_FROM", "CAMPAIGN_UNTIL",
              "CAMPAIGN_PRICE_YEN"):
        monkeypatch.delenv(k, raising=False)
    assert not campaign.active()


def test_the_end_date_reads_as_japanese(env):
    assert campaign.until_ja() == "2026年12月7日"


# ---- 画面と請求 -----------------------------------------------------------

def _app(monkeypatch, day):
    """その日付でアプリを組み立てる。"""
    path = os.path.join(tempfile.mkdtemp(prefix="hi_camp_"), "t.db")
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.replace(os.sep, "/"))
    monkeypatch.setenv("SECRET_KEY", "camp")
    monkeypatch.setenv("RESEND_API_KEY", "")
    from src import db, accounts, saved
    import app as webapp
    for m in (db, accounts, saved, campaign, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    monkeypatch.setattr(webapp.campaign, "today", lambda: _d(day))
    db.init_schema()
    return webapp, accounts


def _login(webapp, accounts, email):
    c = webapp.app.test_client()
    c.get("/login/" + accounts.issue_login_token(email))
    return c


def test_the_pages_show_the_campaign_price(monkeypatch):
    webapp, accounts = _app(monkeypatch, "2026-10-01")
    c = _login(webapp, accounts, "in@example.com")
    h = c.get("/plan").get_data(as_text=True)
    assert "1,980円" in h
    assert "2026年12月7日までにお申し込みの方は" in h
    assert "そのあともずっとこの金額です" in h
    assert "再開のお申し込みは通常価格" in h, "解約後の扱いを書いておく"
    k = c.get("/plan/confirm").get_data(as_text=True)
    assert "月額 1,980円（税込）" in k
    assert "毎月1980円（税込）が発生します" in k, "総額の欄も同じ金額であること"
    assert "あとから値上がりすることはありません" in k
    assert "3か月なら5940円" in k


def test_the_pages_go_back_to_normal_after_the_window(monkeypatch):
    webapp, accounts = _app(monkeypatch, "2026-12-08")
    c = _login(webapp, accounts, "out@example.com")
    h = c.get("/plan").get_data(as_text=True)
    assert "2,980円" in h
    assert "1,980" not in h, "終わったキャンペーンを出したままにしない"
    k = c.get("/plan/confirm").get_data(as_text=True)
    assert "月額 2,980円（税込）" in k
    assert "初回だけ安くなる" in k, "通常時の一文に戻ること"


def test_the_price_charged_matches_the_price_shown(monkeypatch):
    """画面が1,980円なら、Stripeに渡すPriceもキャンペーンのものであること。

    表示と請求を別々に組み立てると、片方だけ直し忘れる。
    """
    for day, want in (("2026-10-01", "price_campaign"),
                      ("2026-12-08", None)):
        webapp, accounts = _app(monkeypatch, day)
        c = _login(webapp, accounts, f"pay{day}@example.com")
        seen = {}
        monkeypatch.setattr(webapp.billing, "checkout_url",
                            lambda **kw: (seen.update(kw) or "https://x/"))
        assert c.post("/plan/subscribe").status_code == 303
        assert seen["price"] == want, day


def test_the_window_does_not_touch_anyone_already_subscribed(monkeypatch):
    """契約中の人の金額は、期間が終わっても変わらない。

    据え置きはStripe側の仕組みで効く（サブスクリプションがPriceを参照
    し続ける）。こちらが後から何かする経路が無いことを確かめる。
    """
    webapp, accounts = _app(monkeypatch, "2026-12-08")
    c = _login(webapp, accounts, "already@example.com")
    uid = webapp.db.run("SELECT id FROM users WHERE email = ?",
                        ("already@example.com",), "one")["id"]
    accounts.set_plan(uid, accounts.PLAN_PRO, "2099-01-01T00:00:00+00:00")
    h = c.get("/plan").get_data(as_text=True)
    assert "PROをご利用中です" in h
    assert "解約する" in h
