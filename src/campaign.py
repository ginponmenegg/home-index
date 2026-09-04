# -*- coding: utf-8 -*-
"""ローンチ時のキャンペーン価格。

■据え置きにする
期間中に申し込んだ人は、そのあともずっとその金額。特定商取引法に基づく
最終確認画面で「2回目以降も同額です。初回だけ安くなる、あとから値上がり
する、といったことはありません」と表示しているので、期間限定の割引に
すると、あの画面が誤認表示になる（誤認して申し込んだ人は契約を取り消せる）。

据え置きなら、あの一文は真実のまま置いておける。

■仕組みで据え置く
「安いほうのPriceで契約する」だけ。割引という状態を持たない。サブスク
リプションはPriceを参照し続けるので、期間が終わっても契約中の人の金額は
変わらない。クーポンだと duration の設定を誤ったときに全員が値上がりする
が、この形ならその事故が起きない。

■日付は日本時間で見る
本番（Render）のタイムゾーンはUTC。素直に date.today() を使うと、日本の
12月8日午前0時ではなく午前9時に終わることになる。境目の9時間、終わった
はずの価格で申し込める。ここは必ずJSTで判定する。
"""
from __future__ import annotations

import datetime
import os

JST = datetime.timezone(datetime.timedelta(hours=9))

PRICE_ID = "CAMPAIGN_PRICE_ID"
FROM = "CAMPAIGN_FROM"       # この日から（YYYY-MM-DD・その日を含む）
UNTIL = "CAMPAIGN_UNTIL"     # この日まで（YYYY-MM-DD・その日を含む）
YEN = "CAMPAIGN_PRICE_YEN"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def today() -> datetime.date:
    """日本時間での今日。"""
    return datetime.datetime.now(JST).date()


def _date(name: str):
    raw = _env(name)
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        # 設定を書き損じたときに、黙って通常価格に戻すほうが安全。
        # キャンペーン価格で請求してしまうより、出さないほうがよい。
        print(f"[campaign] {name} を日付として読めない: {raw!r}")
        return None


def price_yen() -> int:
    try:
        return int(_env(YEN) or 0)
    except ValueError:
        return 0


def window():
    """(開始日, 終了日)。どちらも含む。決まっていなければ None。"""
    return _date(FROM), _date(UNTIL)


def active(on: datetime.date | None = None) -> bool:
    """その日がキャンペーン期間内か。

    価格IDと金額が揃っていることまで見る。日付だけ設定されていても、
    請求する先のPriceが無ければ意味がない。
    """
    if not _env(PRICE_ID) or price_yen() <= 0:
        return False
    start, end = window()
    if not start or not end:
        return False
    d = on or today()
    return start <= d <= end


def price_id() -> str:
    return _env(PRICE_ID)


def label() -> str:
    return f"月額 {price_yen():,}円（税込）"


def until_ja() -> str:
    """終了日を「2026年12月7日」の形で。"""
    _s, end = window()
    if not end:
        return ""
    return f"{end.year}年{end.month}月{end.day}日"
