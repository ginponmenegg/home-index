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

# 決済の鍵が無いと申込画面は出ない（押した先で失敗するため）。
# ここではStripeを呼ばないので、形だけのテスト鍵で足りる。
_ENV = {"BILLING_ENABLED": "1", "OPERATOR_NAME": "山田 太郎",
        "OPERATOR_ADDRESS": "神奈川県小田原市栄町1-1-1",
        "OPERATOR_TEL": "0465-00-0000",
        "CONTACT_EMAIL": "support@example.jp",
        "STRIPE_SECRET_KEY": "sk_test_dummy",
        "STRIPE_PRICE_ID": "price_dummy",
        "STRIPE_WEBHOOK_SECRET": "whsec_dummy"}


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
    """フラグを立てても、運営者情報が無ければ課金の画面は出ない。

    値を消すのではなく空文字にする。app.py は起動時に .env を読み直すので、
    消しただけでは開発用のダミーが戻ってきてしまう。
    """
    keys = ["BILLING_ENABLED", "OPERATOR_NAME", "OPERATOR_ADDRESS",
            "OPERATOR_TEL", "CONTACT_EMAIL"]
    keep = {k: os.environ.get(k) for k in keys}
    try:
        env = {k: "" for k in keys}
        env["BILLING_ENABLED"] = "1"
        webapp, _, _ = _reload(env)
        assert not webapp.billing_on()
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
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
        "②販売価格": "2,980円（税込）",
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


def test_the_trade_name_is_composed_on_the_page_not_in_the_variable(billing):
    """屋号は画面側で組み立てる。環境変数には氏名だけを入れる。

    「HOME INDEX 運営責任者 氏名」をまるごと変数に入れると、
    運営責任者の欄で二重になり、構造化データの Person.name も
    肩書き込みになって、誰が書いたかの信号として働かなくなる。
    """
    c = billing.app.app.test_client()
    h = c.get("/tokushoho").get_data(as_text=True)
    assert "HOME INDEX（山田 太郎）" in h, "販売事業者は屋号と氏名を併記"
    i = h.index("運営責任者")
    assert "HOME INDEX" not in h[i:i + 120], "運営責任者の欄で屋号を繰り返さない"
    # 構造化データの名前は、人の名前だけ
    a = c.get("/about").get_data(as_text=True)
    assert '"name":"山田 太郎"' in a


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


# ---- Stripeとの接続 -------------------------------------------------------
#
# 実際にStripeを呼ばない。呼ぶ関数を差し替えて、こちら側の道筋だけを見る。
# 見たいのは「プランが動くのは署名を確かめたWebhookだけ」という一点。


def test_subscribe_sends_the_buyer_to_stripe(billing, monkeypatch):
    """カード番号はこのサーバーを通さない。Stripeのページへ送る。"""
    c, uid = _login(billing, "checkout@example.com")
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return "https://checkout.stripe.com/c/pay/test123"

    monkeypatch.setattr(billing.app.billing, "checkout_url", fake)
    r = c.post("/plan/subscribe")
    assert r.status_code == 303
    assert r.headers["Location"].startswith("https://checkout.stripe.com/")
    assert seen["user_id"] == uid, "誰の支払いかをStripeに渡すこと"
    assert seen["success_url"].endswith(
        "/plan/done?session_id={CHECKOUT_SESSION_ID}"), \
        "戻り先でStripeに問い合わせられるよう、セッションIDを受け取る"
    # 押しただけではPROにならない
    assert not billing.accounts.is_pro(billing.accounts.get_user(uid))


def test_the_return_screen_does_not_grant_the_plan(billing):
    """戻り先は誰でも開ける。ここでプランを上げない。"""
    c, uid = _login(billing, "done@example.com")
    h = c.get("/plan/done").get_data(as_text=True)
    assert "確認をしています" in h
    assert not billing.accounts.is_pro(billing.accounts.get_user(uid))


