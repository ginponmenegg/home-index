# -*- coding: utf-8 -*-
"""Stripeとのやりとり。

■カード番号をこのアプリに通さない
申込は Stripe Checkout、カードの変更は Customer Portal に任せる。どちらも
Stripeがホストする画面なので、カード番号がこちらのサーバーを通らない。
PCI DSS の対象範囲を持たないための線引きなので、自前のカード入力欄を
作らないこと。

■90日パス（2026-11-03から）
月額のサブスクリプションをやめ、1回払いで90日使えるパスにした。自動更新は
しない。plan_expires_at に期限を入れておけば、accounts.is_pro がそれを見て
無料に戻すので、期限切れの処理も解約の手続きも要らない。

月額のころの契約が残っていた場合に備えて、サブスクリプションのWebhookは
受け続ける（app._apply_stripe_event）。新しく作ることはない。

■Webhookが正
Checkout から戻ってきた画面でプランを上げない。戻る前にブラウザを閉じる
人がいるし、URLは書き換えられる。プランを動かすのは、署名を検証した
Webhookだけにする。
"""
from __future__ import annotations

import datetime
import os
from typing import Optional

SECRET_KEY = "STRIPE_SECRET_KEY"
PASS_PRICE_ID = "STRIPE_PASS_PRICE_ID"
WEBHOOK_SECRET = "STRIPE_WEBHOOK_SECRET"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def enabled() -> bool:
    """決済を動かせる設定が揃っているか。

    鍵と価格IDが無ければ、申込画面を出しても押した先で失敗する。
    出す前にここで止める。
    """
    return bool(_env(SECRET_KEY) and _env(PASS_PRICE_ID))


def _stripe():
    import stripe
    stripe.api_key = _env(SECRET_KEY)
    return stripe


def _live() -> bool:
    """本番の鍵かどうか。テスト鍵は sk_test_ で始まる。"""
    return _env(SECRET_KEY).startswith("sk_live_")


def mode() -> str:
    return "live" if _live() else "test"


def checkout_url(email: Optional[str], user_id, success_url: str,
                 cancel_url: str, customer_id: Optional[str] = None) -> str:
    """申込のCheckoutを作ってURLを返す。

    client_reference_id に会員IDを入れる。Webhookが「どの会員の支払いか」を
    知る手掛かりになる。

    ログインしていない人の申込み（2026-10-23から）は、会員IDもメールも
    無いまま作る。メールアドレスは Checkout の画面で入力してもらい、
    Webhook が customer_details.email から会員を作って結びつける。
    """
    s = _stripe()
    kw = dict(
        mode="payment",
        line_items=[{"price": _env(PASS_PRICE_ID), "quantity": 1}],
        # 領収書が要る人がいる。請求書（PDF）を作っておけば、Stripeの
        # 支払い履歴の画面から本人が取り出せる。
        invoice_creation={"enabled": True},
        success_url=success_url,
        cancel_url=cancel_url,
        locale="ja",
        # 特定商取引法の表示は自前の最終確認画面（/plan/confirm）で
        # 済ませている。Stripe側の同意欄は増やさない。
        allow_promotion_codes=True,
    )
    if user_id:
        kw["client_reference_id"] = str(user_id)
    if customer_id:
        kw["customer"] = customer_id
        try:
            return s.checkout.Session.create(**kw).url
        except Exception as e:
            # 保存してある顧客IDが今の鍵で通らないことがある。
            # テスト環境で作ったIDを持ったまま本番の鍵に切り替えた場合が
            # まさにこれで、そのままだと本人が申し込めなくなる。
            # 紐付けは checkout.session.completed で付け直されるので、
            # ここは顧客IDを外して進めてよい。
            print(f"[stripe] retrying without customer {customer_id}: {e}")
            kw.pop("customer")
    if email:
        kw["customer_email"] = email
    # 1回払いでは、黙っていると顧客が作られない。作っておけば、次に買う
    # ときに同じ顧客に寄せられ、支払い履歴の画面も開ける。
    kw["customer_creation"] = "always"
    return s.checkout.Session.create(**kw).url


def portal_url(customer_id: str, return_url: str) -> str:
    """支払いの履歴と領収書（Stripeの画面）。"""
    s = _stripe()
    return s.billing_portal.Session.create(
        customer=customer_id, return_url=return_url).url


def period_end(sub) -> Optional[str]:
    """サブスクリプションの期間末をISO文字列で返す（月額のころの契約用）。"""
    ts = _get(sub, "current_period_end")
    if not ts:
        # 新しいAPIバージョンでは期間末がサブスクリプションではなく
        # items の側に載る。items も dict ではなく ListObject なので、
        # ここも _get で読む。
        data = _get(_get(sub, "items"), "data") or []
        if data:
            ts = _get(data[0], "current_period_end")
    if not ts:
        return None
    return datetime.datetime.fromtimestamp(
        int(ts), datetime.timezone.utc).isoformat(timespec="seconds")


def _get(obj, key):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def verify_event(payload: bytes, signature: str):
    """Webhookの署名を検証して、イベントを返す。

    検証しないと、誰でもPOSTでプランを上げられる。署名シークレットが
    設定されていないときは受け付けない（黙って通さない）。
    """
    secret = _env(WEBHOOK_SECRET)
    if not secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET が設定されていません")
    s = _stripe()
    return s.Webhook.construct_event(payload, signature, secret)


def subscription(subscription_id: str):
    return _stripe().Subscription.retrieve(subscription_id)


def checkout_session(session_id: str):
    """Checkoutの結果をStripeに直接聞く。

    戻り画面の保険。URLに載ってくるのはセッションIDだけで、支払いが
    済んだかどうかはStripeに聞いて確かめる。URLの中身は信用しない。
    """
    return _stripe().checkout.Session.retrieve(session_id)
