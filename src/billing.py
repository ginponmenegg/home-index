# -*- coding: utf-8 -*-
"""Stripeとのやりとり。

■カード番号をこのアプリに通さない
申込は Stripe Checkout、カードの変更は Customer Portal に任せる。どちらも
Stripeがホストする画面なので、カード番号がこちらのサーバーを通らない。
PCI DSS の対象範囲を持たないための線引きなので、自前のカード入力欄を
作らないこと。

■解約だけは自前で受ける
Customer Portal でも解約はできるが、そちらは使わない。解約の導線は
「確認を1枚挟み、引き止めない」形をこちらで持ちたいため（仕様書§8）。
Portal 側では解約を無効にし、支払い方法の変更と請求履歴だけを開ける。

■期限を残して落とす
解約は cancel_at_period_end。日割り返金はしない代わりに、支払い済みの
期間は最後まで使える。plan_expires_at に期間末を入れておき、
accounts.is_pro がそれを見て切り替える。

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
PRICE_ID = "STRIPE_PRICE_ID"
WEBHOOK_SECRET = "STRIPE_WEBHOOK_SECRET"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def enabled() -> bool:
    """決済を動かせる設定が揃っているか。

    鍵と価格IDが無ければ、申込画面を出しても押した先で失敗する。
    出す前にここで止める。
    """
    return bool(_env(SECRET_KEY) and _env(PRICE_ID))


def _stripe():
    import stripe
    stripe.api_key = _env(SECRET_KEY)
    return stripe


def _live() -> bool:
    """本番の鍵かどうか。テスト鍵は sk_test_ で始まる。"""
    return _env(SECRET_KEY).startswith("sk_live_")


def mode() -> str:
    return "live" if _live() else "test"


def checkout_url(email: str, user_id, success_url: str,
                 cancel_url: str, customer_id: Optional[str] = None) -> str:
    """申込のCheckoutを作ってURLを返す。

    client_reference_id に会員IDを入れる。Webhookが「どの会員の支払いか」を
    知る唯一の手掛かりになるので、必ず入れること。
    """
    s = _stripe()
    kw = dict(
        mode="subscription",
        line_items=[{"price": _env(PRICE_ID), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(user_id),
        locale="ja",
        # 特定商取引法の表示は自前の最終確認画面（/plan/confirm）で
        # 済ませている。Stripe側の同意欄は増やさない。
        allow_promotion_codes=True,
    )
    if customer_id:
        kw["customer"] = customer_id
    else:
        kw["customer_email"] = email
    return s.checkout.Session.create(**kw).url


def portal_url(customer_id: str, return_url: str) -> str:
    """支払い方法の変更と請求履歴。解約はここではなく自前の画面で受ける。"""
    s = _stripe()
    return s.billing_portal.Session.create(
        customer=customer_id, return_url=return_url).url


def cancel_at_period_end(subscription_id: str) -> Optional[str]:
    """期間末で解約する。返すのは期間末（ISO文字列）。

    即時解約にしないのは、日割り返金をしない代わりに支払い済みの期間を
    使えるようにするため。規約と解約画面もその前提で書いてある。
    """
    s = _stripe()
    sub = s.Subscription.modify(subscription_id, cancel_at_period_end=True)
    return period_end(sub)


def period_end(sub) -> Optional[str]:
    """サブスクリプションの期間末をISO文字列で返す。"""
    ts = _get(sub, "current_period_end")
    if not ts:
        items = _get(sub, "items") or {}
        data = (items.get("data") if isinstance(items, dict) else None) or []
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