def test_a_webhook_without_a_valid_signature_is_refused(billing):
    """署名を確かめないと、誰でもPOSTでPROになれる。"""
    c = billing.app.app.test_client()
    r = c.post("/stripe/webhook",
               data=b'{"type":"checkout.session.completed"}',
               headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    assert r.status_code == 400


def _signed(payload_dict, secret="whsec_dummy"):
    """Stripeが送ってくるのと同じ形の本文と署名を作る。

    素のdictを直に渡すテストでは、本番で起きることを再現できない。
    Stripeが実際に渡してくるのは dict ではなく Session/Subscription で、
    それらは .get() を受け付けない。construct_event を通すことで、
    本番と同じ型がハンドラに届く。
    """
    import hashlib
    import hmac
    import json
    import time
    body = json.dumps(payload_dict).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body,
                   hashlib.sha256).hexdigest()
    return body, f"t={ts},v1={sig}"


def _event(kind, obj):
    return {"id": "evt_1", "object": "event", "type": kind,
            "api_version": "2024-06-20", "created": 1700000000,
            "data": {"object": obj}}


def test_a_completed_checkout_turns_the_plan_on(billing, monkeypatch):
    """署名の検証からプラン変更まで、経路を丸ごと通す。

    ここを素のdictで済ませると、Stripeのオブジェクトが dict ではない
    ことに気づけない。実際それで本番だけ500になった。
    """
    c, uid = _login(billing, "hook@example.com")
    monkeypatch.setattr(billing.app.billing, "subscription",
                        lambda i: _stripe_obj("Subscription", {
                            "id": "sub_1", "object": "subscription",
                            "current_period_end": 4102444800}))
    body, sig = _signed(_event("checkout.session.completed", {
        "id": "cs_1", "object": "checkout.session", "payment_status": "paid",
        "client_reference_id": str(uid), "customer": "cus_1",
        "subscription": "sub_1"}))
    r = c.post("/stripe/webhook", data=body,
               headers={"Stripe-Signature": sig,
                        "Content-Type": "application/json"})
    assert r.status_code == 200, r.get_data(as_text=True)
    u = billing.accounts.get_user(uid)
    assert billing.accounts.is_pro(u)
    assert u["stripe_customer_id"] == "cus_1"
    assert u["stripe_subscription_id"] == "sub_1"
    assert u["plan_expires_at"].startswith("2100-"), "期間末を取れていない"


def _stripe_obj(cls, data):
    import stripe
    return getattr(stripe, cls).construct_from(data, "sk_test_dummy")


def test_stripe_objects_are_not_dicts():
    """この前提が崩れたら、読み取りを見直すこと。

    stripe-python の API リソースは dict ではなく、.get() は例外を
    投げる。__getitem__ と getattr は通る。ここが変わると _sv の
    書き方も変わるので、前提そのものを固定しておく。
    """
    import pytest as _pytest
    o = _stripe_obj("Subscription", {"id": "sub_x", "object": "subscription",
                                     "status": "active"})
    assert not isinstance(o, dict)
    with _pytest.raises(AttributeError):
        o.get("status")
    assert o["status"] == "active"
    assert getattr(o, "status", None) == "active"


