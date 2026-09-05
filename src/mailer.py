# -*- coding: utf-8 -*-
"""メール送信（Resend）。

RESEND_API_KEY が無いときは送信せず、失敗として返す。呼び出し側は
ローカル開発ではリンクを画面に出す、本番ではエラーを見せる、と使い分ける。
鍵の有無で挙動を変えるのは、このリポジトリの他の外部APIと同じ流儀。
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.error

API = "https://api.resend.com/emails"
TIMEOUT = 15

# 名乗らないと Cloudflare に 403（error code 1010）で弾かれる。
# Python-urllib という既定の名乗りが拒否されるので、明示する。
UA = "home-index/1.0 (+https://home-index-h2hf.onrender.com)"

# ここだけ requests ではなく urllib を使っている。requests は certifi の
# CA束を見るため、証明書を差し替える環境（社内プロキシや一部のセキュリティ
# ソフト）で検証に失敗する。urllib なら OS の証明書ストアを使うので、
# 開発機でも本番でも同じように通る。検証を切る選択はしない。


def enabled() -> bool:
    return bool((os.environ.get("RESEND_API_KEY") or "").strip())


DEFAULT_FROM = "HOME INDEX <onboarding@resend.dev>"


def sender() -> str:
    """差出人。独自ドメインをResendで認証してから設定する。

    MAIL_FROM を設定しないと resend.dev の共有アドレスから出る。届きは
    するが、本文のドメイン（homeindex.jp）と差出人ドメインが揃わないため、
    受信側の判定を通りにくい。iCloudやGmailでは迷惑メールに落ちる。

    ログインはメールのリンクだけで成立する作りなので、ここが迷惑メールに
    入ると「ログインできないサービス」になる。DNSにSPF/DKIMを入れて
    MAIL_FROM を自ドメインにすること。
    """
    return (os.environ.get("MAIL_FROM") or "").strip() or DEFAULT_FROM


def warn_if_shared_sender() -> str:
    """共有の差出人のままなら警告文を返す（起動時のログ用）。"""
    if enabled() and sender() == DEFAULT_FROM:
        return ("[mailer] MAIL_FROM が未設定です。resend.dev の共有アドレス"
                "から送るため、ログインのメールが迷惑メールに入ります。")
    return ""


def send(to: str, subject: str, html: str, text: str = "") -> tuple[bool, str]:
    """1通送る。(成功したか, メッセージ) を返す。例外は投げない。"""
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not key:
        return False, "RESEND_API_KEY が未設定です"
    body = {"from": sender(), "to": [to], "subject": subject, "html": html}
    if text:
        body["text"] = text
    req = urllib.request.Request(
        API, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return True, json.loads(r.read().decode("utf-8")).get("id", "")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        return False, f"送信に失敗しました（{e.code}）{detail}"
    except Exception as e:
        return False, f"送信に失敗しました（{type(e).__name__}）"


LOGIN_SUBJECT = "【HOME INDEX】ログイン用のリンク"

_LOGIN_HTML = """<div style="font-family:sans-serif;line-height:1.9;color:#111">
<p>HOME INDEX のログイン用リンクです。</p>
<p style="margin:24px 0">
  <a href="{url}" style="display:inline-block;padding:14px 22px;background:#111;
     color:#fff;border-radius:8px;text-decoration:none;font-weight:700">
    ログインする</a></p>
<p style="font-size:13px;color:#555">
  このリンクは{ttl}分で使えなくなります。1回だけ有効です。<br>
  心当たりがない場合は、このメールを破棄してください。
  リンクを踏まない限り、なにも起こりません。</p>
<p style="font-size:13px;color:#555">開かない場合は、次のURLをブラウザに貼り付けてください。<br>
  <span style="word-break:break-all">{url}</span></p>
</div>"""


def send_login_link(to: str, url: str, ttl_min: int) -> tuple[bool, str]:
    html = _LOGIN_HTML.format(url=url, ttl=ttl_min)
    text = (f"HOME INDEX のログイン用リンクです。\n\n{url}\n\n"
            f"このリンクは{ttl_min}分で使えなくなります。1回だけ有効です。\n"
            "心当たりがない場合は破棄してください。")
    return send(to, LOGIN_SUBJECT, html, text)
