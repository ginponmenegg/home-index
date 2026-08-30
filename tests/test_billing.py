# -*- coding: utf-8 -*-
"""有料プランの画面（プラン・申込確認・解約・特商法表記）。ネットワーク不要。

決済サービスはまだ繋いでいない。ここで確かめるのは、
・課金の準備が整うまで画面が表に出ないこと
・出したときに、特定商取引法の表示義務を満たしていること
・解約が引き止め無しで、かつ誤操作しにくい深さになっていること
の3つ。
"""
import os
import sys
import importlib
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ENV = {"BILLING_ENABLED": "1", "OPERATOR_NAME": "山田 太郎",
        "OPERATOR_ADDRESS": "神奈川県小田原市栄町1-1-1",
        "OPERATOR_TEL": "0465-00-0000",
        "CONTACT_EMAIL": "support@example.jp"}


def _reload(extra=None):
    from src import db, accounts, saved
    import app as webapp
    for k, v in (extra or {}).items():
        os.environ[k] = v
    for m in (db, accounts, saved, webapp):
        importlib.reload(m)
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    db.init_schema()
    return webapp, db, accounts


@pytest.fixture(scope="module")
def billing():
    """課金の準備が整った状態の app を返す。終わったら元に戻す。"""
    keep = {k: os.environ.get(k) for k in
            list(_ENV) + ["DATABASE_URL", "SECRET_KEY", "RESEND_API_KEY"]}
    path = os.path.join(tempfile.mkdtemp(prefix="hi_bill_"), "t.db")
    env = dict(_ENV)
    env["DATABASE_URL"] = "sqlite:///" + path.replace(os.sep, "/")
    env["SECRET_KEY"] = "test-billing-key"
    env["RESEND_API_KEY"] = ""
    webapp, db, accounts = _reload(env)
    yield type("E", (), dict(app=webapp, db=db, accounts=accounts))
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _reload()


def _login(env, email="bill@example.com"):
    c = env.app.app.test_client()
    c.get("/login/" + env.accounts.issue_login_token(email))
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     (email,), "one")["id"]
    return c, uid


# ---- 準備が整うまで出さない -----------------------------------------------

def test_hidden_until_the_operator_details_are_real():
    """氏名・住所・電話が仮のままでは、課金の画面を出さない。

    特定商取引法の表示は、この3つを省略できない。仮の値で申込画面を
    出すのは表示義務違反になるので、フラグだけでは開かないようにする。
    """
    keep = {k: os.environ.get(k) for k in _ENV}
    try:
        env = dict(_ENV)
        env["OPERATOR_ADDRESS"] = ""          # 住所だけ欠けている
        webapp, _, _ = _reload(env)
        assert not webapp.billing_on()
        assert webapp.app.test_client().get("/tokushoho").status_code == 404
        h = webapp.app.test_client().get("/plan").get_data(as_text=True)
        assert "試験公開中" in h and "PROに申し込む" not in h
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reload()


def test_billing_flag_alone_is_not_enough():
    keep = os.environ.get("BILLING_ENABLED")
    try:
        webapp, _, _ = _reload({"BILLING_ENABLED": "1"})
        # 運営者情報が仮のまま（〔運営者名〕）なので False
        assert not webapp.billing_on()
    finally:
        if keep is None:
            os.environ.pop("BILLING_ENABLED", None)
        else:
            os.environ["BILLING_ENABLED"] = keep
        _reload()


# ---- 特定商取引法の表示義務 -----------------------------------------------

def test_final_confirmation_screen_has_all_required_items(billing):
    """注文確定の直前画面に、法定の表示事項が揃っていること。

    令和4年6月施行の改正特商法（第12条の6）。表示が欠けていたり
    誤認させる表示があると、申し込んだ人が契約を取り消せる。
    ⑥申込期間は期間限定販売ではないため該当しない。
    """
    c, _ = _login(billing)
    h = c.get("/plan/confirm").get_data(as_text=True)
    required = {
        "①分量": "診断の回数に制限はありません",
        "②販売価格": "1,980円（税込）",
        "②2回目以降": "2回目以降も同額",
        "②総額": "解約されるまで、毎月",
        "③支払方法": "クレジットカード",
        "③支払時期": "毎月同じ日に自動で決済",
        "④提供時期": "決済の完了後、ただちに",
        "⑤解約の方法": "マイページ →「プランと設定」",
        "⑤返金": "日割りでの返金は行っておりません",
    }
    for label, needle in required.items():
        assert needle in h, label


def test_cancellation_method_is_on_the_screen_not_behind_a_link(billing):
    """解約方法は「見やすい位置」に書く。規約へのリンクで済ませない。"""
    c, _ = _login(billing, "onscreen@example.com")
    h = c.get("/plan/confirm").get_data(as_text=True)
    i = h.index("解約の方法")
    # 見出しの近くに手順そのものがあること
    assert "マイページ" in h[i:i + 400]