def test_a_failed_payment_drops_the_plan(billing, monkeypatch):
    """支払いが止まればPROではなくなる。顧客IDから会員を辿る。"""
    c, uid = _login(billing, "unpaid@example.com")
    billing.accounts.set_stripe_ids(uid, "cus_2", "sub_2")
    for status, expect in (("active", True), ("past_due", False)):
        body, sig = _signed(_event("customer.subscription.updated",
                                   {"id": "sub_2", "object": "subscription",
                                    "customer": "cus_2", "status": status}))
        r = c.post("/stripe/webhook", data=body,
                   headers={"Stripe-Signature": sig,
                            "Content-Type": "application/json"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert billing.accounts.is_pro(
            billing.accounts.get_user(uid)) is expect, status


def test_a_deleted_subscription_ends_the_plan(billing):
    c, uid = _login(billing, "gone@example.com")
    billing.accounts.set_stripe_ids(uid, "cus_3", "sub_3")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO,
                              "2099-01-01T00:00:00+00:00")
    body, sig = _signed(_event("customer.subscription.deleted",
                               {"id": "sub_3", "object": "subscription",
                                "customer": "cus_3", "status": "canceled"}))
    r = c.post("/stripe/webhook", data=body,
               headers={"Stripe-Signature": sig,
                        "Content-Type": "application/json"})
    assert r.status_code == 200, r.get_data(as_text=True)
    u = billing.accounts.get_user(uid)
    assert not billing.accounts.is_pro(u)
    assert not u["plan_expires_at"], "期限も消すこと"


def test_an_unknown_event_changes_nothing(billing):
    c, uid = _login(billing, "noise@example.com")
    before = billing.accounts.get_user(uid)
    body, sig = _signed(_event("invoice.upcoming",
                               {"id": "in_1", "object": "invoice",
                                "customer": "cus_x"}))
    r = c.post("/stripe/webhook", data=body,
               headers={"Stripe-Signature": sig,
                        "Content-Type": "application/json"})
    assert r.status_code == 200
    assert billing.accounts.get_user(uid)["plan"] == before["plan"]


def test_the_screens_stay_hidden_without_payment_keys():
    """鍵が無いまま申込画面を出すと、押した先で失敗する。出さない。"""
    import importlib
    keep = os.environ.get("STRIPE_SECRET_KEY")
    os.environ["STRIPE_SECRET_KEY"] = ""
    try:
        from src import billing as b
        importlib.reload(b)
        assert not b.enabled()
    finally:
        if keep is None:
            os.environ.pop("STRIPE_SECRET_KEY", None)
        else:
            os.environ["STRIPE_SECRET_KEY"] = keep
        from src import billing as b2
        importlib.reload(b2)


# ---- 売っているものを、同時に無料で配らない -------------------------------
#
# 試験公開のあいだ /pro/* は誰でも開けた。料金をいただいていなかったため。
# 課金を始めた以上、同じものが無料で使えると、払う理由が無くなる。

_PRO_PAGES = ["/pro/diagnose", "/pro/mansion", "/pro/finance"]
_PRO_POSTS = ["/pro/start", "/pro/mansion_start", "/pro/finance_start",
              "/pro/finance.pdf"]


def test_pro_is_closed_to_visitors_while_charging(billing):
    """未ログインではPROの画面に入れない。"""
    c = billing.app.app.test_client()
    for p in _PRO_PAGES:
        assert c.get(p).status_code in (301, 302), p
    for p in _PRO_POSTS:
        assert c.post(p, data={}).status_code in (301, 302), p


def test_a_free_member_is_shown_the_plan_instead(billing):
    """無料会員には、フォームではなく案内を出す。

    入口で止める。フォームを見せて結果だけ隠すと、入力し終えてから
    お金の話をすることになる。
    """
    c, _uid = _login(billing, "freemember@example.com")
    for p in _PRO_PAGES:
        h = c.get(p).get_data(as_text=True)
        assert "PROプランの機能です" in h, p
        assert "<form" not in h.split("PROプランの機能です")[1][:2000], p
    for p in _PRO_POSTS:
        assert "PROプランの機能です" in c.post(p, data={}).get_data(as_text=True), p


def test_a_subscriber_can_use_pro(billing):
    c, uid = _login(billing, "promember@example.com")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO, None)
    for p in _PRO_PAGES:
        h = c.get(p).get_data(as_text=True)
        assert "PROプランの機能です" not in h, p
        assert "<form" in h, p


def test_the_free_trial_wording_is_gone_once_charging(billing):
    """「いまは無料」と書いたまま2,980円を請求しない。"""
    c, uid = _login(billing, "wording@example.com")
    billing.accounts.set_plan(uid, billing.accounts.PLAN_PRO, None)
    for p in _PRO_PAGES + ["/pro", "/plan"]:
        assert "試験公開中" not in c.get(p).get_data(as_text=True), p


def test_the_sitemap_drops_the_pages_behind_the_paywall(billing):
    """開けないURLを検索エンジンに出さない。/pro は案内なので残す。"""
    sm = billing.app.app.test_client().get("/sitemap.xml").get_data(as_text=True)
    assert "/pro<" in sm or "/pro</loc>" in sm
    for p in _PRO_PAGES:
        assert p not in sm, p


def test_pro_stays_open_while_it_is_free():
    """試験公開中は誰でも使える。閉じるのは課金を始めてから。"""
    keep = {k: os.environ.get(k) for k in ("BILLING_ENABLED",
                                           "STRIPE_SECRET_KEY")}
    try:
        os.environ["BILLING_ENABLED"] = ""
        os.environ["STRIPE_SECRET_KEY"] = ""
        webapp, _db, _ac = _reload()
        assert not webapp.billing_on()
        c = webapp.app.test_client()
        for p in _PRO_PAGES:
            h = c.get(p)
            assert h.status_code == 200, p
            assert "<form" in h.get_data(as_text=True), p
        assert "試験公開中" in c.get("/pro/diagnose").get_data(as_text=True)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reload()


