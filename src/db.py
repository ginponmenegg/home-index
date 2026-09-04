# -*- coding: utf-8 -*-
"""アカウント機能のためのデータベース層。

■なぜ外部DBなのか
Renderの無料プランは永続ディスクを持てない。サーバ上のSQLiteファイルは
再デプロイのたびに消えるので、保存先は外に置くしかない。本番はNeon
(PostgreSQL)を想定している。

■接続を持ち続けてはいけない
Neonの無料枠は「起動していた時間」で課金され、5分アイドルで自動停止する。
接続プールを張りっぱなしにすると永久に停止せず、月100 CU時間の枠を
確実に超える（24時間×31日×0.25CU＝186 CU時間）。
そのため、ここでは1リクエスト1接続・使い終わったら即クローズを徹底する。
プールは張らない。/healthz からDBに触らないことも同じ理由で重要
（5分おきの死活監視でDBまで起こしてしまうため）。

■ローカル開発
DATABASE_URL が sqlite:// で始まればsqlite3を使う。テストと手元の確認は
これで足りるので、開発のためにNeonへ繋ぐ必要はない。
DATABASE_URL が未設定なら enabled() が False を返し、
アプリ側はアカウント機能を丸ごと隠す（既存のAPIキー未設定と同じ扱い）。

■日時の持ち方
タイムスタンプはISO8601のUTC文字列(TEXT)で保存する。sqliteとpostgresで
ドライバごとの型変換の違いに悩まされないための割り切り。
"""
from __future__ import annotations

import os
import contextlib
import datetime


def url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def enabled() -> bool:
    """アカウント機能を使える状態か。未設定なら機能ごと隠す。"""
    return bool(url())


def is_sqlite() -> bool:
    return url().startswith("sqlite:")


def now() -> str:
    """保存用の現在時刻（UTC・秒精度のISO8601）。"""
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat()


def _sqlite_path() -> str:
    # sqlite:///rel/path も sqlite:////abs/path も受ける
    p = url().split("://", 1)[1]
    return p.lstrip("/") if not p.startswith("//") else p[1:]


@contextlib.contextmanager
def connect():
    """1回きりの接続。with を抜けたら必ず閉じる（プールしない）。"""
    if is_sqlite():
        import sqlite3
        conn = sqlite3.connect(_sqlite_path())
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
        return

    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(url(), row_factory=dict_row, connect_timeout=10)
    # プリペアドステートメントを使わせない。
    # psycopg3 は同じSQLを数回投げると自動でプリペアするが、
    # Neonのプール経由（-pooler の接続文字列）はPgBouncerのため
    # これと相性が悪く、状況によってエラーになる。
    # こちらは毎回つなぎ直す作りでプリペアの恩恵が無いので、切っておく。
    conn.prepare_threshold = None
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _sql(q: str) -> str:
    """プレースホルダを方言に合わせる。SQLは常に ? で書く。"""
    return q if is_sqlite() else q.replace("?", "%s")


def run(q: str, params=(), fetch: str = "none"):
    """問い合わせを1回投げる。fetch は none / one / all。

    毎回接続を開き直すので、1リクエストの中で何度も呼ばないこと。
    まとめたい処理は run_many に渡す。
    """
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(_sql(q), params)
        if fetch == "one":
            row = cur.fetchone()
            return dict(row) if row else None
        if fetch == "all":
            return [dict(r) for r in cur.fetchall()]
        return None


def run_many(fn):
    """1接続の中で複数の問い合わせをまとめる。

    fn(cur) を呼ぶ。cur.execute には _sql を通した文を渡すこと
    （ヘルパ exec_ を使えば意識しなくてよい）。
    """
    with connect() as conn:
        cur = conn.cursor()

        def exec_(q, params=()):
            cur.execute(_sql(q), params)
            return cur

        return fn(exec_)


# ---- スキーマ -------------------------------------------------------------
# 主キーの書き方だけ方言差があるので、そこだけ分岐する。
_PK = {"sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT",
       "pg": "BIGSERIAL PRIMARY KEY"}


def schema_sql() -> list[str]:
    pk = _PK["sqlite" if is_sqlite() else "pg"]
    return [
        # 利用者。メールアドレスだけで識別する（パスワードは持たない）。
        f"""CREATE TABLE IF NOT EXISTS users (
              id {pk},
              email TEXT NOT NULL UNIQUE,
              plan TEXT NOT NULL DEFAULT 'free',
              plan_expires_at TEXT,
              created_at TEXT NOT NULL,
              last_login_at TEXT
            )""",
        # ログイン用の使い捨てリンク。トークンそのものは保存せず、
        # ハッシュだけを持つ（DBが漏れてもログインされないように）。
        """CREATE TABLE IF NOT EXISTS login_tokens (
              token_hash TEXT PRIMARY KEY,
              email TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              used_at TEXT
            )""",
        # 保存した診断。比較のために使う。
        # payload には結果の要約をJSONで入れる（点数の再計算はしない）。
        f"""CREATE TABLE IF NOT EXISTS saved_diagnoses (
              id {pk},
              user_id BIGINT NOT NULL,
              kind TEXT NOT NULL,
              title TEXT NOT NULL,
              address TEXT,
              price BIGINT,
              total_score INTEGER,
              grade TEXT,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            )""",
        "CREATE INDEX IF NOT EXISTS ix_saved_user ON saved_diagnoses (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_token_email ON login_tokens (email)",
    ]


# 後から足した列。CREATE TABLE 側は既存の環境では実行されないので、
# ALTER で追加する。sqlite に IF NOT EXISTS が無いため、失敗を握って進める
# （すでに在る場合のエラーは無視してよい）。
ADDED_COLUMNS = [
    ("saved_diagnoses", "note", "TEXT"),
    # 決済（Stripe）。どの会員がどの顧客・どの契約かを結ぶ。
    # Webhookは顧客IDしか持ってこないことがあるので、両方を持つ。
    ("users", "stripe_customer_id", "TEXT"),
    ("users", "stripe_subscription_id", "TEXT"),
    # 解約を受け付けた日ではなく、使えなくなる日を持つ。
    # 「解約済みだが、まだ使える」という状態を画面に出すために要る。
    ("users", "plan_cancel_at", "TEXT"),
]


def init_schema() -> None:
    """テーブルを作る。何度呼んでも安全。"""
    if not enabled():
        return
    stmts = schema_sql()

    def _go(exec_):
        for q in stmts:
            exec_(q)

    run_many(_go)

    for table, col, typ in ADDED_COLUMNS:
        try:
            run(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        except Exception:
            pass          # すでに在る
