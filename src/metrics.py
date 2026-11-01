# -*- coding: utf-8 -*-
"""日ごとの件数だけを数える。

■何を残さないか
個人も物件も一切残さない。行に入るのは「日付・イベント名・件数」の3つだけで、
IPアドレス、住所、価格、会員IDは入れない。プライバシーポリシーに
「診断のために不要な情報は取得しません」と書いてあるので、数えるために
新しく取る情報を増やさない。

リンク元は、ホスト名を5つの枠（X・Threads・検索・その他・なし）の
どれかに丸めて、その枠の件数を1増やすだけ。URLもホスト名も残らない。
「Xから3件」までしか復元できない。

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
import re

from . import db

JST = datetime.timezone(datetime.timedelta(hours=9))

# 数える対象。ここに無い名前は書き込まない（打ち間違いで表が汚れないように）。
EVENTS = {
    "view_lp":      "トップを見た",
    "view_buy":     "戸建の入力画面を見た",
    "view_mansion": "マンションの入力画面を見た",
    "view_land":    "土地の入力画面を見た",
    "view_guide":   "解説記事を見た",
    "view_sample":  "見本の結果を見た",
    # 資金計画の見本。PROの中身を見せる唯一の公開ページなので、
    # ここが読まれているかどうかは、料金表より先に知りたい。
    "view_sample_finance": "資金計画の見本を見た",
    # 貼り付けの自動入力が実際に使われているか。手入力を前面に出したので、
    # この機能を残す意味があるかどうかを、あとで数字で決められるようにする。
    "paste_used":   "貼り付けから自動入力した",
    # 貼り付けたのに、価格も住所も面積も1つも読み取れなかった回数
    # （2026-11-01）。使われないのか、使っても失敗するのかを分ける。
    "paste_none":   "貼り付けたが何も読み取れなかった",
    "diag_kodate":  "戸建の診断が出た",
    "diag_mansion": "マンションの診断が出た",
    "diag_land":    "土地の診断が出た",
    "pro_diag":     "PROの詳細診断が出た",
    "signup":       "会員登録",
    # ログイン用メールの送信に失敗した回数。ここが0でないあいだ、
    # 誰もログインできず、会員登録もできない。17日間ドメインの認証が
    # 通っていないことに気づけなかったので、数えられるようにした。
    "mail_failed":  "ログインのメールを送れなかった",
    "saved":        "診断を保存",
    "plan_view":    "プランの画面を見た",
    # 診断とプラン画面のあいだ、プラン画面と契約のあいだが空白だった。
    # 0件の原因が「そこまで来ていない」のか「来たが払わない」のかは、
    # この3つが無いと切り分けられない。
    "pro_cta":      "PROの画面を開こうとした（未契約の人）",
    # 2026-10-20。プランの画面は28回見られたのに、最終確認まで来たのは1回、
    # 会員登録は0。あいだにログインがあるのに、そこを数えていなかった。
    # どこで抜けたかを読めるよう、ログインの段を分けて数える。
    "login_view":   "ログイン画面を見た",
    "login_plan":   "申込のためにログイン画面へ来た",
    "login_sent":   "ログインのメールを送った",
    "login_open":   "ログインのリンクを開いた",
    "login_plan_open": "申込のためのリンクを開いた",
    # 会員登録で1物件だけPROを試せる（2026-10-23）。お試しを使い始めた回数。
    "trial_start":  "PROのお試しを使い始めた",
    "plan_confirm": "申込の最終確認画面に着いた",
    "checkout":     "決済画面へ進んだ",
    "pro_start":    "PROの契約が始まった",
    "pro_cancel":   "PROを解約した",
    # 実際にブラウザで開かれた回数。HTMLを取るだけの巡回とは別に数える。
    "browser":      "トップをブラウザで開いた",
    # 2026-11-01。普通のブラウザを名乗る巡回が来るようになり、名乗りでは
    # 見分けられなくなった（10/29は記事の閲覧748件に対し、ブラウザで
    # 開いたトップは12件）。画面のJavaScriptが読み込む1×1の画像で数え直す。
    # トップの「browser」と同じ仕組みで、Cookieも識別子も使わない。
    "h_view_buy":     "戸建の入力画面を開いた（ブラウザ）",
    "h_view_mansion": "マンションの入力画面を開いた（ブラウザ）",
    "h_view_land":    "土地の入力画面を開いた（ブラウザ）",
    "h_view_guide":   "解説記事を開いた（ブラウザ）",
    "h_view_sample":  "見本の結果を開いた（ブラウザ）",
    "h_plan_view":    "プランの画面を開いた（ブラウザ）",
    "h_pro_cta":      "PROの壁を開いた（ブラウザ）",
    "h_login_view":   "ログイン画面を開いた（ブラウザ）",
    "h_plan_confirm": "申込の最終確認を開いた（ブラウザ）",
    # どこから来たか。ホスト名は残さず、この5つのどれかを1増やすだけ。
    "ref_x":        "Xから来た",
    "ref_threads":  "Threadsから来た",
    "ref_instagram": "Instagramから来た",
    "ref_tiktok":   "TikTokから来た",
    "ref_search":   "検索から来た",
    # AIの回答に載ったリンクから来た。AEOが効いているかは、ここでしか分からない
    # （Search Console には出ない）。2026-10-19 から数えている。
    "ref_ai":       "AIの回答から来た",
    # 対面で渡すパンフレットのQR（2026-10-24から）。紙からはリンク元が
    # 付かないので、QRのURLに utm_source=pamphlet を付けて見分ける。
    "ref_pamphlet": "パンフレットのQRから来た",
    "ref_other":    "他のサイトから来た",
    "ref_none":     "リンク元なし（直接・アプリ内・巡回）",
}


# リンク元のホスト名を、この枠に丸める。部分一致で見るので
# 「t.co」は「x.com」と同じ枠に入る（Xの短縮URL）。
# 上から順に見て、最初に当たった枠に入れる。**Threads を先に置く**。
# instagram.com を先に見ると、Threads から来た人まで Instagram に
# 入ってしまう。
#
# これまで Instagram は Threads と同じ枠だった。**過去の ref_threads には
# Instagram 由来が混ざっている**ので、分けた日より前の数字は比べられない。
# **AIを検索より先に置く。** gemini.google.com は「google.」を含むので、
# 後ろに置くと Gemini から来た人が「検索から来た」に混ざる（2026-10-19まで
# そうなっていた）。
REF_BUCKETS = (
    ("ref_ai",        ("chatgpt.com", "chat.openai.com", "perplexity.ai",
                       "gemini.google.com", "bard.google.com",
                       "copilot.microsoft.com", "claude.ai")),
    ("ref_x",         ("x.com", "twitter.com", "t.co")),
    ("ref_threads",   ("threads.net", "threads.com")),
    ("ref_instagram", ("instagram.com",)),
    ("ref_tiktok",    ("tiktok.com",)),
    ("ref_search",    ("google.", "yahoo.co.jp", "bing.com", "duckduckgo.com")),
)


# ChatGPT などは、リンクに utm_source を付けて送ってくることがある。リンク元を
# 送らない設定の利用者でも、こちらで拾える。値は枠に丸めるだけで残さない。
AI_UTM = ("chatgpt", "openai", "perplexity", "gemini", "copilot", "claude")
# パンフレットのQRに付けている印。
PAMPHLET_UTM = "pamphlet"


def ref_bucket(referrer: str, own_host: str,
               utm_source: str = "") -> str | None:
    """リンク元を枠の名前にする。同じサイト内の移動なら None。

    None を返すのは「来訪ではない」という意味。トップ→診断のような
    内部移動まで数えると、どこから来た人かが分からなくなる。
    """
    utm = (utm_source or "").strip().lower()
    if utm == PAMPHLET_UTM:
        return "ref_pamphlet"
    if utm and any(a in utm for a in AI_UTM):
        return "ref_ai"
    ref = (referrer or "").strip()
    if not ref:
        return "ref_none"
    try:
        from urllib.parse import urlsplit
        host = (urlsplit(ref).hostname or "").lower()
    except ValueError:
        return "ref_other"
    if not host:
        return "ref_other"
    if own_host and host == (own_host or "").lower().split(":")[0]:
        return None
    for name, needles in REF_BUCKETS:
        if any(nd in host for nd in needles):
            return name
    return "ref_other"


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


# AIの回答から来た人が、最初に着いたページ。2026-10-22 から。
# 件数の名前を「ai_land:/guide/xxx」の形にして、同じ表（daily_counts）に
# 入れる。EVENTS には載せない（ページの数だけ増えるので）。totals と
# all_time は EVENTS の名前だけを拾うので、ここで足した行は混ざらない。
# 残すのはページのパスだけ。誰が来たかは持たない。
AI_LANDING = "ai_land:"
AI_LANDING_SINCE = "2026-10-22"


def bump_landing(path: str) -> None:
    """AIの回答から着いたページを1件数える。パスはこのサイトのものだけ。"""
    if not db.enabled() or not path or len(path) > 120:
        return
    bump_raw(AI_LANDING + path)


def bump_raw(name: str, n: int = 1) -> None:
    try:
        db.run(
            "INSERT INTO daily_counts (day, name, n) VALUES (?, ?, ?) "
            "ON CONFLICT (day, name) DO UPDATE SET n = daily_counts.n + ?",
            (today().isoformat(), name, n, n))
    except Exception as e:
        print(f"[metrics] {name} を数えられませんでした: {e}")


def landings(days: int = 30) -> list[tuple]:
    """(パス, 件数) を多い順に。AIの回答から着いたページ。"""
    if not db.enabled():
        return []
    rows = db.run("SELECT name, SUM(n) AS n FROM daily_counts "
                  "WHERE day >= ? AND name LIKE ? GROUP BY name",
                  (since(days).isoformat(), AI_LANDING + "%"), "all") or []
    out = [(r["name"][len(AI_LANDING):], int(r["n"] or 0)) for r in rows]
    return sorted(out, key=lambda x: (-x[1], x[0]))


# ---- ブラウザで開かれた画面（2026-11-01）-----------------------------------
# 画面を返したときの件数（view_buy など）と並べて読む。差が巡回のぶん。
HUMAN = {n: "h_" + n for n in (
    "view_buy", "view_mansion", "view_land", "view_guide", "view_sample",
    "plan_view", "pro_cta", "login_view", "plan_confirm")}
HUMAN_SINCE = "2026-11-01"


def bump_human(name: str) -> None:
    """画面の中から読み込まれた画像で、人が開いた回数を1件数える。"""
    if name in HUMAN:
        bump(HUMAN[name])


# ---- 入力で止まった欄（2026-11-01）---------------------------------------
# 必須の欄が空のまま「診断する」を押されて、ブラウザが止めた回数を欄ごとに。
# 名前は「form_miss:mansion:area」の形で daily_counts に入れる（EVENTS には
# 載せない）。残すのは診断の種類と欄の名前だけで、入力された値は受け取らない。
FORM_MISS = "form_miss:"
FORM_MISS_SINCE = "2026-11-01"
FORM_KINDS = ("kodate", "mansion", "land")
_FIELD_RE = re.compile(r"^[a-z_]{1,24}$")


def bump_form_miss(kind: str, field: str) -> None:
    if kind not in FORM_KINDS or not _FIELD_RE.match(field or ""):
        return
    if not db.enabled():
        return
    bump_raw(f"{FORM_MISS}{kind}:{field}")


def form_misses(days: int = 30) -> list[tuple]:
    """(種類, 欄, 件数) を多い順に。"""
    if not db.enabled():
        return []
    rows = db.run("SELECT name, SUM(n) AS n FROM daily_counts "
                  "WHERE day >= ? AND name LIKE ? GROUP BY name",
                  (since(days).isoformat(), FORM_MISS + "%"), "all") or []
    out = []
    for r in rows:
        kind, _, field = r["name"][len(FORM_MISS):].partition(":")
        out.append((kind, field, int(r["n"] or 0)))
    return sorted(out, key=lambda x: (-x[2], x[0], x[1]))


def since(days: int) -> datetime.date:
    """「直近N日」の初日。今日を1日目に数えるので、N日前ではなくN-1日前。

    以前は N日前から数えていて、「直近30日」が実際には31日ぶんになっていた。
    """
    return today() - datetime.timedelta(days=max(1, int(days)) - 1)


def series(days: int = 30) -> list[dict]:
    """直近の記録を新しい順に返す。"""
    if not db.enabled():
        return []
    since_ = since(days).isoformat()
    return db.run("SELECT day, name, n FROM daily_counts WHERE day >= ? "
                  "ORDER BY day DESC, name", (since_,), "all") or []


def totals(days: int = 30) -> dict:
    """期間の合計をイベント名ごとに。"""
    out = {k: 0 for k in EVENTS}
    for r in series(days):
        if r["name"] in out:
            out[r["name"]] += int(r["n"])
    return out


def all_time() -> tuple[dict, str | None]:
    """記録を始めた日からの累計と、その初日。

    画面の「直近N日」は枠が1日ずつ進むので、古い日が外れると合計が減る。
    それを数字が消えたと読まないように、減らない累計を並べて出す。
    """
    out = {k: 0 for k in EVENTS}
    if not db.enabled():
        return out, None
    rows = db.run("SELECT name, SUM(n) AS n FROM daily_counts GROUP BY name",
                  (), "all") or []
    for r in rows:
        if r["name"] in out:
            out[r["name"]] += int(r["n"] or 0)
    first = db.run("SELECT MIN(day) AS d FROM daily_counts", (), "one")
    return out, (first or {}).get("d")


def by_day(days: int = 30) -> list[tuple]:
    """(日付, {イベント名: 件数}) を新しい順に。画面の表用。"""
    rows: dict = {}
    for r in series(days):
        rows.setdefault(r["day"], {})[r["name"]] = int(r["n"])
    return sorted(rows.items(), reverse=True)
