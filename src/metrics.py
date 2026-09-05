# -*- coding: utf-8 -*-
"""日ごとの件数だけを数える。

■何を残さないか
個人も物件も一切残さない。行に入るのは「日付・イベント名・件数」の3つだけで、
IPアドレス、住所、価格、会員ID、参照元は入れない。プライバシーポリシーに
「診断のために不要な情報は取得しません」と書いてあるので、数えるために
新しく取る情報を増やさない。

数えているのは、利用者ひとりを追いかけるためではなく、
「今日は診断が何件走ったか」だけを知るため。個人を復元する手がかりは
残らない（同じ人が2回来ても、1が2になるだけ）。

■なぜ外部の解析ツールを使わないか
「世帯年収などの家計の入力は外部に送信していません」と明記している
サービスが、訪問者の行動をGoogleに送るのは筋が通らない。自分のDBに
数えるだけなら何も外に出ないし、Cookieも要らない。

■落ちても診断は止めない
数えるのは付随的な処理。Neonの無料枠は接続をプールしないので、混んでいる
ときに数える側で待たされることがある。利用者の診断をその巻き添えにしない
よう、例外は握りつぶす。数字が1件抜けるより、診断が落ちるほうが悪い。

■日付は日本時間
本番（Render）はUTCで動く。素直に日付を取ると、日本の朝9時までが
前日に計上される。
"""
from __future__ import annotations

import datetime

from . import db

JST = datetime.timezone(datetime.timedelta(hours=9))

# 数える対象。ここに無い名前は書き込まない（打ち間違いで表が汚れないように）。
EVENTS = {
    "view_lp":      "トップを見た",
    "view_buy":     "戸建の入力画面を見た",
    "view_mansion": "マンションの入力画面を見た",
    "view_guide":   "解説記事を見た",
    "diag_kodate":  "戸建の診断が出た",
    "diag_mansion": "マンションの診断が出た",
    "pro_diag":     "PROの詳細診断が出た",
    "signup":       "会員登録",
    "saved":        "診断を保存",
    "plan_view":    "プランの画面を見た",
    "pro_start":    "PROの契約が始まった",
    "pro_cancel":   "PROを解約した",
}


def today() -> datetime.date:
    return datetime.datetime.now(JST).date()


def bump(name: str, n: int = 1) -> None:
    """1件数える。失敗しても呼び出し側に例外を返さない。"""
    if name not in EVENTS or not db.enabled():
        return
    try:
        db.run(
            "INSERT INTO daily_counts (day, name, n) VALUES (?, ?, ?) "
            "ON CONFLICT (day, name) DO UPDATE SET n = daily_counts.n + ?",
            (today().isoformat(), name, n, n))
    except Exception as e:
        print(f"[metrics] {name} を数えられませんでした: {e}")


def series(days: int = 30) -> list[dict]:
    """直近の記録を新しい順に返す。"""
    if not db.enabled():
        return []
    since = (today() - datetime.timedelta(days=days)).isoformat()
    return db.run("SELECT day, name, n FROM daily_counts WHERE day >= ? "
                  "ORDER BY day DESC, name", (since,), "all") or []


def totals(days: int = 30) -> dict:
    """期間の合計をイベント名ごとに。"""
    out = {k: 0 for k in EVENTS}
    for r in series(days):
        if r["name"] in out:
            out[r["name"]] += int(r["n"])
    return out


def by_day(days: int = 30) -> list[tuple]:
    """(日付, {イベント名: 件数}) を新しい順に。画面の表用。"""
    rows: dict = {}
    for r in series(days):
        rows.setdefault(r["day"], {})[r["name"]] = int(r["n"])
    return sorted(rows.items(), reverse=True)
