# -*- coding: utf-8 -*-
"""簡易Web診断アプリ。

ブラウザのフォームに入力→エンジン(run_pipeline)で診断→結果を表示。
実データ(reinfolib/GSI)を使うため、ユーザーのPC上で起動して
http://127.0.0.1:5000 を開いて使う。金額は万円で入力。
"""
import os
import sys
import time
import json
import html
import re
import secrets
import threading
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import (Flask, request, render_template_string,  # noqa: E402
                   session, redirect)
from src import db, accounts, saved, mailer  # noqa: E402
from src.models import SubjectProperty, MansionSubject  # noqa: E402
from src.pipeline import run_pipeline, run_mansion_pipeline  # noqa: E402
from src.extract import parse_listing_text, extract_from_url  # noqa: E402
from src.citycode import CityCodeResolver  # noqa: E402
from src import structure as structure_mod  # noqa: E402
from src import guides  # noqa: E402

_RESOLVER = None


def _resolver():
    global _RESOLVER
    if _RESOLVER is None:
        cache = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "citycode_cache.json")
        _RESOLVER = CityCodeResolver(os.environ.get("REINFOLIB_KEY"), cache)
    return _RESOLVER

def _load_dotenv():
    """プロジェクト直下の .env をローカル起動時だけ読む。

    本番（Render）は環境変数がダッシュボードから入るので .env は無く、
    その場合はなにもしない。既に環境変数がある場合はそちらを優先する。
    キーが無いまま起動すると、取引の取得もハザードも静かにスキップされ、
    「類似成約が不足」「防災未取得」に見えてしまうため、ここで読んでおく。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    import io as _io
    for line in _io.open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _resolve_city(address):
    """住所 → (市区町村コード, 市区町村名, 町名)。

    都道府県が書かれていないと市区町村コードが決まらず、取引データを
    1件も取れない。手入力では省かれやすいので、そのときだけ
    地理院のジオコーダに住所を正規化させ、都道府県だけを補う。

    正規化した住所をそのまま使わないのは、町名が「城山四丁目」のように
    丁目付きになり、成約データ側の町名「城山」と一致しなくなるため。
    補うのは都道府県だけにして、市区町村名と町名は元の住所から取る。
    """
    address = (address or "").strip()
    if not address:
        return None, None, None
    code, name, dist = _resolver().resolve_from_address(address)
    if code:
        return code, name, dist
    try:
        from src.geocoding import make_geocoder
        from src.citycode import detect_prefecture
        g = make_geocoder(os.environ.get("GOOGLE_KEY")).geocode(address)
        pref = detect_prefecture(g.address_normalized or "")
    except Exception:
        return None, None, None
    if not pref:
        return None, None, None
    return _resolver().resolve_from_address(address, pref_hint=pref)


_load_dotenv()

app = Flask(__name__)

# セッションはサーバに持たず、署名つきCookieだけで完結させる。
# Renderの無料プランは永続ディスクを持てないので、サーバ側にセッションを
# 置く方式は最初から採れない。
#
# SECRET_KEY は本番では必ず環境変数で固定すること。起動のたびに変わると、
# 再デプロイやスリープ復帰のたびに全員がログアウトされる。
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax",
                  # RENDER は Render が全サービスに自動で入れる（値は "true"）。
                  # 本番(https)だけ Secure を立てる。ローカルはhttpなので外す。
                  SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")))

if db.enabled():
    # テーブル作成は起動時に1回。CREATE TABLE IF NOT EXISTS なので何度でも安全。
    # ここで落ちても診断そのものは動かしたいので、握って警告に留める。
    if not os.environ.get("SECRET_KEY"):
        # 起動のたびに鍵が変わると、再デプロイのたびに全員ログアウトになる。
        # 気づきにくい割に影響が大きいので、起動時に必ず知らせる。
        print("[warn] SECRET_KEY が未設定です。起動するたびに全員ログアウトされます。")
    try:
        db.init_schema()
    except Exception as e:          # pragma: no cover - 接続先依存
        print(f"[warn] DBに接続できませんでした（アカウント機能は無効）: {e}")


# 構造の選択肢は複数のフォームで使う。render_template_string の呼び出しは
# 9か所あるので、その都度渡さずテンプレート全体から見えるようにしておく。
app.jinja_env.globals["structures"] = structure_mod.CHOICES


# 正とするホスト名（例：home-index.jp）。未設定なら転送しない。
CANONICAL_HOST = (os.environ.get("CANONICAL_HOST") or "").strip().lower()


@app.before_request
def _force_canonical_host():
    """独自ドメイン以外で開かれたら、そちらへ転送する。

    転送するのは GET と HEAD だけにしておく。POST を301で転送すると
    メソッドが GET に変わる実装があり、フォームの送信内容が消える。
    誤ったホストへのPOSTはそのまま処理させたほうが害がない。

    /healthz は転送しない。UptimeRobot は onrender.com を叩いて
    サービスを起こしているので、ここを転送すると監視の意味が薄れる。
    """
    if not CANONICAL_HOST or request.method not in ("GET", "HEAD"):
        return None
    host = (request.host or "").split(":")[0].lower()
    if not host or host == CANONICAL_HOST:
        return None
    # /healthz は転送しない。UptimeRobot は onrender.com を叩いて
    # サービスを起こしているので、転送すると監視の意味が薄れる。
    #
    # /.well-known/ も転送しない。証明書の更新（ACMEのHTTP-01）は、
    # 検証したいホスト名でこのパスを読みに来る。別のホストへ飛ばすと
    # www 側の更新が通らず、90日後に証明書が切れる。
    if request.path == "/healthz" or request.path.startswith("/.well-known/"):
        return None
    url = f"https://{CANONICAL_HOST}{request.path}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8", "ignore")
    return redirect(url, 301)


def accounts_on() -> bool:
    """アカウント機能を出してよいか。DATABASE_URL が無ければ丸ごと隠す。"""
    return db.enabled()


def current_user():
    """ログイン中の利用者。未ログイン・退会済みなら None。"""
    if not accounts_on():
        return None
    uid = session.get("uid")
    if not uid:
        return None
    try:
        u = accounts.get_user(uid)
    except Exception:               # pragma: no cover - 接続先依存
        return None
    if not u:
        session.pop("uid", None)
    return u

# ブランド：HOME INDEX（シンボル＝家×棒グラフ / モノクロ #111111・#E5E5E5）
# 欧文は Jost（Futura系ジオメトリックサンセリフ）。未読込環境では端末フォントへ退避。
FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link href="https://fonts.googleapis.com/css2?family=Jost:wght@300;700'
             '&display=swap" rel="stylesheet">')

# LPだけは本文にNoto Sans JP、見出しに明朝（Zen Old Mincho）、数値にIBM Plex Monoを使う。
# 欧文ワードマークのJostは共通。
LP_FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
                '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
                '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
                'family=Jost:wght@300;700'
                '&family=Zen+Kaku+Gothic+New:wght@400;500;700;900'
                '&family=Zen+Old+Mincho:wght@600;700'
                '&family=Noto+Sans+JP:wght@400;500;700'
                '&family=IBM+Plex+Mono:wght@400;500'
                '&display=swap">')

# タブとホーム画面のアイコン（全ページ共通）。画像は tools/make_images.py で焼く。
ICON_LINKS = ('<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">'
              '<link rel="apple-touch-icon" href="/static/icon-180.png">')


def symbol(uid, cls="hi-sym"):
    """HOME INDEX シンボル（単色・currentColor）。uid は clipPath の重複回避用。"""
    return (f'<svg class="{cls}" viewBox="0 0 120 120" aria-hidden="true">'
            f'<defs><clipPath id="c{uid}">'
            '<path d="M60 5 108 48 108 112 12 112 12 48Z"/></clipPath></defs>'
            f'<g clip-path="url(#c{uid})">'
            '<g stroke="currentColor" stroke-width="2.2" fill="none">'
            '<path d="M28 0V112M44 0V112M60 0V112M76 0V112M92 0V112"/>'
            '<path d="M0 16H120M0 32H120M0 48H120M0 64H120M0 80H120M0 96H120"/></g>'
            '<g fill="currentColor"><rect x="12" y="104" width="96" height="8"/>'
            '<rect x="18" y="92" width="9" height="12"/>'
            '<rect x="33" y="84" width="9" height="20"/>'
            '<rect x="48" y="74" width="9" height="30"/>'
            '<rect x="63" y="67" width="9" height="37"/>'
            '<rect x="78" y="88" width="9" height="16"/>'
            '<rect x="93" y="80" width="9" height="24"/></g></g>'
            '<path d="M60 5 108 48 108 112 12 112 12 48Z" fill="none"'
            ' stroke="currentColor" stroke-width="7" stroke-linejoin="miter"/></svg>')


def symbol_small(cls="hi-sym"):
    """小サイズ用の簡略シンボル。格子を省き、28px以下でも潰れない。"""
    return (f'<svg class="{cls}" viewBox="0 0 120 120" aria-hidden="true">'
            '<path d="M60 5 108 48 108 112 12 112 12 48Z" fill="none"'
            ' stroke="currentColor" stroke-width="9" stroke-linejoin="miter"/>'
            '<g fill="currentColor"><rect x="12" y="100" width="96" height="12"/>'
            '<rect x="30" y="84" width="12" height="16"/>'
            '<rect x="48" y="72" width="12" height="28"/>'
            '<rect x="66" y="62" width="12" height="38"/>'
            '<rect x="84" y="78" width="12" height="22"/></g></svg>')


WORDMARK = '<span class="hi-wm"><b>HOME</b> <i>INDEX</i></span>'

BRAND_CSS = (
    # --- ワードマーク ---
    '.hi-wm{font-family:Jost,"Century Gothic",Futura,"Avenir Next",'
    '"Helvetica Neue",Arial,sans-serif;letter-spacing:.16em;white-space:nowrap;'
    'line-height:1;font-size:16px;color:#111}'
    '.hi-wm b{font-weight:700}.hi-wm i{font-style:normal;font-weight:300}'
    # --- 固定ヘッダーバー ---
    '.hi-bar{position:sticky;top:0;z-index:50;background:#fff;'
    'border-bottom:1px solid #e5e5e5}'
    '.hi-bar-in{max-width:760px;margin:0 auto;padding:10px 16px;display:flex;'
    'align-items:center;justify-content:space-between;gap:12px}'
    '.hi-lock{display:flex;align-items:center;gap:9px;text-decoration:none;color:#111}'
    '.hi-sym{width:28px;height:28px;flex:0 0 auto}'
    '.chip{font-size:10.5px;font-weight:700;letter-spacing:.06em;color:#111;'
    'background:#e5e5e5;border-radius:999px;padding:3px 9px;white-space:nowrap}'
    # --- 三本線メニュー ---
    '.hi-right{display:flex;align-items:center;gap:10px}'
    # 各ページの汎用 button 指定（min-height:50px 等）を打ち消してから組み立てる
    '.hi-burger{width:26px;min-width:26px;height:28px;min-height:28px;'
    'padding:7px 4px;margin:0;background:none;border:0;border-radius:0;'
    'flex:0 0 auto;cursor:pointer;display:flex;flex-direction:column;'
    'justify-content:space-between}'
    '.hi-burger span{display:block;height:2px;background:#111;border-radius:2px;'
    'transition:transform .18s ease,opacity .18s ease}'
    '.hi-burger.is-open span:nth-child(1){transform:translateY(6px) rotate(45deg)}'
    '.hi-burger.is-open span:nth-child(2){opacity:0}'
    '.hi-burger.is-open span:nth-child(3){transform:translateY(-6px) rotate(-45deg)}'
    '.hi-menu{max-width:760px;margin:0 auto;padding:4px 16px 10px;'
    'display:flex;flex-direction:column;border-top:1px solid #e5e5e5}'
    # hidden属性を効かせる。指定しないと上の display:flex に負けて開いたままになる
    '.hi-menu[hidden]{display:none}'
    '.hi-menu a{display:block;padding:11px 2px;font-size:14px;color:#111;'
    'text-decoration:none;border-bottom:1px solid #f0f0f0}'
    '.hi-menu a:last-child{border-bottom:0}'
    '.hi-menu a:hover{color:#6b7280}'
    # --- 結果画像用の小ロックアップ ---
    '.hi-lock-sm{margin-bottom:10px}'
    '.hi-lock-sm .hi-sym{width:22px;height:22px}'
    '.hi-lock-sm .hi-wm{font-size:13px}')


# PRO（/pro/diagnose・/pro/mansion・/pro/finance）はここに入れない。
# ログインも課金もまだ噛ませていないので、メニューに常設すると課金前提の
# 機能を誰にでも開いたままにすることになる。無料診断を終えた人にだけ、
# 結果画面の導線から案内する。課金の仕組みが入ったら戻す。
MENU_ITEMS = [("/", "トップ"),
              ("/buy", "購入診断（戸建）"),
              ("/mansion", "購入診断（マンション）"),
              ("/terms", "利用規約"),
              ("/privacy", "プライバシーポリシー")]

if db.enabled():
    # 未ログインで開けばログイン画面に回るので、1項目で足りる。
    MENU_ITEMS.insert(3, ("/mypage", "マイページ"))


def lp_menu_links():
    """LPのメニュー。MENU_ITEMS から作る。

    以前ここに直接リンクを書いていたため、メニューにページを足しても
    LPだけ反映されないことがあった（PROの2ページが載っていなかった）。
    同じ取りこぼしを繰り返さないよう、他のページと同じ一覧から組み立てる。
    """
    labels = {"/buy": "無料で診断する（戸建）",
              "/mansion": "無料で診断する（マンション）"}
    out = []
    for href, label in MENU_ITEMS:
        if href == "/":
            continue          # LP自身への導線は要らない
        cls = ' class="is-cta"' if href in labels else ""
        out.append(f'    <a{cls} href="{href}">{labels.get(href, label)}</a>')
    return "\n".join(out)

def brand_bar(chip="購入診断"):
    """ページ最上部の固定ヘッダーバー（全ページ共通）。

    右端の三本線からページを切り替えられる。開閉のスクリプトはバー自身に
    同梱しているので、テンプレート側で用意する必要はない。
    """
    links = "".join(f'<a href="{href}">{label}</a>' for href, label in MENU_ITEMS)
    return ('<div class="hi-bar"><div class="hi-bar-in">'
            f'<a class="hi-lock" href="/">{symbol_small()}{WORDMARK}</a>'
            '<div class="hi-right">'
            f'<span class="chip">{chip}</span>'
            '<button type="button" class="hi-burger" id="hiBurger"'
            ' aria-label="メニューを開く" aria-expanded="false" aria-controls="hiMenu">'
            '<span></span><span></span><span></span></button>'
            '</div></div>'
            f'<nav class="hi-menu" id="hiMenu" hidden>{links}</nav>'
            '</div>'
            '<script>(function(){'
            'var b=document.getElementById("hiBurger"),m=document.getElementById("hiMenu");'
            'if(!b||!m)return;'
            'function set(open){m.hidden=!open;b.setAttribute("aria-expanded",open?"true":"false");'
            'b.setAttribute("aria-label",open?"メニューを閉じる":"メニューを開く");'
            'b.classList.toggle("is-open",open);}'
            # 三本線：押すたびに開閉を切り替える
            'b.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();'
            'set(m.hidden);});'
            # メニュー内のリンク：押したら閉じる（同じページを選んだときも閉じる）
            'Array.prototype.forEach.call(m.querySelectorAll("a"),function(a){'
            'a.addEventListener("click",function(){set(false);});});'
            # メニューの外側をクリックしても閉じる
            'document.addEventListener("click",function(e){'
            'if(!m.hidden&&!m.contains(e.target)&&!b.contains(e.target))set(false);});'
            'document.addEventListener("keydown",function(e){'
            'if(e.key==="Escape"&&!m.hidden)set(false);});'
            '})();</script>')


def brand_lockup(uid="lock"):
    """カード内に置く小さいロックアップ（保存画像・規約ページ用）。"""
    return f'<div class="hi-lock hi-lock-sm">{symbol_small()}{WORDMARK}</div>'


# 運営者情報（Renderの環境変数で設定可能。未設定は仮表示）
# 氏名だけを入れること。屋号や肩書き（「HOME INDEX 運営責任者 …」など）を
# 混ぜると、特商法の「運営責任者」欄で二重になり、構造化データの
# Person.name も肩書き込みになって、誰が書いたかの信号として働かなくなる。
# 屋号との併記は、表示する側（特商法の販売事業者欄）で組み立てている。
OPERATOR = os.environ.get("OPERATOR_NAME", "〔運営者名〕")


def operator_named() -> bool:
    """運営者の氏名が実名で入っているか。

    仮の値（〔運営者名〕）のまま運営者ページを出すと、
    誰が作ったかを示すどころか逆効果になる。揃うまで出さない。
    """
    return bool(OPERATOR) and not OPERATOR.startswith("〔")


def _about_link() -> str:
    if not operator_named():
        return ""
    return ('<a href="/about" style="color:#111">運営者について</a>'
            '　・　')
CONTACT = os.environ.get("CONTACT_EMAIL", "〔連絡先メール〕")
# 特定商取引法に基づく表記に要る項目。氏名・住所・電話番号は省略できない
# （請求があれば開示する、という省略規定の対象外）。
OPERATOR_ADDRESS = os.environ.get("OPERATOR_ADDRESS", "")
OPERATOR_TEL = os.environ.get("OPERATOR_TEL", "")

# ---- 課金 -----------------------------------------------------------------
# 決済サービスはまだ繋いでいない。課金開始日も未定なので、
# BILLING_ENABLED が立つまでプランの申込・解約の画面は出さない。
# 立てるのは、特定商取引法に基づく表記が実名・実住所で出せるようになり、
# 規約の課金条項も整えてから。
PRICE_YEN = 2980                     # 税込。総額表示義務があるため税別で持たない
PRICE_LABEL = f"月額 {PRICE_YEN:,}円（税込）"


def billing_on() -> bool:
    """課金の画面を出してよいか。

    特商法の表記に必要な項目が揃っていない状態で申込画面を出すのは、
    表示義務違反になる。フラグだけでなく、実際に値があるかも見る。
    """
    if (os.environ.get("BILLING_ENABLED") or "").strip() not in ("1", "true"):
        return False
    return bool(OPERATOR_ADDRESS and OPERATOR_TEL
                and not OPERATOR.startswith("〔")
                and not CONTACT.startswith("〔"))

# 有料提供を始めたら、特定商取引法に基づく表記への導線が要る。
LEGAL_LINKS = (_about_link()
               + '<a href="/terms" style="color:#111">利用規約</a>　・　'
               '<a href="/privacy" style="color:#111">プライバシーポリシー</a>'
               + ('　・　<a href="/tokushoho" style="color:#111">'
                  '特定商取引法に基づく表記</a>' if billing_on() else ''))

PRO_LINKS = ('<a href="/pro" style="color:#111">PRO（試験公開中）</a>：'
             '<a href="/pro/diagnose" style="color:#111">購入診断（戸建）</a>　・　'
             '<a href="/pro/mansion" style="color:#111">購入診断（マンション）</a>　・　'
             '<a href="/pro/finance" style="color:#111">詳細な資金計画</a>')

FOOTER = ('<div style="text-align:center;margin-top:16px;font-size:12px;color:#6b7280;line-height:1.9">'
          '<a href="/guide" style="color:#111">解説</a><br>'
          + PRO_LINKS + '<br>'
          + LEGAL_LINKS + '<br>'
          '出典：国土交通省 不動産情報ライブラリ／国土地理院<br>'
          '商業施設：© OpenStreetMap contributors（ODbL）<br>'
          '© HOME INDEX</div>')

# ---- 負荷・不正対策（プロセス内・簡易） ----
_RATE: dict = {}
_RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_DAY", "40"))
_SEM = threading.Semaphore(int(os.environ.get("MAX_CONCURRENT", "4")))


def _client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "?")


def _rate_ok(ip):
    now = time.time()
    win = now - 86400
    lst = [t for t in _RATE.get(ip, []) if t > win]
    if len(lst) >= _RATE_LIMIT:
        _RATE[ip] = lst
        return False
    lst.append(now)
    _RATE[ip] = lst
    return True


def _legal_page(title, body):
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'{FONT_LINK}{ICON_LINKS}'
            f'<title>{title}｜HOME INDEX</title><style>'
            'body{margin:0;background:#f5f7fa;color:#1f2937;'
            'font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN",Meiryo,sans-serif}'
            '.wrap{max-width:720px;margin:0 auto;padding:24px 16px}'
            '.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:22px}'
            'h1{font-size:20px;margin:0 0 6px}h2{font-size:15px;margin:20px 0 6px;color:#111}'
            'p,li{font-size:14px;line-height:1.8}a{color:#111}.sub{color:#6b7280;font-size:12px}.logo-img{height:64px;width:auto;max-width:100%;display:block}'
            + BRAND_CSS +
            '</style></head><body>'
            + brand_bar() +
            '<div class="wrap">'
            '<a href="/" style="color:#111;font-size:14px">← トップへ</a>'
            f'<div class="card">{brand_lockup("lgl")}<h1>{title}</h1>{body}</div>'
            f'{FOOTER}</div></body></html>')


def man(yen):
    if yen is None:
        return "—"
    return f"{yen/10000:,.0f}万円"


def to_yen(man_str):
    s = (man_str or "").strip().replace(",", "").replace("万", "")
    if not s:
        return None
    try:
        return int(round(float(s) * 10000))
    except ValueError:
        return None


def to_int(s):
    s = (s or "").strip().replace(",", "")
    try:
        return int(s)
    except ValueError:
        return None


def to_float(s):
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


FORM = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
FONT_LINK_PLACEHOLDER
<title>HOME INDEX｜購入診断</title>
<style>
 :root{--bg:#f5f7fa;--card:#fff;--ink:#1f2937;--sub:#6b7280;--acc:#111111;--line:#e5e5e5}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN",Meiryo,sans-serif}
 .wrap{max-width:720px;margin:0 auto;padding:24px 16px}
 h1{font-size:22px;margin:8px 0 2px} .lead{color:var(--sub);margin:0 0 16px;font-size:14px}
 .aim{color:var(--ink);margin:0 0 10px;font-size:14px;line-height:1.85}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;
  box-shadow:0 1px 2px rgba(0,0,0,.04);margin-bottom:16px}
 label{display:block;font-size:13px;color:var(--sub);margin:12px 0 4px}
 input,textarea,select{width:100%;padding:13px 12px;border:1px solid var(--line);border-radius:10px;font-size:16px;font-family:inherit;background:#fff}
 textarea{min-height:120px;resize:vertical}
 .row{display:flex;gap:12px}.row>div{flex:1;min-width:0}
 .hint{font-size:12px;color:var(--sub);margin-top:3px}
 button{margin-top:14px;width:100%;padding:15px;background:var(--acc);color:#fff;border:0;
  border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;min-height:50px}
 button:hover{background:#333}
 button.sub{background:#eef2f7;color:var(--ink);font-size:15px;font-weight:600}
 button.sub:hover{background:#e2e8f0}
 .badge{display:inline-block;background:#f1f1f1;color:#374151;border:1px solid #e5e5e5;
  border-radius:999px;padding:3px 10px;font-size:12px;margin-bottom:10px}
 .banner{background:#fafafa;border:1px solid #e5e5e5;color:#374151;border-radius:10px;
  padding:12px 14px;font-size:14px;margin-bottom:16px}
 .banner b{color:#111}
 BRAND_CSS_PLACEHOLDER
 @media (max-width:560px){
  .wrap{padding:16px 12px} h1{font-size:19px} .card{padding:16px}
  .row{flex-direction:column;gap:0}
 }
</style></head><body>
BRAND_BAR
<div class="wrap">
 <h1>住まいを100点で採点します</h1>
 <p class="aim">「この価格は妥当か」「災害リスクはないか」「無理なく返せるか」。住まい選びで迷う点を<b>公的データ</b>から集め、ルール計算で100点に換算します。</p>
 <p class="lead">物件説明を貼り付けると自動で項目を埋めます。内容を確認・修正して診断してください。金額は<b>万円</b>。物件の評価 × ご自身の属性で、あなたに合っている物件かを診断します。<br><a href="/mansion">マンションの診断はこちら</a></p>

 {% if banner %}<div class="banner">{{banner|safe}}</div>{% endif %}

 <form class="card" method="post" action="/parse">
  <label>① 物件説明を貼り付け（SUUMO等の物件ページの<b>説明文</b>をコピペ）</label>
  <textarea name="listing" placeholder="例）中古一戸建て 〇〇県〇〇市〇〇町1-2-3 価格3,500万円 土地面積120.00㎡ 建物面積95.00㎡ 4LDK 築2010年 〇〇駅 徒歩12分">{{listing or ''}}</textarea>
  <button class="sub" type="submit">貼り付けから自動入力する</button>
  <div class="hint">※ <b>URLではなく、物件ページの文章</b>（価格・所在地・面積・築年・駅など）を選択してコピーしてください。ご自身がコピーした情報を解析します（私的利用）。抽出後、下で確認・修正できます。<br>
   <b>販売図面のPDFをお持ちの場合も、PDFを開いて文字を選択・コピーし、この欄に貼り付けてください。</b>文字が選択できないPDF（スキャン画像）からは読み取れません。<br>
   <b><a href="/copy-guide">スマホアプリで文字がコピーできない場合はこちら</a></b></div></form>

 <form class="card" method="post" action="/diagnose">
  <label>② 内容を確認・修正して診断</label>
  <label>物件の所在地</label>
  <input name="address" value="{{v.address}}" required>
  <div class="row">
   <div><label>売出価格（万円）</label><input name="price" value="{{v.price}}" placeholder="例）3500" required></div>
   <div><label>築年（西暦）</label><input name="byear" value="{{v.byear}}" placeholder="例）2010"></div>
  </div>
  <div class="row">
   <div><label>土地面積（㎡）</label><input name="land" value="{{v.land}}" placeholder="例）120"></div>
   <div><label>建物面積（㎡）</label><input name="building" value="{{v.building}}" placeholder="例）95"></div>
  </div>
  <div class="row">
   <div><label>市区町村コード</label><input name="city" value="{{v.city}}" placeholder="住所から自動判定"></div>
   <div><label>町名</label><input name="district" value="{{v.district}}" placeholder="住所から自動判定"></div>
  </div>
  <div class="row">
   <div><label>駅/バス停まで徒歩（分）</label><input name="station" value="{{v.station}}" placeholder="例）12"></div>
   <div><label>駅までバス（分・バス便のみ）</label><input name="bus" value="{{v.bus}}">
     <div class="hint">バス便のときだけ入力</div></div>
  </div>
  <div class="row">
   <div><label>種別</label>
    <select name="ptype">
     <option value="chuko_kodate" {{'selected' if v.ptype=='chuko_kodate' else ''}}>中古戸建</option>
     <option value="shinchiku_kodate" {{'selected' if v.ptype=='shinchiku_kodate' else ''}}>新築戸建</option>
    </select></div>
   <div><label>リフォーム</label>
    <select name="reno">
     <option value="0" {{'selected' if not v.reno else ''}}>リフォームなし／不明</option>
     <option value="1" {{'selected' if v.reno else ''}}>リフォーム済み</option>
    </select>
    <div class="hint">築古でも「リフォーム済み」なら価格・建物評価を調整します</div></div>
  </div>
  <div class="row">
   <div><label>構造</label>
    <select name="structure">
     {% for val, lbl in structures %}
      <option value="{{val}}" {{'selected' if v.structure==val else ''}}>{{lbl}}</option>
     {% endfor %}
    </select>
    <div class="hint">同じ築年数でも、構造によって建物の残り時間が違います。
     国税庁の耐用年数（木造22年・RC47年など）を目安に、木造を基準として換算します。
     不明なら選ばなくて構いません（木造として計算します）。</div></div>
   <div></div>
  </div>
  <div class="row">
   <div><label>世帯年収（万円・任意）</label><input name="income" value="{{v.income}}" placeholder="例）800"></div>
   <div><label>頭金（万円・任意）</label><input name="down" value="{{v.down}}" placeholder="例）500"></div>
  </div>
  <div class="row">
   <div><label>借入年数（年）</label><input name="loan_years" value="{{v.loan_years}}">
     <div class="hint">住宅ローンの返済年数（未入力は35年）</div></div>
   <div></div>
  </div>
  <button type="submit">この物件を診断する</button>
  <div class="hint" style="text-align:center;margin-top:8px">※物件解析・情報収集に数分程度かかる場合があります</div>
 </form>
 <p class="hint" style="text-align:center">
  ※ 本診断は確認できた公的データ範囲のルール計算です。最終判断は現地・専門家確認を前提としてください。</p>
<script>
(function(){
  var form = document.querySelector('form[action="/diagnose"]');
  if(!form) return;
  var addr = form.querySelector('input[name="address"]');
  var city = form.querySelector('input[name="city"]');
  var dist = form.querySelector('input[name="district"]');
  if(!addr || !city) return;
  var last = "";
  function resolve(){
    var a = (addr.value || "").trim();
    if(!a || a === last) return;
    last = a;
    var fd = new FormData();
    fd.append("address", a);
    var ph = city.placeholder;
    city.placeholder = "自動判定中…";
    fetch("/resolve_city", {method:"POST", body: fd})
      .then(function(r){ return r.json(); })
      .then(function(j){
        city.placeholder = ph || "";
        if(j && j.city){ city.value = j.city; }
        if(j && j.district && dist && !dist.value){ dist.value = j.district; }
      })
      .catch(function(){ city.placeholder = ph || ""; });
  }
  addr.addEventListener("change", resolve);
  addr.addEventListener("blur", resolve);
})();
</script>
</div></body></html>
"""


def _example_v():
    """入力欄の初期値。すべて空にする。

    以前ここに小田原の実例を入れていたため、フォームを開いた時点で住所も
    価格も年収も埋まっていた。気づかずにそのまま診断すると、自分の物件では
    ないものの結果が出る。例は placeholder で見せる。
    """
    return dict(address="", price="", byear="", land="", building="",
                city="", district="", station="", bus="",
                ptype="chuko_kodate", income="", down="",
                reno=False, structure="", loan_years="35")


def _v_from_parsed(p):
    def s(x):
        return "" if x is None else str(x)
    return dict(address=s(p.get("address")), price=s(p.get("price_man")),
                byear=s(p.get("byear")), land=s(p.get("land")),
                building=s(p.get("building")), city=s(p.get("city")),
                district=s(p.get("district")), station=s(p.get("station")),
                bus=s(p.get("bus")),
                ptype=p.get("ptype") or "chuko_kodate", income="", down="",
                reno=bool(p.get("renovated")),
                structure=s(p.get("structure")), loan_years="35")


def _parse_banner(p):
    labels = [("price_man", "価格"), ("address", "所在地"), ("land", "土地面積"),
              ("building", "建物面積"), ("byear", "築年"), ("layout", "間取り"),
              ("station", "駅徒歩"), ("city", "市区町村")]
    got = [nm for k, nm in labels if p.get(k) is not None]
    miss = [nm for k, nm in labels if p.get(k) is None]
    b = f"<b>自動入力しました。</b>抽出：{ '・'.join(got) if got else 'なし' }。"
    if miss:
        b += f"　未抽出（手入力してください）：{ '・'.join(miss) }。"
    return b