def test_tokushoho_page_shows_the_three_items_that_cannot_be_omitted(billing):
    h = billing.app.app.test_client().get("/tokushoho").get_data(as_text=True)
    for k in ["山田 太郎", "神奈川県小田原市栄町1-1-1", "0465-00-0000"]:
        assert k in h, k
    assert "日割りでの返金も行っておりません" in h


def test_terms_gain_the_billing_clauses(billing):
    h = billing.app.app.test_client().get("/terms").get_data(as_text=True)
    for k in ["有料プラン", "自動で更新", "第10条（解約）", "第11条（返金）",
              "1か月前までに"]:
        assert k in h, k


# ---- 解約導線 -------------------------------------------------------------

def test_cancelling_takes_a_confirmation_step(billing):
    """ボタン1つで即完了にしない。誤操作を防ぐため。"""
    c, uid = _login(billing, "cancel@example.com")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO,
                              "2099-01-01T00:00:00+00:00")
    # 1画面目：プランから解約へ入れる
    assert "/plan/cancel" in c.get("/plan").get_data(as_text=True)
    # 2画面目：GETでは解約されない
    h = c.get("/plan/cancel").get_data(as_text=True)
    assert "解約を確定する" in h
    assert billing.accounts.is_pro(billing.accounts.get_user(uid)), \
        "確認画面を見ただけで解約されてはいけない"
    # 3画面目：POSTで確定
    h = c.post("/plan/cancel").get_data(as_text=True)
    assert "解約しました" in h
    assert not billing.accounts.is_pro(billing.accounts.get_user(uid))


def test_the_confirmation_says_what_survives(billing):
    """解約する人が知るべきことを、確認画面で示す。"""
    c, uid = _login(billing, "survive@example.com")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO,
                              "2099-12-31T00:00:00+00:00")
    h = c.get("/plan/cancel").get_data(as_text=True)
    assert "2099-12-31" in h, "いつまで使えるか"
    assert "保存した診断は消えません" in h
    assert "日割りでの返金はありません" in h
    assert "いつでも再開できます" in h


def test_no_retention_tactics(billing):
    """引き止めは置かない。前回の合意どおり。"""
    c, uid = _login(billing, "noretain@example.com")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO, None)
    h = c.get("/plan/cancel").get_data(as_text=True)
    for banned in ["一時停止", "プランを変更", "特典", "もう一度お考え",
                   "お得"]:
        assert banned not in h, banned


def test_the_survey_comes_after_cancelling_not_before(billing):
    """アンケートを解約の前に置くと妨害になる。後なら妨害にならない。"""
    c, uid = _login(billing, "survey@example.com")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO, None)
    before = c.get("/plan/cancel").get_data(as_text=True)
    assert "理由" not in before, "解約の前に理由を聞かない"
    after = c.post("/plan/cancel").get_data(as_text=True)
    assert "理由を1つだけ" in after
    # 答えなくても解約は済んでいる
    assert not billing.accounts.is_pro(billing.accounts.get_user(uid))
    assert "答えずに戻る" in after


def test_saved_diagnoses_survive_cancelling(billing):
    """解約しても保存は消さない。超過分も読める（新規保存だけ止まる）。"""
    from src import saved as saved_mod
    c, uid = _login(billing, "keep@example.com")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO, None)
    u = billing.accounts.get_user(uid)
    payload = {"kind": "chuko_kodate", "total": 70, "grade": "B",
               "sufficiency": 48.0, "categories": [], "risks": [],
               "strengths": [], "weaknesses": [], "spec": {},
               "price": {"verdict": "判定不可"}}
    ids = [saved_mod.save(u, "chuko_kodate", f"物件{i}", "住所", 1, 70, "B",
                          payload) for i in range(saved_mod.FREE_LIMIT + 2)]
    c.post("/plan/cancel")

    u2 = billing.accounts.get_user(uid)
    assert not billing.accounts.is_pro(u2)
    # 全部残っていて、読める
    assert len(saved_mod.listing(uid)) == len(ids)
    assert len(saved_mod.get_many(uid, ids[:3])) == 3
    assert c.get(f"/saved/{ids[0]}").status_code == 200
    # 比較もできる
    assert c.get(f"/compare?id={ids[0]}&id={ids[1]}").status_code == 200
    # 新規保存だけ止まる
    with pytest.raises(saved_mod.LimitReached):
        saved_mod.save(u2, "chuko_kodate", "追加", "住所", 1, 70, "B", payload)
    # そのことが一覧に書いてある
    assert "これまでの保存はすべてご覧いただけます" in \
        c.get("/mypage").get_data(as_text=True)


def test_subscribe_is_not_wired_yet(billing):
    """決済は未接続。押しても課金されないこと。"""
    c, uid = _login(billing, "notwired@example.com")
    h = c.post("/plan/subscribe").get_data(as_text=True)
    assert "準備中" in h
    assert not billing.accounts.is_pro(billing.accounts.get_user(uid))
