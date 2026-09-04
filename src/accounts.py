# -*- coding: utf-8 -*-
"""アカウント（パスワードなし・メールリンク方式）。

■なぜパスワードを持たないか
保管しなければ漏れない。実装量も、問い合わせ対応（再設定）も減る。
利用者にとっても、住宅を検討している数か月のあいだに数回しか
ログインしない性質のサービスなので、毎回リンクを送るほうが負担が軽い。

■トークンの扱い
発行した文字列そのものはDBに残さず、SHA-256のハッシュだけを保存する。
DBの中身が漏れても、そこからログインリンクは復元できない。
有効期限は30分、1回使ったら無効。

■送信数の保護
Resendの無料枠は1日100通。誰かがフォームを叩き続けると、その日の
ログインが全員できなくなる。同じアドレスへの連続発行と、1日の総発行数の
両方に上限を設ける。
"""
from __future__ import annotations

import hashlib
import secrets
import datetime
import re

from . import db

TOKEN_TTL_MIN = 30          # ログインリンクの有効時間（分）
PER_EMAIL_PER_HOUR = 5      # 同じアドレスへの1時間あたりの発行上限
PER_DAY_TOTAL = 80          # 全体の1日あたり発行上限（Resend無料枠100通の手前）

PLAN_FREE = "free"
PLAN_PRO = "pro"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def valid_email(s: str) -> bool:
    s = (s or "").strip()
    return bool(s) and len(s) <= 254 and bool(_EMAIL_RE.match(s))


def normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(dt: datetime.datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---- ログインリンクの発行と消費 -------------------------------------------

class TooManyRequests(Exception):
    """発行上限に達した。利用者には時間を置くよう伝える。"""


def issue_login_token(email: str) -> str:
    """使い捨てのログイントークンを発行して返す（呼び出し側がメールで送る）。"""
    email = normalize_email(email)
    now = _utcnow()
    hour_ago = _iso(now - datetime.timedelta(hours=1))
    day_ago = _iso(now - datetime.timedelta(days=1))
    token = secrets.token_urlsafe(32)

    def _go(exec_):
        # 期限切れの行はここで掃除しておく（別途のバッチを持たないため）
        exec_("DELETE FROM login_tokens WHERE expires_at < ?", (_iso(now),))
        n = exec_("SELECT COUNT(*) AS c FROM login_tokens "
                  "WHERE email = ? AND expires_at > ?",
                  (email, hour_ago)).fetchone()
        if int(dict(n)["c"]) >= PER_EMAIL_PER_HOUR:
            raise TooManyRequests("email")
        total = exec_("SELECT COUNT(*) AS c FROM login_tokens "
                      "WHERE expires_at > ?", (day_ago,)).fetchone()
        if int(dict(total)["c"]) >= PER_DAY_TOTAL:
            raise TooManyRequests("daily")
        exec_("INSERT INTO login_tokens (token_hash, email, expires_at) "
              "VALUES (?, ?, ?)",
              (_hash(token), email,
               _iso(now + datetime.timedelta(minutes=TOKEN_TTL_MIN))))

    db.run_many(_go)
    return token


def consume_login_token(token: str):
    """トークンを検証して使用済みにし、対応する利用者を返す。

    使えないトークンなら None。成否の理由は返さない（総当たりの手がかりを
    与えないため、画面には一律「リンクが無効です」と出す）。
    """
    if not token:
        return None
    th = _hash(token)
    now = _iso(_utcnow())

    def _go(exec_):
        row = exec_("SELECT email, expires_at, used_at FROM login_tokens "
                    "WHERE token_hash = ?", (th,)).fetchone()
        if not row:
            return None
        row = dict(row)
        if row.get("used_at") or row["expires_at"] <= now:
            return None
        exec_("UPDATE login_tokens SET used_at = ? WHERE token_hash = ?",
              (now, th))
        return row["email"]

    email = db.run_many(_go)
    if not email:
        return None
    return _upsert_user(email)


# ---- 利用者 ---------------------------------------------------------------

def _upsert_user(email: str) -> dict:
    """居なければ作り、居ればログイン時刻を更新して返す。"""
    now = db.now()

    def _go(exec_):
        row = exec_("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            exec_("UPDATE users SET last_login_at = ? WHERE email = ?",
                  (now, email))
            u = dict(row)
            u["last_login_at"] = now
            return u
        exec_("INSERT INTO users (email, plan, created_at, last_login_at) "
              "VALUES (?, ?, ?, ?)", (email, PLAN_FREE, now, now))
        return dict(exec_("SELECT * FROM users WHERE email = ?",
                          (email,)).fetchone())

    return db.run_many(_go)


def _uid(user_id):
    """会員IDを整数にそろえる。

    Webhookの client_reference_id は文字列で届く。SQLiteは型親和性で
    '3' を 3 として扱うのでテストでは通るが、PostgreSQL は
    integer = text を受け付けずエラーになる。本番だけ落ちるという
    一番たちの悪い形になるので、DBに触る手前でそろえる。
    """
    return int(user_id)


def get_user(user_id) -> dict | None:
    if not user_id:
        return None
    return db.run("SELECT * FROM users WHERE id = ?", (user_id,), "one")


def is_pro(user: dict | None) -> bool:
    """有料プランが有効か。期限切れは free として扱う。"""
    if not user or user.get("plan") != PLAN_PRO:
        return False
    exp = user.get("plan_expires_at")
    if not exp:
        return True          # 期限なし（手動付与など）
    return str(exp) > db.now()


def set_plan(user_id, plan: str, expires_at: str | None = None) -> None:
    """プランを変更する。決済を繋いだあとはWebhookからここを呼ぶ。"""
    db.run("UPDATE users SET plan = ?, plan_expires_at = ? WHERE id = ?",
           (plan, expires_at, _uid(user_id)))


def set_stripe_ids(user_id, customer_id: str | None = None,
                   subscription_id: str | None = None) -> None:
    """決済側のIDを結びつける。渡したものだけ書き換える。"""
    sets, args = [], []
    if customer_id is not None:
        sets.append("stripe_customer_id = ?")
        args.append(customer_id)
    if subscription_id is not None:
        sets.append("stripe_subscription_id = ?")
        args.append(subscription_id)
    if not sets:
        return
    args.append(_uid(user_id))
    db.run(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(args))


def user_by_customer(customer_id: str) -> dict | None:
    """決済側の顧客IDから会員を引く。

    Webhookには会員IDが載らないイベントがある（更新・解約など）。
    そのときはこちらで辿る。
    """
    if not customer_id:
        return None
    return db.run("SELECT * FROM users WHERE stripe_customer_id = ?",
                  (customer_id,), "one")