RESULT = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
FONT_LINK_PLACEHOLDER
<title>HOME INDEX｜{{s.address}}</title>
<style>
 :root{--bg:#f5f7fa;--card:#fff;--ink:#1f2937;--sub:#6b7280;--acc:#111111;--line:#e5e5e5}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN",Meiryo,sans-serif}
 .wrap{max-width:760px;margin:0 auto;padding:24px 16px}
 a.back{color:var(--acc);text-decoration:none;font-size:14px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;
  box-shadow:0 1px 2px rgba(0,0,0,.04);margin-top:16px}
 h1{font-size:20px;margin:10px 0 2px} .sub{color:var(--sub);font-size:14px;margin:0 0 6px}
 .hero{background:#fff}
 .hero-score{display:flex;align-items:center;gap:24px;margin-top:14px;flex-wrap:wrap}
 .ring{position:relative;width:132px;height:132px;flex:0 0 auto}
 .ring .num{position:absolute;inset:0;display:flex;flex-direction:column;
   align-items:center;justify-content:center}
 .ring .num b{font-size:40px;font-weight:800;line-height:1}
 .ring .num small{font-size:11px;color:var(--sub);margin-top:2px}
 .gradebox{display:flex;flex-direction:column;gap:2px}
 .gletter{font-size:60px;font-weight:800;line-height:.9;letter-spacing:-1px}
 .gcomment{font-size:16px;font-weight:700}
 .muted{color:var(--sub);font-size:13px}
 .verdict{display:inline-block;border-radius:999px;padding:4px 12px;font-weight:700;font-size:14px}
 .v-under{background:#dcfce7;color:#166534}.v-fair{background:#e0f2fe;color:#075985}
 .v-over{background:#ffedd5;color:#9a3412}.v-none{background:#f3f4f6;color:#6b7280}
 table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
 th,td{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line)}
 th{color:var(--sub);font-weight:600}
 .cat{margin:12px 0}.cat .top{display:flex;justify-content:space-between;font-size:14px}
 .bar{height:10px;background:#eef2f7;border-radius:6px;overflow:hidden;margin:5px 0}
 .bar>span{display:block;height:100%}
 .rsk{display:block;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;
  padding:10px 12px;margin:8px 0;font-size:14px}
 .rsk b{color:#9a3412}
 h2{font-size:16px;margin:2px 0 6px} .foot{color:var(--sub);font-size:12px;margin-top:8px}
 ul{margin:6px 0 0;padding-left:18px}li{margin:3px 0;font-size:14px}
 ul.strong li{color:#dc2626}ul.weak li{color:#0ea5e9}
 .hz{display:inline-block;border-radius:8px;padding:6px 11px;margin:5px 5px 0 0;font-size:13px;font-weight:600}
 .hz-ok{background:#dcfce7;color:#166534}
 .hz-warn{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
 .hz-muted{background:#f3f4f6;color:#6b7280}
 .tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
 ul.seen{list-style:none;padding:0;margin:6px 0 0}
 ul.seen li{display:flex;gap:10px;font-size:13px;line-height:1.75;margin:0;
  padding:8px 0;border-top:1px solid var(--line)}
 ul.seen li b{flex:0 0 38px;color:var(--ink)}
 .savebar{background:#fff;border:1px solid var(--line);border-radius:14px;
  padding:14px 18px;margin-top:12px;display:flex;gap:12px;align-items:center;
  flex-wrap:wrap;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .savebar button,.savebar a.b{display:inline-block;padding:11px 18px;
  background:#111;color:#fff;border:0;border-radius:9px;font-weight:700;
  font-size:14px;cursor:pointer;text-decoration:none;font-family:inherit}
 .savebar .why{font-size:13px;color:var(--sub);line-height:1.7;flex:1;
  min-width:200px}
 @media (max-width:560px){
  .wrap{padding:16px 12px} h1{font-size:18px} .card{padding:16px}
  .score{font-size:44px} .gletter{font-size:48px}
  .ring{width:112px;height:112px} .ring svg{width:112px;height:112px}
  table{font-size:12px} th,td{padding:6px 5px;white-space:nowrap}
 }
 .only-print{display:none}
 .fixrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 0}
 .fixrow .muted{font-size:12px}
 button.fix{width:auto;margin:0;padding:8px 14px;font-size:13px;font-weight:700;
   background:#fff;color:#111;border:1px solid #c6ced7;border-radius:8px;cursor:pointer}
 button.fix:hover{border-color:#111}
 .asklist{margin:0;padding:0;list-style:none}
 .asklist li{margin-bottom:9px}
 .asklist label{display:flex;gap:10px;align-items:flex-start;cursor:pointer;
   line-height:1.8}
 .asklist input{flex:none;width:16px;height:16px;margin-top:5px}
 /* 聞くことの一覧だけを刷る。結果を全部刷ると、持ち歩きたい1枚が
    何ページもの中に埋もれる。visibility で消すのは、#ask の祖先
    （.wrap や body）を display:none にすると #ask ごと消えるため。 */
 @media print{
   body{background:#fff}
   body *{visibility:hidden}
   #ask, #ask *{visibility:visible}
   #ask{position:absolute;left:0;top:0;width:100%;border:none;
     box-shadow:none;padding:0;margin:0}
   .no-print, .no-print *{visibility:hidden}
   .only-print{display:block;color:#444;font-size:12px;margin:0 0 12px}
   .asklist input{-webkit-appearance:none;appearance:none;
     border:1px solid #888;border-radius:3px}
 }
 BRAND_CSS_PLACEHOLDER
</style></head><body>
BRAND_BAR
<div class="wrap" id="report">
 <a class="back" href="/">← 別の物件を診断</a>

 <div class="card hero">
  BRAND_LOCKUP
  <p class="sub">{{s.address}}</p>
  <h1>{{s.ptype}}　売出 {{price_man}}</h1>
  <p class="muted">{{s.specs}}</p>
  {% if edit %}
  <form method="post" action="{{edit.action}}" class="fixrow no-print">
   {% for k, val in edit.fields.items() %}
   <input type="hidden" name="{{k}}" value="{{val}}">
   {% endfor %}
   <button type="submit" class="fix">入力を修正する</button>
   <span class="muted">数字や住所を直して、もう一度診断できます。入力はそのまま残ります。</span>
  </form>
  {% endif %}
  <div class="hero-score">
   <div class="ring">
    <svg viewBox="0 0 132 132" width="132" height="132">
     <circle cx="66" cy="66" r="58" fill="none" stroke="#e8eef2" stroke-width="12"/>
     <circle class="ring-arc" cx="66" cy="66" r="58" fill="none" stroke="{{grade_color}}" stroke-width="12"
       stroke-linecap="round" stroke-dasharray="{{ring_circ}}" stroke-dashoffset="{{ring_off}}"
       transform="rotate(-90 66 66)"/>
    </svg>
    <div class="num"><b style="color:{{grade_color}}">{{d.total}}</b><small>点 / 100</small></div>
   </div>
   <div class="gradebox">
    <div class="gletter" style="color:{{grade_color}}">{{d.grade}}</div>
    <div class="gcomment" style="color:{{grade_color}}">{{grade_comment}}</div>
    <div class="muted">情報充足度 {{d.suff}}%</div>
    <div class="muted" style="font-size:12px">未確認項目は評価に反映していません</div>
   </div>
  </div>
 </div>

 {% if save %}
 <div class="savebar">
  {% if save.logged_in %}
   <form method="post" action="/save" style="margin:0">
    <input type="hidden" name="snap" value="{{save.token}}">
    <button type="submit">この結果を保存する</button>
   </form>
   <span class="why">保存すると、他の物件と項目ごとに並べて比べられます。
    <a href="/mypage">保存した物件を見る</a></span>
  {% else %}
   <a class="b" href="/login" target="_blank" rel="noopener">ログインして保存する</a>
   <span class="why">保存すると、他の物件と項目ごとに並べて比べられます。<br>
    ログイン画面は<b>別のタブで開きます</b>。この結果は開いたまま残るので、
    ログインしたあとこの画面に戻って保存してください。</span>
  {% endif %}
 </div>
 {% endif %}

 <div class="card">
  <h2>価格評価</h2>
  {% if p.has %}
   <p style="margin:4px 0"><span class="verdict {{p.vclass}}">{{p.verdict}}</span>
     <span class="muted">（中央値比 {{p.dev}}%）</span></p>
   <p style="font-size:22px;font-weight:700;margin:8px 0">
     推定 {{p.low}} 〜 {{p.high}}<span class="muted" style="font-size:14px">（中央値 {{p.mid}}）</span></p>
   <p class="muted">確信度 {{p.conf}} ・ 使用 {{p.count}}件 ・ レンジ幅 {{p.disp}}%
     ／ ㎡単価(中央) 建物 {{p.ub}}・土地 {{p.ul}}</p>
   {% if p.same %}
   <div class="banner" style="margin:10px 0">
    <b>同じ建物の可能性がある成約 {{p.same|length}}件</b>
    <span class="muted">（{{p.same_label}}）</span>
    <table style="margin-top:6px"><tr><th>成約時期</th><th>専有</th>
     <th style="text-align:right">成約価格</th><th style="text-align:right">㎡単価</th></tr>
    {% for c in p.same %}<tr><td>{{c.period}}</td><td>{{c.area}}㎡</td>
     <td style="text-align:right">{{c.price}}</td>
     <td style="text-align:right">{{c.unit}}</td></tr>{% endfor %}</table>
    <div class="muted" style="font-size:12px;margin-top:6px">
     取引価格情報に建物名は含まれないため、同一マンションとは断定できません。
     町名と築年が一致する成約を集めたものです。</div>
   </div>
   {% endif %}
   {% if p.comps %}
   <div class="tablewrap">
   <table><tr><th>町名</th><th>築年</th><th>土地/建物</th><th>成約</th><th>類似</th><th>→推定</th></tr>
    {% for c in p.comps %}<tr><td>{{c.d}}</td><td>{{c.y}}</td><td>{{c.l}}/{{c.b}}㎡</td>
     <td>{{c.price}}</td><td>{{c.sim}}</td><td>{{c.est}}</td></tr>{% endfor %}</table>
   </div>
   {% endif %}
  {% else %}
   <p class="muted">類似成約が不足し価格評価できませんでした（情報不足）。</p>
  {% endif %}
 </div>

 {% if enr %}
 <div class="card">
  <h2>立地・防災・人口</h2>
  <p class="muted" style="margin:2px 0 4px">用途地域：{{enr.use_district}}　／　人口：{{enr.population}}（動向 {{enr.trend}}）</p>
  {% if enr.districts %}<p class="muted" style="margin:2px 0 0">学区：{{enr.districts}}</p>{% endif %}
  {% if enr.facilities %}<p class="muted" style="margin:2px 0 8px">周辺施設：{{enr.facilities}}</p>{% endif %}
  {% for label,val,kind in enr.hazard_items %}<span class="hz hz-{{kind}}">{{label}}：{{val}}</span>{% endfor %}
 </div>
 {% endif %}

 <div class="card">
  {% if handover %}
  <div class="card" style="border-color:#111">
   <h2 style="margin-top:0">このまま詳細診断に進む（PRO）</h2>
   <p class="muted" style="margin:6px 0 10px">
    いまの診断は、{{handover_unknowns}}を
    「未確認」として点数に入れていません。情報充足度は
    <b>{{d.suff}}%</b> です。これらに答えると、その分だけ評価に反映されます。
    <b>入力済みの内容はそのまま引き継がれます。</b><br>
    推定価格レンジは無料診断と同じ計算で、PROでも変わりません。
   </p>
   <form method="post" action="{{handover_action}}">
    {% for k, val in handover.items() %}
    <input type="hidden" name="{{k}}" value="{{val}}">
    {% endfor %}
    <button type="submit">詳細診断に進む（{{handover_label}}）</button>
   </form>
  </div>
  {% endif %}

  {% if finance_carry %}
  <div class="card" style="border-color:#111">
   <h2 style="margin-top:0">諸費用まで含めて資金を見る（PRO）</h2>
   <p class="muted" style="margin:6px 0 10px">
    上のローン試算は月々の返済額までです。仲介手数料・印紙税・登録免許税・
    不動産取得税・司法書士報酬・火災保険までを積み上げ、金利が上がった場合や
    繰上返済をした場合、住宅ローン控除まで含めて試算します。<br>
    <b>この物件の価格・面積・築年・借入条件は引き継がれます。</b>
   </p>
   <form method="post" action="/pro/finance_start">
    {% for k, val in finance_carry.items() %}
    <input type="hidden" name="{{k}}" value="{{val}}">
    {% endfor %}
    <button type="submit">資金計画に進む</button>
   </form>
  </div>
  {% endif %}

  {% if pro %}
  <div class="banner" style="margin-bottom:10px">
   <b>PRO診断：情報充足度 {{pro.free_suff}}% → {{pro.suff}}%</b>
   （総合 {{pro.free_total}}点 → {{pro.total}}点／{{"%+d"|format(pro.diff)}}点）<br>
   <span class="muted">点数が動いたのは、無料診断では評価に入れていなかった項目に回答があったためです。推定価格レンジは無料診断と同じ計算です。</span>
  </div>
  {% endif %}
  <h2>スコア内訳</h2>
  {% for c in cats %}
  <div class="cat"><div class="top"><span>{{c.name}}</span>
    <span class="muted">{{c.points}} / {{c.weight}}</span></div>
   <div class="bar"><span style="width:{{c.pct}}%;background:{{c.color}}"></span></div>
   <div class="muted">{{c.reason}}</div></div>
  {% endfor %}
 </div>

 <div class="card">
  <h2>住宅ローン（無料版：月々返済額まで）</h2>
  <p style="margin:4px 0">借入額 {{loan.principal}}（頭金 {{loan.down}}） 金利{{loan.rate}}% {{loan.years}}年</p>
  <p style="font-size:20px;font-weight:700;margin:6px 0">月々 約 {{loan.monthly}}
    {% if loan.extra %}<span class="muted" style="font-size:14px">＋ 管理費・修繕積立金 {{loan.extra}}</span>{% endif %}</p>
  {% if loan.extra %}<p style="font-size:18px;font-weight:700;margin:2px 0 6px">
    実質の月額負担 約 {{loan.total_monthly}}
    {% if loan.burden %}<span class="muted" style="font-size:14px">／ 負担率 {{loan.burden}}%（管理費等込み）</span>{% endif %}</p>
  {% elif loan.burden %}<p class="muted" style="font-size:14px;margin:2px 0 6px">返済負担率 {{loan.burden}}%</p>{% endif %}
 </div>

 {% if d.risks %}
 <div class="card"><h2>⚠ 重大リスク（要確認）</h2>
  {% for r in d.risks %}<span class="rsk"><b>[{{r.sev}}] {{r.type}}</b>（{{r.status}}）：{{r.ev}}</span>{% endfor %}
 </div>{% endif %}

 <div class="card">
  {% if d.strengths %}<h2 style="color:#dc2626">◎ 強み</h2><ul class="strong">{% for x in d.strengths %}<li>{{x}}</li>{% endfor %}</ul>{% endif %}
  {% if d.weaknesses %}<h2 style="margin-top:12px;color:#0ea5e9">△ 弱み</h2><ul class="weak">{% for x in d.weaknesses %}<li>{{x}}</li>{% endfor %}</ul>{% endif %}
  {% if d.confirm %}<h2 style="margin-top:12px">? 要確認（情報不足）</h2><ul>{% for x in d.confirm %}<li>{{x}}</li>{% endfor %}</ul>{% endif %}
  <p class="foot">{{d.comment}}</p>
 </div>

  {% if questions %}
  <div class="card" id="ask">
   <h2 style="margin-top:0">仲介業者に聞くこと（{{questions|length}}件）</h2>
   <p class="only-print">{{s.address}}　{{s.ptype}}　{{price_man}}</p>
   <p class="muted no-print" style="margin:6px 0 10px">
    {{questions_note}}
    下の質問をそのまま仲介業者にお伝えください。
    答えが分かったらもう一度診断すると、情報充足度が上がります。
   </p>
   <ol class="asklist">
    {% for q in questions %}
    <li><label><input type="checkbox"><span>{{q}}</span></label></li>
    {% endfor %}
   </ol>
   <p class="no-print" style="margin:14px 0 0">
    <button type="button" class="sub" onclick="window.print()">この一覧だけ印刷する</button>
   </p>
   <p class="muted no-print" style="font-size:12px;margin:8px 0 0">
    内見や商談に持っていけます。チェックは印刷の前に付けるためのもので、
    どこにも保存されません。
   </p>
  </div>
  {% endif %}

 <div class="card">
  <h2>この診断が見ているもの</h2>
  <ul class="seen">
   <li><b>価格</b><span>近隣の実際の取引価格から推定レンジを出し、割安・適正・割高を判定</span></li>
   <li><b>防災</b><span>洪水・土砂災害・津波・高潮の指定区域と用途地域を確認</span></li>
   <li><b>資金</b><span>年収と頭金から月々の返済額と返済負担率を試算</span></li>
  </ul>
  <p class="foot">点数はすべてルール計算で、AIが価格や点数を決めることはありません。
   取得できなかった項目は点数に反映せず、上の「要確認」に表示しています。</p>
 </div>

 {% if warnings %}<p class="foot">{% for w in warnings %}・{{w}}<br>{% endfor %}</p>{% endif %}
 <div class="foot" style="background:#f9fafb;border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:8px">
  <b>免責</b>：本サービスはAIと公的データにもとづく<b>参考情報</b>であり、不動産の価値・適法性・
  再建築可否・取引の可否を保証するものではありません。推定価格は類似事例からの目安で、
  実際の価格・契約条件・重要事項は、宅地建物取引士など有資格の専門家の確認を前提としてください。
  掲載データは取得時点のもので、最新性・正確性を保証しません。
 </div>
 <p class="foot" style="text-align:center">HOME INDEX｜購入診断 — 全国対応。周辺施設・建物内部状態など一部は今後拡充</p>
</div>
<div class="wrap" style="padding-top:0">
<div class="card" style="text-align:center">
  <button onclick="saveReport()" class="sub" type="button">📷 画像を保存</button>
  <button onclick="shareReport()" class="sub" type="button" style="margin-left:8px">🔗 共有する</button>
  <div class="hint" style="margin-top:6px">結果カードを1枚の画像にして保存・共有できます</div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
async function makeReportImage(){
  const el=document.getElementById('report');
  const canvas=await html2canvas(el,{scale:2,backgroundColor:'#ffffff',useCORS:true});
  return new Promise(res=>canvas.toBlob(res,'image/png'));
}
async function saveReport(){
  const blob=await makeReportImage();
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download='HOME INDEX_診断結果.png';a.click();
  URL.revokeObjectURL(url);
}
async function shareReport(){
  try{
    const blob=await makeReportImage();
    const file=new File([blob],'HOME INDEX_診断結果.png',{type:'image/png'});
    if(navigator.canShare&&navigator.canShare({files:[file]})){
      await navigator.share({files:[file],title:'HOME INDEX 診断結果'});
    }else{
      await saveReport();
      alert('この端末は共有に未対応のため、画像を保存しました。');
    }
  }catch(e){}
}

/* 点数を0から数え上げ、輪を空から描く。
   サーバーが出したままの数字が最初から入っているので、この処理が
   動かなくても表示は正しい。動かすときだけ0に戻して始める。 */
(function(){
  if (window.matchMedia &&
      matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  var num = document.querySelector(".ring .num b");
  var arc = document.querySelector(".ring-arc");
  if (!num || !arc) return;
  var total = parseInt(num.textContent, 10);
  var circ = parseFloat(arc.getAttribute("stroke-dasharray"));
  var off = parseFloat(arc.getAttribute("stroke-dashoffset"));
  if (isNaN(total) || isNaN(circ) || isNaN(off)) return;
  num.textContent = "0";
  arc.setAttribute("stroke-dashoffset", circ);
  var t0 = null, dur = 900;
  function step(t){
    if (t0 === null) t0 = t;
    var p = Math.min(1, (t - t0) / dur);
    var e = 1 - Math.pow(1 - p, 3);      /* 終わりに向けて減速する */
    num.textContent = Math.round(total * e);
    arc.setAttribute("stroke-dashoffset", circ - (circ - off) * e);
    if (p < 1) { requestAnimationFrame(step); }
    else { num.textContent = total; arc.setAttribute("stroke-dashoffset", off); }
  }
  requestAnimationFrame(step);
})();
</script>
</div></body></html>
"""

# ブランドのCSS/ヘッダー・フッターをテンプレートへ差し込む
FORM = (FORM.replace("BRAND_CSS_PLACEHOLDER", BRAND_CSS)
        .replace("FONT_LINK_PLACEHOLDER", FONT_LINK + ICON_LINKS)
        .replace("BRAND_BAR", brand_bar())
        .replace("</div></body></html>", FOOTER + "</div></body></html>"))
RESULT = (RESULT.replace("BRAND_CSS_PLACEHOLDER", BRAND_CSS)
          .replace("FONT_LINK_PLACEHOLDER", FONT_LINK + ICON_LINKS)
          .replace("BRAND_BAR", brand_bar())
          .replace("BRAND_LOCKUP", brand_lockup())
          .replace("</div></body></html>", FOOTER + "</div></body></html>"))


# ---- トップページ（LP）----------------------------------------------
# 診断そのものは /buy。ここは「何のサービスか」を説明して診断へ送る入口。
# 背景の地形図はcanvasで毎回描いている（画像ファイルを持たない）。
LP = """<!doctype html><html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>この家、かっていい？｜中古戸建を無料で100点診断 HOME INDEX</title>
<meta name="description" content="気になる中古戸建の価格・災害リスク・住宅ローン返済を、国土交通省の成約データなど公的データから100点で採点します。会員登録不要・無料。物件は売りません。">
<!-- Google Search Console の所有権確認。トップページに置く必要がある。外すと確認が外れるので消さないこと。 -->
<meta name="google-site-verification" content="tER7-_YVLyWZvwij74TSUG5JBXrwLA9Z8xQ2GdtbRLc">
<link rel="canonical" href="CANONICAL_URL">
<meta property="og:type" content="website">
<meta property="og:site_name" content="HOME INDEX">
<meta property="og:title" content="この家、かっていい？｜中古戸建を無料で100点診断 HOME INDEX">
<meta property="og:description" content="気になる中古戸建の価格・災害リスク・住宅ローン返済を、国土交通省の成約データなど公的データから100点で採点します。会員登録不要・無料。物件は売りません。">
<meta property="og:url" content="CANONICAL_URL">
<meta property="og:image" content="CANONICAL_URLstatic/ogp.png?v=1">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="この家、かっていい？ 買う前に、データで答え合わせ。HOME INDEX">
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
{"@type":"Organization","@id":"CANONICAL_URL#org","name":"HOME INDEX","url":"CANONICAL_URL",
 "logo":"CANONICAL_URLstatic/icon-180.png"},
{"@type":"WebSite","@id":"CANONICAL_URL#site","name":"HOME INDEX","url":"CANONICAL_URL",
 "inLanguage":"ja","publisher":{"@id":"CANONICAL_URL#org"}},
{"@type":"WebApplication","name":"HOME INDEX 購入診断","url":"CANONICAL_URLbuy",
 "applicationCategory":"FinanceApplication","operatingSystem":"Web","inLanguage":"ja",
 "isAccessibleForFree":true,
 "offers":{"@type":"Offer","price":"0","priceCurrency":"JPY"},
 "description":"中古戸建の価格・立地・災害リスク・住宅ローン返済を公的データから100点で採点する無料の診断ツール。",
 "publisher":{"@id":"CANONICAL_URL#org"}},
{"@type":"FAQPage","mainEntity":[
{"@type":"Question","name":"本当に無料ですか。あとから請求されませんか。","acceptedAnswer":{"@type":"Answer","text":"無料です。会員登録も不要で、料金が発生する画面はありません。物件の仲介やローンの紹介も行わないため、診断後に営業の連絡が来ることもありません。"}},
{"@type":"Question","name":"入力した年収や住所は保存されますか。","acceptedAnswer":{"@type":"Answer","text":"診断の計算に使うだけで、営業目的では利用しません。詳細はプライバシーポリシーに記載しています。気になる場合は、年収や頭金を概算で入力しても価格・リスクの診断は機能します。"}},
{"@type":"Question","name":"物件のURLを貼れば診断できますか。","acceptedAnswer":{"@type":"Answer","text":"URLではなく、物件ページに書かれている説明文（価格・所在地・面積・築年・駅徒歩など）をコピーして貼り付けてください。ご自身がコピーした情報を解析する形をとっています。販売図面のPDFも、開いて文字をコピーすれば同じように読み取れます。"}},
{"@type":"Question","name":"新築でも診断できますか。","acceptedAnswer":{"@type":"Answer","text":"できます。新築は近隣の新築成約事例を優先し、土地相当分と建物相当分を分けて価格を推定します。中古とは類似度の重み付けを変えています。"}},
{"@type":"Question","name":"マンションには対応していますか。","acceptedAnswer":{"@type":"Answer","text":"対応しています。所在階・向き・専有面積あたりの単価など戸建とは評価軸が違うため、別の診断として用意しています。管理費と修繕積立金も、国土交通省のガイドラインの目安と照らして評価に含めています。"}},
{"@type":"Question","name":"点数が低い物件は、買ってはいけないということですか。","acceptedAnswer":{"@type":"Answer","text":"違います。点数は「その価格と条件が、公的データから見てどのあたりに位置するか」を示すものです。低い点数は、値引き交渉の材料や、事前に確認すべき項目のリストとして使ってください。最終的な判断は、現地の確認と専門家への相談のうえで行ってください。"}}
]}]}
</script>
<meta name="twitter:card" content="summary_large_image">
LP_FONT_LINK_PLACEHOLDER
ICON_LINKS_PLACEHOLDER

<style>
:root{
  --ground:#F1F3F6; --surface:#FFFFFF; --surface-2:#F6F8FA;
  --ink:#14181D; --sub:#68707B; --line:#E1E5EB; --line-strong:#C6CED7;
  --accent:#14395C; --accent-ink:#F0EEE9; --accent-soft:#E6EDF4;
  --spark:#9A6A12; --good:#2E6F4E; --warn:#8A6512;
  --shadow:0 1px 2px rgba(20,24,29,.05);
  --shadow-lift:0 10px 30px -18px rgba(12,27,42,.45);
  /* ヒーローは明暗どちらでも同じ表情にする（意図的な単一テーマ） */
  --hero-bg:#0C1B2A; --hero-bg-2:#112537;
  --paper:#F0EEE9; --paper-dim:rgba(240,238,233,.70);
  --pin:#E0A83F;
}
/* サイトの他ページ（/buy・結果・規約）が明るい配色のみなので、LPも明るい配色に固定する。
   ヒーローとCTAバンドの濃紺は、テーマ切替ではなく意匠として常に濃紺。 */

*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:"Noto Sans JP","Hiragino Kaku Gothic ProN",Meiryo,sans-serif;
  font-size:15px; line-height:1.9; -webkit-font-smoothing:antialiased;
}
h1,h2,h3{margin:0; text-wrap:balance}
a{color:inherit}
:focus-visible{outline:2px solid var(--spark); outline-offset:3px; border-radius:4px}

.wrap{max-width:780px; margin:0 auto; padding:0 20px}
.eyebrow{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11px; letter-spacing:.16em; color:var(--sub);
  text-transform:uppercase; margin:0 0 14px;
}

/* ---------- 固定ヘッダー（ヒーロー上では透過） ---------- */
.bar{
  position:fixed; top:0; left:0; right:0; z-index:30;
  background:var(--surface); color:var(--ink);
  border-bottom:1px solid var(--line);
  transition:background .25s ease, color .25s ease, border-color .25s ease;
}
.bar.is-top{background:transparent; color:var(--paper); border-bottom-color:transparent}
.bar-in{max-width:780px; margin:0 auto; padding:10px 20px; display:flex; align-items:center; justify-content:space-between; gap:10px}
.lock{display:flex; align-items:center; gap:8px; text-decoration:none; color:inherit}
.hi-sym{width:24px; height:24px; flex:0 0 auto}
.hi-wm{font-family:Jost,"Century Gothic",Futura,"Avenir Next","Helvetica Neue",Arial,sans-serif;
  letter-spacing:.16em; white-space:nowrap; line-height:1; font-size:15px; color:inherit}
.hi-wm b{font-weight:700}
.hi-wm i{font-style:normal; font-weight:300}
.bar-right{display:flex; align-items:center; gap:10px}
.chip{
  font-size:10.5px; font-weight:700; letter-spacing:.06em; color:inherit;
  border:1px solid currentColor; border-radius:999px; padding:3px 9px; white-space:nowrap; opacity:.8;
}
.burger{width:26px; height:24px; padding:5px 3px; background:none; border:0; cursor:pointer; display:flex; flex-direction:column; justify-content:space-between; color:inherit}
.burger span{display:block; height:2px; background:currentColor; border-radius:2px; transition:transform .18s ease,opacity .18s ease}
.burger.is-open span:nth-child(1){transform:translateY(6px) rotate(45deg)}
.burger.is-open span:nth-child(2){opacity:0}
.burger.is-open span:nth-child(3){transform:translateY(-6px) rotate(-45deg)}
.menu{max-width:780px; margin:0 auto; padding:2px 20px 10px; display:flex; flex-direction:column; border-top:1px solid var(--line)}
.menu[hidden]{display:none}
.menu a{padding:11px 2px; font-size:14px; text-decoration:none; border-bottom:1px solid var(--line)}
.menu a:last-child{border-bottom:0}
.menu a.is-cta{color:var(--accent); font-weight:700}

/* ---------- ヒーロー ---------- */
.hero{
  position:relative; isolation:isolate; overflow:hidden;
  background:radial-gradient(120% 90% at 78% 18%, var(--hero-bg-2) 0%, var(--hero-bg) 62%);
  color:var(--paper);
  padding-top:56px;
}
#map{position:absolute; inset:0; width:100%; height:100%; opacity:0; transition:opacity 1.1s ease .15s}
#map.is-in{opacity:1}
.hero::after{
  content:""; position:absolute; inset:0; pointer-events:none;
  background:
    linear-gradient(to bottom, rgba(12,27,42,.55) 0%, rgba(12,27,42,.15) 34%, rgba(12,27,42,.92) 100%),
    linear-gradient(to right, rgba(12,27,42,.86) 0%, rgba(12,27,42,.35) 58%, rgba(12,27,42,0) 100%);
}
.hero .wrap{position:relative; z-index:2; padding-top:44px; padding-bottom:46px}
.hero .eyebrow{color:var(--paper-dim)}
.hero h1{
  font-family:"Zen Kaku Gothic New",sans-serif; font-weight:900;
  font-size:clamp(26px,7.9vw,58px); letter-spacing:-.02em; line-height:1.18;
  white-space:nowrap; margin:0 0 14px;
}
.hero .tag{
  font-family:"Zen Old Mincho",serif; font-weight:600;
  font-size:clamp(16px,4.2vw,22px); letter-spacing:.02em;
  margin:0 0 20px; color:var(--paper);
}
.hero .tag em{font-style:normal; border-bottom:1px solid var(--pin); padding-bottom:2px}
.hero .lead{font-size:15px; line-height:1.95; color:var(--paper-dim); max-width:32em; margin:0 0 28px}
.hero .lead b{color:var(--paper); font-weight:700}

/* ピン（地図上の物件マーカー） */
.pin{position:absolute; left:72%; top:34%; z-index:1; width:14px; height:14px; margin:-7px 0 0 -7px}
.pin i{position:absolute; inset:4px; border-radius:50%; background:var(--pin); display:block}
.pin b, .pin s{position:absolute; inset:0; border-radius:50%; border:1px solid var(--pin); opacity:0; animation:ping 3.6s ease-out infinite}
.pin s{animation-delay:1.8s}
@keyframes ping{0%{transform:scale(1); opacity:.85}70%{transform:scale(3.6); opacity:0}100%{opacity:0}}
/* スマホ用。輪が大きいと文字にかぶるので、広がり方を抑える。 */
@keyframes ping-sm{0%{transform:scale(1); opacity:.85}70%{transform:scale(2.6); opacity:0}100%{opacity:0}}
.pin-label{
  position:absolute; left:calc(72% + 16px); top:calc(34% - 9px); z-index:1;
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:10px; letter-spacing:.1em;
  color:var(--pin); white-space:nowrap; opacity:.9;
}
/* 561〜700pxの帯では、ピンとラベルが本文の上に乗る。
   ピンは hero の幅に対する72%で置いているのに、本文（.lead）の幅は
   32em で固定なので、画面が狭くなるほど本文の右端がピンに近づく。
   700px を切ると重なり、560px 以下は下の @media で右端に置き直している。
   その間は飾りのほうを消す。位置をずらして逃げると、本文を書き換えた
   ときにまた乗る。飾りなので、入らないなら出さないほうがよい。 */
@media (min-width:561px) and (max-width:700px){
  .pin, .pin-label{display:none}
}

.cta-row{display:flex; flex-wrap:wrap; gap:12px; align-items:center}
.btn{
  display:inline-flex; align-items:center; justify-content:center; gap:9px;
  padding:15px 26px; border-radius:11px; font-size:16px; font-weight:700;
  text-decoration:none; border:1px solid transparent; min-height:53px; cursor:pointer;
  transition:transform .14s ease, background .18s ease, box-shadow .18s ease;
}
.btn svg{width:15px; height:15px; flex:0 0 auto}
.hero .btn-primary{background:var(--paper); color:var(--hero-bg); box-shadow:0 12px 28px -16px rgba(0,0,0,.9)}
.hero .btn-primary:hover{transform:translateY(-2px)}
.hero .btn-ghost{color:var(--paper); border-color:rgba(240,238,233,.38); background:rgba(240,238,233,.04)}
.hero .btn-ghost:hover{background:rgba(240,238,233,.11)}
.micro{font-size:12.5px; margin:16px 0 0; color:var(--paper-dim)}

/* 入力の手順。ファーストビューのすぐ下に置いて、「何をすればいいか」を
   スクロールせずに分からせる。URLではなく本文をコピーする点が伝わらないと
   最初のつまずきになるので、そこだけ太字にしている。 */
.howto{position:relative; z-index:2; background:rgba(240,238,233,.06);
  border-top:1px solid rgba(240,238,233,.14)}
.howto .wrap{padding:18px 20px}
.howto ol{margin:0; padding-left:0; list-style:none; counter-reset:h;
  display:grid; gap:10px}
.howto li{counter-increment:h; position:relative; padding-left:34px;
  font-size:14.5px; color:var(--paper); line-height:1.7}
.howto li::before{content:counter(h); position:absolute; left:0; top:1px;
  width:22px; height:22px; border-radius:50%; background:var(--pin);
  color:var(--hero-bg); font-size:12px; font-weight:700;
  display:flex; align-items:center; justify-content:center}
.howto p{margin:12px 0 0; font-size:12.5px; color:var(--paper-dim)}
@media (min-width:680px){
  .howto ol{grid-template-columns:repeat(3,1fr); gap:18px}
}
.sources{position:relative; z-index:2; border-top:1px solid rgba(240,238,233,.14); background:rgba(8,18,29,.5)}
.sources .wrap{padding:16px 20px}
.sources .k{display:block; margin-bottom:10px}
.sources dl{margin:0; display:grid; gap:8px}
.sources div{display:flex; gap:10px; align-items:baseline}
.sources dt{font-size:12.5px; font-weight:700; color:var(--paper);
  white-space:nowrap; min-width:7.5em}
.sources dd{margin:0; font-size:12.5px; color:var(--paper-dim); line-height:1.6}
@media (min-width:680px){
  .sources dl{grid-template-columns:repeat(3,1fr); gap:18px}
  .sources div{display:block}
  .sources dt{margin-bottom:3px}
}
.sources .k{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:10px; letter-spacing:.14em; color:var(--pin); opacity:.85}

/* ---------- セクション共通 ---------- */
section{padding:52px 0; scroll-margin-top:56px}
section + section{border-top:1px solid var(--line)}
section h2{
  font-family:"Zen Old Mincho",serif; font-weight:600;
  font-size:clamp(21px,5.2vw,29px); line-height:1.62; letter-spacing:.01em; margin:0 0 10px;
}
section .sub{color:var(--sub); font-size:14px; margin:0 0 26px; max-width:36em}

/* 自分ごと化のためのチェックリスト。読ませるのではなく、目で拾わせる。 */
.checks{list-style:none; margin:18px 0 0; padding:0; display:grid; gap:10px}
.checks li{position:relative; padding-left:30px; font-size:15px; line-height:1.8}
.checks li::before{content:"☑"; position:absolute; left:0; top:0;
  color:var(--accent); font-size:16px}
@media (min-width:680px){
  .checks{grid-template-columns:1fr 1fr; gap:10px 24px}
}

.rows{display:flex; flex-direction:column; border-top:1px solid var(--line)}
.rowitem{display:flex; flex-direction:column; gap:5px; padding:19px 0; border-bottom:1px solid var(--line)}
.rowitem .q{font-family:"Zen Kaku Gothic New",sans-serif; font-size:17px; font-weight:700; margin:0}
.rowitem .a{font-size:14px; color:var(--sub); margin:0}
.rowitem .tagline{
  font-family:"IBM Plex Mono",ui-monospace,monospace; margin:0;
  font-size:10.5px; letter-spacing:.14em; color:var(--spark);
}

/* ---------- 結果サンプル ---------- */
.sample{background:var(--surface); border:2px solid var(--ink);
  border-radius:16px; box-shadow:var(--shadow-lift); overflow:hidden}
.sample-head{display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 18px; border-bottom:1px solid var(--line); background:var(--surface-2)}
.sample-head .t{font-size:12px; color:var(--sub)}
.stamp{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:10px; letter-spacing:.14em; border:1px solid var(--line-strong); color:var(--sub); border-radius:4px; padding:2px 7px; white-space:nowrap}
.sample-body{padding:22px 18px}
.score{display:flex; align-items:flex-end; gap:14px; margin-bottom:8px}
.score .n{font-family:"Zen Kaku Gothic New",sans-serif; font-weight:900;
  font-size:clamp(64px,17vw,96px); line-height:.9; letter-spacing:-.03em;
  font-variant-numeric:tabular-nums}
.score .d{font-size:15px; color:var(--sub); padding-bottom:12px}
.verdict{font-size:clamp(15px,4vw,17px); font-weight:700; color:var(--good);
  margin:0 0 22px}
.bars{display:flex; flex-direction:column; gap:10px; margin-bottom:22px}
.b{display:grid; grid-template-columns:66px 1fr 52px; gap:10px; align-items:center}
.b .lbl{font-size:12.5px; color:var(--sub)}
.b .track{height:7px; background:var(--surface-2); border:1px solid var(--line); border-radius:999px; overflow:hidden}
.b .fill{display:block; height:100%; background:var(--accent); border-radius:999px}
.rangefig{margin:0 0 22px}
.rangefig svg{display:block; width:100%; height:auto}
/* SVG内の色はクラス経由で当てる（presentation属性のvar()は環境差がある） */
.r-band{fill:var(--accent-soft)}
.r-axis{stroke:var(--line)}
.r-cap{stroke:var(--line-strong)}
.r-med{stroke:var(--accent)}
.r-lbl{fill:var(--sub)}
.r-dot{fill:var(--spark)}
.r-ring{fill:none; stroke:var(--spark)}
.r-dotlbl{fill:var(--spark)}
.f-node{fill:var(--accent)}
.f-node-t{fill:var(--accent-ink)}
.f-box{fill:var(--surface-2); stroke:var(--line)}
.f-flow{fill:none; stroke:var(--line-strong)}
.f-t{fill:var(--ink)}
.f-s{fill:var(--sub)}
.f-rule{fill:none; stroke:var(--spark)}
.b .val{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11.5px; color:var(--sub); text-align:right; font-variant-numeric:tabular-nums}
.facts{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px; background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden}
.fact{background:var(--surface); padding:13px 14px}
.fact .k{font-size:11px; color:var(--sub)}
.fact .v{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:16px; font-weight:500; margin-top:3px; font-variant-numeric:tabular-nums}
.fact .v .u{font-size:11px}
.fact .v.warn{color:var(--warn)}
.fact .v.good{color:var(--good)}
.fact .n{font-size:11px; color:var(--sub); margin-top:2px}
.sample-note{font-size:12px; color:var(--sub); margin:16px 0 0}

/* ---------- ステップ ---------- */
.steps{display:flex; flex-direction:column}
.step{display:grid; grid-template-columns:34px 1fr; gap:16px; padding:20px 0; border-bottom:1px solid var(--line)}
.step:first-child{border-top:1px solid var(--line)}
.step .num{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:13px;
  color:var(--accent-ink); background:var(--accent); border-radius:9px;
  width:34px; height:34px; display:flex; align-items:center; justify-content:center; margin-top:4px;
}
.step h3{font-family:"Zen Kaku Gothic New",sans-serif; font-size:16px; font-weight:700; margin:4px 0}
.step p{margin:0; font-size:14px; color:var(--sub)}

/* ---------- 仕組み図 ---------- */
.figure{background:var(--surface); border:1px solid var(--line); border-radius:16px; padding:20px 18px; box-shadow:var(--shadow); margin-bottom:22px}
.figure svg{display:block; width:100%; height:auto}
.figure figcaption{font-size:12px; color:var(--sub); margin-top:12px}

.grid2{display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px}
.card{background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:19px; box-shadow:var(--shadow)}
.card h3{font-family:"Zen Kaku Gothic New",sans-serif; font-size:15px; font-weight:700; margin:0 0 6px}
.card p{margin:0; font-size:13.5px; color:var(--sub)}

/* 立場の表明。点数を信じてよいか判断するのは結果を見た直後なので、
   サンプルのすぐ後ろに置く。詳しい説明は下の中立性セクションが担う。 */
.stance{background:var(--accent); color:var(--accent-ink)}
.stance .wrap{padding:28px 20px}
.stance .big{font-family:"Zen Old Mincho",serif; font-weight:600;
  font-size:clamp(19px,5vw,26px); line-height:1.6; margin:0 0 8px}
.stance p:last-child{margin:0; font-size:14px; line-height:1.9; opacity:.9}
.stance a{color:inherit; text-underline-offset:3px}

/* ---------- CTAバンド ---------- */
.band{position:relative; overflow:hidden; background:var(--hero-bg); color:var(--paper)}
.band::before{
  content:""; position:absolute; inset:0;
  background:radial-gradient(90% 120% at 12% 0%, rgba(158,195,230,.16) 0%, rgba(12,27,42,0) 60%);
}
.band .wrap{position:relative; padding:46px 20px}
.band h2{font-family:"Zen Old Mincho",serif; font-weight:600; font-size:clamp(20px,5vw,27px); line-height:1.6; margin:0 0 10px}
.band p{margin:0 0 22px; font-size:14px; color:var(--paper-dim); max-width:32em}
.band .btn-primary{background:var(--paper); color:var(--hero-bg)}
.band .btn-primary:hover{transform:translateY(-2px)}
.band .btn-ghost{color:var(--paper); border-color:rgba(240,238,233,.38)}
.band .btn-ghost:hover{background:rgba(240,238,233,.11)}
.soon{margin:24px 0 0; padding-top:16px; border-top:1px solid rgba(240,238,233,.18); font-size:13px; color:var(--paper-dim)}

/* ---------- FAQ ---------- */
details{border-bottom:1px solid var(--line)}
details:first-of-type{border-top:1px solid var(--line)}
summary{
  cursor:pointer; list-style:none; padding:17px 30px 17px 0; position:relative;
  font-family:"Zen Kaku Gothic New",sans-serif; font-size:15px; font-weight:700;
}
summary::-webkit-details-marker{display:none}
summary::after{content:"＋"; position:absolute; right:2px; top:16px; color:var(--sub); font-weight:400}
details[open] summary::after{content:"−"}
details p{margin:0 0 18px; font-size:14px; color:var(--sub); max-width:40em}

/* ---------- 免責・フッター ---------- */
.note{background:var(--surface-2); border:1px solid var(--line); border-radius:14px; padding:19px}
.note p{margin:0; font-size:13px; color:var(--sub)}
footer{padding:30px 0 46px; text-align:center}
footer p{margin:0 0 6px; font-size:12px; color:var(--sub); line-height:1.9}
footer a{color:var(--ink)}

.glist{list-style:none; padding:0; margin:18px 0 14px;
  display:grid; gap:2px}
.glist li{border-top:1px solid var(--line, rgba(240,238,233,.14))}
.glist li:last-child{border-bottom:1px solid var(--line, rgba(240,238,233,.14))}
.glist a{display:block; padding:13px 2px; text-decoration:none; font-weight:700;
  font-size:15px; line-height:1.55}
.lead-sm{font-size:14px; line-height:1.9; max-width:34em; margin:0}
.reveal{opacity:0; transform:translateY(14px); transition:opacity .7s ease, transform .7s ease}
.reveal.is-in{opacity:1; transform:none}

@media (max-width:560px){
  .hero .wrap{padding-top:34px; padding-bottom:38px}
  /* スマホでは「買う前に、データで答え合わせ。」の行の右に置く。
     この行は240pxほどで終わるので、右側が空く（見出しは折り返さず
     幅いっぱい、本文とボタンも幅いっぱいなので、他に余地は無い）。
     ％ではなくpxで指定する。％だと本文を書き換えるたびに動いて、
     文字の上に乗ってしまう。
     輪は一回り小さくして、上の見出しと下の本文それぞれと間隔を取る。
     ラベルはピンの左側に置く（右側だと画面外にはみ出す）。
     注意：タグラインを長くすると、ラベルとぶつかる。 */
  .pin{left:auto; right:24px; top:183px; width:10px; height:10px;
    margin:-5px 0 0 -5px}
  .pin i{inset:3px}
  .pin b, .pin s{animation-name:ping-sm}
  .pin-label{left:auto; right:46px; top:176px; font-size:9px}
  section{padding:42px 0}
  .cta-row .btn{width:100%}
  .b{grid-template-columns:56px 1fr 48px}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *{animation:none !important; transition:none !important}
  .reveal{opacity:1; transform:none}
  #map{opacity:1}
}
</style>
</head><body>

<div class="bar is-top" id="bar">
  <div class="bar-in">
    <a class="lock" href="/">
      HI_SYMBOL_PLACEHOLDER
      HI_WORDMARK_PLACEHOLDER
    </a>
    <div class="bar-right">
      <span class="chip">住宅購入診断</span>
      <button type="button" class="burger" id="burger" aria-label="メニューを開く" aria-expanded="false" aria-controls="menu">
        <span></span><span></span><span></span>
      </button>
    </div>
  </div>
  <nav class="menu" id="menu" hidden>
LP_MENU_PLACEHOLDER
    <a href="#wakaru">診断でわかること</a>
    <a href="#shikumi">仕組みと中立性</a>
    <a href="#faq">よくある質問</a>
  </nav>
</div>

<header class="hero">
  <canvas id="map" aria-hidden="true"></canvas>
  <span class="pin" aria-hidden="true"><b></b><s></s><i></i></span>
  <span class="pin-label" aria-hidden="true">検討中の物件</span>
  <div class="wrap">
    <p class="eyebrow">住宅購入 セカンドオピニオン</p>
    <h1>この家、かっていい？</h1>
    <p class="tag">買う前に、<em>データで答え合わせ。</em></p>
    <!-- 不安の列挙は下のチェックリストが、中立性は結果サンプル直後の帯が
         それぞれ担っている。ここは問いを1つと、何をするのかだけに絞る。 -->
    <p class="lead"><b>この価格で本当にいいのか。</b>
     確かめないまま決めるには、大きすぎる買い物です。
     公的データから、価格・災害リスク・返済までを<b>100点で採点</b>します。</p>
    <div class="cta-row">
      <a class="btn btn-primary" href="/buy">
        無料で診断する（戸建）
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 8h11M9 4l4.2 4L9 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </a>
      <a class="btn btn-ghost" href="/mansion">マンションを診断する</a>
    </div>
    <!-- 本文から「物件は売りません」を外したぶん、ここで拾う。中立性は
         このサービスの一番の武器なので、ファーストビューから消せない。 -->
    <p class="micro">会員登録なし ／ 料金なし ／ 売り込みなし ／ 約3分</p>
  </div>
  <div class="howto">
    <div class="wrap">
      <ol>
        <li><b>SUUMO等で</b>気になる物件のページを開く</li>
        <li><b>説明文をコピー</b>（URLではなく、価格や面積が書かれた文章）<br>
          <a href="/copy-guide" style="color:var(--pin)">アプリでコピーできないときは</a></li>
        <li><b>貼り付けて診断</b>。約3分で100点の採点が出ます</li>
      </ol>
      <p>販売図面のPDFも、開いて文字をコピーすれば同じように読み取れます。</p>
    </div>
  </div>

  <div class="sources">
    <div class="wrap">
      <span class="k">DATA SOURCE</span>
      <dl>
        <div><dt>国土交通省</dt><dd>成約価格・災害リスク・用途地域・学区</dd></div>
        <div><dt>国土地理院</dt><dd>住所から座標の特定</dd></div>
        <div><dt>OpenStreetMap</dt><dd>スーパー・商業施設</dd></div>
      </dl>
    </div>
  </div>
</header>

<main>
  <section>
    <div class="wrap reveal">
      <p class="eyebrow">こんな状態ではありませんか</p>
      <h2>数千万円の買い物なのに、<br>手元にある判断材料が少なすぎる。</h2>
      <ul class="checks">
        <li>気に入った家だが、<b>この価格が妥当なのか分からない</b></li>
        <li>近隣の相場と比べて高いのか安いのか、調べ方が分からない</li>
        <li>「人気の物件です」と言われたが、確かめようがない</li>
        <li>ハザードマップを見ても、結局どう判断すればいいか分からない</li>
        <li>住宅ローンを<b>無理なく返し続けられるか不安</b></li>
        <li>買ったあとに後悔しないか、それだけが怖い</li>
      </ul>
      <p class="sub" style="margin-top:16px">ひとつでも当てはまるなら、まず採点してみてください。
       内見して、気に入って、でも決め手がない。その迷いを公的データで裏取りするためのツールです。</p>
      <div class="rows">
        <div class="rowitem">
          <p class="tagline">PRICE</p>
          <p class="q">この価格、高いのか安いのか分からない</p>
          <p class="a">周辺で実際にいくらで売買されたのか。近隣・同規模・同築年の成約事例から適正レンジを復元し、提示価格がその中のどこに位置するかを示します。</p>
        </div>
        <div class="rowitem">
          <p class="tagline">RISK</p>
          <p class="q">災害リスクを、後から知るのが怖い</p>
          <p class="a">洪水・高潮・津波・土砂災害の指定状況を住所から自動で照合。用途地域や周辺施設（学校・医療・駅）もあわせて確認します。</p>
        </div>
        <div class="rowitem">
          <p class="tagline">LOAN</p>
          <p class="q">この返済、本当に続けられるのか</p>
          <p class="a">年収・頭金・金利から毎月の返済額と返済比率を計算。物件の点数だけでなく、あなたの家計に対して無理がないかまで含めて診断します。</p>
        </div>
      </div>
    </div>
  </section>

  <section id="wakaru">
    <div class="wrap reveal">
      <p class="eyebrow">診断でわかること</p>
      <h2>点数と、その根拠が返ってきます。</h2>
      <p class="sub">総合点だけでなく、6つの観点それぞれの内訳と、その点数になった根拠（採点に使った成約事例の一覧）まで表示します。</p>

      <div class="sample">
        <div class="sample-head">
          <span class="t">中古戸建／築19年・3,500万円　※表示は見本です</span>
          <span class="stamp">SAMPLE</span>
        </div>
        <div class="sample-body">
          <div class="score">
            <span class="n">75</span>
            <span class="d">／ 100点</span>
          </div>
          <p class="verdict">総合判定：条件を詰めれば買ってよい水準</p>

          <div class="rangefig">
            <svg viewBox="0 0 620 92" role="img" aria-label="推定価格レンジ3,140万円から3,600万円に対し、提示価格3,500万円はレンジ内の上寄り。中央値は3,380万円。">
              <rect class="r-band" x="120" y="44" width="380" height="14" rx="7"/>
              <line class="r-axis" x1="30" y1="51" x2="590" y2="51" stroke-width="1"/>
              <line class="r-cap" x1="120" y1="38" x2="120" y2="64" stroke-width="1"/>
              <line class="r-cap" x1="500" y1="38" x2="500" y2="64" stroke-width="1"/>
              <line class="r-med" x1="322" y1="40" x2="322" y2="62" stroke-width="2" stroke-dasharray="3 3"/>
              <text class="r-lbl" x="322" y="80" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11">中央値 3,380</text>
              <text class="r-lbl" x="120" y="30" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11">3,140</text>
              <text class="r-lbl" x="500" y="30" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11">3,600</text>
              <circle class="r-dot" cx="440" cy="51" r="9"/>
              <circle class="r-ring" cx="440" cy="51" r="15" stroke-width="1" opacity=".45"/>
              <text class="r-dotlbl" x="440" y="22" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="12">提示 3,500万円</text>
            </svg>
          </div>

          <div class="bars">
            <div class="b"><span class="lbl">物件</span><span class="track"><span class="fill" style="width:76%"></span></span><span class="val">19/25</span></div>
            <div class="b"><span class="lbl">立地</span><span class="track"><span class="fill" style="width:80%"></span></span><span class="val">16/20</span></div>
            <div class="b"><span class="lbl">価格</span><span class="track"><span class="fill" style="width:85%"></span></span><span class="val">17/20</span></div>
            <div class="b"><span class="lbl">リスク</span><span class="track"><span class="fill" style="width:60%"></span></span><span class="val">9/15</span></div>
            <div class="b"><span class="lbl">資金</span><span class="track"><span class="fill" style="width:80%"></span></span><span class="val">8/10</span></div>
            <div class="b"><span class="lbl">資産性</span><span class="track"><span class="fill" style="width:60%"></span></span><span class="val">6/10</span></div>
          </div>

          <div class="facts">
            <div class="fact">
              <div class="k">推定価格レンジ</div>
              <div class="v good">3,140–3,600<span class="u">万円</span></div>
              <div class="n">近隣の成約6件から復元</div>
            </div>
            <div class="fact">
              <div class="k">提示価格の位置</div>
              <div class="v">レンジ内・上寄り</div>
              <div class="n">中央値比 ＋3.2%</div>
            </div>
            <div class="fact">
              <div class="k">洪水浸水想定</div>
              <div class="v warn">0.5–3.0<span class="u">m</span></div>
              <div class="n">土砂・津波の指定はなし</div>
            </div>
            <div class="fact">
              <div class="k">毎月返済／返済比率</div>
              <div class="v">10.4<span class="u">万円</span> ／ 21.4<span class="u">%</span></div>
              <div class="n">金利1.25%・35年・頭金300万円</div>
            </div>
          </div>

          <p class="sample-note"><b>100点は「良い家度」ではありません。</b>
           価格・立地・災害リスク・資金・資産性を公的データから評価した、
           <b>購入判断の目安</b>です。点数が低い物件は「買ってはいけない物件」
           ではなく、値引き交渉の材料や、契約前に確認すべきことが多い物件を
           意味します。</p>
          <p class="sample-note">※ 表示はサンプルです。実際の結果には、採点に使った成約事例が1件ずつ（所在・面積・築年・成約価格・類似度）並びます。</p>
        </div>
      </div>
    </div>
  </section>

  <section>
    <div class="wrap reveal">
      <p class="eyebrow">使い方</p>
      <h2>入力は3分。物件ページのコピペから。</h2>
      <p class="sub">用意するものは、気になっている物件のページだけです。
       URLではなく、そこに書かれている<b>説明文</b>をコピーします。</p>
      <div class="steps">
        <div class="step">
          <span class="num">1</span>
          <div>
            <h3>物件ページの説明文を貼り付ける</h3>
            <p>SUUMOやアットホームのページ本文をコピーして貼るだけで、
             <b>価格・所在地・土地／建物面積・築年・駅徒歩・構造</b>を読み取ります。
             販売図面のPDFも、開いて文字をコピーすれば同じことができます。</p>
            <p class="fine">スマホアプリは文字を選択できません。
             <a href="/copy-guide">コピーの仕方はこちら</a></p>
          </div>
        </div>
        <div class="step">
          <span class="num">2</span>
          <div>
            <h3>読み取れた内容を確認して直す</h3>
            <p>自動で埋まった欄を目で確かめ、空いているところだけ手で入れます。
             必ず要るのは<b>価格・所在地・面積・築年・駅徒歩の5つ</b>だけです。</p>
            <p class="fine">世帯年収と頭金は任意です。入れると、
             月々の返済額と返済負担率まで見られます。</p>
          </div>
        </div>
        <div class="step">
          <span class="num">3</span>
          <div>
            <h3>診断する</h3>
            <p>住所から座標を割り出し、公的データを照合して<b>100点で採点</b>します。
             数十秒で終わります。結果は<b>1枚の画像として保存</b>できるので、
             内見や商談の前に見返せます。</p>
            <p class="fine">確かめられなかった項目は点数に入れず、「未取得」と表示します。
             埋めた振りはしません。</p>
          </div>
        </div>
      </div>

      <div class="cta-row cta-mid">
        <a class="btn btn-primary" href="/buy">
          無料で診断する（戸建）
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 8h11M9 4l4.2 4L9 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </a>
        <a class="btn btn-ghost" href="/mansion">マンションを診断する</a>
      </div>
      <p class="sub" style="text-align:center;margin-top:10px">
       会員登録も費用もかかりません。</p>
    </div>
  </section>

  <div class="stance">
    <div class="wrap">
      <p class="big">不動産を売る立場の人間が、<br>何も売らないサービスとして運営しています。</p>
      <p>運営者は宅地建物取引士として、住宅の売買仲介に従事しています。
       販売図面に記載される情報がいかに限られているかは、実務のなかで
       日々目にしてきました。<br>
       HOME INDEX において、物件の仲介は行いません。仲介手数料および紹介料を
       一切受け取らず、特定の物件・事業者へ誘導することもありません。
       点数を上げる理由も、下げる理由もありません。
       <a href="/about">運営者について</a>　/
       <a href="#shikumi">なぜ中立だと言えるのか</a></p>
    </div>
  </div>


  <section id="shikumi">
    <div class="wrap reveal">
      <p class="eyebrow">なぜ中立だと言えるのか</p>
      <h2>物件を売らないから、<br>点数を盛る理由がない。</h2>
      <p class="sub">住所を起点に4系統の公的データを取り寄せ、<b>公開された配点ルール</b>で計算します。
       だから、なぜこの点数になったのかを1項目ずつ説明できます。
       点数はAIの判断ではなく計算の結果です（機械学習による推定は使っていません）。</p>

      <figure class="figure">
        <svg viewBox="0 0 660 250" role="img" aria-label="住所を起点に、成約価格・ハザード・用途地域と周辺施設・人口統計の4系統の公的データを取得し、公開された配点ルールで計算して100点の採点票と根拠一覧を出力する流れ図。">
          <g font-family="Noto Sans JP, sans-serif" font-size="12">
            <rect class="f-node" x="8" y="98" width="96" height="54" rx="10"/>
            <text class="f-node-t" x="56" y="121" text-anchor="middle" font-size="13" font-weight="700">住所</text>
            <text class="f-node-t" x="56" y="139" text-anchor="middle" font-size="10" opacity=".8">＋ 物件条件</text>

            <g class="f-flow" stroke-width="1.2">
              <path d="M104 125 C140 125 140 34 176 34"/>
              <path d="M104 125 C140 125 140 95 176 95"/>
              <path d="M104 125 C140 125 140 155 176 155"/>
              <path d="M104 125 C140 125 140 216 176 216"/>
            </g>

            <g class="f-box" stroke-width="1">
              <rect x="176" y="12" width="212" height="44" rx="9"/>
              <rect x="176" y="73" width="212" height="44" rx="9"/>
              <rect x="176" y="133" width="212" height="44" rx="9"/>
              <rect x="176" y="194" width="212" height="44" rx="9"/>
            </g>
            <g class="f-t" font-size="12.5">
              <text x="192" y="33">成約価格（近隣・同規模）</text>
              <text x="192" y="94">ハザード（洪水・土砂・津波）</text>
              <text x="192" y="154">用途地域・周辺施設</text>
              <text x="192" y="215">将来推計人口（250mメッシュ）</text>
            </g>
            <g class="f-s" font-family="IBM Plex Mono, monospace" font-size="9" letter-spacing="1">
              <text x="192" y="48">国土交通省</text>
              <text x="192" y="109">国土交通省・国土地理院</text>
              <text x="192" y="169">国土交通省</text>
              <text x="192" y="230">国土交通省</text>
            </g>

            <g class="f-flow" stroke-width="1.2">
              <path d="M388 34 C424 34 424 125 460 125"/>
              <path d="M388 95 C424 95 424 125 460 125"/>
              <path d="M388 155 C424 155 424 125 460 125"/>
              <path d="M388 216 C424 216 424 125 460 125"/>
            </g>

            <rect class="f-rule" x="460" y="86" width="86" height="78" rx="10" stroke-width="1.4" stroke-dasharray="4 3"/>
            <text class="f-t" x="503" y="116" text-anchor="middle" font-size="12" font-weight="700">配点ルール</text>
            <text class="f-s" x="503" y="134" text-anchor="middle" font-size="10">公開・変更履歴あり</text>
            <text class="f-s" x="503" y="150" text-anchor="middle" font-size="10">機械学習なし</text>

            <path class="f-flow" d="M546 125 H588" stroke-width="1.2"/>
            <path class="f-flow" d="M580 119 L588 125 L580 131" stroke-width="1.2"/>
            <rect class="f-node" x="588" y="92" width="64" height="66" rx="10"/>
            <text class="f-node-t" x="620" y="124" text-anchor="middle" font-size="20" font-weight="700" font-family="Zen Kaku Gothic New, sans-serif">100</text>
            <text class="f-node-t" x="620" y="142" text-anchor="middle" font-size="10" opacity=".85">点＋根拠</text>
          </g>
        </svg>
        <figcaption>住所から採点までの流れ。どの数値がどの出典から来たかは、結果画面で1項目ずつ確認できます。</figcaption>
      </figure>

      <div class="grid2">
        <div class="card">
          <h3>物件を売らない・紹介しない</h3>
          <p>仲介手数料も紹介料も受け取りません。特定の物件や不動産会社へ誘導する仕組みを持ちません。</p>
        </div>
        <div class="card">
          <h3>使うのは公的データ</h3>
          <p>国土交通省の成約価格・災害リスク・将来推計人口、国土地理院の住所検索。商業施設については公的データに無いためOpenStreetMapを使用しています。出典はすべて結果画面に明示します。</p>
        </div>
        <div class="card">
          <h3>なぜその点数なのか、たどれる</h3>
          <p>点数は公開された配点ルールの計算結果です。採点に使った成約事例と類似度を1件ずつ並べるので、同じ計算を手で追いかけられます。推測で点をつけることはありません。</p>
        </div>
        <div class="card">
          <h3>推定であることを隠さない</h3>
          <p>適正価格は推定であって絶対値ではありません。建物内部の劣化や液状化など、まだ取得していない要素は「未取得」と表示し、埋めた振りをしません。</p>
        </div>
      </div>
    </div>
  </section>
</main>

<div class="band">
  <div class="wrap">
    <h2>気になっている物件、いま採点してみますか。</h2>
    <p>入力は物件説明のコピペから。会員登録も費用もかかりません。</p>
    <div class="cta-row">
      <a class="btn btn-primary" href="/buy">
        無料で診断する（戸建）
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 8h11M9 4l4.2 4L9 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </a>
      <a class="btn btn-ghost" href="/mansion">マンションを診断する</a>
    </div>
    <p class="soon">どちらも会員登録は不要です。マンションは管理費・修繕積立金も評価に含めます。</p>
  </div>
</div>

<section id="faq">
  <div class="wrap reveal">
    <p class="eyebrow">よくある質問</p>
    <h2>先に聞かれることを、先に答えます。</h2>
    <details open>
      <summary>本当に無料ですか。あとから請求されませんか。</summary>
      <p>無料です。会員登録も不要で、料金が発生する画面はありません。物件の仲介やローンの紹介も行わないため、診断後に営業の連絡が来ることもありません。</p>
    </details>
    <details>
      <summary>入力した年収や住所は保存されますか。</summary>
      <p>診断の計算に使うだけで、営業目的では利用しません。詳細はプライバシーポリシーに記載しています。気になる場合は、年収や頭金を概算で入力しても価格・リスクの診断は機能します。</p>
    </details>
    <details>
      <summary>物件のURLを貼れば診断できますか。</summary>
      <p>URLではなく、物件ページに書かれている説明文（価格・所在地・面積・築年・駅徒歩など）をコピーして貼り付けてください。ご自身がコピーした情報を解析する形をとっています。販売図面のPDFも、開いて文字をコピーすれば同じように読み取れます。</p>
    </details>
    <details>
      <summary>新築でも診断できますか。</summary>
      <p>できます。新築は近隣の新築成約事例を優先し、土地相当分と建物相当分を分けて価格を推定します。中古とは類似度の重み付けを変えています。</p>
    </details>
    <details>
      <summary>マンションには対応していますか。</summary>
      <p>対応しています。マンションは所在階・向き・専有面積あたりの単価など戸建と評価軸が違うため、別の診断として用意しています。管理費と修繕積立金は、国土交通省のガイドラインの目安と照らして評価に含めています。ただし修繕積立金の残高・大規模修繕の履歴・滞納の有無は公的データから取得できないため、点数には入れていません。</p>
    </details>
    <details>
      <summary>点数が低い物件は、買ってはいけないということですか。</summary>
      <p>違います。点数は「その価格と条件が、公的データから見てどのあたりに位置するか」を示すものです。低い点数は、値引き交渉の材料や、事前に確認すべき項目のリストとして使ってください。最終的な判断は、現地の確認と専門家への相談のうえで行ってください。</p>
    </details>
  </div>
</section>

<section id="guides">
  <div class="wrap reveal">
    <p class="eyebrow">解説</p>
    <h2>点数の根拠は、開いて書いています。</h2>
    <p class="lead-sm">どの公的データのどの区分を、何点として扱っているか。
     採点に使っている数字そのものを記事にしています。</p>
    <ul class="glist">GUIDE_LINKS_PLACEHOLDER</ul>
    <p class="fine"><a href="/guide">解説の一覧を見る →</a></p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="note">
      <p><b>ご利用にあたって。</b>本サービスの診断結果は公的データにもとづく推定であり、物件の価値や安全性を保証するものではありません。建物内部の劣化状況、境界や権利関係、地盤の詳細などは診断に含まれていません。売買契約の判断にあたっては、必ず宅地建物取引士・建築士・ファイナンシャルプランナー等の専門家にご確認ください。</p>
    </div>
  </div>
</section>

<footer>
  <div class="wrap">FOOTER_PLACEHOLDER</div>
</footer>

<script>
/* ---- ヘッダー：ヒーロー上は透過、スクロールで白背景 ---- */
(function(){
  var bar=document.getElementById("bar"), b=document.getElementById("burger"), m=document.getElementById("menu");
  function sync(){
    var open = m && !m.hidden;
    bar.classList.toggle("is-top", window.scrollY < 24 && !open);
  }
  window.addEventListener("scroll", sync, {passive:true});
  if(b && m){
    function set(open){
      m.hidden=!open;
      b.setAttribute("aria-expanded", open?"true":"false");
      b.setAttribute("aria-label", open?"メニューを閉じる":"メニューを開く");
      b.classList.toggle("is-open", open);
      sync();
    }
    b.addEventListener("click", function(e){ e.preventDefault(); e.stopPropagation(); set(m.hidden); });
    Array.prototype.forEach.call(m.querySelectorAll("a"), function(a){
      a.addEventListener("click", function(){ set(false); });
    });
    document.addEventListener("click", function(e){
      if(!m.hidden && !m.contains(e.target) && !b.contains(e.target)) set(false);
    });
    document.addEventListener("keydown", function(e){
      if(e.key==="Escape" && !m.hidden) set(false);
    });
  }
  sync();
})();

/* ---- ヒーロー背景：等高線と区画を手続き的に描く地形図 ---- */
(function(){
  var cv=document.getElementById("map");
  if(!cv || !cv.getContext) return;
  var ctx=cv.getContext("2d");

  // 毎回同じ絵になるよう、乱数は固定シードの線形合同法で回す
  function seeded(s){ return function(){ s=(s*1664525+1013904223)%4294967296; return s/4294967296; }; }

  function draw(){
    var dpr=Math.min(window.devicePixelRatio||1, 2);
    var w=cv.clientWidth, h=cv.clientHeight;
    if(!w || !h) return;
    cv.width=Math.round(w*dpr); cv.height=Math.round(h*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,w,h);

    var rand=seeded(20260820);

    // 等高線：3つの正弦波を重ねて地形の起伏をつくる
    ctx.lineWidth=1;
    for(var i=-6;i<26;i++){
      var y0=h*0.12 + i*(h*0.052);
      ctx.beginPath();
      for(var x=-20;x<=w+20;x+=6){
        var t=x/w;
        var y=y0
          + Math.sin(t*3.1 + i*0.22)*(h*0.075)
          + Math.sin(t*7.4 + i*0.11)*(h*0.026)
          + Math.sin(t*1.7 - i*0.30)*(h*0.045);
        if(x<=-20) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      }
      ctx.strokeStyle = (i%5===0) ? "rgba(168,203,233,.30)" : "rgba(150,187,220,.15)";
      ctx.stroke();
    }

    // 区画：斜めに振った矩形のかたまり（宅地の割付のイメージ）
    ctx.save();
    ctx.translate(w*0.60, h*0.42);
    ctx.rotate(-0.19);
    ctx.lineWidth=1;
    for(var r=0;r<5;r++){
      for(var c=0;c<7;c++){
        if(rand()<0.24) continue;
        var pw=w*0.052*(0.7+rand()*0.6), ph=h*0.10*(0.7+rand()*0.5);
        var px=(c-3)*(w*0.058), py=(r-2)*(h*0.115);
        ctx.strokeStyle="rgba(198,222,244,.20)";
        ctx.strokeRect(px,py,pw,ph);
        if(rand()<0.22){ ctx.fillStyle="rgba(198,222,244,.06)"; ctx.fillRect(px,py,pw,ph); }
      }
    }
    ctx.restore();

    // 道路：太めの線を2本通して地図らしさを出す
    ctx.lineWidth=2.4;
    ctx.strokeStyle="rgba(226,238,250,.13)";
    ctx.beginPath();
    ctx.moveTo(-10,h*0.74); ctx.bezierCurveTo(w*0.3,h*0.62,w*0.55,h*0.58,w+10,h*0.30);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(w*0.36,-10); ctx.bezierCurveTo(w*0.42,h*0.35,w*0.66,h*0.52,w+10,h*0.80);
    ctx.stroke();
  }

  var t=null;
  function schedule(){ clearTimeout(t); t=setTimeout(draw,120); }
  window.addEventListener("resize", schedule);
  draw();
  requestAnimationFrame(function(){ cv.classList.add("is-in"); });
})();

/* ---- スクロールでセクションをふわりと出す ---- */
(function(){
  var els=document.querySelectorAll(".reveal");
  if(!("IntersectionObserver" in window)){
    Array.prototype.forEach.call(els, function(e){ e.classList.add("is-in"); });
    return;
  }
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(en){
      if(en.isIntersecting){ en.target.classList.add("is-in"); io.unobserve(en.target); }
    });
  }, {rootMargin:"0px 0px -12% 0px"});
  Array.prototype.forEach.call(els, function(e){ io.observe(e); });
})();
</script>
</body></html>
"""

# トップから各記事へ内部リンクを張る。記事は src/guides.py が持っている
# ので、増えれば自動で並ぶ。手で書くと、書き足すたびに直し忘れる。
_LP_GUIDE_LINKS = "".join(
    f'<li><a href="/guide/{g.slug}">{html.escape(g.title)}</a></li>'
    for g in guides.all_guides())

LP = (LP.replace("GUIDE_LINKS_PLACEHOLDER", _LP_GUIDE_LINKS)
      .replace("LP_FONT_LINK_PLACEHOLDER", LP_FONT_LINK)
      .replace("ICON_LINKS_PLACEHOLDER", ICON_LINKS)
      .replace("HI_SYMBOL_PLACEHOLDER", symbol_small())
      .replace("HI_WORDMARK_PLACEHOLDER", WORDMARK)
      .replace("LP_MENU_PLACEHOLDER", lp_menu_links())
      .replace("FOOTER_PLACEHOLDER", FOOTER))

GRADE_COLOR = {"A": "#15803d", "B": "#16a34a", "C": "#d97706",
               "D": "#dc2626", "E": "#b91c1c"}
GRADE_COMMENT = {"A": "非常に検討価値が高い", "B": "検討価値あり",
                 "C": "要検討", "D": "慎重に検討", "E": "要注意"}


def _vclass(v):
    return {"割安の可能性": "v-under", "概ね適正": "v-fair",
            "割高の可能性": "v-over"}.get(v, "v-none")


def _catcolor(raw):
    if raw >= 0.8:
        return "#15803d"
    if raw >= 0.6:
        return "#16a34a"
    if raw >= 0.4:
        return "#d97706"
    return "#dc2626"


@app.route("/healthz")
def healthz():
    return "ok", 200


_BILLING_TERMS = ("""<h2>第9条（有料プラン）</h2>
<p>本サービスには、無料で利用できる範囲と、有料プラン（以下「PRO」）があります。
PROの内容・料金・支払方法・提供時期・解約の方法は、申込みの最終確認画面および
<a href="/tokushoho">特定商取引法に基づく表記</a>に表示します。</p>
<p>PROは、""" + PRICE_LABEL + """の月額制です。
<b>解約されない限り、毎月同じ日に自動で更新されます。</b>
初回と2回目以降で金額が変わることはありません。</p>
<h2>第10条（解約）</h2>
<p>利用者は、マイページからいつでもPROを解約できます。解約の手続に、
電話や書面は必要ありません。</p>
<p>解約後も、支払済みの期間の末日まではPROをご利用いただけます。
その後は無料プランに切り替わります。</p>
<h2>第11条（返金）</h2>
<p>本サービスは役務の提供であり、その性質上、返品はできません。
<b>日割りその他の返金は行いません。</b>
ただし、当方の責めに帰すべき事由により長期間サービスを提供できなかった場合は、
個別に対応します。</p>
<h2>第12条（保存データの扱い）</h2>
<p>解約しても、保存された診断結果およびメモは消去しません。
無料プランの保存件数を超えている分についても、引き続き閲覧および比較が
できます。ただし、上限を超えている間は新たな保存ができません。</p>
<h2>第13条（料金の改定）</h2>
<p>料金を改定する場合は、<b>改定の1か月前までに本サービス上で告知します。</b>
改定後の料金は、告知後に到来する更新日から適用します。改定に同意されない
場合は、更新日までに解約してください。</p>
""" if billing_on() else "")

_TERMS_BODY = ("""
<p class="sub">最終改定日：2026年8月28日</p>
<h2>第1条（本サービス）</h2>
<p>「HOME INDEX（購入診断）」（以下「本サービス」）は、利用者が入力・貼り付けした物件情報と、
国土交通省 不動産情報ライブラリ・国土地理院等の公的データ、および OpenStreetMap に
もとづき、
住まいに関する<b>参考情報</b>を提供するものです。本サービスは不動産の売買・交換・貸借の
媒介・代理・査定・鑑定を行うものではなく、宅地建物取引業法上の取引・査定に該当しません。</p>
<h2>第2条（参考情報であること・免責）</h2>
<p>本サービスの診断結果（推定価格・スコア・防災/人口情報等）は、AIと公的データによる
<b>目安・参考情報</b>であり、物件の価値・適法性・再建築可否・取引の可否・安全性を保証するものでは
ありません。掲載データは取得時点のもので、最新性・正確性・完全性を保証しません。
実際の購入判断・契約・重要事項の確認は、宅地建物取引士など有資格の専門家および現地確認を
前提としてください。本サービスの利用または利用不能により生じたいかなる損害についても、
運営者は法令上許容される範囲で責任を負いません。</p>
<h2>第3条（禁止事項）</h2>
<p>利用者は、次の行為を行ってはなりません。(1) 法令または公序良俗に反する行為、
(2) 本サービスや第三者の権利・利益を侵害する行為、(3) 自動化ツール等による過度なアクセス・
本サービスの運営を妨げる行為、(4) 取得した情報の権利者の許諾なき再配布・商用転用、
(5) 本サービスを不動産取引の唯一の判断根拠として用いること。</p>
<h2>第4条（データの出典・第三者コンテンツ）</h2>
<p>本サービスは、国土交通省 不動産情報ライブラリ（不動産取引価格情報、用途地域、
災害リスク、周辺施設、学区、将来推計人口等）、国土地理院（住所から座標の特定）、
および OpenStreetMap のデータを加工して利用しています。
OpenStreetMap のデータは © OpenStreetMap contributors によるもので、
Open Database License（ODbL）に基づいて利用しています。
各データの権利は各提供元に帰属し、その利用条件にも従います。
将来、統計データ等のために他の公的データを追加して利用する場合があります。</p>
<h2>第5条（貼り付け情報の取り扱い）</h2>
<p>物件説明の解析は、利用者ご自身が取得・入力した情報を利用者の私的利用の範囲で処理するものです。
物件情報サイト等の規約に反する形での情報取得・利用は行わないでください。</p>
<h2>第6条（変更・中断）</h2>
<p>運営者は、利用者への事前通知なく本サービスの内容を変更・中断・終了することがあります。</p>
""" + _BILLING_TERMS + """
<h2>第7条（準拠法・管轄）</h2>
<p>本規約は日本法に準拠し、本サービスに関する紛争は運営者所在地を管轄する裁判所を
第一審の専属的合意管轄とします。</p>
<h2>第8条（運営者）</h2>
<p>運営者：""" + OPERATOR + """<br>お問い合わせ：""" + CONTACT + """</p>
""")

# アカウント機能を使うときだけ足す記載。DATABASE_URL が無いときは
# 空文字になり、ポリシーは今までどおり「保存しない」と述べる。
_ACC_COLLECT = ("""
あわせて、アカウントをご利用の場合はメールアドレスをお預かりします。"""
                if db.enabled() else "")

_ACC_STORE = ("""<p>アカウントをご利用の場合に限り、利用者ご自身が「保存する」を
押した診断結果を、比較のためにお預かりします。保存されるのは、そのときの
点数・カテゴリ別の内訳・価格判定・返済額と、対象物件の所在地・価格などの
入力内容です。<b>世帯年収は保存しません</b>（返済額の計算結果のみを持ちます）。
保存した内容は、マイページからいつでも削除できます。</p>
<p>ログインはパスワードを用いず、メールアドレス宛の使い捨てリンクで行います。
<b>パスワードは保管しません。</b>リンクの文字列そのものも保存せず、
照合用のハッシュのみを持ち、30分・1回かぎりで無効になります。</p>"""
              if db.enabled() else "")

_ACC_PURPOSE = ("""、(4) アカウントの認証と、保存された診断結果の保管・表示"""
                if db.enabled() else "")

_ACC_MAIL = ("""<h2>5-2. メールの送信</h2>
<p>ログイン用リンクの送信に、メール配信事業者（Resend）を利用します。
この目的のためにメールアドレスを同社へ送信します。広告・宣伝のメールは
お送りしません。</p>""" if db.enabled() else "")

_PRIVACY_BODY = ("""
<p class="sub">最終改定日：2026年8月28日</p>
<h2>1. 取得する情報</h2>
<p>本サービスは、診断のために利用者が入力・貼り付けした情報（物件の所在地・価格・面積・築年・
駅距離・種別、任意入力の世帯年収・頭金等）を処理します。あわせて、アクセスに伴う技術情報
（IPアドレス等）を、不正利用防止・レート制限の目的で一時的に参照する場合があります。"""
+ _ACC_COLLECT + """</p>
<h2>2. 利用目的</h2>
<p>取得した情報は、(1) 診断結果の生成・表示、(2) 本サービスの品質改善、
(3) 不正・過度なアクセスの防止""" + _ACC_PURPOSE + """、
の目的にのみ利用します。診断のために不要な情報は取得しません。</p>
<h2>3. 保存・安全管理</h2>
<p>入力情報は原則として診断処理のために用い、サーバー上での恒久的な保存は行いません
（不正防止のためのアクセス記録を除く）。取り扱いにあたっては適切な安全管理措置を講じます。
地図・座標情報は各提供元の利用条件に従って取り扱います。</p>""" + _ACC_STORE + """
<h2>4. 第三者提供</h2>
<p>法令に基づく場合を除き、利用者の同意なく個人情報を第三者に提供しません。
診断に必要な範囲で公的データAPI等の外部サービスに対し、住所等の照会を行う場合があります。</p>
<h2>5. 外部サービス</h2>
<p>本サービスは、国土交通省 不動産情報ライブラリ・国土地理院のAPI、および
OpenStreetMap の Overpass API を利用します。診断のために、<b>入力された住所</b>
（座標を求めるため）と、そこから得た座標・市区町村コードを送信します。
<b>世帯年収・頭金・他の借入などの家計に関する入力は、外部に送信していません。</b>
これらは当サービス内での計算にのみ用います。
これらへの照会内容は各提供元の規約・プライバシーポリシーに従います。</p>
""" + _ACC_MAIL + """
<h2>6. お問い合わせ・開示等の請求</h2>
<p>本ポリシーに関するお問い合わせ、保有個人データの開示・訂正・削除等のご請求は、
下記までご連絡ください。</p>
<p>運営者：""" + OPERATOR + """<br>お問い合わせ：""" + CONTACT + """</p>
<h2>7. 改定</h2>
<p>本ポリシーは、必要に応じて改定することがあります。重要な変更は本ページに掲示します。</p>
""")


# ---- 運営者について --------------------------------------------------
# 誰が作ったのかを示すページ。YMYL（金銭にかかわる話題）では、
# 書き手が分からないと内容が評価されない。中立性の主張の裏付けでもある。

_ABOUT_BODY = ("""
<p class="sub">HOME INDEX は、個人が運営しているサービスです。</p>

<h2>運営責任者</h2>
<p><b>""" + OPERATOR + """</b><br>
宅地建物取引士。住宅の売買仲介に従事しています。</p>
<p>宅地建物取引業者に在籍しておりますが、<b>本サービスは勤務先とは関係のない、
個人による運営</b>です。勤務先の業務として行うものではなく、勤務先が保有する
情報を利用したものでもありません。</p>

<h2>開発の経緯</h2>
<p>売買仲介の実務において、購入を検討される方が手にできる判断材料は
限られています。販売図面に記載されるのは、価格・面積・築年・駅からの距離など、
ごく一部の項目にとどまります。</p>
<p>近隣の成約価格と比べて割高であるか、災害の想定区域に該当するか、
将来にわたって人口が維持される地域か。いずれも公開されている情報でありながら、
検討段階の方の手元には届いていないのが実情です。</p>
<p>本サービスは、その隔たりを公的データによって埋めることを目的として
開発しました。</p>

<h2>本サービスが行わないこと</h2>
<ul>
<li><b>不動産の仲介・媒介は行いません。</b></li>
<li><b>仲介手数料・紹介料は一切受け取りません。</b>特定の物件、事業者、
 金融機関へ誘導することもありません。</li>
<li><b>採点に人の判断を介在させません。</b>配点は公開したルールのとおりであり、
 個別の診断に手を加えることはありません。</li>
<li><b>事業者間の情報は使用しません。</b>利用しているのは、どなたでも
 参照できる公的データに限られます。</li>
</ul>

<h2>使用しているデータ</h2>
<p>国土交通省 不動産情報ライブラリ（成約価格・災害リスク・用途地域・学区・
将来推計人口）、国土地理院（住所からの座標特定）、OpenStreetMap（商業施設）。
出典は診断結果の画面にも表示しています。</p>
<p>取得できなかった項目は「未取得」と表示し、採点には反映しません。
不明な項目を推測で補うことはいたしません。</p>

<h2>お問い合わせ</h2>
<p>""" + CONTACT + """</p>
<p class="sub">診断結果に関するご意見、記載の誤りのご指摘をお寄せください。
公的データの解釈に誤りがあった場合は訂正いたします。</p>
""")

_ABOUT_JSONLD = (
    '<script type="application/ld+json">'
    '{"@context":"https://schema.org","@type":"ProfilePage",'
    '"mainEntity":{"@type":"Person","name":"' + OPERATOR + '",'
    '"jobTitle":"宅地建物取引士",'
    '"knowsAbout":["不動産売買仲介","中古住宅","住宅購入"],'
    '"description":"住宅の売買仲介に従事する宅地建物取引士。"}}'
    '</script>')


@app.route("/about")
def about():
    """運営者について。氏名が仮のままなら出さない。"""
    if not operator_named():
        from flask import abort
        abort(404)
    return _legal_page("運営者について", _ABOUT_BODY + _ABOUT_JSONLD)


@app.route("/terms")
def terms():
    return _legal_page("利用規約", _TERMS_BODY)


@app.route("/privacy")
def privacy():
    return _legal_page("プライバシーポリシー", _PRIVACY_BODY)


# ---- 特定商取引法に基づく表記 ----------------------------------------
# 有償で提供するときに義務が生じる。氏名（個人事業者は戸籍上の氏名）・住所・
# 電話番号は「請求があれば遅滞なく提供する」という省略規定の対象外で、
# 広告に表示しなければならない。
# 仮の値のまま出すと表示義務を満たさないので、揃うまでページごと出さない。

_TOKUSHO_BODY = ("""
<p class="sub">本表記は、有料プラン（PRO）の提供に関するものです。
 無料でご利用いただける範囲には課金は発生しません。</p>
<h2>販売事業者</h2>
<p>HOME INDEX（""" + OPERATOR + """）<br>
<span class="sub">個人事業として運営しています。屋号のみでは足りないため、
 氏名を併記しています。</span></p>
<h2>運営責任者</h2>
<p>""" + OPERATOR + """</p>
<h2>所在地</h2>
<p>""" + (OPERATOR_ADDRESS or "〔所在地〕") + """</p>
<h2>電話番号</h2>
<p>""" + (OPERATOR_TEL or "〔電話番号〕") + """<br>
<span class="sub">受付時間および連絡方法は、下記のメールでもお受けします。</span></p>
<h2>メールアドレス</h2>
<p>""" + CONTACT + """</p>
<h2>販売価格</h2>
<p>""" + PRICE_LABEL + """<br>
<span class="sub">解約されるまで毎月同額が発生します。初回と2回目以降で
 金額が変わることはありません。</span></p>
<h2>商品代金以外に必要な費用</h2>
<p>インターネット接続に要する通信料は、お客様のご負担となります。
 それ以外に当方が申し受ける費用はありません。</p>
<h2>支払方法・支払時期</h2>
<p>クレジットカード決済。お申込み時に初回分をお支払いいただき、
 以降は毎月同日に自動で決済されます。</p>
<h2>サービスの提供時期</h2>
<p>決済の完了後、ただちにご利用いただけます。</p>
<h2>解約・返金について</h2>
<p>マイページの「プランと設定」からいつでも解約できます。
 解約後も、お支払い済みの期間の末日まではご利用いただけます。<br>
 <b>本サービスは役務の提供であり、性質上、返品はできません。</b>
 日割りでの返金も行っておりません。<br>
 解約後も、保存された診断結果は消去されません。無料プランの保存件数を
 超えている分も引き続きご覧いただけます（新たな保存はできません）。</p>
<h2>動作環境</h2>
<p>インターネットに接続できる端末と、標準的なウェブブラウザ。</p>
""")


@app.route("/tokushoho")
def tokushoho():
    """特定商取引法に基づく表記。有料提供の準備が整うまでは出さない。"""
    if not billing_on():
        from flask import abort
        abort(404)
    return _legal_page("特定商取引法に基づく表記", _TOKUSHO_BODY)


@app.route("/")
def index():
    """トップページ。サービスの説明を置き、診断（/buy）へ送る。"""
    base = request.url_root.rstrip("/")
    return LP.replace("CANONICAL_URL", base + "/")


@app.route("/buy")
def buy():
    """購入診断（戸建）の入力フォーム。以前の / がこのURLになった。"""
    return render_template_string(FORM, v=_example_v(), listing="", banner=None)


# ---- 解説記事 --------------------------------------------------------
# 記事の中身は src/guides.py。ここは器だけを持つ。
#
# 法的なページ（_legal_page）と分けているのは、検索結果に出す前提の
# 作りが要るため。meta description、canonical、OGP、Article の構造化
# データ、パンくず。とくに author を /about の運営者に紐づけることが
# 効く。YMYL（金銭にかかわる話題）では、誰が書いたか分からない内容は
# 評価されない。

_GUIDE_CSS = (
    'body{margin:0;background:#f5f7fa;color:#1f2937;'
    'font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN",Meiryo,sans-serif}'
    '.wrap{max-width:720px;margin:0 auto;padding:24px 16px}'
    '.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:22px}'
    'h1{font-size:21px;margin:0 0 8px;line-height:1.55}'
    # 目次から飛んだとき、見出しが上のバーに隠れないようにする
    'h2{font-size:16px;margin:34px 0 10px;color:#111;'
    'border-left:3px solid #14395C;padding-left:9px;line-height:1.55;'
    'scroll-margin-top:80px}'
    'p,li{font-size:15px;line-height:1.95}'
    'li{margin-bottom:4px}'
    'a{color:#111}'
    '.sub{color:#6b7280;font-size:12px;line-height:1.8}'
    '.meta{color:#6b7280;font-size:12px;margin:0 0 16px}'
    '.lead{background:#f6f8fa;border:1px solid #e5e7eb;border-radius:10px;'
    'padding:14px 16px;margin:0 0 6px}'
    '.formula{background:#f6f8fa;border:1px solid #e5e7eb;border-radius:8px;'
    'padding:11px 14px;text-align:center}'
    '.toc{border:1px solid #e5e7eb;border-radius:10px;padding:14px 18px 15px;'
    'margin:20px 0 4px}'
    '.toc>span{display:block;font-size:11px;color:#6b7280;letter-spacing:.14em;'
    'margin-bottom:8px}'
    '.toc ol{margin:0;padding-left:1.35em}'
    '.toc li{font-size:14px;line-height:1.85;margin:0}'
    '.toc a{color:#1f2937;text-decoration:none}'
    '.toc a:hover{text-decoration:underline}'
    'table{border-collapse:collapse;width:100%;margin:12px 0}'
    'th,td{border:1px solid #e5e7eb;padding:7px 10px;text-align:left;font-size:13px}'
    'th{background:#f6f8fa;font-weight:600}'
    '.share{margin:26px 0 0;display:flex;align-items:center;gap:10px;'
    'flex-wrap:wrap;font-size:13px;color:#6b7280}'
    '.share a,.share button{display:inline-block;border:1px solid #e5e7eb;'
    'border-radius:8px;padding:7px 13px;font-size:13px;color:#1f2937;'
    'background:#fff;text-decoration:none;cursor:pointer;font-family:inherit}'
    '.share a:hover,.share button:hover{border-color:#c6ced7}'
    '.after{margin-top:34px;border-left:3px solid #14395C;background:#f6f8fa;'
    'padding:14px 16px}'
    '.after p{margin:0;font-size:14px;line-height:1.85}'
    '.after a{color:#14395C;font-weight:700}'
    '.cards{list-style:none;padding:0;margin:0}'
    '.cards li{border-top:1px solid #e5e7eb;padding:16px 0;margin:0}'
    '.cards li:first-child{border-top:none;padding-top:4px}'
    '.cards a{font-weight:700;font-size:16px;text-decoration:none;line-height:1.5}'
    '.logo-img{height:64px;width:auto;max-width:100%;display:block}'
    + BRAND_CSS)


def _guide_base() -> str:
    return request.url_root.rstrip("/")


def _load_og_manifest():
    """記事ごとのOG画像の対応表。無ければ空。

    画像は tools/make_images.py で焼いてコミットする。実行時に作らない
    のは、本番のコンテナに日本語フォントが無いため。焼いていない記事は
    共通の画像に落ちるだけで、ページは普通に出る。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "og", "manifest.json")
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


OG_GUIDES = _load_og_manifest()


def _guide_og(base: str, g=None) -> str:
    """OG画像のURL。記事のものが焼いてあればそれを使う。"""
    if g is not None and g.slug in OG_GUIDES:
        return f"{base}/static/og/{g.slug}.png?v=1"
    return f"{base}/static/ogp.png?v=1"


_H2 = re.compile(r"<h2>(.*?)</h2>", re.S)


def _with_toc(body: str):
    """本文の見出しから目次を作り、あわせて見出しに id を振る。

    id を記事側に書かせない。書き忘れると目次のリンクだけが外れ、
    しかも見た目では気づけない。

    見出しが2つ以下の記事に目次は要らない。画面を占めるだけになる。
    """
    heads = []

    def tag(m):
        n = len(heads) + 1
        heads.append((f"h{n}", re.sub(r"<[^>]+>", "", m.group(1)).strip()))
        return f'<h2 id="h{n}">{m.group(1)}</h2>'

    body = _H2.sub(tag, body)
    if len(heads) < 3:
        return "", body
    items = "".join(f'<li><a href="#{i}">{html.escape(t)}</a></li>'
                    for i, t in heads)
    return f'<nav class="toc"><span>目次</span><ol>{items}</ol></nav>', body


def _jsonld(obj) -> str:
    """構造化データを埋め込む。閉じタグに化ける文字だけ逃がす。"""
    body = json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{body}</script>'


def _guide_shell(title, description, path, head_extra, body, og=None) -> str:
    """記事と一覧に共通の外側。検索結果に出す前提の head を持つ。"""
    base = _guide_base()
    url = base + path
    t = html.escape(title)
    d = html.escape(description)
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{t}｜HOME INDEX</title>'
            f'<meta name="description" content="{d}">'
            f'<link rel="canonical" href="{url}">'
            '<meta property="og:type" content="article">'
            '<meta property="og:site_name" content="HOME INDEX">'
            f'<meta property="og:title" content="{t}">'
            f'<meta property="og:description" content="{d}">'
            f'<meta property="og:url" content="{url}">'
            f'<meta property="og:image" content="{og or _guide_og(base)}">'
            '<meta property="og:image:width" content="1200">'
            '<meta property="og:image:height" content="630">'
            '<meta name="twitter:card" content="summary_large_image">'
            + FONT_LINK + ICON_LINKS + head_extra
            + f'<style>{_GUIDE_CSS}</style></head><body>'
            + brand_bar("解説")
            + '<div class="wrap">'
            + f'<div class="card">{brand_lockup("gd")}{body}</div>'
            + FOOTER + '</div></body></html>')


def _share_row(base, g) -> str:
    """記事を共有するボタン。記事だけに置く（診断結果には置かない）。

    診断結果を共有できる形にすると、住所や年収を含むURLが出回る作りに
    なる。結果画面の「画像として保存」は、中身を自分で見てから渡せる
    ので別の話。
    """
    url = f"{base}/guide/{g.slug}"
    q = urllib.parse.quote(url, safe="")
    t = urllib.parse.quote(g.title, safe="")
    return (
        '<p class="share"><span>この記事を共有</span>'
        f'<a href="https://x.com/intent/post?text={t}&amp;url={q}"'
        ' target="_blank" rel="noopener nofollow">X</a>'
        f'<a href="https://social-plugins.line.me/lineit/share?url={q}"'
        ' target="_blank" rel="noopener nofollow">LINE</a>'
        f'<button type="button" class="copy" data-url="{html.escape(url)}">'
        'URLをコピー</button></p>'
        '<script>document.querySelectorAll(".copy").forEach(function(b){'
        'b.addEventListener("click",function(){'
        'navigator.clipboard.writeText(b.dataset.url).then(function(){'
        'var o=b.textContent;b.textContent="コピーしました";'
        'setTimeout(function(){b.textContent=o},1600);})'
        '.catch(function(){});});});</script>')


def _breadcrumbs(base, extra=None):
    items = [{"@type": "ListItem", "position": 1, "name": "ホーム",
              "item": base + "/"},
             {"@type": "ListItem", "position": 2, "name": "解説",
              "item": base + "/guide"}]
    if extra:
        items.append({"@type": "ListItem", "position": 3, "name": extra})
    return {"@type": "BreadcrumbList", "itemListElement": items}


@app.route("/guide")
def guide_index():
    base = _guide_base()
    items = "".join(
        f'<li><a href="/guide/{g.slug}">{html.escape(g.title)}</a>'
        f'<p class="sub" style="margin:6px 0 0">{html.escape(g.description)}</p>'
        f'<p class="meta" style="margin:6px 0 0">{g.published}</p></li>'
        for g in guides.all_guides())
    body = ('<h1>解説</h1>'
            '<p class="sub">診断で使っている数字の根拠を開いて書いています。'
            '出典は本文に明記します。</p>'
            f'<ul class="cards">{items}</ul>')
    return _guide_shell(
        "解説", "住宅の購入判断に必要な公的データの読み方を、"
                "出典を示して解説します。HOME INDEX の診断で使っている数字の根拠です。",
        "/guide", _jsonld({"@context": "https://schema.org",
                           "@graph": [_breadcrumbs(base)]}), body)


@app.route("/guide/<slug>")
def guide_page(slug):
    g = guides.by_slug(slug)
    if g is None:
        from flask import abort
        abort(404)
    base = _guide_base()
    article = {"@type": "Article", "headline": g.title,
               "description": g.description, "inLanguage": "ja",
               "datePublished": g.published, "dateModified": g.updated,
               "mainEntityOfPage": f"{base}/guide/{g.slug}",
               "publisher": {"@type": "Organization", "name": "HOME INDEX",
                             "url": base + "/"}}
    if operator_named():
        # 書き手を /about の運営者ページに紐づける。名前が仮のままなら
        # 出さない。誰でもない著者を名乗るくらいなら、著者を書かない。
        article["author"] = {"@type": "Person", "name": OPERATOR,
                             "url": base + "/about"}
    head = _jsonld({"@context": "https://schema.org",
                    "@graph": [article, _breadcrumbs(base, g.title)]})
    updated = (f"　更新 {g.updated}" if g.updated != g.published else "")
    toc, article = _with_toc(g.body)
    body = ('<p class="meta"><a href="/guide">← 解説の一覧</a></p>'
            f'<h1>{html.escape(g.title)}</h1>'
            f'<p class="meta">{g.published}{updated}'
            + (f'　/　{html.escape(OPERATOR)}（宅地建物取引士）'
               '　<a href="/about">運営者について</a>' if operator_named() else '')
            + '</p>'
            f'<div class="lead"><p style="margin:0">{g.lead}</p></div>'
            + toc + article
            + '<div class="after"><p>この記事の数字は、診断の採点にそのまま'
              f'使っています。<a href="{g.cta_href}">{html.escape(g.cta_text)}'
              '</a></p></div>'
            + _share_row(base, g))
    return _guide_shell(g.title, g.description, f"/guide/{g.slug}", head, body,
                        og=_guide_og(base, g))


# サイトマップに載せるのはGETで開けるページだけ。
# 診断結果はPOSTでしか生成されず、固有のURLを持たないのでクロール対象にならない。
SITEMAP_PATHS = ["/", "/buy", "/mansion", "/copy-guide", "/pro",
                 "/pro/diagnose", "/pro/mansion", "/pro/finance",
                 "/terms", "/privacy"]
if operator_named():
    # 誰が作ったかは検索エンジンにも見せる（YMYLではここが効く）
    SITEMAP_PATHS.insert(3, "/about")
SITEMAP_PATHS += guides.paths()


@app.route("/robots.txt")
def robots():
    from flask import Response
    base = request.url_root.rstrip("/")
    body = f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    """lastmod は書かない。毎回今日の日付にすると軽視され、固定値を書くと嘘になる。"""
    from flask import Response
    base = request.url_root.rstrip("/")
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in SITEMAP_PATHS)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f"{urls}</urlset>")
    return Response(xml, mimetype="application/xml")


@app.route("/resolve_city", methods=["POST"])
def resolve_city():
    """住所から市区町村コード・町名を返す（手入力時の自動補完用）。"""
    from flask import jsonify
    addr = (request.form.get("address") or "").strip()
    if not addr:
        return jsonify({})
    try:
        code, name, dist = _resolve_city(addr)
        return jsonify({"city": code or "", "cityname": name or "",
                        "district": dist or ""})
    except Exception:
        return jsonify({})


@app.route("/parse", methods=["POST"])
def parse():
    text = request.form.get("listing", "")
    if not text.strip():
        return render_template_string(FORM, v=_example_v(), listing="",
                                      banner="貼り付け欄が空です。物件説明を貼り付けてください。")
    p = parse_listing_text(text)
    # 全国：住所から市区町村コード・町名を解決（神奈川以外も対応）
    if p.get("address"):
        try:
            code, cityname, dist = _resolve_city(p["address"])
            if code:
                p["city"] = code
            if dist and not p.get("district"):
                p["district"] = dist
        except Exception:
            pass
    return render_template_string(FORM, v=_v_from_parsed(p), listing=text,
                                  banner=_parse_banner(p))


@app.route("/fetch", methods=["POST"])
def fetch():
    url = request.form.get("url", "").strip()
    try:
        p = extract_from_url(url)
        return render_template_string(FORM, v=_v_from_parsed(p), listing="",
                                      banner="（実験的）URLから取得しました。" + _parse_banner(p))
    except Exception as e:
        return render_template_string(FORM, v=_example_v(), listing="",
                                      banner=f"URL取得に失敗しました：{e}")

def _form_values(f):
    """送られてきた値を戸建フォームの初期値に載せ替える。"""
    v = _example_v()
    for k in v:
        if k == "reno":
            v[k] = (f.get("reno") == "1")
        elif f.get(k) is not None:
            v[k] = f.get(k)
    return v


_EDIT_BANNER = ("<b>入力した内容を残してあります。</b>"
                "直したいところを書き換えて、もう一度診断してください。")


@app.route("/buy/edit", methods=["POST"])
def buy_edit():
    """結果から入力へ戻る（戸建）。

    住所や年収を含むので、クエリ文字列ではなくPOSTで受ける。
    """
    return render_template_string(FORM, v=_form_values(request.form),
                                  listing="", banner=_EDIT_BANNER)


@app.route("/mansion/edit", methods=["POST"])
def mansion_edit():
    """結果から入力へ戻る（マンション）。"""
    return render_template_string(
        MANSION_FORM, v=_mansion_form_values(request.form),
        directions=DIRECTIONS, listing="", banner=_EDIT_BANNER)


@app.route("/diagnose", methods=["POST"])
def diagnose():
    f = request.form
    import datetime

    # 負荷・不正対策：1日あたりの回数制限（IP単位）
    if not _rate_ok(_client_ip()):
        return render_template_string(
            FORM, v=_example_v(), listing="",
            banner=(f"本日の診断回数の上限（{_RATE_LIMIT}回）に達しました。"
                    "時間をおいて再度お試しください。")), 429

    # 同時実行数を制限（無料枠のAPIレート・サーバー負荷を保護）
    if not _SEM.acquire(timeout=25):
        return render_template_string(
            FORM, v=_example_v(), listing="",
            banner="ただいまアクセスが集中しています。少し時間をおいて再度お試しください。"), 503
    try:
        return _run_diagnose(f, datetime)
    finally:
        _SEM.release()


def _finance_carry(subject, down_yen, loan_years, income_yen=None):
    """診断の入力から、資金計画のフォームを埋める値を作る。

    資金計画の金額欄はすべて万円。面積は㎡。単位を間違えると桁が
    変わるので、ここで一度だけ変換する。
    """
    def man_(yen):
        return str(int(round((yen or 0) / 10000))) if yen else ""

    import datetime
    byear = getattr(subject, "build_year", None)
    newbuild = "1" if getattr(subject, "property_type", "") == "shinchiku_kodate" \
        else "0"
    v = {"price": man_(getattr(subject, "price", None)),
         "newbuild": newbuild,
         "byear": str(byear) if byear else "",
         "down": man_(down_yen),
         "income": man_(income_yen),
         "loan_years": str(loan_years or ""),
         # 1982年以降の新築なら耐震基準に適合しているものとして扱う。
         # 記事（/guide/shin-taishin-kenchiku-kakunin）に書いたとおり、
         # 本当の境目は建築確認の日なので、境界の年は「不明」に倒す。
         "quake": ("yes" if byear and byear >= 1983 else
                   ("unknown" if byear else "unknown"))}
    land = getattr(subject, "land_area_m2", None)
    floor = getattr(subject, "building_area_m2", None) \
        or getattr(subject, "exclusive_area_m2", None)
    if land:
        v["land_area"] = str(land)
    if floor:
        v["floor_area"] = str(floor)
    return v


def _edit_carry(action: str, f, keys) -> dict:
    """結果画面から入力画面へ戻すための持ち物。

    送られてきた値をそのまま返すだけ。診断し直すのではなく、入力欄を
    埋め直して見せるためのもの。年収や頭金も含むが、もともと同じ画面から
    送られてきた値なので、新しく外へ出すものは無い。
    """
    fields = {k: (f.get(k) or "") for k in keys if f.get(k)}
    # 引き継ぎと戻りで同じ入口を使うので、どちらから来たかを渡す。
    # 画面に出す案内が変わる。
    fields["edit"] = "1"
    return {"action": action, "fields": fields}


def _render_result(res, subject, sctx, down_yen, loan_years,
                   free_diagnosis=None, carry=None, questions=None,
                   questions_note=None, redo=None, finance_carry=None,
                   edit=None):
    """診断結果ページを描画する。戸建とマンションで共通。

    res は run_pipeline / run_mansion_pipeline のどちらの戻り値でもよい。
    sctx は物件の見出しに出す表示用の辞書（呼び出し側で組み立てる）。
    """
    import datetime
    p = res.price
    comps = []
    if p and p.comparables:
        for c in p.comparables[:8]:
            t = c.txn
            dlabel = t.district_name or "—"
            if t.distance_m is not None:
                dlabel += f"({int(t.distance_m)}m)"
            comps.append(dict(d=dlabel, y=t.build_year,
                              l=t.land_area_m2, b=t.building_area_m2,
                              price=man(t.trade_price), sim=c.similarity_score,
                              est=man(c.subject_price_estimate)))
    # 同じ建物の可能性がある成約（マンションのみ。断定はできないと明記する）
    same = []
    for c in getattr(res, "same_building", [])[:6]:
        t = c.txn
        area = t.land_area_m2 or t.building_area_m2
        unit = int(t.trade_price / area) if (area and t.trade_price) else None
        same.append(dict(period=(f"{t.period_year}年" if t.period_year else "—"),
                         area=(f"{area:.0f}" if area else "—"),
                         price=man(t.trade_price),
                         unit=(f"{unit:,}円/㎡" if unit else "—")))
    price_ctx = dict(has=bool(p and p.verdict != "判定不可"), same=same,
                     same_label=(f"{subject.district_name}・{subject.build_year}年築"
                                 if same else ""))
    if price_ctx["has"]:
        price_ctx.update(verdict=p.verdict, vclass=_vclass(p.verdict),
                         dev=p.deviation_pct, low=man(p.estimate_low),
                         mid=man(p.estimate_mid), high=man(p.estimate_high),
                         conf=p.confidence, count=p.comparable_count,
                         disp=p.dispersion_pct,
                         ub=(f"{p.unit_building_median:,}円/㎡" if p.unit_building_median else "—"),
                         ul=(f"{p.unit_land_median:,}円/㎡" if p.unit_land_median else "—"),
                         comps=comps)

    d = res.diagnosis
    # 無料診断の結果から、入力をそのままPROへ持っていくための値。
    # 年収などを含むのでURLには載せず、hiddenでPOSTする。
    handover = None
    handover_action = handover_label = handover_unknowns = None
    if carry:
        handover = {k: v for k, v in carry["_fields"].items()
                    if v not in (None, "")}
        handover_action = carry["action"]
        handover_label = carry["label"]
        handover_unknowns = carry["unknowns"]
    # PROのときだけ、無料診断からどれだけ情報が埋まったかを見せる
    pro_delta = None
    if free_diagnosis is not None:
        pro_delta = dict(free_total=free_diagnosis.total_score,
                         free_suff=free_diagnosis.data_sufficiency,
                         total=d.total_score, suff=d.data_sufficiency,
                         diff=d.total_score - free_diagnosis.total_score)
    cats = [dict(name=c.name, points=c.points, weight=c.weight,
                 pct=int(round(c.raw * 100)), color=_catcolor(c.raw),
                 reason=c.reason) for c in d.categories]
    dctx = dict(total=d.total_score, grade=d.grade, suff=d.data_sufficiency,
                comment=d.comment,
                risks=[dict(sev=r.severity, type=r.type, status=r.status, ev=r.evidence)
                       for r in d.critical_risks],
                strengths=d.strengths, weaknesses=d.weaknesses, confirm=d.to_confirm)
    L = res.loan
    extra = getattr(L, "monthly_extra", 0) or 0
    loan = dict(principal=man(L.principal), down=man(down_yen),
                rate="1.25", years=str(loan_years), monthly=f"{L.monthly_payment:,}円",
                burden=L.burden_ratio,
                extra=(f"{extra:,}円" if extra else None),
                total_monthly=f"{L.monthly_payment + extra:,}円")
    age = (datetime.date.today().year - subject.build_year) if subject.build_year else "—"

    # 見出しに出す物件情報（sctx）は呼び出し側が組み立てて渡す。
    # 戸建は「土地/建物」、マンションは「専有/階数/向き」と中身が違うため。

    # 立地・防災・人口カード
    en = res.enrichment
    enr = None
    if en:
        hz = en.hazard
        items = []
        if hz.checked:
            if hz.flood_rank:
                lbl = hz.flood_label + (f"（{hz.flood_river}）" if hz.flood_river else "")
                items.append(("洪水浸水", lbl, "warn"))
            if hz.sediment:
                items.append(("土砂災害", hz.sediment, "warn"))
            if hz.tsunami:
                items.append(("津波", "浸水想定域", "warn"))
            if hz.storm_surge:
                items.append(("高潮", "浸水想定域", "warn"))
            if getattr(hz, "danger_zone", None):
                items.append(("災害危険区域", hz.danger_zone, "warn"))
            if getattr(hz, "steep_slope", False):
                items.append(("急傾斜地", "崩壊危険区域", "warn"))
            if getattr(hz, "landslide_zone", False):
                items.append(("地すべり", "防止地区", "warn"))
            if getattr(hz, "embankment", None):
                items.append(("大規模盛土", hz.embankment, "warn"))
            liq = getattr(hz, "liquefaction", None)
            if liq:
                items.append(("液状化", liq,
                              "warn" if "しやすい" in liq else "ok"))
            if not items:
                items.append(("防災", "指定区域に該当なし", "ok"))
        else:
            items.append(("防災", "未取得（要確認）", "muted"))
        fa = en.facility
        fac_bits = []
        if fa and fa.checked:
            if fa.nearest_station_m is not None:
                nm = f"（{fa.nearest_station_name}）" if fa.nearest_station_name else ""
                fac_bits.append(f"最寄駅 {fa.nearest_station_m}m{nm}")
            if fa.nearest_hospital_m is not None:
                fac_bits.append(f"病院 {fa.nearest_hospital_m}m")
            if fa.nearest_school_m is not None:
                fac_bits.append(f"学校 {fa.nearest_school_m}m")
            if fa.hospital_count_1km:
                fac_bits.append(f"1km内の医療 {fa.hospital_count_1km}件")
            if fa.nearest_preschool_m is not None:
                fac_bits.append(f"保育園・幼稚園 {fa.nearest_preschool_m}m")
            if fa.nearest_library_m is not None:
                fac_bits.append(f"図書館 {fa.nearest_library_m}m")
        shops = getattr(en, "shops", None)
        if shops is not None and getattr(shops, "checked", False):
            big = shops.nearest_big
            daily = shops.nearest_daily
            if big:
                fac_bits.append(
                    f"大型商業施設 {big.distance_m}m"
                    + (f"（{big.name}）" if big.name else ""))
            if daily:
                fac_bits.append(f"スーパー {daily.distance_m}m"
                                + (f"（{daily.name}）" if daily.name else ""))
            if not big and not daily:
                fac_bits.append("商業施設は付近に見当たらず")

        # 学区と将来推計人口。人口はメッシュの推計があればそちらを優先する。
        districts = []
        if getattr(en, "elementary_district", None):
            districts.append(f"小学校区 {en.elementary_district}")
        if getattr(en, "junior_district", None):
            districts.append(f"中学校区 {en.junior_district}")
        # e-Stat（市区町村の総人口）は使っていないので en.population は空。
        # 代わりに国交省の250mメッシュ将来推計を出す。市全体の人口より、
        # その地点に何人住んでいるかのほうが立地の判断に効く。
        pop_label = f"{en.population:,}人" if en.population else "—"
        if not en.population and getattr(en, "mesh_pop_now", None):
            pop_label = f"この地点周辺 約{round(en.mesh_pop_now):,}人（250mメッシュ）"
        trend = en.population_trend or "—"
        if getattr(en, "mesh_pop_change_pct", None) is not None:
            trend = f"この地点の推計 2050年に{en.mesh_pop_change_pct:+}%"

        enr = dict(use_district=en.use_district or "—",
                   population=pop_label,
                   trend=trend, hazard_items=items,
                   districts=("　/　".join(districts) if districts else None),
                   facilities=("　/　".join(fac_bits) if fac_bits else None))

    # スコアリング（円形ゲージ）
    import math
    circ = 2 * math.pi * 58
    ring_off = round(circ * (1 - d.total_score / 100.0), 1)
    grade_color = GRADE_COLOR.get(d.grade, "#0d9488")
    grade_comment = GRADE_COMMENT.get(d.grade, "")

    # 保存バー。DATABASE_URL が無ければ save は None のままで、
    # テンプレート側ごと出ない。改ざん防止のため、保存する中身は
    # ここで署名して hidden に載せる（受け取り側で署名を検証する）。
    save = None
    if accounts_on():
        kind = getattr(subject, "property_type", "") or ""
        title = f"{sctx.get('ptype', '物件')}　{sctx.get('address', '')}".strip()
        save = {"logged_in": bool(current_user())}
        if save["logged_in"]:
            save["token"] = sign_snapshot(dict(
                kind=kind, title=title, address=sctx.get("address"),
                price=getattr(subject, "price", None),
                total=d.total_score, grade=d.grade,
                payload=saved.snapshot(res, subject, sctx, kind, enr,
                                       redo)))

    if finance_carry is not None:
        # 資金計画のPDFに診断を載せるため、点数とリスクを署名して持ち回す。
        # 作り直すと外部APIを二度叩くうえ、画面と数字がずれ得る。
        finance_carry = dict(finance_carry)
        finance_carry["dx"] = sign_snapshot({
            "title": f"{sctx.get('ptype', '')}　{sctx.get('address', '')}".strip(),
            "total": d.total_score, "grade": d.grade,
            "suff": d.data_sufficiency,
            "cats": [[c.name, c.points, c.weight, c.reason]
                     for c in d.categories],
            "risks": [[r.severity, r.type, r.status, r.evidence]
                      for r in d.critical_risks],
            "ask": list(questions or [])[:20]})

    return render_template_string(
        RESULT, s=sctx, price_man=man(subject.price), age=age, save=save,
        p=price_ctx, cats=cats, d=dctx, loan=loan, warnings=res.warnings,
        enr=enr, ring_circ=round(circ, 1), ring_off=ring_off,
        grade_color=grade_color, grade_comment=grade_comment,
        pro=pro_delta, handover=handover,
        handover_action=handover_action, handover_label=handover_label,
        handover_unknowns=handover_unknowns, questions=questions,
        questions_note=questions_note, finance_carry=finance_carry,
        edit=edit)


def _run_diagnose(f, datetime):
    address = (f.get("address") or "").strip()
    city = (f.get("city") or "").strip()
    district = (f.get("district") or "").strip()
    # 手入力で市区町村コードが空でも、住所から自動補完（保険）
    if not city and address:
        try:
            code, _nm, dist = _resolve_city(address)
            if code:
                city = code
            if dist and not district:
                district = dist
        except Exception:
            pass

    subject = SubjectProperty(
        property_type=f.get("ptype") or "chuko_kodate",
        price=to_yen(f.get("price")) or 0,
        address=address,
        land_area_m2=to_float(f.get("land")),
        building_area_m2=to_float(f.get("building")),
        build_year=to_int(f.get("byear")),
        municipality_code=city or None,
        district_name=district or None,
        station_walk_min=to_int(f.get("station")),
        bus_min=to_int(f.get("bus")),
        structure=(f.get("structure") or "").strip() or None,
        renovated=((f.get("reno") or "0").strip() == "1"))

    # 借入年数（未入力・範囲外は35年）
    loan_years = to_int(f.get("loan_years")) or 35
    loan_years = max(1, min(50, loan_years))

    mock = os.environ.get("SHINDAN_MOCK") == "1"
    res = run_pipeline(
        subject, reinfolib_key=os.environ.get("REINFOLIB_KEY"),
        google_key=os.environ.get("GOOGLE_KEY"), mock=mock,
        annual_income=to_yen(f.get("income")), down_payment=to_yen(f.get("down")) or 0,
        loan_years=loan_years,
        estat_appid=os.environ.get("ESTAT_APPID"),
        estat_table=os.environ.get("ESTAT_TABLE", "0000020201"))

    ptype_ja = {"chuko_kodate": "中古戸建", "shinchiku_kodate": "新築戸建"}.get(
        subject.property_type, subject.property_type)
    dash = lambda v: v if v is not None else "—"
    age = (datetime.date.today().year - subject.build_year) \
        if subject.build_year else "—"
    specs = (f"土地 {dash(subject.land_area_m2)}㎡ ・ 建物 "
             f"{dash(subject.building_area_m2)}㎡ ・ 築{age}年 ・ "
             f"駅徒歩{dash(subject.station_walk_min)}分")
    skey = structure_mod.normalize(subject.structure)
    if skey:
        specs += f" ・ {structure_mod.label(skey)}"
    sctx = dict(address=subject.address, ptype=ptype_ja, specs=specs)
    # PROへ持っていく入力。PRO側のフォームと同じ name にそろえてある。
    carry = {
        "action": "/pro/start", "label": "購入診断(戸建)(PRO)",
        "unknowns": "建物の中の状態・設備の更新時期・接道や再建築の可否",
        "_fields": {
            "address": subject.address, "price": f.get("price") or "",
            "byear": f.get("byear") or "", "land": f.get("land") or "",
            "building": f.get("building") or "",
            "ptype": subject.property_type,
            "station": f.get("station") or "",
            "income": f.get("income") or "", "down": f.get("down") or "",
            "loan_years": str(loan_years),
            "structure": subject.structure or "",
            # 無料版は「リフォーム済み」の有無しか聞いていない。どの箇所かは
            # 分からないので、チェックを勝手に入れず、選び直してもらう。
            "renovated_hint": "1" if subject.renovated else ""}}
    # 再診断のためにフォームへ戻す値。キーは入力欄の name にそろえる。
    # 世帯年収・頭金は src/saved.py の NEVER_SAVE で落とされる。
    redo = {"kind": "kodate", "ptype": subject.property_type,
            "address": subject.address, "price": f.get("price") or "",
            "byear": f.get("byear") or "", "land": f.get("land") or "",
            "building": f.get("building") or "",
            "station": f.get("station") or "", "bus": f.get("bus") or "",
            "structure": subject.structure or "",
            "reno": "1" if subject.renovated else "",
            "loan_years": str(loan_years)}
    return _render_result(res, subject, sctx,
                          to_yen(f.get("down")) or 0, loan_years, carry=carry,
                          redo=redo,
                          edit=_edit_carry("/buy/edit", f, _example_v()))



# ---- マンション診断 --------------------------------------------------
# 戸建とは別フロー。貼り付け自動入力はまだ無く、手入力だけ。
MANSION_FORM = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
FONT_LINK_PLACEHOLDER
<title>HOME INDEX｜マンション購入診断</title>
<style>
MANSION_CSS_PLACEHOLDER
</style></head><body>
BRAND_BAR
<div class="wrap">
 <h1>マンションを100点で採点します</h1>
 <p class="aim">「この価格は妥当か」「資産価値は保てるか」「無理なく返せるか」。
  近隣の<b>マンションの成約事例</b>から専有面積あたりの単価を出して、100点に換算します。</p>
 <p class="lead">物件説明を貼り付けると自動で項目を埋めます。内容を確認・修正して診断してください。
  金額は<b>万円</b>（管理費・修繕積立金は<b>円</b>）。
  <a href="/buy">戸建の診断はこちら</a></p>

 {% if banner %}<div class="banner">{{banner|safe}}</div>{% endif %}

 <form class="card" method="post" action="/mansion_parse">
  <label>① 物件説明を貼り付け（SUUMO等の物件ページの<b>説明文</b>をコピペ）</label>
  <textarea name="listing" placeholder="例）中古マンション 〇〇県〇〇市〇〇町2-3-4 〇〇マンション 価格3,480万円 専有面積70.00㎡ 3LDK 5階/10階建 築2010年 南向き 管理費12,000円 修繕積立金13,000円 〇〇駅 徒歩8分">{{listing or ''}}</textarea>
  <button class="sub" type="submit">貼り付けから自動入力する</button>
  <div class="hint">※ <b>URLではなく、物件ページの文章</b>をコピーしてください。ご自身がコピーした情報を解析します（私的利用）。
   <b><a href="/copy-guide">スマホアプリで文字がコピーできない場合はこちら</a></b>。
   マンション名は自動では取れないため、手で入力してください。抽出後、下で確認・修正できます。<br>
   <b>販売図面のPDFをお持ちの場合も、PDFを開いて文字を選択・コピーし、この欄に貼り付けてください。</b>文字が選択できないPDF（スキャン画像）からは読み取れません。</div>
 </form>

 <div class="banner">
  <b>この診断に含まれないもの：</b>修繕積立金の<b>残高</b>、大規模修繕の履歴、管理形態、
  滞納の有無。いずれもマンションの価値を左右しますが、公的データからは取得できません。
  重要事項説明でご確認ください。
 </div>

 <form class="card" method="post" action="/mansion_diagnose">
  <label>所在地（必須）</label>
  <input name="address" value="{{v.address}}" placeholder="例）〇〇県〇〇市〇〇町2-3-4" required>
  <div class="hint">住所を入れると市区町村コードを自動で判定します</div>

  <label>マンション名</label>
  <input name="name" value="{{v.name}}" placeholder="例）〇〇マンション">
  <div class="hint">建物の位置を正確に取るために使います。<b>成約事例を名前で検索することはできません</b>（取引価格情報は匿名化されていて建物名を含まないため）。同じ町名・同じ築年の成約を「同じ建物の可能性がある事例」として別枠で表示します。</div>

  <div class="row">
   <div><label>売出価格（万円・必須）</label>
    <input name="price" value="{{v.price}}" placeholder="例）3480" required></div>
   <div><label>専有面積（㎡・必須）</label>
    <input name="area" value="{{v.area}}" placeholder="例）70" required>
    <div class="hint">バルコニーは含めません</div></div>
  </div>

  <div class="row">
   <div><label>築年（西暦）</label>
    <input name="byear" value="{{v.byear}}" placeholder="例）2010"></div>
   <div><label>駅/バス停まで徒歩（分）</label>
    <input name="station" value="{{v.station}}" placeholder="例）8"></div>
   <div><label>駅までバス（分・バス便のみ）</label>
    <input name="bus" value="{{v.bus}}">
    <div class="hint">バス便のときだけ入力</div></div>
  </div>

  <div class="row">
   <div><label>所在階</label>
    <input name="floor" value="{{v.floor}}" placeholder="例）5"></div>
   <div><label>総階数</label>
    <input name="total_floors" value="{{v.total_floors}}" placeholder="例）10"></div>
   <div><label>向き</label>
    <select name="direction">
     {% for d in directions %}
     <option value="{{d}}" {{'selected' if v.direction==d else ''}}>{{d}}</option>
     {% endfor %}
    </select></div>
  </div>

  <div class="row">
   <div><label>リフォーム</label>
    <select name="reno">
     <option value="0" {{'selected' if not v.reno else ''}}>リフォームなし／不明</option>
     <option value="1" {{'selected' if v.reno else ''}}>リフォーム済み</option>
    </select>
    <div class="hint">築15年以上のリフォーム済みは、推定価格と資産性を調整します（内容は見ていません）</div></div>
  </div>

  <div class="row">
   <div><label>管理費（円／月）</label>
    <input name="mfee" value="{{v.mfee}}" placeholder="例）12000">
    <div class="hint">万円ではなく<b>円</b>で入力</div></div>
   <div><label>修繕積立金（円／月）</label>
    <input name="rfund" value="{{v.rfund}}" placeholder="例）13000">
    <div class="hint">専有面積あたりの月額に直して、国土交通省「マンションの修繕積立金に関するガイドライン」（令和6年6月改定）の目安と比べます</div></div>
  </div>

  <div class="row">
   <div><label>市区町村コード</label>
    <input name="city" value="{{v.city}}" placeholder="住所から自動判定"></div>
   <div><label>町名</label>
    <input name="district" value="{{v.district}}" placeholder="住所から自動判定"></div>
  </div>

  <h2>あなたの条件（返済の無理がないかを見ます）</h2>
  <div class="row">
   <div><label>世帯年収（万円）</label>
    <input name="income" value="{{v.income}}" placeholder="例）800"></div>
   <div><label>頭金（万円）</label>
    <input name="down" value="{{v.down}}" placeholder="例）500"></div>
   <div><label>借入年数（年）</label>
    <input name="loan_years" value="{{v.loan_years}}" placeholder="35"></div>
  </div>

  <button type="submit">このマンションを診断する</button>
  <div class="hint">診断結果は公的データにもとづく推定です。契約の判断は専門家の確認を前提としてください。</div>
 </form>
</div>
<script>
(function(){
  var form = document.querySelector('form[action="/mansion_diagnose"]');
  if(!form) return;
  var addr = form.querySelector('input[name="address"]');
  var city = form.querySelector('input[name="city"]');
  var dist = form.querySelector('input[name="district"]');
  if(!addr || !city) return;
  var last = "";
  function resolve(){
    var a = (addr.value || "").trim();
    if(!a || a === last) return;
    last = a;
    var fd = new FormData();
    fd.append("address", a);
    var ph = city.placeholder;
    city.placeholder = "自動判定中…";
    fetch("/resolve_city", {method:"POST", body: fd})
      .then(function(r){ return r.json(); })
      .then(function(j){
        city.placeholder = ph || "";
        if(j && j.city){ city.value = j.city; }
        if(j && j.district && dist && !dist.value){ dist.value = j.district; }
      })
      .catch(function(){ city.placeholder = ph || ""; });
  }
  addr.addEventListener("change", resolve);
  addr.addEventListener("blur", resolve);
})();
</script>
</div></body></html>
"""

DIRECTIONS = ["不明", "南", "南東", "南西", "東", "西", "北東", "北西", "北"]

# 見た目は戸建フォームと同じにしたいので、CSSは FORM から借りる。
# 片方だけ直して見た目がずれる事故を防ぐため、コピーではなく参照にしている。
_FORM_CSS = FORM[FORM.index("<style>") + len("<style>"):FORM.index("</style>")]
MANSION_FORM = (MANSION_FORM
                .replace("MANSION_CSS_PLACEHOLDER", _FORM_CSS)
                .replace("FONT_LINK_PLACEHOLDER", FONT_LINK)
                .replace("BRAND_BAR", brand_bar("マンション診断"))
                .replace("</div></body></html>", FOOTER + "</div></body></html>"))


def _mansion_example_v():
    return dict(address="", name="", price="", area="", byear="", station="",
                bus="", floor="", total_floors="", direction="不明", city="",
                district="", reno=False, mfee="", rfund="", income="", down="",
                loan_years="35")


def _mansion_v_from_parsed(p):
    """抽出結果をフォームの値に移す。取れなかった項目は空のままにする。"""
    v = _mansion_example_v()
    if p.get("price_man") is not None:
        v["price"] = str(p["price_man"])
    if p.get("area") is not None:
        v["area"] = str(p["area"])
    for src_key, dst in (("byear", "byear"), ("station", "station"),
                         ("bus", "bus"),
                         ("floor", "floor"), ("total_floors", "total_floors"),
                         ("mfee", "mfee"), ("rfund", "rfund")):
        if p.get(src_key) is not None:
            v[dst] = str(p[src_key])
    for src_key, dst in (("address", "address"), ("city", "city"),
                         ("district", "district")):
        if p.get(src_key):
            v[dst] = p[src_key]
    if p.get("direction"):
        v["direction"] = p["direction"]
    # 記載が無ければ None。そのときは「なし／不明」のままにする。
    if p.get("renovated") is not None:
        v["reno"] = bool(p["renovated"])
    return v


def _mansion_parse_banner(p):
    """何が取れて何が取れなかったかを、そのまま伝える。"""
    labels = [("price_man", "価格"), ("area", "専有面積"), ("byear", "築年"),
              ("station", "駅徒歩"), ("floor", "所在階"),
              ("total_floors", "総階数"), ("direction", "向き"),
              ("mfee", "管理費"), ("rfund", "修繕積立金"),
              ("renovated", "リフォーム有無"), ("address", "所在地")]
    got = [name for k, name in labels if p.get(k) is not None]
    miss = [name for k, name in labels if p.get(k) is None]
    msg = ""
    if p.get("is_mansion") is False:
        msg += ("<b>戸建の物件情報のようです。</b>"
                "マンションでなければ<a href=\"/buy\">戸建の診断</a>をお使いください。<br>")
    msg += "読み取れた項目：" + ("、".join(got) if got else "なし")
    if miss:
        msg += "<br>読み取れなかった項目：" + "、".join(miss) + "（手で入力してください）"
    msg += "<br><b>金額と面積は必ず目視で確認してください。</b>"
    return msg


@app.route("/mansion")
def mansion():
    """マンション診断の入力フォーム。"""
    return render_template_string(MANSION_FORM, v=_mansion_example_v(),
                                  directions=DIRECTIONS, banner=None,
                                  listing="")


@app.route("/mansion_parse", methods=["POST"])
def mansion_parse():
    """貼り付けたテキストから項目を埋める。戸建の /parse と同じ役割。"""
    from src.extract import parse_mansion_text
    text = request.form.get("listing", "")
    if not text.strip():
        return render_template_string(
            MANSION_FORM, v=_mansion_example_v(), directions=DIRECTIONS,
            listing="", banner="貼り付け欄が空です。物件説明を貼り付けてください。")
    p = parse_mansion_text(text)
    if p.get("address") and not p.get("city"):
        try:
            code, _nm, dist = _resolve_city(p["address"])
            if code:
                p["city"] = code
            if dist and not p.get("district"):
                p["district"] = dist
        except Exception:
            pass
    return render_template_string(MANSION_FORM, v=_mansion_v_from_parsed(p),
                                  directions=DIRECTIONS, listing=text,
                                  banner=_mansion_parse_banner(p))


@app.route("/mansion_diagnose", methods=["POST"])
def mansion_diagnose():
    f = request.form

    if not _rate_ok(_client_ip()):
        return render_template_string(
            MANSION_FORM, v=_mansion_form_values(f), directions=DIRECTIONS,
            listing=f.get("listing", ""),
            banner=(f"本日の診断回数の上限（{_RATE_LIMIT}回）に達しました。"
                    "時間をおいて再度お試しください。")), 429
    if not _SEM.acquire(timeout=25):
        return render_template_string(
            MANSION_FORM, v=_mansion_form_values(f), directions=DIRECTIONS,
            listing=f.get("listing", ""),
            banner="ただいまアクセスが集中しています。少し時間をおいて再度お試しください。"), 503
    try:
        return _run_mansion_diagnose(f)
    finally:
        _SEM.release()


def _mansion_form_values(f):
    v = _mansion_example_v()
    for k in v:
        if f.get(k) is not None:
            v[k] = f.get(k)
    return v


def _run_mansion_diagnose(f):
    import datetime

    address = (f.get("address") or "").strip()
    city = (f.get("city") or "").strip()
    district = (f.get("district") or "").strip()
    if not city and address:
        try:
            code, _nm, dist = _resolve_city(address)
            if code:
                city = code
            if dist and not district:
                district = dist
        except Exception:
            pass

    area = to_float(f.get("area"))
    if not area or area <= 0:
        return render_template_string(
            MANSION_FORM, v=_mansion_form_values(f), directions=DIRECTIONS,
            listing=f.get("listing", ""),
            banner="専有面積を入力してください。㎡単価で比較するため必須です。")

    direction = (f.get("direction") or "不明").strip()
    subject = MansionSubject(
        address=address,
        name=(f.get("name") or "").strip() or None,
        price=to_yen(f.get("price")) or 0,
        build_year=to_int(f.get("byear")),
        station_walk_min=to_int(f.get("station")),
        bus_min=to_int(f.get("bus")),
        exclusive_area_m2=area,
        floor=to_int(f.get("floor")),
        total_floors=to_int(f.get("total_floors")),
        direction=direction,
        municipality_code=city or None,
        district_name=district or None,
        management_fee=to_int(f.get("mfee")),
        repair_fund=to_int(f.get("rfund")),
        renovated=((f.get("reno") or "0").strip() == "1"))

    loan_years = max(1, min(50, to_int(f.get("loan_years")) or 35))
    down_yen = to_yen(f.get("down")) or 0

    res = run_mansion_pipeline(
        subject, reinfolib_key=os.environ.get("REINFOLIB_KEY"),
        google_key=os.environ.get("GOOGLE_KEY"),
        mock=(os.environ.get("SHINDAN_MOCK") == "1"),
        annual_income=to_yen(f.get("income")), down_payment=down_yen,
        loan_years=loan_years,
        estat_appid=os.environ.get("ESTAT_APPID"),
        estat_table=os.environ.get("ESTAT_TABLE", "0000020201"))

    age = (datetime.date.today().year - subject.build_year) \
        if subject.build_year else None
    bits = [f"専有 {area}㎡"]
    if subject.floor:
        bits.append(f"{subject.floor}階" + (f"/{subject.total_floors}階"
                                            if subject.total_floors else ""))
    if direction and direction != "不明":
        bits.append(f"{direction}向き")
    bits.append(f"築{age}年" if age is not None else "築年不明")
    if subject.bus_min:
        stop = (f"＋バス停徒歩{subject.station_walk_min}分"
                if subject.station_walk_min is not None else "")
        bits.append(f"駅までバス{subject.bus_min}分{stop}")
    else:
        bits.append(f"駅徒歩{subject.station_walk_min}分"
                    if subject.station_walk_min is not None else "駅徒歩不明")
    if subject.renovated:
        bits.append("リフォーム済み")
    if subject.management_fee or subject.repair_fund:
        monthly = (subject.management_fee or 0) + (subject.repair_fund or 0)
        bits.append(f"管理費等 月{monthly:,}円")
    sctx = dict(address=(f"{subject.address}　{subject.name}"
                         if subject.name else subject.address),
                ptype="中古マンション",
                specs=" ・ ".join(bits))
    carry = {
        "action": "/pro/mansion_start", "label": "購入診断(マンション)(PRO)",
        "unknowns": "修繕積立金の残高・大規模修繕の履歴・管理形態・滞納の有無",
        "_fields": {
            "address": subject.address, "name": subject.name or "",
            "price": f.get("price") or "", "area": f.get("area") or "",
            "byear": f.get("byear") or "", "station": f.get("station") or "",
            "bus": f.get("bus") or "",
            "floor": f.get("floor") or "",
            "total_floors": f.get("total_floors") or "",
            "direction": subject.direction or "不明",
            "mfee": f.get("mfee") or "", "rfund": f.get("rfund") or "",
            "income": f.get("income") or "", "down": f.get("down") or "",
            "loan_years": str(loan_years)}}
    redo = {"kind": "mansion", "address": subject.address,
            "name": subject.name or "", "price": f.get("price") or "",
            "area": f.get("area") or "", "byear": f.get("byear") or "",
            "station": f.get("station") or "", "bus": f.get("bus") or "",
            "floor": f.get("floor") or "",
            "total_floors": f.get("total_floors") or "",
            "direction": subject.direction or "不明",
            "layout": f.get("layout") or "",
            "mfee": f.get("mfee") or "", "rfund": f.get("rfund") or "",
            "reno": "1" if subject.renovated else "",
            "loan_years": str(loan_years)}
    return _render_result(res, subject, sctx, down_yen, loan_years,
                          carry=carry, redo=redo,
                          edit=_edit_carry("/mansion/edit", f,
                                           _mansion_example_v()))

# ---- PRO 購入診断（たたき台・戸建）--------------------------------
# 仕様書§4-A/§4-C。ここで受けた詳細は物件スコアとリスクにだけ反映し、
# 価格推定には渡さない（§1「点数は売るが円は売らない」）。
# 認証と課金はまだ噛ませていない。外側にログイン判定を足せる形にしてある。

# 選択肢の定義から入力欄を組み立てる。20個以上あるので手書きしない。
_PRO_CHOICES = {
    "condition": [("ok", "問題なし"), ("concern", "気になる点あり"),
                  ("unknown", "未確認")],
    "equipment": [("le5", "5年以内"), ("le10", "5〜10年"),
                  ("gt10", "10年超"), ("unknown", "未確認")],
    "yesno": [("done", "実施済み"), ("none", "なし"), ("unknown", "未確認")],
    "insulation": [("high", "高い（等級4相当以上）"), ("standard", "標準"),
                   ("low", "低い"), ("unknown", "未確認")],
    "cert_yesno": [("yes", "あり"), ("no", "なし"), ("unknown", "未確認")],
    "performance": [("construction", "建設住宅性能評価あり"),
                    ("design", "設計住宅性能評価のみ"),
                    ("existing", "既存住宅性能評価あり"),
                    ("none", "なし"), ("unknown", "未確認")],
    "energy": [("zeh", "ZEH水準"), ("meets", "省エネ基準に適合"),
               ("below", "適合していない"), ("unknown", "未確認")],
    "quake_grade": [("g3", "等級3"), ("g2", "等級2"),
                    ("g1", "等級1（建築基準法と同等）"), ("unknown", "未確認")],
    "road": [("ge4", "幅員4m以上に接道"), ("lt4", "幅員4m未満"),
             ("none", "接道なし"), ("unknown", "未確認")],
    "rebuild": [("yes", "再建築可"), ("no", "再建築不可"), ("unknown", "未確認")],
    "boundary": [("fixed", "確定済み"), ("unfixed", "未確定"),
                 ("unknown", "未確認")],
    "encroach": [("none", "なし"), ("exists", "あり"), ("unknown", "未確認")],
    "employment": [("seishain", "正社員"), ("keiyaku", "契約・派遣"),
                   ("jieigyo", "自営業"), ("part", "パート・アルバイト"),
                   ("unknown", "未回答")],
}

# 各項目に、どの種別で聞くかを付ける。
#   ""        … 中古でも新築でも聞く
#   chuko     … 中古だけ。新築では答えようがない（築0年の設備の更新時期など）
#   shinchiku … 新築だけ
#
# 新築の画面から中古前提の項目を消すのは、見た目の問題だけではない。
# 答えようのない項目を分母に残すと、いくら答えても情報充足度が上がりきらない。
# 採点側の分母は src/pro_scoring.py の property_fields() が持っている。
_PRO_SECTIONS = [
    ("chuko", "建物の中の状態", "実際に見て確認できたことを選んでください。未確認のままでも診断はできますが、情報充足度は上がりません。",
     [("leak", "雨漏りの跡", "condition", ""), ("termite", "シロアリ・腐朽", "condition", ""),
      ("tilt", "床の傾き", "condition", ""), ("plumbing", "給排水の不具合", "condition", ""),
      ("foundation", "基礎のひび", "condition", "")]),
    ("chuko", "主要設備の更新時期", "給湯器は寿命が短く、10年を超えると交換費用を見込む必要があります。",
     [("water_heater", "給湯器", "equipment", ""), ("kitchen", "キッチン", "equipment", ""),
      ("bath", "浴室", "equipment", ""), ("electrical", "電気設備・分電盤", "equipment", "")]),
    ("", "性能・診断", "住宅性能評価書や認定通知書が残っていれば、売主か仲介会社に確認できます。",
     [("quake_retrofit", "耐震補強", "yesno", "chuko"),
      ("inspection", "住宅診断（インスペクション）", "yesno", "chuko"),
      ("insulation", "断熱性能", "insulation", ""),
      ("energy_saving", "省エネ基準への適合", "energy", "")]),
    ("", "公的な認定・評価", "第三者の検査や基準に裏付けられているため、自己申告の項目より重く評価します。",
     [("long_term_excellent", "長期優良住宅の認定", "cert_yesno", ""),
      ("performance_cert", "住宅性能評価書", "performance", ""),
      ("quake_grade", "耐震等級", "quake_grade", ""),
      ("defect_insurance", "既存住宅売買瑕疵保険", "cert_yesno", "chuko")]),
    ("", "敷地・法規", "再建築の可否と接道は、将来の建て替えと売却に直結します。重要事項説明書で確認できます。",
     [("road_width", "接道", "road", ""), ("rebuildable", "再建築の可否", "rebuild", ""),
      ("boundary", "隣地との境界", "boundary", ""),
      ("encroachment", "越境（塀・屋根・配管）", "encroach", "")]),
]

_PRO_RENO = [("reno_water", "水回り"), ("reno_exterior", "外壁・屋根"),
             ("reno_interior", "内装"), ("reno_pipes", "給排水管")]


def _pro_select(name, kind):
    opts = "".join(
        f'<option value="{val}" {{{{\'selected\' if v.{name}==\'{val}\' else \'\'}}}}>{label}</option>'
        for val, label in _PRO_CHOICES[kind])
    return f'<select name="{name}">{opts}</select>'


def _ptype_attr(scope: str) -> str:
    return f' data-only="{scope}"' if scope else ""


def _pro_detail_html():
    """詳細入力のHTML。種別で出し分ける印だけ付け、実際に隠すのはCSS。

    サーバー側で組み立てを分けないのは、フォームの中で種別を選び直せる
    ようにするため。隠れた項目は未回答（unknown）のまま送られるので、
    採点には効かない。
    """
    out = []
    for scope, title, note, fields in _PRO_SECTIONS:
        out.append(f'<div class="card"{_ptype_attr(scope)}>')
        out.append(f"<h2 style=\"font-size:15px;margin:0 0 6px\">{title}</h2>")
        if note:
            out.append(f'<div class="hint" style="margin-bottom:8px">{note}</div>')
        for name, label, kind, fscope in fields:
            out.append(f"<div{_ptype_attr(fscope)}><label>{label}</label>"
                       f"{_pro_select(name, kind)}</div>")
        out.append("</div>")
    # リフォーム箇所はチェックボックス。新築には無い話なので中古だけ。
    out.append('<div class="card" data-only="chuko">'
               '<h2 style="font-size:15px;margin:0 0 6px">'
               "リフォームした箇所</h2>"
               '<div class="hint" style="margin-bottom:8px">'
               "箇所ごとに評価します。無料診断は有無だけを見ています。</div>")
    for name, label in _PRO_RENO:
        out.append(f'<label style="display:flex;align-items:center;gap:8px">'
                   f'<input type="checkbox" name="{name}" value="1" '
                   f'style="width:auto" {{{{\'checked\' if v.{name} else \'\'}}}}>'
                   f"{label}</label>")
    out.append("</div>")
    return "".join(out)


PRO_DIAGNOSE_FORM = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
FONT_LINK_PLACEHOLDER
<title>購入診断(戸建)(PRO)｜HOME INDEX</title>
<style>
MANSION_CSS_PLACEHOLDER
</style></head><body>
BRAND_BAR
<div class="wrap">
 <h1>購入診断(戸建)(PRO)</h1>
 <p class="aim">無料診断で「未確認」として点数に入れていなかったことを、あなたの回答で埋めます。
  情報が増えるぶん、同じ物件でも点数は変わります。</p>
 <div class="banner"><b>試験公開中です。</b>現在は無料でお使いいただけますが、将来は有料（月額）になる予定です。会員登録はまだ不要です。</div>
 <div class="banner">
  <b>推定価格レンジは無料診断と同じ計算です。</b>ここで入力していただく建物の状態や
  リフォームの内容は、<b>物件評価とリスクにのみ</b>反映し、価格の推定には使いません。
 </div>

 {% if banner %}<div class="banner">{{banner|safe}}</div>{% endif %}

 <form method="post" action="/pro/diagnose">
  <div class="card">
   <h2 style="font-size:15px;margin:0 0 6px">物件の基本情報</h2>
   <label>所在地（必須）</label>
   <input name="address" value="{{v.address}}" placeholder="例）〇〇県〇〇市〇〇町1-2-3" required>
   <div class="row">
    <div><label>売出価格（万円・必須）</label>
     <input name="price" value="{{v.price}}" required></div>
    <div><label>築年（西暦）</label>
     <input name="byear" value="{{v.byear}}"></div>
   </div>
   <div class="row">
    <div><label>土地面積（㎡）</label><input name="land" value="{{v.land}}"></div>
    <div><label>建物面積（㎡）</label><input name="building" value="{{v.building}}"></div>
    <div><label>駅/バス停まで徒歩（分）</label>
     <input name="station" value="{{v.station}}"></div>
    <div><label>駅までバス（分）</label><input name="bus" value="{{v.bus}}">
     <div class="hint">バス便のときだけ入力</div></div>
   </div>
   <div class="row">
    <div><label>種別</label>
     <select name="ptype" id="ptype">
      <option value="chuko_kodate" {{'selected' if v.ptype!='shinchiku_kodate' else ''}}>中古戸建</option>
      <option value="shinchiku_kodate" {{'selected' if v.ptype=='shinchiku_kodate' else ''}}>新築戸建</option>
     </select>
     <div class="hint">新築を選ぶと、答えようのない項目（設備の更新時期・
      耐震補強・リフォーム箇所など）は出しません。価格の比較に使う成約事例も
      新築のものに切り替わります。</div></div>
    <div><label>構造</label>
     <select name="structure">
      {% for val, lbl in structures %}
       <option value="{{val}}" {{'selected' if v.structure==val else ''}}>{{lbl}}</option>
      {% endfor %}
     </select>
     <div class="hint">国税庁の耐用年数（木造22年・RC47年など）を目安に、
      木造を基準として築年数を換算します。</div></div>
    <div></div>
   </div>
  </div>

<div id="detail" class="{{ 'is-new' if v.ptype=='shinchiku_kodate' else 'is-old' }}">
PRO_DETAIL_PLACEHOLDER
</div>
<style>
 /* 種別で聞く項目が変わる。隠すのはCSSだけで、項目そのものは残す。
    隠れた項目は未回答（unknown）のまま送られるので採点には効かない。
    採点側の分母は src/pro_scoring.py の property_fields が持っている。 */
 #detail.is-new [data-only="chuko"]{display:none}
 #detail.is-old [data-only="shinchiku"]{display:none}
</style>
<script>
(function(){
  var sel=document.getElementById("ptype"), box=document.getElementById("detail");
  if(!sel||!box) return;
  sel.addEventListener("change",function(){
    box.className = sel.value === "shinchiku_kodate" ? "is-new" : "is-old";
  });
})();
</script>

  <div class="card">
   <h2 style="font-size:15px;margin:0 0 6px">お住まいになる方について</h2>
   <div class="hint" style="margin-bottom:8px">返済の重さと、何年住むか（出口）を見るために使います。
    借入の可否を判断するものではありません。</div>
   <div class="row">
    <div><label>年齢</label><input name="age" value="{{v.age}}" placeholder="例）38"></div>
    <div><label>世帯人数</label><input name="household_size" value="{{v.household_size}}" placeholder="例）4"></div>
    <div><label>お子さまの人数</label><input name="children" value="{{v.children}}" placeholder="例）2"></div>
   </div>
   <div class="row">
    <div><label>雇用形態</label>EMPLOYMENT_SELECT</div>
    <div><label>勤続年数</label><input name="tenure_years" value="{{v.tenure_years}}" placeholder="例）10"></div>
    <div><label>何年住む見込みか</label><input name="hold_years" value="{{v.hold_years}}" placeholder="例）20"></div>
   </div>
   <div class="row">
    <div><label>世帯年収（万円）</label><input name="income" value="{{v.income}}" placeholder="例）600"></div>
    <div><label>頭金（万円）</label><input name="down" value="{{v.down}}" placeholder="例）300"></div>
    <div><label>手元に残す額（万円）</label><input name="reserve" value="{{v.reserve}}" placeholder="例）200"></div>
   </div>
   <div class="row">
    <div><label>他の借入の月々返済（円）</label>
     <input name="other_debt" value="{{v.other_debt}}" placeholder="例）35000">
     <div class="hint">車・教育・カードなど。返済負担率に含めます</div></div>
    <div><label>借入年数（年）</label><input name="loan_years" value="{{v.loan_years}}"></div>
   </div>
  </div>

  <button type="submit">PROで診断する</button>
 </form>
</div></body></html>
"""


def _pro_defaults_full():
    v = {"address": "", "price": "", "byear": "", "land": "", "building": "",
         "station": "", "bus": "", "ptype": "chuko_kodate",
         "age": "", "household_size": "", "children": "",
         "employment": "unknown", "tenure_years": "", "hold_years": "",
         "income": "", "down": "", "reserve": "", "other_debt": "",
         "structure": "", "loan_years": "35"}
    for _s, _t, _n, fields in _PRO_SECTIONS:
        for name, _label, _kind, _scope in fields:
            v[name] = "unknown"
    for name, _label in _PRO_RENO:
        v[name] = False
    return v


PRO_DIAGNOSE_FORM = (PRO_DIAGNOSE_FORM
                     .replace("PRO_DETAIL_PLACEHOLDER", _pro_detail_html())
                     .replace("EMPLOYMENT_SELECT",
                              _pro_select("employment", "employment"))
                     .replace("MANSION_CSS_PLACEHOLDER", _FORM_CSS)
                     .replace("FONT_LINK_PLACEHOLDER", FONT_LINK + ICON_LINKS)
                     .replace("BRAND_BAR", brand_bar("購入診断(戸建)(PRO)"))
                     .replace("</div></body></html>",
                              FOOTER + "</div></body></html>"))


@app.route("/pro/start", methods=["POST"])
def pro_start():
    """無料診断の結果から、入力を引き継いでPROのフォームを開く。

    年収や検討中の住所を含むため、クエリ文字列ではなくPOSTで受ける
    （URLに残さない・アクセスログに出さない）。
    """
    v = _pro_form_values(request.form)
    if request.form.get("edit"):
        return render_template_string(PRO_DIAGNOSE_FORM, v=v,
                                      banner=_EDIT_BANNER)
    msg = ("無料診断の入力を引き継ぎました。"
           "以下の項目に答えるほど、情報充足度が上がります。")
    if request.form.get("renovated_hint") == "1":
        msg += ("<br>無料診断では「リフォーム済み」とだけ伺っています。"
                "PROは箇所ごとに評価するので、下の<b>リフォームした箇所</b>を"
                "選び直してください。")
    return render_template_string(PRO_DIAGNOSE_FORM, v=v, banner=msg)


@app.route("/pro/diagnose", methods=["GET", "POST"])
def pro_diagnose():
    """PROの購入診断（戸建）。まだログイン判定は入れていない。"""
    if request.method == "GET":
        return render_template_string(PRO_DIAGNOSE_FORM,
                                      v=_pro_defaults_full(), banner=None)
    if not _rate_ok(_client_ip()):
        return render_template_string(
            PRO_DIAGNOSE_FORM, v=_pro_form_values(request.form),
            banner=f"本日の診断回数の上限（{_RATE_LIMIT}回）に達しました。"), 429
    if not _SEM.acquire(timeout=25):
        return render_template_string(
            PRO_DIAGNOSE_FORM, v=_pro_form_values(request.form),
            banner="ただいまアクセスが集中しています。"), 503
    try:
        return _run_pro_diagnose(request.form)
    finally:
        _SEM.release()


def _pro_form_values(f):
    v = _pro_defaults_full()
    for k in v:
        if isinstance(v[k], bool):
            v[k] = (f.get(k) == "1")
        elif f.get(k) is not None:
            v[k] = f.get(k)
    return v


def _run_pro_diagnose(f):
    """無料の診断を普通に走らせ、その結果にPROの回答を重ねる。"""
    import datetime
    from src.models import ProDetail, BuyerProfile
    from src.pro_scoring import apply_pro, agent_questions

    address = (f.get("address") or "").strip()
    city = district = ""
    if address:
        try:
            code, _nm, dist = _resolve_city(address)
            city, district = code or "", dist or ""
        except Exception:
            pass

    # 無料診断が新築と判定した物件を、PROで中古として採点しない。
    # 種別は推定価格の比較対象（新築の成約事例だけを見るか）まで変える。
    ptype = ("shinchiku_kodate" if f.get("ptype") == "shinchiku_kodate"
             else "chuko_kodate")
    subject = SubjectProperty(
        property_type=ptype, price=to_yen(f.get("price")) or 0,
        address=address, land_area_m2=to_float(f.get("land")),
        building_area_m2=to_float(f.get("building")),
        build_year=to_int(f.get("byear")),
        station_walk_min=to_int(f.get("station")),
        bus_min=to_int(f.get("bus")),
        municipality_code=city or None, district_name=district or None,
        structure=(f.get("structure") or "").strip() or None,
        renovated=any(f.get(n) == "1" for n, _l in _PRO_RENO))

    detail = ProDetail(
        **{name: (f.get(name) or "unknown")
           for _s, _t, _n, fields in _PRO_SECTIONS
           for name, _l, _k, _sc in fields},
        **{name: (f.get(name) == "1") for name, _l in _PRO_RENO})

    buyer = BuyerProfile(
        age=to_int(f.get("age")), household_size=to_int(f.get("household_size")),
        children=to_int(f.get("children")),
        employment=(f.get("employment") or "unknown"),
        tenure_years=to_int(f.get("tenure_years")),
        other_debt_monthly=to_int(f.get("other_debt")),
        own_funds=to_yen(f.get("down")), reserve=to_yen(f.get("reserve")),
        hold_years=to_int(f.get("hold_years")))

    loan_years = max(1, min(50, to_int(f.get("loan_years")) or 35))
    down_yen = to_yen(f.get("down")) or 0

    res = run_pipeline(
        subject, reinfolib_key=os.environ.get("REINFOLIB_KEY"),
        google_key=os.environ.get("GOOGLE_KEY"),
        mock=(os.environ.get("SHINDAN_MOCK") == "1"),
        annual_income=to_yen(f.get("income")), down_payment=down_yen,
        loan_years=loan_years,
        estat_appid=os.environ.get("ESTAT_APPID"),
        estat_table=os.environ.get("ESTAT_TABLE", "0000020201"))

    # 他の借入は毎月出ていくので、返済負担率に含めて計算し直す
    if buyer.other_debt_monthly:
        from src.loan import compute_loan
        res.loan = compute_loan(subject.price, down_yen, 0.0125, loan_years,
                                to_yen(f.get("income")),
                                monthly_extra=buyer.other_debt_monthly)

    free = res.diagnosis
    res.diagnosis = apply_pro(free, detail, subject, buyer)
    questions = agent_questions(detail, subject,
                                ptype == "shinchiku_kodate")
    questions_note = ("未回答だった項目です。建物の中の状態は売主が記入する"
                      "物件状況報告書に、境界や再建築の可否は測量図と"
                      "重要事項説明書に書かれています。")

    age = (datetime.date.today().year - subject.build_year) \
        if subject.build_year else None
    bits = []
    if subject.land_area_m2:
        bits.append(f"土地 {subject.land_area_m2}㎡")
    if subject.building_area_m2:
        bits.append(f"建物 {subject.building_area_m2}㎡")
    bits.append(f"築{age}年" if age is not None else "築年不明")
    if buyer.hold_years:
        bits.append(f"居住予定 {buyer.hold_years}年")
    sctx = dict(address=subject.address,
                ptype=("新築戸建（PRO）" if ptype == "shinchiku_kodate"
                       else "中古戸建（PRO）"),
                specs=" ・ ".join(bits))
    return _render_result(res, subject, sctx, down_yen, loan_years,
                          free_diagnosis=free, questions=questions,
                          questions_note=questions_note,
                          finance_carry=_finance_carry(
                              subject, down_yen, loan_years,
                              to_yen(f.get("income"))),
                          edit=_edit_carry("/pro/start", f,
                                           list(_pro_defaults_full()) + ["edit"]))


# ---- マンションPRO 購入診断 ----------------------------------------
# 無料版が「取得できない」として未評価のまま残している管理の中身を埋める。
# 戸建PROと同じく、価格推定には渡さない。

_MPRO_CHOICES = {
    "major_repair": [("recent", "実施あり（直近10年以内）"),
                     ("old", "実施あり（10年以上前）"),
                     ("never", "未実施"), ("unknown", "未確認")],
    "long_term_plan": [("long", "あり（30年以上）"),
                       ("short", "あり（期間が短い・不明）"),
                       ("none", "なし"), ("unknown", "未確認")],
    "management_form": [("full", "全部委託"), ("partial", "一部委託"),
                        ("self", "自主管理"), ("unknown", "未確認")],
    "manager_style": [("live_in", "常駐"), ("daily", "日勤"),
                      ("rounds", "巡回"), ("none", "なし"),
                      ("unknown", "未確認")],
    "arrears": [("none", "なし"), ("few", "少数あり"), ("many", "多い"),
                ("unknown", "未確認")],
    "reserve_increase": [("planned", "計画的な値上げ予定あり"),
                         ("steep", "急な値上げ予定あり"),
                         ("none", "予定なし"), ("unknown", "未確認")],
    "management_cert": [("certified", "認定あり"), ("applying", "申請中"),
                        ("none", "なし"), ("unknown", "未確認")],
    "common_area": [("good", "良好"), ("normal", "普通"),
                    ("concern", "気になる点あり"), ("unknown", "未確認")],
    "condition": [("ok", "問題なし"), ("concern", "気になる点あり"),
                  ("unknown", "未確認")],
    "equipment": [("le5", "5年以内"), ("le10", "5〜10年"),
                  ("gt10", "10年超"), ("unknown", "未確認")],
    "land_right": [("ownership", "所有権"), ("leasehold", "借地権"),
                   ("unknown", "未確認")],
    "quake_diagnosis": [("ok", "実施済み・問題なし"),
                        ("done", "実施済み・補強済み"),
                        ("need", "実施済み・要補強"),
                        ("never", "未実施"), ("unknown", "未確認")],
    "performance": [("construction", "建設住宅性能評価あり"),
                    ("design", "設計住宅性能評価のみ"),
                    ("existing", "既存住宅性能評価あり"),
                    ("none", "なし"), ("unknown", "未確認")],
    "cert_yesno": [("yes", "あり"), ("no", "なし"), ("unknown", "未確認")],
}

# 検討段階の買主が「いま答えられるか」で並べる。マンションの管理の中身は
# 重要事項調査報告書を見ないと分からないものが多く、それは売主か仲介業者が
# 契約前に取得するもので、内見の段階では手元に無いのが普通。だから答えられる
# ものから順に置き、答えられない項目は結果画面で質問文に変える。
_MPRO_SECTIONS = [
    ("① 手元の資料と、見てきた印象で答えられること",
     "物件ページや販売図面を見ながら、または内見で見た印象で選んでください。ここまで答えれば診断できます。",
     [("management_form", "管理形態", "management_form"),
      ("manager_style", "管理員の勤務", "manager_style"),
      ("common_area", "共用部の管理状態", "common_area"),
      ("land_right", "敷地の権利", "land_right"),
      ("plumbing", "専有部：給排水の不具合", "condition"),
      ("sash", "専有部：サッシ・建具の不具合", "condition"),
      ("mold", "専有部：結露・カビ", "condition"),
      ("tilt", "専有部：床の傾き", "condition"),
      ("water_heater", "設備：給湯器の更新", "equipment"),
      ("kitchen", "設備：キッチンの更新", "equipment"),
      ("bath", "設備：浴室の更新", "equipment")]),
    ("② 聞けば教えてもらえること",
     "いま分からなくても大丈夫です。「未確認」のまま進めると、最後にそのまま送れる質問文をまとめます。",
     [("major_repair", "大規模修繕の実施", "major_repair"),
      ("long_term_plan", "長期修繕計画", "long_term_plan"),
      ("reserve_increase", "修繕積立金の値上げ予定", "reserve_increase"),
      ("management_cert", "管理計画認定・管理適正評価", "management_cert"),
      ("quake_diagnosis", "耐震診断", "quake_diagnosis"),
      ("performance_cert", "住宅性能評価書", "performance"),
      ("defect_insurance", "既存住宅売買瑕疵保険", "cert_yesno")]),
    ("③ 契約前に受け取る書類で分かること",
     "重要事項調査報告書に書かれています。この段階で分からないのが普通なので、「未確認」のままで構いません。",
     [("arrears", "管理費等の滞納", "arrears")]),
]

_MPRO_RENO = [("reno_water", "水回り"), ("reno_interior", "内装"),
              ("reno_pipes", "給排水管")]


def _mpro_select(name, kind):
    opts = "".join(
        f'<option value="{val}" {{{{\'selected\' if v.{name}==\'{val}\' else \'\'}}}}>{label}</option>'
        for val, label in _MPRO_CHOICES[kind])
    return f'<select name="{name}">{opts}</select>'


def _mpro_detail_html():
    out = []
    for title, note, fields in _MPRO_SECTIONS:
        out.append('<div class="card">')
        out.append(f'<h2 style="font-size:15px;margin:0 0 6px">{title}</h2>')
        if note:
            out.append(f'<div class="hint" style="margin-bottom:8px">{note}</div>')
        for name, label, kind in fields:
            out.append(f"<label>{label}</label>{_mpro_select(name, kind)}")
        out.append("</div>")
    out.append('<div class="card"><h2 style="font-size:15px;margin:0 0 6px">'
               "リフォームした箇所</h2>")
    for name, label in _MPRO_RENO:
        out.append(f'<label style="display:flex;align-items:center;gap:8px">'
                   f'<input type="checkbox" name="{name}" value="1" '
                   f'style="width:auto" {{{{\'checked\' if v.{name} else \'\'}}}}>'
                   f"{label}</label>")
    out.append("</div>")
    return "".join(out)


MANSION_PRO_FORM = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
FONT_LINK_PLACEHOLDER
<title>購入診断(マンション)(PRO)｜HOME INDEX</title>
<style>
MANSION_CSS_PLACEHOLDER
</style></head><body>
BRAND_BAR
<div class="wrap">
 <h1>購入診断(マンション)(PRO)</h1>
 <p class="aim">分かる範囲で答えるほど、診断の確かさが上がります。
  <b>分からない項目は「未確認」のままで構いません。</b>
  答えられなかったことは、最後に「仲介業者に聞くこと」としてまとめてお渡しします。</p>
 <div class="banner"><b>試験公開中です。</b>現在は無料でお使いいただけますが、将来は有料（月額）になる予定です。会員登録はまだ不要です。</div>
 <div class="banner">
  <b>推定価格レンジは無料診断と同じ計算です。</b>ここで入力していただく内容は、
  <b>管理・資産性・リスクにのみ</b>反映し、価格の推定には使いません。
 </div>

 {% if banner %}<div class="banner">{{banner|safe}}</div>{% endif %}

 <form method="post" action="/pro/mansion">
  <div class="card">
   <h2 style="font-size:15px;margin:0 0 6px">物件の基本情報</h2>
   <label>所在地（必須）</label>
   <input name="address" value="{{v.address}}" placeholder="例）〇〇県〇〇市〇〇町2-3-4" required>
   <label>マンション名</label>
   <input name="name" value="{{v.name}}" placeholder="例）〇〇マンション">
   <div class="row">
    <div><label>売出価格（万円・必須）</label>
     <input name="price" value="{{v.price}}" required></div>
    <div><label>専有面積（㎡・必須）</label>
     <input name="area" value="{{v.area}}" required></div>
   </div>
   <div class="row">
    <div><label>築年（西暦）</label><input name="byear" value="{{v.byear}}"></div>
    <div><label>駅/バス停まで徒歩（分）</label>
     <input name="station" value="{{v.station}}"></div>
    <div><label>駅までバス（分）</label><input name="bus" value="{{v.bus}}">
     <div class="hint">バス便のときだけ入力</div></div>
   </div>
   <div class="row">
    <div><label>所在階</label><input name="floor" value="{{v.floor}}"></div>
    <div><label>総階数</label><input name="total_floors" value="{{v.total_floors}}"></div>
    <div><label>向き</label>
     <select name="direction">
      {% for d in directions %}
      <option value="{{d}}" {{'selected' if v.direction==d else ''}}>{{d}}</option>
      {% endfor %}
     </select></div>
   </div>
   <div class="row">
    <div><label>管理費（円／月）</label><input name="mfee" value="{{v.mfee}}"></div>
    <div><label>修繕積立金（円／月）</label><input name="rfund" value="{{v.rfund}}"></div>
   </div>
  </div>

MPRO_DETAIL_PLACEHOLDER

  <div class="card">
   <h2 style="font-size:15px;margin:0 0 6px">お住まいになる方について</h2>
   <div class="row">
    <div><label>世帯年収（万円）</label><input name="income" value="{{v.income}}"></div>
    <div><label>頭金（万円）</label><input name="down" value="{{v.down}}"></div>
    <div><label>借入年数（年）</label><input name="loan_years" value="{{v.loan_years}}"></div>
   </div>
   <div class="row">
    <div><label>他の借入の月々返済（円）</label>
     <input name="other_debt" value="{{v.other_debt}}" placeholder="例）35000">
     <div class="hint">管理費・修繕積立金とあわせて返済負担率に含めます</div></div>
    <div><label>何年住む見込みか</label><input name="hold_years" value="{{v.hold_years}}"></div>
   </div>
  </div>

  <button type="submit">PROで診断する</button>
 </form>
</div></body></html>
"""


def _mpro_defaults():
    v = {"address": "", "name": "", "price": "", "area": "", "byear": "",
         "station": "", "bus": "", "floor": "", "total_floors": "",
         "direction": "不明",
         "mfee": "", "rfund": "",
         "income": "", "down": "", "loan_years": "35", "other_debt": "",
         "hold_years": ""}
    for _t, _n, fields in _MPRO_SECTIONS:
        for name, _label, _kind in fields:
            v[name] = "unknown"
    for name, _label in _MPRO_RENO:
        v[name] = False
    return v


MANSION_PRO_FORM = (MANSION_PRO_FORM
                    .replace("MPRO_DETAIL_PLACEHOLDER", _mpro_detail_html())
                    .replace("MANSION_CSS_PLACEHOLDER", _FORM_CSS)
                    .replace("FONT_LINK_PLACEHOLDER", FONT_LINK + ICON_LINKS)
                    .replace("BRAND_BAR", brand_bar("購入診断(マンション)(PRO)"))
                    .replace("</div></body></html>",
                             FOOTER + "</div></body></html>"))


def _mpro_form_values(f):
    v = _mpro_defaults()
    for k in v:
        if isinstance(v[k], bool):
            v[k] = (f.get(k) == "1")
        elif f.get(k) is not None:
            v[k] = f.get(k)
    return v


@app.route("/pro/mansion_start", methods=["POST"])
def pro_mansion_start():
    """無料のマンション診断から、入力を引き継いでPROのフォームを開く。"""
    return render_template_string(
        MANSION_PRO_FORM, v=_mpro_form_values(request.form),
        directions=DIRECTIONS,
        banner=(_EDIT_BANNER if request.form.get("edit") else
                "無料診断の入力を引き継ぎました。"
                "管理の中身に答えるほど、情報充足度が上がります。"))


@app.route("/pro/mansion", methods=["GET", "POST"])
def pro_mansion():
    """マンションのPRO診断。まだログイン判定は入れていない。"""
    if request.method == "GET":
        return render_template_string(MANSION_PRO_FORM, v=_mpro_defaults(),
                                      directions=DIRECTIONS, banner=None)
    if not _rate_ok(_client_ip()):
        return render_template_string(
            MANSION_PRO_FORM, v=_mpro_form_values(request.form),
            directions=DIRECTIONS,
            banner=f"本日の診断回数の上限（{_RATE_LIMIT}回）に達しました。"), 429
    if not _SEM.acquire(timeout=25):
        return render_template_string(
            MANSION_PRO_FORM, v=_mpro_form_values(request.form),
            directions=DIRECTIONS,
            banner="ただいまアクセスが集中しています。"), 503
    try:
        return _run_mansion_pro(request.form)
    finally:
        _SEM.release()


def _run_mansion_pro(f):
    import datetime
    from src.models import MansionProDetail, BuyerProfile
    from src.mansion_pro_scoring import apply_pro_mansion, agent_questions

    address = (f.get("address") or "").strip()
    city = district = ""
    if address:
        try:
            code, _nm, dist = _resolve_city(address)
            city, district = code or "", dist or ""
        except Exception:
            pass

    area = to_float(f.get("area"))
    if not area or area <= 0:
        return render_template_string(
            MANSION_PRO_FORM, v=_mpro_form_values(f), directions=DIRECTIONS,
            banner="専有面積を入力してください。㎡単価で比較するため必須です。")

    subject = MansionSubject(
        address=address, name=(f.get("name") or "").strip() or None,
        price=to_yen(f.get("price")) or 0, build_year=to_int(f.get("byear")),
        station_walk_min=to_int(f.get("station")), bus_min=to_int(f.get("bus")),
        exclusive_area_m2=area,
        floor=to_int(f.get("floor")), total_floors=to_int(f.get("total_floors")),
        direction=(f.get("direction") or "不明").strip(),
        municipality_code=city or None, district_name=district or None,
        management_fee=to_int(f.get("mfee")), repair_fund=to_int(f.get("rfund")),
        renovated=any(f.get(n) == "1" for n, _l in _MPRO_RENO))

    detail = MansionProDetail(
        **{name: (f.get(name) or "unknown")
           for _t, _n, fields in _MPRO_SECTIONS for name, _l, _k in fields},
        **{name: (f.get(name) == "1") for name, _l in _MPRO_RENO})

    buyer = BuyerProfile(other_debt_monthly=to_int(f.get("other_debt")),
                         hold_years=to_int(f.get("hold_years")),
                         own_funds=to_yen(f.get("down")))

    loan_years = max(1, min(50, to_int(f.get("loan_years")) or 35))
    down_yen = to_yen(f.get("down")) or 0

    res = run_mansion_pipeline(
        subject, reinfolib_key=os.environ.get("REINFOLIB_KEY"),
        google_key=os.environ.get("GOOGLE_KEY"),
        mock=(os.environ.get("SHINDAN_MOCK") == "1"),
        annual_income=to_yen(f.get("income")), down_payment=down_yen,
        loan_years=loan_years, estat_appid=os.environ.get("ESTAT_APPID"),
        estat_table=os.environ.get("ESTAT_TABLE", "0000020201"))

    # 他の借入も毎月出ていくので、管理費・修繕積立金と合わせて負担率に入れる
    if buyer.other_debt_monthly:
        from src.loan import compute_loan
        extra = ((subject.management_fee or 0) + (subject.repair_fund or 0)
                 + buyer.other_debt_monthly)
        res.loan = compute_loan(subject.price, down_yen, 0.0125, loan_years,
                                to_yen(f.get("income")), monthly_extra=extra)

    free = res.diagnosis
    res.diagnosis = apply_pro_mansion(free, detail, subject, buyer)
    questions = agent_questions(detail, subject)
    questions_note = ("未回答だった項目です。管理の中身は、契約前に売主か"
                      "仲介業者が用意する重要事項調査報告書と、"
                      "総会議事録に書かれています。")

    age = (datetime.date.today().year - subject.build_year) \
        if subject.build_year else None
    bits = [f"専有 {area}㎡"]
    if subject.floor:
        bits.append(f"{subject.floor}階" + (f"/{subject.total_floors}階"
                                            if subject.total_floors else ""))
    bits.append(f"築{age}年" if age is not None else "築年不明")
    sctx = dict(address=(f"{subject.address}　{subject.name}"
                         if subject.name else subject.address),
                ptype="中古マンション（PRO）", specs=" ・ ".join(bits))
    return _render_result(res, subject, sctx, down_yen, loan_years,
                          free_diagnosis=free, questions=questions,
                          questions_note=questions_note,
                          finance_carry=_finance_carry(
                              subject, down_yen, loan_years,
                              to_yen(f.get("income"))),
                          edit=_edit_carry("/pro/mansion_start", f,
                                           list(_mpro_defaults()) + ["edit"]))


# ---- アカウント（ログイン・保存・比較・プラン） ----------------------
# DATABASE_URL が未設定のときは、この一連のルートは登録するが中身を出さない
# （accounts_on() が False なら案内だけ返す）。診断そのものは今までどおり動く。

_ACCOUNT_CSS = """
 .lead{color:#6b7280;font-size:13px;line-height:1.9;margin:0 0 14px}
 .btn{display:inline-block;padding:13px 20px;background:#111;color:#fff;
   border:0;border-radius:10px;font-weight:700;font-size:15px;cursor:pointer;
   text-decoration:none;font-family:inherit}
 .btn.ghost{background:#eef2f7;color:#111}
 .btn.sm{padding:7px 12px;font-size:13px;border-radius:8px}
 .btn:disabled{opacity:.45;cursor:default}
 input[type=email]{width:100%;padding:13px;border:1px solid #d1d5db;
   border-radius:10px;font-size:16px;font-family:inherit}
 .note{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;
   padding:12px 14px;font-size:13px;line-height:1.85;color:#374151}
 .warn{background:#fff7ed;border-color:#fed7aa;color:#9a3412}
 .ok{background:#ecfdf5;border-color:#a7f3d0;color:#065f46}
 .plan-badge{display:inline-block;border-radius:999px;padding:3px 10px;
   font-size:12px;font-weight:700;background:#eef2f7;color:#374151}
 .plan-badge.is-pro{background:#111;color:#fff}
 .items{list-style:none;padding:0;margin:12px 0 0}
 .items li{border-top:1px solid #e5e7eb;padding:13px 0;display:flex;
   gap:12px;align-items:flex-start}
 .items li:first-child{border-top:0}
 .items .pick{margin-top:3px;width:18px;height:18px;flex:0 0 auto}
 .items .body{flex:1;min-width:0}
 .items .ttl{font-size:14px;font-weight:700;line-height:1.5;
   word-break:break-word}
 .items .meta{font-size:12px;color:#6b7280;margin-top:3px}
 .items .sc{font-size:20px;font-weight:800;white-space:nowrap;margin-left:6px}
 .tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:10px}
 table.cmp{border-collapse:collapse;font-size:13px;min-width:100%}
 table.cmp th,table.cmp td{border-bottom:1px solid #e5e7eb;padding:9px 11px;
   text-align:left;white-space:nowrap}
 table.cmp thead th{font-size:13px;color:#111;border-bottom:2px solid #111;
   vertical-align:bottom}
 table.cmp thead th span{display:block;font-weight:400;font-size:11px;
   color:#6b7280;margin-top:2px}
 table.cmp th.rowlbl{color:#6b7280;font-weight:600;position:sticky;left:0;
   background:#fff}
 table.cmp td.best{background:#ecfdf5;font-weight:700}
 table.cmp td.best::after{content:" ◎";color:#059669;font-size:11px}
 table.cmp tr.sect th{background:#f8fafc;color:#111;font-size:12px}
 /* スマホでは表を積む。横スクロールだと比べたい2列を同時に見られず、
    比較表の意味が無くなるため。DOMは共通でCSSだけ切り替える。 */
 @media (max-width:600px){
  .tablewrap{overflow:visible}
  table.cmp{display:block; min-width:0}
  table.cmp thead{display:none}
  table.cmp tbody{display:block}
  /* 列数は --n（保存件数、最大3）で決める。auto-fit にすると3件目が
     折り返して横に並ばず、比べられなくなる。 */
  table.cmp tr{display:grid; gap:7px; padding:12px 0;
    grid-template-columns:repeat(var(--n,2),minmax(0,1fr));
    border-bottom:1px solid #e5e7eb}
  table.cmp th.rowlbl{grid-column:1/-1; display:block; position:static;
    background:none; border:0; padding:0; white-space:normal; font-size:13px}
  table.cmp td{display:block; border:0; padding:7px 9px; white-space:normal;
    background:#f8fafc; border-radius:9px; font-size:14px; font-weight:700;
    overflow-wrap:anywhere}
  /* どの物件の値かを、セル自身に持たせる（見出し行を隠すため） */
  table.cmp td::before{content:attr(data-name); display:block; font-size:11px;
    color:#6b7280; font-weight:400; margin-bottom:3px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  table.cmp td.best{background:#ecfdf5; box-shadow:inset 0 0 0 1.5px #a7f3d0}
  table.cmp tr.sect{display:block; padding:16px 0 2px; border-bottom:0}
  table.cmp tr.sect th{background:none; padding:0}
  table.cmp tr.sect th:not(.rowlbl){display:none}
 }

 /* 保存した診断の詳細 */
 .sc-big{display:flex;align-items:baseline;gap:10px;margin:2px 0 4px}
 .sc-big b{font-size:44px;font-weight:800;line-height:1}
 .sc-big .g{font-size:22px;font-weight:800}
 .bars{margin-top:8px}
 .bars .b{margin:11px 0}
 .bars .top{display:flex;justify-content:space-between;gap:10px;
   font-size:14px;align-items:baseline}
 .bars .top b{font-weight:700}
 .bars .track{height:9px;background:#eef2f7;border-radius:5px;overflow:hidden;
   margin:5px 0 3px}
 .bars .fill{display:block;height:100%}
 .bars .why{font-size:12.5px;color:#6b7280;line-height:1.75}
 .rsk{background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;
   padding:10px 12px;margin:8px 0;font-size:13.5px;line-height:1.8}
 .rsk b{color:#9a3412}
 .memo textarea{width:100%;min-height:110px;padding:12px;font-size:15px;
   border:1px solid #d1d5db;border-radius:10px;font-family:inherit;
   line-height:1.8;resize:vertical}
 .memo-read{white-space:pre-wrap;font-size:14px;line-height:1.9;
   background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;
   padding:12px 14px}
 .items .memo-tag{font-size:12px;color:#6b7280;margin-top:5px;
   display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

 .selbar{position:sticky;bottom:0;background:#fff;border-top:1px solid #e5e7eb;
   padding:12px 0;margin-top:6px;display:flex;gap:10px;align-items:center}
 .selbar .n{font-size:13px;color:#6b7280}
"""


def _account_page(title, body, chip="マイページ"):
    """アカウント系ページの共通の枠。フォームと同じ見た目にそろえる。"""
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex">'
            f'{FONT_LINK}{ICON_LINKS}'
            f'<title>{title}｜HOME INDEX</title>'
            f'<style>{_FORM_CSS}{_ACCOUNT_CSS}</style></head><body>'
            + brand_bar(chip)
            + f'<div class="wrap">{body}{FOOTER}</div></body></html>')


_OFF_BODY = ('<div class="card"><h1>アカウント機能は準備中です</h1>'
             '<p class="lead">保存と比較は、もうすこしお待ちください。'
             '診断そのものは今までどおりご利用いただけます。</p>'
             '<a class="btn" href="/buy">診断にもどる</a></div>')


def _require_login():
    """ログインしていなければリダイレクト応答を返す。していれば None。"""
    if not accounts_on():
        return _account_page("準備中", _OFF_BODY)
    if not current_user():
        return redirect("/login")
    return None


# ---- ログイン ------------------------------------------------------------

LOGIN_PAGE = """
<div class="card">
 <h1>ログイン</h1>
 {% if user %}
  <p class="lead">{{ user.email }} でログイン中です。</p>
  <a class="btn" href="/mypage">マイページへ</a>
 {% else %}
  <p class="lead">パスワードはありません。メールアドレスを入れると、
   ログイン用のリンクをお送りします。<br>
   保存した診断を、あとから別の端末でも見られるようにするためのものです。</p>
  {% if error %}<div class="note warn">{{ error }}</div><br>{% endif %}
  <form method="post" action="/login">
   <label for="email" style="font-size:13px;font-weight:700">メールアドレス</label>
   <div style="margin:6px 0 14px"><input id="email" type="email" name="email"
     required autocomplete="email" placeholder="you@example.com"
     value="{{ email or '' }}"></div>
   <button class="btn" type="submit">ログイン用リンクを送る</button>
  </form>
  <p class="sub" style="margin-top:14px;line-height:1.9">
   ご登録のメールアドレスは、ログインと、保存した診断をお預かりするためだけに
   使います。広告メールはお送りしません。
   詳しくは<a href="/privacy">プライバシーポリシー</a>をご覧ください。</p>
 {% endif %}
</div>
"""

LOGIN_SENT = """
<div class="card">
 <h1>リンクをお送りしました</h1>
 <div class="note ok">{{ email }} 宛にログイン用のリンクを送りました。
  メールを開いて、{{ ttl }}分以内にリンクを押してください。</div>
 <p class="lead" style="margin-top:14px">届かない場合は、迷惑メールに入っていないか
  ご確認ください。それでも見当たらなければ、
  <a href="/login">もう一度お試しください</a>。</p>
 {% if devlink %}
  <div class="note warn" style="margin-top:10px">
   <b>開発用の表示です。</b>メール送信が設定されていないため、リンクを直接出しています。<br>
   <a href="{{ devlink }}" style="word-break:break-all">{{ devlink }}</a></div>
 {% endif %}
</div>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if not accounts_on():
        return _account_page("準備中", _OFF_BODY)
    user = current_user()
    if request.method == "GET" or user:
        return _account_page("ログイン", render_template_string(
            LOGIN_PAGE, user=user, error=None, email=None), chip="ログイン")

    email = accounts.normalize_email(request.form.get("email"))
    err = None
    if not accounts.valid_email(email):
        err = "メールアドレスの形式をご確認ください。"
    if err:
        return _account_page("ログイン", render_template_string(
            LOGIN_PAGE, user=None, error=err, email=email), chip="ログイン")

    try:
        token = accounts.issue_login_token(email)
    except accounts.TooManyRequests as e:
        err = ("短い時間に何度も送信されています。しばらく置いてからお試しください。"
               if str(e) == "email" else
               "本日の送信上限に達しました。お手数ですが翌日にお試しください。")
        return _account_page("ログイン", render_template_string(
            LOGIN_PAGE, user=None, error=err, email=email), chip="ログイン")
    except Exception:
        return _account_page("ログイン", render_template_string(
            LOGIN_PAGE, user=None, email=email,
            error="ただいま混み合っています。時間をおいてお試しください。"),
            chip="ログイン")

    link = urllib.parse.urljoin(request.url_root, f"login/{token}")
    ok, msg = mailer.send_login_link(email, link, accounts.TOKEN_TTL_MIN)
    # メールを出せない環境（鍵未設定のローカル）では、画面にリンクを出す。
    # 本番でこれを出すと誰でもログインできてしまうので、鍵が無いときに限る。
    devlink = None if (ok or mailer.enabled()) else link
    if not ok and mailer.enabled():
        return _account_page("ログイン", render_template_string(
            LOGIN_PAGE, user=None, email=email,
            error=f"メールを送れませんでした。{msg}"), chip="ログイン")
    return _account_page("確認", render_template_string(
        LOGIN_SENT, email=email, ttl=accounts.TOKEN_TTL_MIN, devlink=devlink),
        chip="ログイン")


@app.route("/login/<token>")
def login_verify(token):
    if not accounts_on():
        return _account_page("準備中", _OFF_BODY)
    try:
        user = accounts.consume_login_token(token)
    except Exception:
        user = None
    if not user:
        body = ('<div class="card"><h1>リンクが使えません</h1>'
                '<div class="note warn">期限が切れているか、すでに使われたリンクです。'
                'ログイン用リンクは30分・1回かぎり有効です。</div>'
                '<p style="margin-top:14px"><a class="btn" href="/login">'
                'もう一度送る</a></p></div>')
        return _account_page("ログイン", body, chip="ログイン"), 400
    session.clear()
    session["uid"] = user["id"]
    session.permanent = True
    return redirect("/mypage")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/")


# ---- 保存 ----------------------------------------------------------------
# 結果ページから戻ってくる内容は、こちらが署名したものだけを受け付ける。
# 署名が無いと、任意のJSONを保存させられてしまう。

def _snap_serializer():
    from itsdangerous import URLSafeSerializer
    return URLSafeSerializer(app.secret_key, salt="hi-snapshot")


def sign_snapshot(meta: dict) -> str:
    return _snap_serializer().dumps(meta)


def _unsign_snapshot(token: str):
    from itsdangerous import BadSignature
    try:
        return _snap_serializer().loads(token)
    except BadSignature:
        return None
    except Exception:
        return None


@app.route("/save", methods=["POST"])
def save_diagnosis():
    r = _require_login()
    if r is not None:
        return r
    user = current_user()
    meta = _unsign_snapshot(request.form.get("snap") or "")
    if not meta:
        return redirect("/mypage")
    try:
        saved.save(user, meta.get("kind", ""), meta.get("title", "診断結果"),
                   meta.get("address"), meta.get("price"),
                   int(meta.get("total") or 0), meta.get("grade") or "",
                   meta.get("payload") or {})
    except saved.LimitReached:
        return redirect("/mypage?full=1")
    except Exception:
        return redirect("/mypage?err=1")
    return redirect("/mypage?added=1")


SAVED_DETAIL = """
<div class="card">
 <a href="/mypage" style="font-size:14px">← 保存した物件へ</a>
 <p class="sub" style="margin:10px 0 2px">{{ it.address or '' }}</p>
 <h1 style="margin:0 0 2px">{{ short }}</h1>
 <p class="sub">{{ kindja(it.kind) }}
  {%- if it.price %}　売出 {{ man(it.price) }}{% endif %}
  　診断日 {{ it.created_at[:10] }}</p>
 {% if p.spec and p.spec.specs %}<p class="lead" style="margin:8px 0 0">{{ p.spec.specs }}</p>{% endif %}

 <div class="sc-big">
  <b>{{ p.total }}</b><span class="sub">/ 100点</span>
  <span class="g">{{ p.grade }}</span>
 </div>
 <p class="sub" style="margin:0">情報充足度 {{ p.sufficiency }}%
  未確認の項目は点数に入れていません</p>
 {% if p.comment %}<p class="lead" style="margin-top:10px">{{ p.comment }}</p>{% endif %}
</div>

<div class="card memo" style="margin-top:14px">
 <h2 style="margin-top:0">メモ</h2>
 <p class="lead">点数に出ないことを書き留めておけます。
  駐車場の停めやすさ、隣家との距離、内見したときの印象など。
  <b>採点には使いません。</b></p>
 <form method="post" action="/saved/{{ it.id }}/note">
  <textarea name="note" maxlength="{{ note_max }}"
    placeholder="例）駐車場が縦列で出し入れがしにくい。南隣が空き地で、将来建つと日当たりが変わりそう。">{{ it.note or '' }}</textarea>
  <div style="display:flex;gap:10px;align-items:center;margin-top:10px">
   <button class="btn" type="submit">メモを保存</button>
   <span class="sub">{{ note_max }}文字まで</span>
  </div>
 </form>
</div>

<div class="card" style="margin-top:14px">
 <h2 style="margin-top:0">価格と返済</h2>
 <table class="cmp" style="width:100%">
  <tbody>
   <tr><th class="rowlbl">価格の妥当性</th>
    <td>{% if p.price and p.price.verdict != '判定不可' %}{{ p.price.verdict }}
     （中央値比 {{ '%+.1f'|format(p.price.dev) if p.price.dev is not none else '—' }}%
      ・類似{{ p.price.count }}件）{% else %}判定不可（類似成約が不足）{% endif %}</td></tr>
   {% if p.price and p.price.mid %}
   <tr><th class="rowlbl">推定価格の中央値</th><td>{{ man(p.price.mid) }}</td></tr>
   {% endif %}
   {% if p.loan %}
   <tr><th class="rowlbl">月々の返済</th><td>{{ '{:,}'.format(p.loan.monthly) }}円</td></tr>
   <tr><th class="rowlbl">返済の負担率</th><td>{{ p.loan.burden }}%</td></tr>
   {% endif %}
  </tbody>
 </table>
</div>

<div class="card" style="margin-top:14px">
 <h2 style="margin-top:0">スコアの内訳</h2>
 <div class="bars">
  {% for c in p.categories %}
   <div class="b">
    <div class="top"><span>{{ c.name }}</span>
     <b>{{ c.points }} / {{ c.weight }}</b></div>
    <span class="track"><span class="fill"
      style="width:{{ c.pct }}%;background:{{ catcolor(c.pct / 100.0) }}"></span></span>
    {% if c.reason %}<div class="why">{{ c.reason }}</div>{% endif %}
   </div>
  {% endfor %}
 </div>
</div>

{% if p.risks %}
<div class="card" style="margin-top:14px">
 <h2 style="margin-top:0">重大なリスク</h2>
 {% for r in p.risks %}
  <div class="rsk"><b>[{{ r.sev }}] {{ r.type }}</b>（{{ r.status }}）
   {% if r.ev %}<br>{{ r.ev }}{% endif %}</div>
 {% endfor %}
</div>
{% endif %}

{% if p.enr %}
<div class="card" style="margin-top:14px">
 <h2 style="margin-top:0">立地・防災・人口</h2>
 <table class="cmp" style="width:100%"><tbody>
  <tr><th class="rowlbl">用途地域</th><td>{{ p.enr.use_district }}</td></tr>
  <tr><th class="rowlbl">人口</th><td>{{ p.enr.population }}（動向 {{ p.enr.trend }}）</td></tr>
  {% if p.enr.districts %}<tr><th class="rowlbl">学区</th><td>{{ p.enr.districts }}</td></tr>{% endif %}
  {% if p.enr.facilities %}<tr><th class="rowlbl">周辺施設</th><td>{{ p.enr.facilities }}</td></tr>{% endif %}
 </tbody></table>
 {% if p.enr.hazard_items %}
  <div style="margin-top:10px">
   {% for h in p.enr.hazard_items %}<span class="hz {{ h.cls }}">{{ h.label }}</span>{% endfor %}
  </div>
 {% endif %}
</div>
{% endif %}

{% if p.strengths or p.weaknesses or p.confirm %}
<div class="card" style="margin-top:14px">
 {% if p.strengths %}<h2 style="margin-top:0">強み</h2>
  <ul>{% for x in p.strengths %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
 {% if p.weaknesses %}<h2>弱み</h2>
  <ul>{% for x in p.weaknesses %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
 {% if p.confirm %}<h2>要確認（情報不足）</h2>
  <ul>{% for x in p.confirm %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
</div>
{% endif %}

<div class="card" style="margin-top:14px">
 <p class="sub" style="margin:0">{{ it.created_at[:10] }}に診断した時点の結果です。
  公的データも配点も時間とともに変わるため、開くたびに計算し直すことはしていません。
  {% if p.redo %}下のボタンで、そのときの入力を戻して採り直せます。
  {% else %}時間が経っている場合は、もう一度診断し直してください。{% endif %}</p>
 {% if p.redo %}
  <p style="margin-top:12px">
   <a class="btn" href="/saved/{{ it.id }}/redo">最新のデータで再診断する</a></p>
  <p class="sub" style="margin-top:8px">物件の入力は戻りますが、
   <b>世帯年収と頭金はお預かりしていないため空欄</b>になります。</p>
 {% endif %}
 <p style="margin-top:12px">
  <a class="btn ghost sm" href="/buy">戸建をもう1件診断する</a>
  <a class="btn ghost sm" href="/mansion">マンションを診断する</a></p>
</div>
"""


@app.route("/saved/<int:sid>")
def saved_detail(sid):
    """保存した診断を見返す。保存した時点の内容をそのまま出す。"""
    r = _require_login()
    if r is not None:
        return r
    it = saved.get_one(current_user()["id"], sid)
    if not it:
        body = ('<div class="card"><h1>見つかりません</h1>'
                '<p class="lead">削除されたか、別のアカウントの保存です。</p>'
                '<a class="btn" href="/mypage">保存した物件へ</a></div>')
        return _account_page("保存した診断", body), 404
    body = render_template_string(
        SAVED_DETAIL, it=it, p=it["payload"], man=man, kindja=_kindja,
        catcolor=_catcolor, short=short_label(it),
        note_max=saved.NOTE_MAX)
    return _account_page("保存した診断", body)


@app.route("/saved/<int:sid>/redo")
def saved_redo(sid):
    """保存した入力をフォームに戻し、最新の公的データで採り直してもらう。

    その場で計算し直さずフォームに戻すのは、値下げされていたり、
    あとから構造が分かっていたりするため。確認・修正してから診断できる。

    世帯年収と頭金は保存していないので空欄になる。入れ直していただく必要が
    あることを、画面で伝える。
    """
    r = _require_login()
    if r is not None:
        return r
    it = saved.get_one(current_user()["id"], sid)
    if not it:
        from flask import abort
        abort(404)
    redo = (it["payload"] or {}).get("redo")
    if not redo:
        # 入力を残す前に保存されたもの。作り直してもらうしかない。
        body = ('<div class="card"><h1>この保存からは再診断できません</h1>'
                '<p class="lead">再診断の仕組みを入れる前に保存されたため、'
                'そのときの入力が残っていません。'
                'お手数ですが、新しく診断してください。</p>'
                '<p><a class="btn" href="/buy">戸建を診断する</a> '
                '<a class="btn ghost" href="/mansion">マンションを診断する</a></p>'
                f'<p style="margin-top:12px"><a href="/saved/{sid}">'
                '保存した診断にもどる</a></p></div>')
        return _account_page("再診断", body)

    banner = ("以前の入力を戻しました。<b>最新の公的データで採り直します。</b>"
              "価格が変わっていれば直してから診断してください。<br>"
              "<b>世帯年収と頭金はお預かりしていないため、空欄です。</b>"
              "返済の評価も見たい場合は入れ直してください。")
    if redo.get("kind") == "mansion":
        v = _mansion_example_v()
        for k in ("address", "name", "price", "area", "byear", "station",
                  "bus", "floor", "total_floors", "direction", "layout",
                  "mfee", "rfund", "loan_years"):
            if redo.get(k):
                v[k] = redo[k]
        v["reno"] = redo.get("reno") == "1"
        return render_template_string(MANSION_FORM, v=v, listing="",
                                      directions=DIRECTIONS, banner=banner)

    v = _example_v()
    for k in ("address", "price", "byear", "land", "building", "station",
              "bus", "structure", "loan_years", "ptype"):
        if redo.get(k):
            v[k] = redo[k]
    v["reno"] = redo.get("reno") == "1"
    return render_template_string(FORM, v=v, listing="", banner=banner)


@app.route("/saved/<int:sid>/note", methods=["POST"])
def saved_note(sid):
    r = _require_login()
    if r is not None:
        return r
    saved.set_note(current_user()["id"], sid, request.form.get("note") or "")
    return redirect(f"/saved/{sid}")


@app.route("/saved/<int:sid>/delete", methods=["POST"])
def delete_saved(sid):
    r = _require_login()
    if r is not None:
        return r
    saved.delete(current_user()["id"], sid)
    return redirect("/mypage")


# ---- マイページ ----------------------------------------------------------

MYPAGE = """
<div class="card">
 <div style="display:flex;justify-content:space-between;align-items:baseline;
   gap:10px;flex-wrap:wrap">
  <h1 style="margin:0">保存した物件</h1>
  <span class="plan-badge {{ 'is-pro' if pro }}">{{ 'PRO' if pro else '無料プラン' }}</span>
 </div>
 <p class="lead" style="margin-top:8px">{{ user.email }}
  <span class="sub">保存 {{ items|length }} / {{ limit }} 件</span></p>

 {% if added %}<div class="note ok">保存しました。</div>{% endif %}
 {% if full %}<div class="note warn">保存できる件数（{{ limit }}件）に達しています。
   いずれかを削除するか、PROで上限を増やしてください。</div>{% endif %}
 {% if items|length > limit %}
  <div class="note">PROのときに保存した分が残っています。
   <b>これまでの保存はすべてご覧いただけます</b>が、
   いまの上限（{{ limit }}件）を超えているため、新しく保存するには
   いずれかを削除してください。</div>
 {% endif %}
 {% if err %}<div class="note warn">保存できませんでした。時間をおいてお試しください。</div>{% endif %}

 {% if not items %}
  <div class="note" style="margin-top:12px">まだ保存がありません。
   診断結果の画面にある「この結果を保存する」から追加できます。</div>
  <p style="margin-top:14px">
   <a class="btn" href="/buy">戸建を診断する</a>
   <a class="btn ghost" href="/mansion">マンションを診断する</a></p>
 {% else %}
  {# 削除フォームは「比べる」フォームの外に置く。<form>は入れ子にできず、
     入れ子にするとブラウザに捨てられて削除が効かなくなる。
     ボタン側から form="del<id>" で紐づける。 #}
  {% for it in items %}
   <form method="post" action="/saved/{{ it.id }}/delete" id="del{{ it.id }}"
     onsubmit="return confirm('この保存を削除します。よろしいですか？')"></form>
  {% endfor %}
  <form method="get" action="/compare">
   <ul class="items">
    {% for it in items %}
     <li>
      <input class="pick" type="checkbox" name="id" value="{{ it.id }}"
        id="p{{ it.id }}" aria-label="比較に含める"
        {% if items|length <= 3 %}checked{% endif %}>
      <div class="body">
       <a class="ttl" href="/saved/{{ it.id }}"
         style="display:block">{{ it.title }}</a>
       <div class="meta">{{ it.address or '—' }}</div>
       <div class="meta">{{ it.created_at[:10] }}　{{ kindja(it.kind) }}
        {%- if it.price %}　売出 {{ man(it.price) }}{% endif %}</div>
       {% if it.note %}<span class="memo-tag">📝 {{ it.note }}</span>{% endif %}
      </div>
      <div style="text-align:right">
       <div class="sc">{{ it.total_score }}<span class="sub">点</span></div>
       <button class="btn ghost sm" type="submit" form="del{{ it.id }}"
         style="margin-top:6px">削除</button>
      </div>
     </li>
    {% endfor %}
   </ul>
   <div class="selbar">
    <button class="btn" type="submit">選んだ物件を比べる</button>
    <span class="n">2件以上えらんでください</span>
   </div>
  </form>
 {% endif %}
</div>

<div class="card" style="margin-top:14px">
 <h2 style="margin-top:0">プランと設定</h2>
 <p class="lead">いまは<b>{{ 'PRO' if pro else '無料プラン' }}</b>です。
  保存できるのは{{ limit }}件までです。</p>
 <p><a class="btn ghost sm" href="/plan">プランを見る</a></p>
 <form method="post" action="/logout" style="margin-top:14px">
  <button class="btn ghost sm" type="submit">ログアウト</button></form>
</div>
"""


def _kindja(kind):
    return {"chuko_kodate": "中古戸建", "shinchiku_kodate": "新築戸建",
            "chuko_mansion": "中古マンション"}.get(kind, "物件")


@app.route("/mypage")
def mypage():
    r = _require_login()
    if r is not None:
        return r
    user = current_user()
    items = saved.listing(user["id"])
    body = render_template_string(
        MYPAGE, user=user, items=items, man=man, kindja=_kindja,
        pro=accounts.is_pro(user), limit=saved.limit_for(user),
        added=request.args.get("added"), full=request.args.get("full"),
        err=request.args.get("err"))
    return _account_page("マイページ", body)


# ---- 比較 ----------------------------------------------------------------
# 「どちらが良いか」を行ごとに示すのが目的なので、単に並べるだけにはしない。
# ただし戸建とマンションは配点そのものが違うため、種別が混ざったときは
# カテゴリ別の行を出さない。出すと、比べてはいけないものを比べさせてしまう。

def _fmt_pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def short_label(item):
    """狭い列に出すための短い名前。都道府県と市区町村を落として町名以降を使う。

    「神奈川県小田原市城山1-2-3」→「城山1-2-3」。
    住所が無いか短縮できないときは表題を切り詰めて使う。
    """
    import re as _re
    addr = (item.get("address") or "").strip()
    if addr:
        m = _re.match(r"^(?:.*?[都道府県])?(?:.*?[市区町村])?(.+)$", addr)
        rest = (m.group(1) if m else "").strip()
        if rest:
            return rest[:14]
        return addr[:14]
    return (item.get("title") or "物件")[:14]


def compare_rows(items):
    """比較表の行を組み立てる。best には「その行で優れている列」の番号を入れる。

    優劣を機械的に決められる行だけに印をつける。たとえば情報充足度は
    「物件の良さ」ではなく「診断の確かさ」なので、印はつけるが別の節に置く。
    """
    n = len(items)
    pays = [it["payload"] for it in items]

    def mk(label, vals, better=None, note=None):
        """better: 'max' | 'min' | None。比較できない値は None を入れておく。"""
        best = []
        nums = [v[1] for v in vals]
        usable = [x for x in nums if x is not None]
        if better and len(usable) >= 2 and len(set(usable)) > 1:
            target = max(usable) if better == "max" else min(usable)
            best = [i for i, x in enumerate(nums) if x == target]
        return dict(label=label, texts=[v[0] for v in vals], best=best,
                    note=note)

    rows = [mk("総合点", [(f'{p.get("total", "—")}点', p.get("total"))
                          for p in pays], "max"),
            mk("判定", [(p.get("grade", "—"), None) for p in pays])]

    price = []
    for p in pays:
        pr = p.get("price") or {}
        if pr.get("verdict") in (None, "判定不可"):
            price.append(("判定不可", None))
        else:
            dev = pr.get("dev")
            price.append((f'{pr["verdict"]}（{_fmt_pct(dev)}）', dev))
    rows.append(mk("価格の妥当性", price, "min",
                   "推定価格に対する差。マイナスが割安。"))

    rows.append(mk("月々の返済", [
        ((f'{(p.get("loan") or {}).get("monthly"):,}円'
          if (p.get("loan") or {}).get("monthly") else "—"),
         (p.get("loan") or {}).get("monthly")) for p in pays], "min"))
    rows.append(mk("返済の負担率", [
        ((f'{(p.get("loan") or {}).get("burden")}%'
          if (p.get("loan") or {}).get("burden") is not None else "—"),
         (p.get("loan") or {}).get("burden")) for p in pays], "min"))

    risks = []
    for p in pays:
        rs = [r for r in (p.get("risks") or []) if r.get("sev") in ("高", "中")]
        risks.append((f"{len(rs)}件" if rs else "なし", len(rs)))
    rows.append(mk("重大なリスク", risks, "min"))

    # data_sufficiency はすでに百分率（0〜100）で入っている。
    # 結果ページも {{d.suff}}% とそのまま出しているので、ここでも掛けない。
    rows.append(mk("情報の充足度", [
        ((f'{int(round(p["sufficiency"]))}%'
          if p.get("sufficiency") is not None else "—"), p.get("sufficiency"))
        for p in pays], "max",
        "物件の良し悪しではなく、診断の確かさです。"))

    # カテゴリ別は、種別がそろっているときだけ
    kinds = {it["kind"] for it in items}
    cats = []
    if len(kinds) == 1:
        names = [c["name"] for c in (pays[0].get("categories") or [])]
        for nm in names:
            vals = []
            for p in pays:
                hit = next((c for c in (p.get("categories") or [])
                            if c["name"] == nm), None)
                if hit:
                    vals.append((f'{hit["points"]}/{hit["weight"]}',
                                 hit["points"]))
                else:
                    vals.append(("—", None))
            cats.append(mk(nm, vals, "max"))
    return rows, cats, (len(kinds) > 1)


COMPARE = """
<div class="card">
 <a href="/mypage" style="font-size:14px">← 保存した物件へ</a>
 <h1 style="margin-top:10px">比べる</h1>
 {% if mixed %}
  <div class="note warn">戸建とマンションが混ざっています。
   この2つは配点の項目そのものが違うため、<b>カテゴリ別の比較は出していません</b>。
   総合点も、同じものさしで並べたものではない点にご注意ください。</div>
 {% endif %}
 <p class="lead">◎ は、その行で条件の良いほうです。
  合計点だけで決めず、気になる行を見てください。</p>

 <div class="tablewrap"><table class="cmp" style="--n:{{ cols }}">
  <thead><tr><th class="rowlbl">項目</th>
   {% for it in items %}<th>{{ shorts[loop.index0] }}<span>{{ kindja(it.kind) }}
     ／ {{ it.address or '—' }}</span></th>{% endfor %}
  </tr></thead>
  <tbody>
   {% for r in rows %}
    <tr><th class="rowlbl">{{ r.label }}{% if r.note %}<span class="sub"
      style="display:block;font-weight:400">{{ r.note }}</span>{% endif %}</th>
     {% for t in r.texts %}<td data-name="{{ shorts[loop.index0] }}"
       class="{{ 'best' if loop.index0 in r.best }}">{{ t }}</td>{% endfor %}
    </tr>
   {% endfor %}
   {% if items|selectattr('note')|list %}
    <tr><th class="rowlbl">メモ<span class="sub"
      style="display:block;font-weight:400">点数には入れていません</span></th>
     {% for it in items %}<td data-name="{{ shorts[loop.index0] }}"
       style="white-space:normal;font-weight:400">{{ it.note or '—' }}</td>{% endfor %}
    </tr>
   {% endif %}
   {% if cats %}
    <tr class="sect"><th class="rowlbl">カテゴリ別</th>
     {% for it in items %}<th></th>{% endfor %}</tr>
    {% for r in cats %}
     <tr><th class="rowlbl">{{ r.label }}</th>
      {% for t in r.texts %}<td data-name="{{ shorts[loop.index0] }}"
        class="{{ 'best' if loop.index0 in r.best }}">{{ t }}</td>{% endfor %}
     </tr>
    {% endfor %}
   {% endif %}
  </tbody>
 </table></div>
</div>

<div class="card" style="margin-top:14px">
 <h2 style="margin-top:0">それぞれの弱点</h2>
 <p class="lead">点数に出ない部分です。上の表で拮抗しているときは、こちらが決め手になります。</p>
 {% for it in items %}
  <h3 style="font-size:14px;margin:14px 0 4px">
   <a href="/saved/{{ it.id }}">{{ shorts[loop.index0] }}</a></h3>
  {% set w = it.payload.get('weaknesses') or [] %}
  {% if w %}<ul>{% for x in w %}<li>{{ x }}</li>{% endfor %}</ul>
  {% else %}<p class="sub">特筆すべき弱点は挙がっていません。</p>{% endif %}
 {% endfor %}
</div>

<div class="card" style="margin-top:14px">
 <h2 style="margin-top:0">もう1件くらべる</h2>
 {% if others %}
  <p class="lead">保存済みの物件を、この比較に足せます。</p>
  <ul class="items">
   {% for o in others %}
    <li>
     <div class="body">
      <span class="ttl">{{ o.short }}</span>
      <div class="meta">{{ kindja(o.kind) }}
       {%- if o.price %}　売出 {{ man(o.price) }}{% endif %}</div>
     </div>
     <div style="text-align:right">
      <div class="sc">{{ o.total_score }}<span class="sub">点</span></div>
      <a class="btn ghost sm" href="{{ o.href }}"
        style="margin-top:6px">くらべる</a>
     </div>
    </li>
   {% endfor %}
  </ul>
 {% else %}
  <p class="lead">ほかに保存された物件がありません。
   もう1件診断すると、ここに並べて比べられます。</p>
 {% endif %}
 <p style="margin-top:12px">
  <a class="btn ghost sm" href="/buy">戸建を診断する</a>
  <a class="btn ghost sm" href="/mansion">マンションを診断する</a>
  <a class="btn ghost sm" href="/mypage">保存した物件へ</a></p>
</div>

<div class="card" style="margin-top:14px">
 <p class="sub" style="margin:0">保存した時点の診断結果を並べています。
  公的データも配点も時間とともに変わるため、あとから再計算はしていません。
  日付の離れた結果を比べるときはご注意ください。</p>
</div>
"""


@app.route("/compare")
def compare():
    r = _require_login()
    if r is not None:
        return r
    user = current_user()
    try:
        ids = [int(x) for x in request.args.getlist("id")][:6]
    except ValueError:
        ids = []
    items = saved.get_many(user["id"], ids) if ids else []
    if len(items) < 2:
        body = ('<div class="card"><h1>比べる物件を選んでください</h1>'
                '<p class="lead">2件以上を選ぶと、項目ごとに並べて比べられます。</p>'
                '<a class="btn" href="/mypage">保存した物件へ</a></div>')
        return _account_page("比べる", body, chip="比べる")
    rows, cats, mixed = compare_rows(items)
    # まだ並べていない保存。ここから足せるようにする。
    # 6件を超えると表が読めなくなるので、そのときは出さない。
    others = []
    if len(items) < 6:
        shown = {int(i["id"]) for i in items}
        for r in saved.listing(user["id"]):
            if int(r["id"]) in shown:
                continue
            q = "&".join(f"id={i}" for i in ids + [r["id"]])
            others.append(dict(r, short=short_label(r), href=f"/compare?{q}"))
    body = render_template_string(COMPARE, items=items, rows=rows, cats=cats,
                                  mixed=mixed, kindja=_kindja, man=man,
                                  cols=min(len(items), 3), others=others,
                                  shorts=[short_label(i) for i in items])
    return _account_page("比べる", body, chip="比べる")


# ---- プラン --------------------------------------------------------------
# 決済はまだ繋いでいない。特定商取引法に基づく表記や、課金・解約・返金の
# 定めを整えてから繋ぐ。ここでは枠組み（プランの区別と上限）だけを持つ。

PLAN_PAGE = """
<div class="card">
 <h1>プラン</h1>
 <p class="lead">いまは<b>{{ 'PRO' if pro else '無料プラン' }}</b>をご利用中です。</p>
 <div class="tablewrap"><table class="cmp">
  <thead><tr><th class="rowlbl">できること</th><th>無料</th><th>PRO</th></tr></thead>
  <tbody>
   <tr><th class="rowlbl">購入診断（戸建・マンション）</th><td>○</td><td>○</td></tr>
   <tr><th class="rowlbl">診断結果の保存</th><td>{{ free_limit }}件</td><td>{{ pro_limit }}件</td></tr>
   <tr><th class="rowlbl">保存した物件の比較</th><td>○</td><td>○</td></tr>
   <tr><th class="rowlbl">詳細診断（PRO）</th><td>—</td><td>○</td></tr>
   <tr><th class="rowlbl">仲介業者に聞くことの一覧</th><td>—</td><td>○</td></tr>
   <tr><th class="rowlbl">資金計画のPDF</th><td>—</td><td>○</td></tr>
  </tbody>
 </table></div>

{% if not billing %}
 <div class="note warn" style="margin-top:14px">
  <b>PROは試験公開中です。</b>いまのところ料金はいただいていません。
  どなたでも<a href="/pro/diagnose">戸建</a>・<a href="/pro/mansion">マンション</a>の
  詳細診断をお試しいただけます。<br>
  有料でのご提供を始めるときは、事前にこのページでご案内します。
 </div>
{% elif pro %}
 <div class="note ok" style="margin-top:14px">
  PROをご利用中です。{{ price_label }}が毎月かかります。
  {% if expires %}<br>次回の更新日：{{ expires[:10] }}{% endif %}
 </div>
 <p style="margin-top:14px"><a class="btn ghost" href="/plan/cancel">解約する</a></p>
{% else %}
 <div class="note" style="margin-top:14px">
  <p style="margin:0 0 6px"><b>PRO　{{ price_label }}</b></p>
  <p style="margin:0">解約されるまで毎月自動で更新されます。
   金額は初回も2回目以降も同じです。<br>
   <b>マイページからいつでも解約できます。</b>
   解約後も、保存した診断はそのままご覧いただけます。</p>
 </div>
 <p style="margin-top:14px">
  <a class="btn" href="/plan/confirm">PROに申し込む</a></p>
 <p class="sub" style="margin-top:10px">
  <a href="/tokushoho">特定商取引法に基づく表記</a>　・
  <a href="/terms">利用規約</a>　・
  <a href="/privacy">プライバシーポリシー</a></p>
{% endif %}
 <p style="margin-top:14px"><a class="btn ghost sm" href="/mypage">マイページへ</a></p>
</div>
"""


# ---- 申込の最終確認画面 --------------------------------------------------
# 特定商取引法（令和4年6月施行）は、注文確定の直前画面に
#   ①分量 ②販売価格・対価 ③支払の時期・方法 ④提供時期
#   ⑤申込みの撤回・解除に関すること ⑥申込期間（定めがある場合）
# を表示することを義務づけている。表示が無い、または誤認させる表示をした
# 場合、誤認して申し込んだ人は契約を取り消せる。
# ⑤は「顧客が見やすい位置に」とされているので、規約へのリンクではなく
# この画面に手順そのものを書く。
# ⑥は期間限定販売ではないため該当しない。
PLAN_CONFIRM = """
<div class="card">
 <a href="/plan" style="font-size:14px">← プランへもどる</a>
 <h1 style="margin-top:10px">お申込み内容の確認</h1>
 <p class="lead">下記の内容でお申込みになります。
  内容をご確認のうえ、いちばん下のボタンを押してください。</p>

 <div class="tablewrap"><table class="cmp">
  <tbody>
   <tr><th class="rowlbl">内容</th>
    <td>HOME INDEX PRO<br>
     <span class="sub">契約期間中、詳細診断・仲介業者に聞くことの一覧・
      資金計画のPDFをご利用いただけます。診断の回数に制限はありません。
      診断結果の保存は{{ pro_limit }}件までです。</span></td></tr>
   <tr><th class="rowlbl">料金</th>
    <td><b>{{ price_label }}</b><br>
     <span class="sub">2回目以降も同額です。初回だけ安くなる、
      あとから値上がりする、といったことはありません。</span></td></tr>
   <tr><th class="rowlbl">支払総額</th>
    <td><b>解約されるまで、毎月{{ price_yen }}円（税込）が発生します。</b><br>
     <span class="sub">契約期間の定めがないため、総額はご利用期間によります。
      3か月なら{{ price_yen * 3 }}円、6か月なら{{ price_yen * 6 }}円です。</span></td></tr>
   <tr><th class="rowlbl">支払方法</th><td>クレジットカード</td></tr>
   <tr><th class="rowlbl">支払時期</th>
    <td>初回はお申込み時。以降は<b>毎月同じ日に自動で決済</b>されます。</td></tr>
   <tr><th class="rowlbl">提供時期</th>
    <td>決済の完了後、ただちにご利用いただけます。</td></tr>
   <tr><th class="rowlbl">解約の方法</th>
    <td><b>マイページ →「プランと設定」→「プランを見る」→「解約する」</b><br>
     <span class="sub">いつでも解約できます。解約後も、お支払い済みの期間の
      末日まではご利用いただけます。<b>日割りでの返金は行っておりません。</b><br>
      本サービスは役務の提供であり、性質上、返品はできません。</span></td></tr>
   <tr><th class="rowlbl">解約後の保存データ</th>
    <td>消去されません。無料プランの{{ free_limit }}件を超えている分も、
     引き続きご覧いただけます（新たな保存はできません）。</td></tr>
  </tbody>
 </table></div>

 <p class="sub" style="margin-top:14px">
  <a href="/tokushoho">特定商取引法に基づく表記</a>　・
  <a href="/terms">利用規約</a>　・
  <a href="/privacy">プライバシーポリシー</a></p>

 <form method="post" action="/plan/subscribe" style="margin-top:16px">
  <button class="btn" type="submit">上記に同意して申し込む</button>
 </form>
 <p class="sub" style="margin-top:8px">
  このボタンを押すと有料の契約が成立し、初回の決済が行われます。</p>
</div>
"""


# ---- 解約 ----------------------------------------------------------------
# 引き止めは置かない。一方でボタン1つで即完了にもしない。
# 誤操作を防ぐためと、「いつまで使えるか」は解約する人が知るべき情報だから。
# アンケートは完了画面に置く。解約の前に置くと妨害になるが、後ならならない。
PLAN_CANCEL = """
<div class="card">
 <a href="/plan" style="font-size:14px">← プランへもどる</a>
 <h1 style="margin-top:10px">解約の確認</h1>
 <div class="note" style="margin-top:12px">
  <p style="margin:0 0 8px"><b>解約すると、こうなります。</b></p>
  <ul style="margin:0;padding-left:18px">
   <li>{% if expires %}<b>{{ expires[:10] }}まで</b>は、これまでどおりPROをご利用いただけます{% else %}お支払い済みの期間の末日までご利用いただけます{% endif %}</li>
   <li>その後は無料プランに切り替わります</li>
   <li><b>保存した診断は消えません。</b>無料プランの{{ free_limit }}件を超えている分も、
    引き続き閲覧・比較できます（新たな保存はできなくなります）</li>
   <li>メモもそのまま残ります</li>
   <li>日割りでの返金はありません</li>
   <li>いつでも再開できます。そのときデータはそのままです</li>
  </ul>
 </div>
 <form method="post" action="/plan/cancel" style="margin-top:16px">
  <button class="btn" type="submit">解約を確定する</button>
  <a class="btn ghost" href="/plan" style="margin-left:8px">やめる</a>
 </form>
</div>
"""

PLAN_CANCELED = """
<div class="card">
 <h1>解約しました</h1>
 <div class="note ok">
  {% if expires %}{{ expires[:10] }}まではPROをご利用いただけます。
  その後は無料プランに切り替わります。{% else %}無料プランに切り替わりました。{% endif %}<br>
  保存した診断とメモはそのまま残っています。
 </div>
 <p class="lead" style="margin-top:14px">ご利用ありがとうございました。
  またお使いいただけるよう、<a href="/plan">プランのページ</a>からいつでも再開できます。</p>

 <form method="post" action="/plan/cancel/why" style="margin-top:8px">
  <p style="font-size:14px;font-weight:700;margin:0 0 8px">
   よろしければ、理由を1つだけ教えてください（任意）</p>
  {% for val, lbl in reasons %}
   <label style="display:block;font-size:14px;line-height:2.1">
    <input type="radio" name="why" value="{{ val }}"> {{ lbl }}</label>
  {% endfor %}
  <button class="btn ghost sm" type="submit" style="margin-top:10px">送信する</button>
  <a class="btn ghost sm" href="/mypage" style="margin-left:8px">答えずに戻る</a>
 </form>
</div>
"""

# 1問だけにする。設問が多いほど答えてもらえなくなるうえ、
# 解約したい人を引き止める形になってしまう。
CANCEL_REASONS = [
    ("bought", "住まいが決まった"),
    ("stopped", "購入の検討をやめた"),
    ("not_useful", "期待した内容ではなかった"),
    ("price", "料金が見合わなかった"),
    ("other", "その他"),
]


@app.route("/plan")
def plan_page():
    if not accounts_on():
        return _account_page("準備中", _OFF_BODY)
    u = current_user()
    body = render_template_string(
        PLAN_PAGE, pro=accounts.is_pro(u), billing=billing_on(),
        price_label=PRICE_LABEL,
        expires=(u or {}).get("plan_expires_at"),
        free_limit=saved.FREE_LIMIT, pro_limit=saved.PRO_LIMIT)
    return _account_page("プラン", body, chip="プラン")


@app.route("/plan/confirm")
def plan_confirm():
    """申込の最終確認画面（特定商取引法の表示義務を満たす画面）。"""
    r = _require_login()
    if r is not None:
        return r
    if not billing_on():
        from flask import abort
        abort(404)
    if accounts.is_pro(current_user()):
        return redirect("/plan")
    body = render_template_string(
        PLAN_CONFIRM, price_label=PRICE_LABEL, price_yen=PRICE_YEN,
        free_limit=saved.FREE_LIMIT, pro_limit=saved.PRO_LIMIT)
    return _account_page("お申込みの確認", body, chip="プラン")


@app.route("/plan/subscribe", methods=["POST"])
def plan_subscribe():
    """ここで決済サービスのCheckoutを開く。まだ繋いでいない。

    課金開始日が決まり、特定商取引法に基づく表記が実名・実住所で出せて、
    規約の課金条項を整えてから接続する。それまでは billing_on() が
    False なので、この経路には入らない。
    """
    r = _require_login()
    if r is not None:
        return r
    if not billing_on():
        from flask import abort
        abort(404)
    body = ('<div class="card"><h1>準備中です</h1>'
            '<p class="lead">決済サービスの接続はこれからです。'
            'いましばらくお待ちください。</p>'
            '<a class="btn ghost" href="/plan">プランへもどる</a></div>')
    return _account_page("準備中", body, chip="プラン")


@app.route("/plan/cancel", methods=["GET", "POST"])
def plan_cancel():
    """解約。GETで確認画面、POSTで確定する。"""
    r = _require_login()
    if r is not None:
        return r
    u = current_user()
    if not accounts.is_pro(u):
        return redirect("/plan")
    if request.method == "GET":
        body = render_template_string(
            PLAN_CANCEL, expires=u.get("plan_expires_at"),
            free_limit=saved.FREE_LIMIT)
        return _account_page("解約の確認", body, chip="プラン")

    # 決済側の解約はここで呼ぶ（未接続）。いまは期限を残したまま
    # プランを free に落とす＝支払い済みの期間は使えるという扱い。
    expires = u.get("plan_expires_at")
    accounts.set_plan(u["id"], accounts.PLAN_FREE, expires)
    body = render_template_string(PLAN_CANCELED, expires=expires,
                                  reasons=CANCEL_REASONS)
    return _account_page("解約しました", body, chip="プラン")


@app.route("/plan/cancel/why", methods=["POST"])
def plan_cancel_why():
    """解約理由の任意アンケート。答えても答えなくても結果は変わらない。"""
    r = _require_login()
    if r is not None:
        return r
    why = (request.form.get("why") or "").strip()
    if why:
        # いまは記録先を持たないのでログに残すだけ。
        # 集計が要るようになったら列を足す。
        print(f"[cancel-reason] {why}")
    return redirect("/mypage")



# ---- コピーの仕方 ----------------------------------------------------
# SUUMOやアットホームのスマホアプリは、画面の文字をそのまま選択できない。
# 「コピペしてください」とだけ書いても、アプリしか使っていない人はそこで
# 止まってしまう。回避のしかたを1ページにまとめて、フォームから案内する。

COPY_GUIDE = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>物件情報のコピーの仕方｜HOME INDEX</title>
<meta name="description" content="SUUMOやアットホームのスマホアプリで文字がコピーできないときの対処。スクリーンショットから文字を読み取ってコピーする手順を、iPhone・Androidそれぞれで説明します。">
FONT_LINK_PLACEHOLDER
<style>
MANSION_CSS_PLACEHOLDER
 /* 手順の中の補足。本文より一段落として、読み飛ばせるようにする。 */
 .step .fine{font-size:13px; color:var(--sub); line-height:1.8; margin-top:6px}
 .step .fine a{color:var(--pin)}
 .cta-mid{margin-top:26px; justify-content:center}
 .steps{counter-reset:s; display:grid; gap:14px; margin:14px 0 0}
 .step{display:grid; grid-template-columns:30px 1fr; gap:14px}
 .step .n{width:30px;height:30px;border-radius:50%;background:#111;color:#fff;
   font-size:14px;font-weight:700;display:flex;align-items:center;
   justify-content:center;margin-top:2px}
 .step h3{font-size:15px;margin:4px 0 4px}
 .step p{margin:0;font-size:14px;color:var(--sub);line-height:1.85}
 .fig{margin:6px 0 0}
 .fig svg{display:block;width:100%;height:auto}
 .os{border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:12px}
 .os h3{margin:0 0 6px;font-size:15px}
</style></head><body>
BRAND_BAR
<div class="wrap">
 <a href="/buy" style="color:#111;font-size:14px">← 診断にもどる</a>
 <h1>物件情報のコピーの仕方</h1>
 <p class="aim">SUUMOやアットホームの<b>スマホアプリは、画面の文字をそのまま選択できません</b>。
  「コピーして貼り付けてください」と言われても手が止まってしまうので、
  やり方をまとめました。</p>

 <div class="card">
  <h2 style="font-size:16px;margin:0 0 4px">まずこれを試してください</h2>
  <p class="hint" style="margin-bottom:10px">アプリではなく<b>ブラウザ</b>（Safari・Chrome）で
   同じ物件ページを開くと、文字を普通に選択してコピーできます。
   アプリの共有ボタンから「ブラウザで開く」を選ぶのが早いです。<br>
   これができるなら、以下の手順は不要です。</p>
 </div>

 <div class="card">
  <h2 style="font-size:16px;margin:0 0 4px">アプリしか使えないときは、画面を撮ってから文字を読み取る</h2>
  <p class="hint">スマホには、写真の中の文字を認識してコピーする機能が入っています。
   物件ページを撮影して、その写真から文字を取り出します。</p>
  <div class="fig">COPY_FLOW_SVG</div>

  <div class="steps">
   <div class="step"><span class="n">1</span><div>
    <h3>物件ページを表示したまま、スクリーンショットを撮る</h3>
    <p>価格・所在地・面積・築年・駅からの徒歩分が写るようにしてください。
     1枚に収まらなければ、スクロールして複数枚に分けて構いません。</p></div></div>
   <div class="step"><span class="n">2</span><div>
    <h3>写真から文字を読み取る</h3>
    <p>端末ごとの手順は下にまとめています。</p></div></div>
   <div class="step"><span class="n">3</span><div>
    <h3>読み取った文字をコピーして、診断の貼り付け欄に貼る</h3>
    <p>複数枚に分けた場合は、続けて貼り付けてください。順番は問いません。</p></div></div>
  </div>

  <div class="os">
   <h3>iPhone の場合</h3>
   <p class="hint">写真アプリでスクリーンショットを開き、<b>文字の部分を長押し</b>します。
    または画面の隅に出る<b>テキスト認識のアイコン</b>を押すと、文字が選べる状態になります。
    そのまま範囲を選んで「コピー」。<br>
    日本語の読み取りは iOS 16 以降で使えます。うまくいかない場合は、
    次のGoogleアプリを使う方法をお試しください。</p>
  </div>

  <div class="os">
   <h3>Android の場合</h3>
   <p class="hint">Google フォトでスクリーンショットを開き、下部の<b>「レンズ」</b>を押します。
    文字が認識されたら、範囲を選んで「テキストをコピー」。<br>
    機種によっては、スクリーンショットを撮った直後に出る通知から
    直接コピーできることもあります。</p>
  </div>

  <div class="os">
   <h3>パソコンの場合</h3>
   <p class="hint">ブラウザで物件ページを開き、必要な範囲をドラッグして選択し、
    Ctrl+C（Macは⌘+C）でコピーしてください。</p>
  </div>
 </div>

 <div class="card">
  <h2 style="font-size:16px;margin:0 0 4px">読み取りがうまくいかないときは</h2>
  <p class="hint"><b>販売図面のPDFがあれば、開いて文字を選択・コピーし、
   診断画面の貼り付け欄に貼ってください。</b>物件ページの文章より項目がそろって
   いることが多く、こちらのほうが確実です。<br>
   <b>文字が選択できないPDF</b>は、紙をスキャンした画像です。この場合、中身は
   画像なので文字を取り出せません。<br>
   どちらも難しければ、<b>手入力でも構いません</b>。必要なのは、価格・所在地・面積・
   築年・駅からの徒歩分の5つだけです。</p>
 </div>

 <div class="card">
  <h2 style="font-size:16px;margin:0 0 4px">コピーする範囲</h2>
  <p class="hint">物件ページの説明文をまるごと貼っていただいて構いません。
   多すぎて困ることはありません。読み取れなかった項目は、貼り付けたあとの画面で
   手直しできます。<br><br>
   なお解析するのは、<b>ご自身がコピーした情報</b>です（私的利用の範囲）。
   URLを送信して物件ページを自動で読みにいくことはしていません。</p>
 </div>

 <a class="cta" href="/buy" style="display:block;text-align:center;margin-top:18px;
   padding:15px;background:#111;color:#fff;border-radius:10px;font-weight:700;
   text-decoration:none">戸建の診断にもどる</a>
 <a class="cta" href="/mansion" style="display:block;text-align:center;margin-top:10px;
   padding:15px;background:#eef2f7;color:#111;border-radius:10px;font-weight:700;
   text-decoration:none">マンションの診断にもどる</a>
</div></body></html>
"""


def _copy_flow_svg():
    """スクリーンショットから文字を取り出す流れの図。

    画像ファイルではなくSVGにしてあるので、拡大しても崩れず、
    スマホの細い画面でも読める。
    """
    phones = [
        (0, "アプリの物件ページ", "文字を選べない"),
        (1, "スクリーンショット", "画面を撮る"),
        (2, "文字を読み取る", "長押し／レンズ"),
        (3, "診断に貼り付け", "コピーして貼る"),
    ]
    out = ['<svg viewBox="0 0 640 200" role="img" aria-label="'
           'アプリの物件ページをスクリーンショットで撮り、写真から文字を読み取って'
           'コピーし、診断の貼り付け欄に貼るまでの流れ。">']
    for i, title, note in phones:
        x = 12 + i * 158
        out.append(f'<rect x="{x}" y="18" width="96" height="132" rx="12" '
                   'fill="none" stroke="#c7cfd8" stroke-width="2"/>')
        out.append(f'<rect x="{x + 34}" y="24" width="28" height="5" rx="2.5" '
                   'fill="#c7cfd8"/>')
        # 画面の中身をそれらしく数本の線で表す
        for j in range(4):
            w = [64, 52, 70, 44][j]
            fill = "#14395c" if (i == 2 and j in (1, 2)) else "#e1e5eb"
            out.append(f'<rect x="{x + 14}" y="{44 + j * 18}" width="{w}" '
                       f'height="9" rx="4.5" fill="{fill}"/>')
        if i == 2:
            out.append(f'<rect x="{x + 10}" y="{58}" width="{76}" height="{34}" '
                       'rx="6" fill="none" stroke="#e0a83f" stroke-width="2"/>')
        out.append(f'<text x="{x + 48}" y="170" text-anchor="middle" '
                   'font-size="12" font-weight="700" fill="#14181d">'
                   f'{title}</text>')
        out.append(f'<text x="{x + 48}" y="187" text-anchor="middle" '
                   f'font-size="11" fill="#68707b">{note}</text>')
        if i < 3:
            ax = x + 108
            out.append(f'<path d="M{ax} 84 h30 M{ax + 24} 78 l6 6 -6 6" '
                       'fill="none" stroke="#c7cfd8" stroke-width="2" '
                       'stroke-linecap="round" stroke-linejoin="round"/>')
    out.append("</svg>")
    return "".join(out)


COPY_GUIDE = (COPY_GUIDE
              .replace("COPY_FLOW_SVG", _copy_flow_svg())
              .replace("MANSION_CSS_PLACEHOLDER", _FORM_CSS)
              .replace("FONT_LINK_PLACEHOLDER", FONT_LINK + ICON_LINKS)
              .replace("BRAND_BAR", brand_bar("コピーの仕方"))
              .replace("</div></body></html>",
                       FOOTER + "</div></body></html>"))


@app.route("/copy-guide")
def copy_guide():
    """物件情報のコピーの仕方。フォームの注意書きから案内している。"""
    return COPY_GUIDE


PRO_FINANCE_FORM = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
FONT_LINK_PLACEHOLDER
<title>詳細な資金計画｜HOME INDEX PRO</title>
<style>
 :root{--bg:#f5f7fa;--card:#fff;--ink:#1f2937;--sub:#6b7280;--acc:#111111;--line:#e5e5e5}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN",Meiryo,sans-serif}
 .wrap{max-width:760px;margin:0 auto;padding:20px 16px}
 h1{font-size:21px;margin:8px 0 2px} .lead{color:var(--sub);margin:0 0 16px;font-size:13.5px;line-height:1.8}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:16px}
 h2{font-size:14px;margin:0 0 4px;letter-spacing:.04em}
 .h2sub{font-size:12px;color:var(--sub);margin:0 0 10px}
 label{display:block;font-size:12.5px;color:var(--sub);margin:11px 0 4px}
 input,select{width:100%;padding:12px;border:1px solid var(--line);border-radius:9px;
  font-size:16px;font-family:inherit;background:#fff}
 .row{display:flex;gap:12px}.row>div{flex:1;min-width:0}
 .hint{font-size:11.5px;color:var(--sub);margin-top:3px;line-height:1.7}
 button{margin-top:18px;width:100%;padding:15px;background:var(--acc);color:#fff;border:0;
  border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;min-height:50px}
 button:hover{background:#333}
 .tag{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.06em;
  background:#e5e5e5;color:#111;border-radius:999px;padding:3px 9px;margin-left:6px}
 BRAND_CSS_PLACEHOLDER
 @media (max-width:560px){.row{flex-direction:column;gap:0}}
</style></head><body>
BRAND_BAR
<div class="wrap">
 <h1>詳細な資金計画<span class="tag">PRO</span></h1>
 <p style="background:#fafafa;border:1px solid #e5e5e5;border-radius:10px;padding:12px 14px;font-size:14px;line-height:1.8;margin:12px 0"><b>試験公開中です。</b>現在は無料でお使いいただけますが、将来は有料（月額）になる予定です。会員登録はまだ不要です。</p>
 <p class="lead">購入にかかる諸費用、金利が上がったときの返済額、繰上返済の効果、住宅ローン控除の見込みを、
  公的な税率と料率にもとづいて試算します。<b>物件の価格を評価するものではありません。</b></p>
 {% if banner %}<p style="background:#eef4fa;border:1px solid #cddcea;border-radius:10px;padding:12px 14px;font-size:14px;line-height:1.8;margin:12px 0">{{banner|safe}}</p>{% endif %}

 <form method="post" action="/pro/finance">
  <input type="hidden" name="dx" value="{{v.dx}}">
  <div class="card">
   <h2>物件と価格</h2>
   <p class="h2sub">金額はすべて万円で入力してください</p>
   <div class="row">
    <div><label>売買価格（万円・必須）</label>
     <input name="price" id="price" value="{{v.price}}" placeholder="例）3480" required></div>
    <div><label>種別</label>
     <select name="newbuild" id="newbuild">
      <option value="0" {{'selected' if not v.newbuild else ''}}>中古</option>
      <option value="1" {{'selected' if v.newbuild else ''}}>新築建売</option>
     </select>
     <div class="hint">新築のときだけ表題登記・保存登記を計上します</div></div>
   </div>
   <div id="ratio_block">
    <label>土地の割合（％）</label>
    <input name="land_ratio" id="land_ratio" value="{{v.land_ratio}}" placeholder="60">
    <div class="hint">売買価格を土地と建物に振り分ける割合です。下の内訳が自動で入ります。
     実際の内訳が分かる場合は、下の欄を直接書き換えてください。<br>
     <b>【築{{old_years}}年以上の建物については{{old_ratio}}％以下推奨】</b></div>
   </div>
   <div id="newbuild_block" class="hint" style="display:none;margin-top:11px">
    新築建売は建物を一律 <b>{{nb_building}}万円</b> とし、売買価格から差し引いた額を土地価格とします。
    実際の内訳が分かる場合は、下の欄を直接書き換えてください。</div>
   <div class="row">
    <div><label>うち土地価格（万円）</label>
     <input name="land_price" id="land_price" value="{{v.land_price}}" placeholder="自動計算"></div>
    <div><label>うち建物価格（万円）</label>
     <input name="building_price" id="building_price" value="{{v.building_price}}" placeholder="自動計算"></div>
   </div>
   <div class="hint">内訳は登録免許税と不動産取得税の計算に使います。割合による推定であることは根拠欄に明記されます。</div>
   <div class="row">
    <div><label>土地の固定資産税評価額（万円・任意）</label>
     <input name="land_assessed" value="{{v.land_assessed}}" placeholder="分かれば入力"></div>
    <div><label>建物の固定資産税評価額（万円・任意）</label>
     <input name="building_assessed" value="{{v.building_assessed}}" placeholder="分かれば入力"></div>
   </div>
   <div class="hint">課税明細書があれば入力してください。未入力なら上の内訳から推定し、その旨を根拠に明記します。</div>
   <div class="row">
    <div><label>土地面積（㎡）</label>
     <input name="land_area" value="{{v.land_area}}" placeholder="例）120"></div>
    <div><label>建物の床面積（㎡）</label>
     <input name="floor_area" value="{{v.floor_area}}" placeholder="例）95"></div>
   </div>
  </div>

  <div class="card">
   <h2>建物の新築時期</h2>
   <p class="h2sub">不動産取得税の控除額が新築時期で変わります</p>
   <div class="row">
    <div><label>新築年（西暦）</label><input name="byear" value="{{v.byear}}" placeholder="例）2010"></div>
    <div><label>月（任意）</label><input name="bmonth" value="{{v.bmonth}}" placeholder="不明なら空欄"></div>
    <div><label>日（任意）</label><input name="bday" value="{{v.bday}}" placeholder="不明なら空欄"></div>
   </div>
   <div class="hint">月日が不明なら空欄で構いません。その場合は<b>不利側（控除額の小さい方）</b>で試算します。</div>
   <label>耐震基準への適合</label>
   <select name="quake">
    <option value="yes" {{'selected' if v.quake=='yes' else ''}}>適合（1982年以降の新築、または適合証明あり）</option>
    <option value="no" {{'selected' if v.quake=='no' else ''}}>不適合（取得後に耐震改修する）</option>
    <option value="unknown" {{'selected' if v.quake=='unknown' else ''}}>不明</option>
   </select>
  </div>

  <div class="card">
   <h2>借入とご自身の条件</h2>
   <div class="row">
    <div><label>頭金（万円）</label><input name="down" value="{{v.down}}" placeholder="0"></div>
    <div><label>世帯年収（万円）</label><input name="income" value="{{v.income}}" placeholder="例）800"></div>
   </div>
   <div class="row">
    <div><label>借入年数（年）</label><input name="loan_years" value="{{v.loan_years}}" placeholder="35"></div>
    <div><label>金利（％）</label><input name="rate" value="{{v.rate}}" placeholder="1.25"></div>
   </div>
   <div class="row">
    <div><label>地震保険</label>
     <select name="quake_ins">
      <option value="0" {{'selected' if not v.quake_ins else ''}}>付けない</option>
      <option value="1" {{'selected' if v.quake_ins else ''}}>付ける</option>
     </select></div>
    <div><label>オプション費用（カーテン・照明・外構など）</label>
     <select name="option_cost">
      <option value="0" {{'selected' if not v.option_cost else ''}}>計上しない</option>
      <option value="1" {{'selected' if v.option_cost else ''}}>計上する</option>
     </select></div>
   </div>
  </div>

  <div class="card">
   <h2>住宅ローン控除</h2>
   <p class="h2sub">住宅の省エネ性能によって借入限度額と控除期間が変わります</p>
   <label>住宅の区分</label>
   <select name="deduction_cat">
    {% for c in categories %}<option value="{{c}}" {{'selected' if v.deduction_cat==c else ''}}>{{c}}</option>{% endfor %}
   </select>
   <div class="hint">性能の証明書が無い一般的な中古住宅は「その他」です。</div>
   <div class="row">
    <div><label>買取再販住宅</label>
     <select name="resale">
      <option value="0" {{'selected' if not v.resale else ''}}>いいえ</option>
      <option value="1" {{'selected' if v.resale else ''}}>はい（宅建業者がリフォームして販売）</option>
     </select></div>
    <div><label>子育て世帯・若者夫婦世帯</label>
     <select name="kosodate">
      <option value="0" {{'selected' if not v.kosodate else ''}}>いいえ</option>
      <option value="1" {{'selected' if v.kosodate else ''}}>はい</option>
     </select>
     <div class="hint">19歳未満の扶養親族がいる、または夫婦のいずれかが40歳未満</div></div>
   </div>
  </div>

  <div class="card">
   <h2>繰上返済のシミュレーション（任意）</h2>
   <div class="row">
    <div><label>繰上返済額（万円）</label><input name="prepay" value="{{v.prepay}}" placeholder="使わないなら空欄"></div>
    <div><label>何年後に返済するか</label><input name="prepay_after" value="{{v.prepay_after}}" placeholder="10"></div>
    <div><label>方式</label>
     <select name="prepay_kind">
      <option value="期間短縮型" {{'selected' if v.prepay_kind=='期間短縮型' else ''}}>期間短縮型</option>
      <option value="返済額軽減型" {{'selected' if v.prepay_kind=='返済額軽減型' else ''}}>返済額軽減型</option>
     </select></div>
   </div>
  </div>

  <button type="submit">資金計画を試算する</button>
 </form>
<script>
(function(){
  var price = document.getElementById('price');
  var ratio = document.getElementById('land_ratio');
  var land  = document.getElementById('land_price');
  var bldg  = document.getElementById('building_price');
  var nb    = document.getElementById('newbuild');
  var rblk  = document.getElementById('ratio_block');
  var nblk  = document.getElementById('newbuild_block');
  var NB_BUILDING = {{nb_building}};
  if(!price || !ratio || !land || !bldg || !nb) return;
  var manual = false;
  function num(el){ var n = parseFloat((el.value||'').replace(/,/g,'')); return isNaN(n) ? null : n; }
  function isNew(){ return nb.value === '1'; }
  function toggle(){
    if(rblk) rblk.style.display = isNew() ? 'none' : '';
    if(nblk) nblk.style.display = isNew() ? '' : 'none';
  }
  function split(){
    if(manual) return;
    var p = num(price);
    if(p === null){ return; }
    if(isNew()){
      var b = Math.min(NB_BUILDING, p);
      bldg.value = Math.round(b);
      land.value = Math.round(Math.max(0, p - b));
      return;
    }
    var r = num(ratio);
    if(r === null){ return; }
    var l = Math.round(p * r / 100);
    land.value = l;
    bldg.value = Math.round(p - l);
  }
  price.addEventListener('input', split);
  ratio.addEventListener('input', split);
  nb.addEventListener('change', function(){ toggle(); split(); });
  function markManual(){ manual = true; }
  land.addEventListener('input', markManual);
  bldg.addEventListener('input', markManual);
  toggle();
})();
</script>
 <p class="hint" style="text-align:center;margin-top:12px">
  ※ 税率・料率は公的資料にもとづきますが、試算結果は目安です。実際の金額は金融機関・仲介会社・所管の税務署および都道府県にご確認ください。</p>
</div></body></html>
"""

PRO_FINANCE_RESULT = """
<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
FONT_LINK_PLACEHOLDER
<title>資金計画の結果｜HOME INDEX PRO</title>
<style>
 :root{--bg:#f5f7fa;--card:#fff;--ink:#1f2937;--sub:#6b7280;--acc:#111111;--line:#e5e5e5}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN",Meiryo,sans-serif}
 .wrap{max-width:760px;margin:0 auto;padding:20px 16px}
 a.back{color:var(--acc);text-decoration:none;font-size:13.5px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin-top:16px}
 h1{font-size:20px;margin:10px 0 2px}
 h2{font-size:15px;margin:0 0 10px}
 .sub{color:var(--sub);font-size:13px;margin:0 0 8px}
 .big{font-size:26px;font-weight:700;margin:6px 0}
 .kv{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}
 .kv:last-child{border-bottom:0}
 .kv b{font-weight:700}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
 th{color:var(--sub);font-weight:600;white-space:nowrap}
 td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
 .basis{font-size:11.5px;color:var(--sub);line-height:1.7;margin-top:2px}
 .st{display:inline-block;font-size:10px;border-radius:5px;padding:2px 6px;white-space:nowrap}
 .st-computed{background:#e8f0e8;color:#2f5233}
 .st-estimated{background:#eef2f7;color:#3f4a5a}
 .st-unknown{background:#f3f4f6;color:#6b7280}
 .foot{color:var(--sub);font-size:11.5px;line-height:1.9;margin-top:10px}
 .warn{background:#fafafa;border:1px solid var(--line);border-radius:10px;padding:12px 14px;
  font-size:12px;color:var(--sub);line-height:1.85}
 .tablewrap{overflow-x:auto}
 button{width:100%;padding:15px;background:var(--acc);color:#fff;border:0;
  border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;min-height:50px;
  font-family:inherit}
 button:hover{background:#333}
 BRAND_CSS_PLACEHOLDER
 .backform{margin:0}
 button.backbtn{width:auto;margin:0;padding:0;border:none;background:none;
   color:var(--acc);font-size:14px;font-family:inherit;cursor:pointer}
 button.backbtn:hover{text-decoration:underline}
 @media (max-width:560px){.wrap{padding:16px 12px}.card{padding:16px}table{font-size:12px}}
</style></head><body>
BRAND_BAR
<div class="wrap">
 <form method="post" action="/pro/finance_start" class="backform">
  {% for k, val in form.items() %}<input type="hidden" name="{{k}}" value="{{val}}">{% endfor %}
  <input type="hidden" name="edit" value="1">
  <button type="submit" class="backbtn">← 条件を変えて試算する</button>
 </form>

 <form method="post" action="/pro/finance.pdf" class="card" style="text-align:center">
  {% for k, val in form.items() %}<input type="hidden" name="{{k}}" value="{{val}}">{% endfor %}
  <button type="submit" style="margin-top:0">PDFレポートを保存</button>
  <p class="sub" style="margin:8px 0 0">
   この試算結果を1つのPDFにまとめます。住宅ローンの相談や家族との共有にお使いください。{% if form.dx %}<br>
   <b>診断の結果から進んだので、点数・カテゴリ別の評価・重大リスク・仲介業者に聞くことも同じPDFに入ります。</b>{% endif %}</p>
 </form>

 <div class="card">
  <h2>資金の全体像</h2>
  <div class="kv"><span>物件価格</span><b>{{s.price}}</b></div>
  <div class="kv"><span>諸費用（判明分）</span><b>{{s.costs}}</b></div>
  <div class="kv"><span>必要総額</span><b>{{s.total}}</b></div>
  <div class="kv"><span>頭金</span><b>{{s.down}}</b></div>
  <div class="kv"><span>借入額</span><b>{{s.principal}}</b></div>
  <p class="big">月々 約 {{s.monthly}}{% if s.burden %}<span class="sub" style="font-size:14px"> ／ 返済負担率 {{s.burden}}%</span>{% endif %}</p>
  <p class="sub">金利 {{s.rate}}％ ／ {{s.years}}年 ／ 元利均等返済</p>
  <div class="warn">借入額は<b>必要総額から頭金を差し引いた額</b>です（諸費用を借入に含める前提）。
   保証料と抵当権設定の登録免許税は借入額に比例するため、借入額と諸費用が釣り合うまで計算を繰り返しています。
   諸費用を現金で用意する場合は、その分を頭金に加えて入力してください。</div>
 </div>

 <div class="card">
  <h2>諸費用の内訳</h2>
  <div class="tablewrap">
  <table><tr><th>項目</th><th style="text-align:right">金額</th><th>区分</th></tr>
  {% for c in costs %}
   <tr><td>{{c.name}}<div class="basis">{{c.basis}}</div></td>
    <td class="num">{{c.amount}}</td>
    <td><span class="st st-{{c.status}}">{{c.status_ja}}</span></td></tr>
  {% endfor %}
  </table></div>
  {% if reg_total %}<p class="sub" style="margin-top:10px">登記費用の小計（登録免許税＋司法書士報酬{% if s.newbuild %}＋表題・保存登記{% endif %}）：<b>{{reg_total}}</b></p>{% endif %}
  {% if unknown %}<div class="warn" style="margin-top:10px"><b>算出していない項目</b>：{{unknown}}<br>
   情報が足りないため金額を出していません。合計にも含めていません。</div>{% endif %}
 </div>

 <div class="card">
  <h2>金利が上がったら</h2>
  <p class="sub">将来の予測ではなく、その金利になった場合の返済額です。</p>
  <div class="tablewrap">
  <table><tr><th>金利</th><th style="text-align:right">月々</th><th style="text-align:right">現在との差</th><th style="text-align:right">総返済額</th></tr>
  {% for r in scenarios %}<tr><td>{{r.label}}（{{r.rate}}％）</td><td class="num">{{r.monthly}}</td>
   <td class="num">{{r.diff}}</td><td class="num">{{r.total}}</td></tr>{% endfor %}
  </table></div>
 </div>

 {% if prepay %}
 <div class="card">
  <h2>繰上返済の効果</h2>
  <p class="sub">{{prepay.after}}年後に {{prepay.amount}} を繰上返済した場合（{{prepay.kind}}）</p>
  {% if prepay.months_saved %}<div class="kv"><span>返済期間の短縮</span><b>{{prepay.months_saved}}</b></div>{% endif %}
  <div class="kv"><span>軽減される利息</span><b>{{prepay.interest_saved}}</b></div>
  <div class="kv"><span>繰上返済後の月々</span><b>{{prepay.new_monthly}}</b></div>
 </div>
 {% endif %}

 <div class="card">
  <h2>住宅ローン控除</h2>
  {% if deduction.ok %}
   <p class="sub">{{deduction.basis}}</p>
   <p class="big">最大 {{deduction.total}}</p>
   <div class="kv"><span>借入限度額</span><b>{{deduction.limit}}</b></div>
   <div class="kv"><span>控除期間</span><b>{{deduction.years}}年</b></div>
   <div class="tablewrap" style="margin-top:10px">
   <table><tr><th>年目</th>{% for y in deduction.yearly %}<th style="text-align:right">{{loop.index}}</th>{% endfor %}</tr>
    <tr><td>控除額</td>{% for y in deduction.yearly %}<td class="num">{{y}}</td>{% endfor %}</tr></table></div>
  {% else %}
   <p class="sub">{{deduction.basis}}</p>
  {% endif %}
  {% for n in deduction.notes %}<p class="foot">・{{n}}</p>{% endfor %}
 </div>

 {% if afford %}
 <div class="card">
  <h2>年収からみた借入の目安</h2>
  <div class="kv"><span>返済負担率の上限</span><b>{{afford.limit}}％</b></div>
  <div class="kv"><span>月々返済の上限</span><b>{{afford.monthly}}</b></div>
  <div class="kv"><span>借入可能額</span><b>{{afford.principal}}</b></div>
  <div class="kv"><span>頭金を加えた購入可能額</span><b>{{afford.price}}</b></div>
  <p class="foot">{{afford.note}}</p>
 </div>
 {% endif %}

 <div class="card">
  <h2>この試算の根拠</h2>
  {% for s in sources %}<p class="foot">・{{s}}</p>{% endfor %}
  <div class="warn" style="margin-top:10px">
   税率・料率は取得時点の公的資料にもとづきます。法改正で変わること、また不動産取得税の税率は
   <b>標準税率</b>であり都道府県の条例で異なり得ることにご注意ください。
   実際の金額は金融機関・仲介会社・所管の税務署および都道府県にご確認ください。
   本試算は物件の価格を評価するものではありません。
  </div>
 </div>
</div></body></html>
"""

PRO_FINANCE_FORM = (PRO_FINANCE_FORM
                    .replace("BRAND_CSS_PLACEHOLDER", BRAND_CSS)
                    .replace("FONT_LINK_PLACEHOLDER", FONT_LINK + ICON_LINKS)
                    .replace("BRAND_BAR", brand_bar("PRO"))
                    .replace("</div></body></html>",
                             FOOTER + "</div></body></html>"))
PRO_FINANCE_RESULT = (PRO_FINANCE_RESULT
                      .replace("BRAND_CSS_PLACEHOLDER", BRAND_CSS)
                      .replace("FONT_LINK_PLACEHOLDER", FONT_LINK + ICON_LINKS)
                      .replace("BRAND_BAR", brand_bar("PRO"))
                      .replace("</div></body></html>", FOOTER + "</div></body></html>"))

_STATUS_JA = {"computed": "計算", "estimated": "推定", "unknown": "未確認"}


def _pro_defaults():
    """フォームの初期値。数値は空にし、目安はプレースホルダで示す。

    土地の割合だけは設定値（finance_config.json）を初期表示する。
    これは物件データではなく按分の設定のため。
    """
    from src.finance import FCONFIG
    ratio = FCONFIG.get("price_split", {}).get("land_ratio")
    return dict(price="", newbuild=False, land_price="", building_price="",
                land_assessed="", building_assessed="",
                land_ratio=(f"{ratio * 100:.0f}" if ratio else ""),
                land_area="", floor_area="",
                byear="", bmonth="", bday="", quake="yes",
                down="", income="", loan_years="", rate="",
                dx="",   # 診断からの引き継ぎ（署名済み）。無ければ空
                quake_ins=False, option_cost=False,
                deduction_cat="その他", resale=False, kosodate=False,
                prepay="", prepay_after="", prepay_kind="期間短縮型")


# ---- PROの入口 --------------------------------------------------------
# PROは長らく3つのフォームがフッターに並んでいるだけで、/pro は404だった。
# 申し込んだ人が最初に着く場所が無く、何が使えるのかを説明する場所も
# 無かった。ここがその場所になる。

_PRO_HUB_BODY = ("""
<p class="sub">PROは、無料診断が「未確認」として点数に入れていない項目を、
ご自身の回答で埋めるためのものです。<b>試験公開中で、いまは無料で使えます。</b></p>

<h2>1. 購入診断（PRO）</h2>
<p>建物内部の状態、設備の更新時期、リフォームした箇所、接道と再建築の可否、
マンションなら大規模修繕の履歴や管理形態。無料診断では未確認としていた項目に
答えると、その分だけ評価に反映され、情報充足度が上がります。</p>
<p>答えられなかった項目は消えるのではなく、
<b>「仲介業者に確認すること」の一覧</b>になって出てきます。</p>
<p><a href="/pro/diagnose">戸建で始める</a>　/
 <a href="/pro/mansion">マンションで始める</a></p>

<h2>2. 詳細な資金計画</h2>
<p>仲介手数料・印紙税・登録免許税・不動産取得税・司法書士報酬・火災保険を
積み上げ、金利が上がったときの返済額、繰上返済の効果、住宅ローン控除の
見込みまで試算します。結果はPDFで保存できます。</p>
<p><a href="/pro/finance">資金計画を試算する</a></p>
<p class="sub">購入診断の結果画面から進むと、価格・面積・築年・借入の条件は
そのまま引き継がれます。入力し直す必要はありません。</p>
""" + ("""
<h2>3. 保存と比較</h2>
<p>診断の結果を保存しておくと、あとから見返せます。複数の物件を保存すれば、
点数・カテゴリ別の評価・リスクを横並びで比べられます。物件ごとにメモを
残すこともできます。</p>
<p><a href="/mypage">保存した診断</a>　/　<a href="/compare">物件を比べる</a></p>
""" if db.enabled() else "") + """
<h2>推定価格の計算は、無料版と同じです</h2>
<p>PROで入力していただく内容は、<b>物件の評価とリスクにだけ</b>反映します。
推定価格レンジの計算には渡していません。PROは点数の確からしさを上げるもので、
価格を動かすものではありません。同じ物件なら、無料でもPROでも推定価格は
同じ数字になります。</p>
""")


@app.route("/pro")
def pro_hub():
    return _legal_page("PRO", _PRO_HUB_BODY)


def _finance_tmpl_kw():
    """資金計画フォームに渡す設定値。設定ファイルから毎回読み直す。"""
    from src.finance import FCONFIG
    cats = list(FCONFIG.get("loan_deduction", {}).get("existing", {}).keys())
    cats = [c for c in cats if not c.startswith("_")]
    ps = FCONFIG.get("price_split", {})
    return dict(
        categories=cats,
        nb_building=int((ps.get("new_build_building_price") or 0) / 10000),
        old_years=ps.get("old_building_hint_years", 30),
        old_ratio=int((ps.get("old_building_hint_ratio") or 0) * 100))


@app.route("/pro/finance", methods=["GET", "POST"])
def pro_finance():
    if request.method == "GET":
        return render_template_string(PRO_FINANCE_FORM, v=_pro_defaults(),
                                      **_finance_tmpl_kw())
    return render_template_string(PRO_FINANCE_RESULT, **_pro_compute(request.form))


@app.route("/pro/finance_start", methods=["POST"])
def pro_finance_start():
    """診断の結果から資金計画へ、入力を引き継いで開く。

    年収と住所を含むので、クエリ文字列ではなくPOSTで受ける
    （URLに残さない・アクセスログに出さない）。/pro/start と同じ理由。
    """
    f = request.form
    v = _pro_defaults()
    for k in v:
        if k == "newbuild":
            continue
        if f.get(k):
            v[k] = f.get(k)
    v["newbuild"] = (f.get("newbuild") == "1")
    return render_template_string(
        PRO_FINANCE_FORM, v=v, **_finance_tmpl_kw(),
        banner=(_EDIT_BANNER if f.get("edit") else
                "<b>診断の入力を引き継ぎました。</b>"
                "価格・面積・築年・借入の条件は入っています。"
                "土地と建物の按分や、繰上返済の条件を足すと精度が上がります。"))


def _pro_compute(f):
    """フォーム値から試算結果のコンテキストを作る。HTMLとPDFで共用する。"""
    from src.finance import (purchase_costs, registration_cost_total,
                             rate_scenarios, prepayment, loan_deduction,
                             affordable_loan, man_yen, FCONFIG)
    from src.loan import compute_loan

    v = {k: (f.get(k) or "") for k in _pro_defaults()}
    v["newbuild"] = (f.get("newbuild") == "1")
    v["quake_ins"] = (f.get("quake_ins") == "1")
    v["option_cost"] = (f.get("option_cost") == "1")
    v["resale"] = (f.get("resale") == "1")
    v["kosodate"] = (f.get("kosodate") == "1")

    price = to_yen(f.get("price")) or 0
    down = to_yen(f.get("down")) or 0
    income = to_yen(f.get("income"))
    years = max(1, min(50, to_int(f.get("loan_years")) or 35))
    rate = (to_float(f.get("rate")) or 1.25) / 100.0
    quake_map = {"yes": True, "no": False, "unknown": None}

    # 内訳が空なら土地の割合で按分する（JSが動かない場合の保険）
    land_price = to_yen(f.get("land_price"))
    building_price = to_yen(f.get("building_price"))
    if land_price is None and building_price is None and price:
        ps = FCONFIG.get("price_split", {})
        if v["newbuild"]:
            # 新築建売は建物を定額とし、残りを土地とする
            fixed = ps.get("new_build_building_price")
            if fixed:
                building_price = min(int(fixed), price)
                land_price = max(0, price - building_price) or None
        else:
            pct = to_float(f.get("land_ratio"))
            if pct is None:
                pct = (ps.get("land_ratio") or 0) * 100
            if pct:
                land_price = int(round(price * pct / 100.0))
                building_price = price - land_price

    def _costs(principal):
        return purchase_costs(
            price,
            land_price=land_price,
            building_price=building_price,
            loan_amount=principal or None,
            land_assessed=to_yen(f.get("land_assessed")),
            building_assessed=to_yen(f.get("building_assessed")),
            land_area_m2=to_float(f.get("land_area")),
            floor_area_m2=to_float(f.get("floor_area")),
            build_year=to_int(f.get("byear")),
            build_month=to_int(f.get("bmonth")),
            build_day=to_int(f.get("bday")),
            quake_conforming=quake_map.get(f.get("quake"), None),
            earthquake_insurance=v["quake_ins"],
            new_build=v["newbuild"],
            option_cost=v["option_cost"])

    # 借入額＝必要総額−頭金。ただし諸費用のうち保証料と抵当権の登録免許税は
    # 借入額に比例するため、借入額と諸費用が相互に依存する。反復して収束させる。
    principal = max(0, price - down)
    for _ in range(8):
        costs = _costs(principal)
        nxt = max(0, price + costs.total - down)
        if nxt == principal:
            break
        principal = nxt
    costs = _costs(principal)

    L = compute_loan(price + costs.total, down, rate, years, income)
    sctx = dict(price=man_yen(price), costs=man_yen(costs.total),
                total=man_yen(price + costs.total), down=man_yen(down),
                principal=man_yen(principal), monthly=f"{L.monthly_payment:,}円",
                burden=L.burden_ratio, rate=f.get("rate") or "1.25",
                years=years, newbuild=v["newbuild"])

    cctx = [dict(name=c.name, amount=man_yen(c.amount),
                 basis=c.basis, status=c.status,
                 status_ja=_STATUS_JA.get(c.status, c.status))
            for c in costs.items]
    reg = registration_cost_total(costs)

    scen = [dict(label=s.label, rate=f"{s.annual_rate*100:.2f}",
                 monthly=f"{s.monthly:,}円",
                 diff=("—" if s.diff_monthly == 0 else f"{s.diff_monthly:+,}円"),
                 total=man_yen(s.total))
            for s in rate_scenarios(principal, years, rate)]

    pctx = None
    pre_yen = to_yen(f.get("prepay"))
    if pre_yen and principal:
        after_y = max(1, to_int(f.get("prepay_after")) or 10)
        pr = prepayment(principal, rate, years, pre_yen, after_y * 12,
                        f.get("prepay_kind") or "期間短縮型")
        pctx = dict(after=after_y, amount=man_yen(pre_yen), kind=pr.kind,
                    months_saved=(f"{pr.months_saved // 12}年{pr.months_saved % 12}ヶ月"
                                  if pr.months_saved else None),
                    interest_saved=man_yen(pr.interest_saved),
                    new_monthly=f"{pr.new_monthly:,}円")

    d = loan_deduction(principal, rate, years,
                       category=f.get("deduction_cat") or "その他",
                       is_resale=v["resale"], is_kosodate=v["kosodate"],
                       annual_income=income,
                       floor_area_m2=to_float(f.get("floor_area")),
                       build_year=to_int(f.get("byear")))
    dctx = dict(ok=(d.total > 0), basis=d.basis, total=man_yen(d.total),
                limit=man_yen(d.limit), years=d.years,
                yearly=[man_yen(y) for y in d.yearly], notes=d.notes)

    actx = None
    if income:
        a = affordable_loan(income, rate, years, down)
        actx = dict(limit=f"{a.burden_limit:.0f}", monthly=f"{a.max_monthly:,}円",
                    principal=man_yen(a.max_principal), price=man_yen(a.max_price),
                    note=a.note)

    seen, sources = set(), []
    for c in costs.items:
        if c.source and c.source not in seen:
            seen.add(c.source)
            sources.append(c.source)
    if d.source and d.source not in seen:
        sources.append(d.source)

    return dict(
        s=sctx, costs=cctx, reg_total=(man_yen(reg) if reg else None),
        unknown="・".join(costs.unknown_items) if costs.unknown_items else None,
        scenarios=scen, prepay=pctx, deduction=dctx, afford=actx, sources=sources,
        form={k: (f.get(k) or "") for k in _pro_defaults()})


@app.route("/pro/finance.pdf", methods=["POST"])
def pro_finance_pdf():
    """試算結果をPDFレポートとして返す。計算はHTML版と同じ経路を使う。"""
    from flask import Response
    from src.report import build_finance_pdf
    ctx = _pro_compute(request.form)
    # 署名が合わないもの・無いものは黙って落とす。診断の節が出ないだけで、
    # 資金計画のPDFとしては成立する。
    ctx["diag"] = _unsign_snapshot(request.form.get("dx") or "")
    pdf = build_finance_pdf(ctx)
    quoted = urllib.parse.quote("HOME INDEX_資金計画.pdf")
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": ("attachment; filename=\"finance-plan.pdf\"; "
                                f"filename*=UTF-8''{quoted}")})


# ローカル起動のポート。5000が別のプロセスに使われているときは
# 環境変数 PORT で変えられる（例: $env:PORT=5001; python app.py）。
LOCAL_PORT = int(os.environ.get("PORT", "5000"))


def open_browser():
    webbrowser.open(f"http://127.0.0.1:{LOCAL_PORT}")


if __name__ == "__main__":
    threading.Timer(1.3, open_browser).start()
    print(f"ブラウザで http://127.0.0.1:{LOCAL_PORT} を開いてください（自動で開きます）")
    print("停止するには Ctrl+C を押してください")
    app.run(host="127.0.0.1", port=LOCAL_PORT, debug=False)