# ---- Webhookが届かなかったときの保険 ---------------------------------------
#
# 正はWebhook。ただ設定を取り違えると、払った人が「PROになっていない」
# 画面を見ることになる。戻ってきた人については、こちらからStripeに
# 「この決済は済んでいるか」を聞きに行く。


def _fake_session(uid, paid=True, cust="cus_z", sub="sub_z"):
    """本番と同じ型を返す。dictを返すと本番で落ちる書き方を見逃す。"""
    import stripe
    return stripe.checkout.Session.construct_from(
        {"id": "cs_x", "object": "checkout.session",
         "payment_status": "paid" if paid else "unpaid",
         "client_reference_id": str(uid), "customer": cust,
         "subscription": sub}, "sk_test_dummy")


def test_the_return_screen_asks_stripe_when_the_hook_is_late(billing,
                                                             monkeypatch):
    c, uid = _login(billing, "late@example.com")
    monkeypatch.setattr(billing.app.billing, "checkout_session",
                        lambda sid: _fake_session(uid))
    monkeypatch.setattr(billing.app.billing, "subscription", lambda i: {})
    monkeypatch.setattr(billing.app.billing, "period_end",
                        lambda s: "2099-01-01T00:00:00+00:00")
    h = c.get("/plan/done?session_id=cs_test_1").get_data(as_text=True)
    assert billing.accounts.is_pro(billing.accounts.get_user(uid))
    assert "確認をしています" not in h


def test_an_unpaid_session_grants_nothing(billing, monkeypatch):
    c, uid = _login(billing, "unpaidsess@example.com")
    monkeypatch.setattr(billing.app.billing, "checkout_session",
                        lambda sid: _fake_session(uid, paid=False))
    c.get("/plan/done?session_id=cs_test_2")
    assert not billing.accounts.is_pro(billing.accounts.get_user(uid))


def test_someone_elses_session_grants_nothing(billing, monkeypatch):
    """他人のセッションIDを貼っても、自分がPROにならない。"""
    c, uid = _login(billing, "borrower@example.com")
    other = billing.accounts._upsert_user("owner@example.com")
    monkeypatch.setattr(billing.app.billing, "checkout_session",
                        lambda sid: _fake_session(other["id"]))
    monkeypatch.setattr(billing.app.billing, "subscription", lambda i: {})
    monkeypatch.setattr(billing.app.billing, "period_end", lambda s: None)
    c.get("/plan/done?session_id=cs_test_3")
    assert not billing.accounts.is_pro(billing.accounts.get_user(uid))
    assert not billing.accounts.is_pro(
        billing.accounts.get_user(other["id"])), "他人のプランも動かさない"


def test_the_return_screen_needs_no_session_id(billing):
    """直接開かれても落ちない。"""
    c, uid = _login(billing, "bare@example.com")
    assert c.get("/plan/done").status_code == 200


def test_a_member_id_reaches_the_database_as_a_number(billing):
    """会員IDを文字列のままDBに渡さない。

    Webhookの client_reference_id は文字列で届く。SQLiteは型親和性で
    '3' を 3 として扱うため、ここを間違えてもテストでは気づけない。
    PostgreSQL は integer = text を拒むので本番だけ落ちる。
    型がそろっていることを直接見る。
    """
    seen = []
    real = billing.db.run

    def spy(q, params=(), fetch="none"):
        if "users" in q and "WHERE id = ?" in q:
            seen.append(params[-1])
        return real(q, params, fetch)

    billing.accounts.db.run = spy
    try:
        _c, uid = _login(billing, "typed@example.com")
        billing.accounts.set_plan(str(uid), billing.accounts.PLAN_PRO, None)
        billing.accounts.set_stripe_ids(str(uid), "cus_t", "sub_t")
    finally:
        billing.accounts.db.run = real
    assert seen, "更新が走っていない"
    assert all(isinstance(v, int) for v in seen), \
        f"文字列のまま渡っている: {seen}"
    assert billing.accounts.is_pro(billing.accounts.get_user(uid))
