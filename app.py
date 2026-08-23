# -*- coding: utf-8 -*-
"""簡易Web診断アプリ。

ブラウザのフォームに入力→エンジン(run_pipeline)で診断→結果を表示。
実データ(reinfolib/GSI)を使うため、ユーザーのPC上で起動して
http://127.0.0.1:5000 を開いて使う。金額は万円で入力。
"""
import os
import sys
import time
import threading
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, request, render_template_string  # noqa: E402
from src.models import SubjectProperty, MansionSubject  # noqa: E402
from src.pipeline import run_pipeline, run_mansion_pipeline  # noqa: E402
from src.extract import parse_listing_text, extract_from_url  # noqa: E402
from src.citycode import CityCodeResolver  # noqa: E402

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


_load_dotenv()

app = Flask(__name__)

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


MENU_ITEMS = [("/", "トップ"),
              ("/buy", "購入診断（戸建）"),
              ("/mansion", "購入診断（マンション）"),
              ("/pro/finance", "詳細な資金計画（PRO）"),
              ("/terms", "利用規約"),
              ("/privacy", "プライバシーポリシー")]


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
OPERATOR = os.environ.get("OPERATOR_NAME", "〔運営者名〕")
CONTACT = os.environ.get("CONTACT_EMAIL", "〔連絡先メール〕")

FOOTER = ('<div style="text-align:center;margin-top:16px;font-size:12px;color:#6b7280;line-height:1.9">'
          '<a href="/terms" style="color:#111">利用規約</a>　・　'
          '<a href="/privacy" style="color:#111">プライバシーポリシー</a><br>'
          '出典：国土交通省 不動産情報ライブラリ／総務省 e-Stat／国土地理院／Google<br>'
          '買い物施設：© OpenStreetMap contributors（ODbL）<br>'
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
            f'{FONT_LINK}'
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
  <textarea name="listing" placeholder="例）中古一戸建て 神奈川県小田原市城山4-20-18 価格3,880万円 土地面積147.07㎡ 建物面積90.47㎡ 4LDK 築2005年 小田原駅 徒歩20分">{{listing or ''}}</textarea>
  <button class="sub" type="submit">貼り付けから自動入力する</button>
  <div class="hint">※ <b>URLではなく、物件ページの文章</b>（価格・所在地・面積・築年・駅など）を選択してコピーしてください。ご自身がコピーした情報を解析します（私的利用）。抽出後、下で確認・修正できます。</div></form>

  <form class="card" method="post" action="/upload_pdf" enctype="multipart/form-data">
    <label>① 販売図面PDFから読み取る（文字が選択できるPDF）</label>
    <input type="file" name="pdf" accept="application/pdf">
    <button class="sub" type="submit">PDFから自動入力する</button>
    <div class="hint">※ スキャン（画像）のPDFは読み取れません。文字がコピーできるPDFをお使いください。</div>
 </form>

 <form class="card" method="post" action="/diagnose">
  <label>② 内容を確認・修正して診断</label>
  <label>物件の所在地</label>
  <input name="address" value="{{v.address}}" required>
  <div class="row">
   <div><label>売出価格（万円）</label><input name="price" value="{{v.price}}" required></div>
   <div><label>築年（西暦）</label><input name="byear" value="{{v.byear}}"></div>
  </div>
  <div class="row">
   <div><label>土地面積（㎡）</label><input name="land" value="{{v.land}}"></div>
   <div><label>建物面積（㎡）</label><input name="building" value="{{v.building}}"></div>
  </div>
  <div class="row">
   <div><label>市区町村コード</label><input name="city" value="{{v.city}}"></div>
   <div><label>町名</label><input name="district" value="{{v.district}}"></div>
  </div>
  <div class="row">
   <div><label>駅/バス停まで徒歩（分）</label><input name="station" value="{{v.station}}"></div>
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
   <div><label>世帯年収（万円・任意）</label><input name="income" value="{{v.income}}"></div>
   <div><label>頭金（万円・任意）</label><input name="down" value="{{v.down}}"></div>
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
    return dict(address="神奈川県小田原市城山4-20-18", price="3880", byear="2005",
                land="147.07", building="90.47", city="14206", district="城山",
                station="20", bus="", ptype="chuko_kodate", income="800", down="500",
                reno=False, loan_years="35")


def _v_from_parsed(p):
    def s(x):
        return "" if x is None else str(x)
    return dict(address=s(p.get("address")), price=s(p.get("price_man")),
                byear=s(p.get("byear")), land=s(p.get("land")),
                building=s(p.get("building")), city=s(p.get("city")),
                district=s(p.get("district")), station=s(p.get("station")),
                bus=s(p.get("bus")),
                ptype=p.get("ptype") or "chuko_kodate", income="", down="",
                reno=bool(p.get("renovated")), loan_years="35")


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
 @media (max-width:560px){
  .wrap{padding:16px 12px} h1{font-size:18px} .card{padding:16px}
  .score{font-size:44px} .gletter{font-size:48px}
  .ring{width:112px;height:112px} .ring svg{width:112px;height:112px}
  table{font-size:12px} th,td{padding:6px 5px;white-space:nowrap}
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
  <div class="hero-score">
   <div class="ring">
    <svg viewBox="0 0 132 132" width="132" height="132">
     <circle cx="66" cy="66" r="58" fill="none" stroke="#e8eef2" stroke-width="12"/>
     <circle cx="66" cy="66" r="58" fill="none" stroke="{{grade_color}}" stroke-width="12"
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
</script>
</div></body></html>
"""

# ブランドのCSS/ヘッダー・フッターをテンプレートへ差し込む
FORM = (FORM.replace("BRAND_CSS_PLACEHOLDER", BRAND_CSS)
        .replace("FONT_LINK_PLACEHOLDER", FONT_LINK)
        .replace("BRAND_BAR", brand_bar())
        .replace("</div></body></html>", FOOTER + "</div></body></html>"))
RESULT = (RESULT.replace("BRAND_CSS_PLACEHOLDER", BRAND_CSS)
          .replace("FONT_LINK_PLACEHOLDER", FONT_LINK)
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
<link rel="canonical" href="CANONICAL_URL">
<meta property="og:type" content="website">
<meta property="og:site_name" content="HOME INDEX">
<meta property="og:title" content="この家、かっていい？｜中古戸建を無料で100点診断 HOME INDEX">
<meta property="og:description" content="気になる中古戸建の価格・災害リスク・住宅ローン返済を、国土交通省の成約データなど公的データから100点で採点します。会員登録不要・無料。物件は売りません。">
<meta property="og:url" content="CANONICAL_URL">
<meta name="twitter:card" content="summary">
LP_FONT_LINK_PLACEHOLDER

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
.pin-label{
  position:absolute; left:calc(72% + 16px); top:calc(34% - 9px); z-index:1;
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:10px; letter-spacing:.1em;
  color:var(--pin); white-space:nowrap; opacity:.9;
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

.sources{position:relative; z-index:2; border-top:1px solid rgba(240,238,233,.14); background:rgba(8,18,29,.5)}
.sources .wrap{padding:13px 20px; display:flex; flex-wrap:wrap; gap:5px 18px; align-items:baseline}
.sources span{font-size:12px; color:var(--paper-dim)}
.sources .k{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:10px; letter-spacing:.14em; color:var(--pin); opacity:.85}

/* ---------- セクション共通 ---------- */
section{padding:52px 0; scroll-margin-top:56px}
section + section{border-top:1px solid var(--line)}
section h2{
  font-family:"Zen Old Mincho",serif; font-weight:600;
  font-size:clamp(21px,5.2vw,29px); line-height:1.62; letter-spacing:.01em; margin:0 0 10px;
}
section .sub{color:var(--sub); font-size:14px; margin:0 0 26px; max-width:36em}

.rows{display:flex; flex-direction:column; border-top:1px solid var(--line)}
.rowitem{display:flex; flex-direction:column; gap:5px; padding:19px 0; border-bottom:1px solid var(--line)}
.rowitem .q{font-family:"Zen Kaku Gothic New",sans-serif; font-size:17px; font-weight:700; margin:0}
.rowitem .a{font-size:14px; color:var(--sub); margin:0}
.rowitem .tagline{
  font-family:"IBM Plex Mono",ui-monospace,monospace; margin:0;
  font-size:10.5px; letter-spacing:.14em; color:var(--spark);
}

/* ---------- 結果サンプル ---------- */
.sample{background:var(--surface); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow-lift); overflow:hidden}
.sample-head{display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 18px; border-bottom:1px solid var(--line); background:var(--surface-2)}
.sample-head .t{font-size:12px; color:var(--sub)}
.stamp{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:10px; letter-spacing:.14em; border:1px solid var(--line-strong); color:var(--sub); border-radius:4px; padding:2px 7px; white-space:nowrap}
.sample-body{padding:22px 18px}
.score{display:flex; align-items:flex-end; gap:12px; margin-bottom:6px}
.score .n{font-family:"Zen Kaku Gothic New",sans-serif; font-weight:900; font-size:60px; line-height:.92; letter-spacing:-.03em; font-variant-numeric:tabular-nums}
.score .d{font-size:14px; color:var(--sub); padding-bottom:9px}
.verdict{font-size:14px; font-weight:700; color:var(--good); margin:0 0 20px}
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

.reveal{opacity:0; transform:translateY(14px); transition:opacity .7s ease, transform .7s ease}
.reveal.is-in{opacity:1; transform:none}

@media (max-width:560px){
  .hero .wrap{padding-top:34px; padding-bottom:38px}
  .pin{left:78%; top:26%}
  .pin-label{left:auto; right:20px; top:calc(26% + 14px)}
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
    <a class="is-cta" href="/buy">無料で診断する（戸建）</a>
    <a class="is-cta" href="/mansion">無料で診断する（マンション）</a>
    <a href="/pro/finance">詳細な資金計画（PRO）</a>
    <a href="#wakaru">診断でわかること</a>
    <a href="#shikumi">仕組みと中立性</a>
    <a href="#faq">よくある質問</a>
    <a href="/terms">利用規約</a>
    <a href="/privacy">プライバシーポリシー</a>
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
    <p class="lead">気になっている物件の情報を貼り付けるだけ。<b>国土交通省の成約価格データ</b>と公的統計をもとに、価格・立地・災害リスク・返済までを<b>100点で採点</b>します。物件は売りません。売り込みもしません。</p>
    <div class="cta-row">
      <a class="btn btn-primary" href="/buy">
        無料で診断する（戸建）
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 8h11M9 4l4.2 4L9 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </a>
      <a class="btn btn-ghost" href="/mansion">マンションを診断する</a>
    </div>
    <p class="micro">会員登録なし ／ 料金なし ／ 入力から結果まで約3分</p>
  </div>
  <div class="sources">
    <div class="wrap">
      <span class="k">DATA SOURCE</span>
      <span>国土交通省 不動産情報ライブラリ</span>
      <span>総務省 e-Stat</span>
      <span>国土地理院</span>
      <span>OpenStreetMap</span>
    </div>
  </div>
</header>

<main>
  <section>
    <div class="wrap reveal">
      <p class="eyebrow">この3つが決められない</p>
      <h2>数千万円の買い物なのに、<br>判断材料は営業さんの言葉だけ。</h2>
      <p class="sub">内見して、気に入って、でも決め手がない。HOME INDEX は、その迷いを公的データで裏取りするためのツールです。</p>
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
      <h2>結果は、こう返ってきます。</h2>
      <p class="sub">総合点だけでなく、6つの観点それぞれの内訳と、その点数になった根拠（採点に使った成約事例の一覧）まで表示します。</p>

      <div class="sample">
        <div class="sample-head">
          <span class="t">中古戸建／神奈川県小田原市・築19年・3,880万円</span>
          <span class="stamp">SAMPLE</span>
        </div>
        <div class="sample-body">
          <div class="score">
            <span class="n">75</span>
            <span class="d">／ 100点</span>
          </div>
          <p class="verdict">総合判定：条件を詰めれば買ってよい水準</p>

          <div class="rangefig">
            <svg viewBox="0 0 620 92" role="img" aria-label="推定価格レンジ3,520万円から3,980万円に対し、提示価格3,880万円はレンジ内の上寄り。中央値は3,760万円。">
              <rect class="r-band" x="120" y="44" width="380" height="14" rx="7"/>
              <line class="r-axis" x1="30" y1="51" x2="590" y2="51" stroke-width="1"/>
              <line class="r-cap" x1="120" y1="38" x2="120" y2="64" stroke-width="1"/>
              <line class="r-cap" x1="500" y1="38" x2="500" y2="64" stroke-width="1"/>
              <line class="r-med" x1="322" y1="40" x2="322" y2="62" stroke-width="2" stroke-dasharray="3 3"/>
              <text class="r-lbl" x="322" y="80" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11">中央値 3,760</text>
              <text class="r-lbl" x="120" y="30" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11">3,520</text>
              <text class="r-lbl" x="500" y="30" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11">3,980</text>
              <circle class="r-dot" cx="440" cy="51" r="9"/>
              <circle class="r-ring" cx="440" cy="51" r="15" stroke-width="1" opacity=".45"/>
              <text class="r-dotlbl" x="440" y="22" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="12">提示 3,880万円</text>
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
              <div class="v good">3,520–3,980<span class="u">万円</span></div>
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

          <p class="sample-note">※ 表示はサンプルです。実際の結果には、採点に使った成約事例が1件ずつ（所在・面積・築年・成約価格・類似度）並びます。</p>
        </div>
      </div>
    </div>
  </section>

  <section>
    <div class="wrap reveal">
      <p class="eyebrow">使い方</p>
      <h2>入力は3分。物件ページのコピペから。</h2>
      <p class="sub">物件のURLではなく、ページに書かれている説明文をそのままコピーして貼り付けてください。</p>
      <div class="steps">
        <div class="step">
          <span class="num">1</span>
          <div>
            <h3>物件説明を貼り付ける</h3>
            <p>SUUMO等の物件ページの説明文をコピペすると、価格・所在地・土地／建物面積・間取り・築年・駅徒歩を自動で読み取ります。物件チラシのPDFもそのまま使えます。</p>
          </div>
        </div>
        <div class="step">
          <span class="num">2</span>
          <div>
            <h3>内容を確認して直す</h3>
            <p>自動で埋まった項目を目視で確認。読み取れなかった欄だけ手で入力します。年収・頭金・借入条件もここで指定します。</p>
          </div>
        </div>
        <div class="step">
          <span class="num">3</span>
          <div>
            <h3>診断する</h3>
            <p>公的データを照合して100点で採点。結果はPDFレポートとして持ち出せます。内見や商談の前に目を通しておく資料として使ってください。</p>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section id="shikumi">
    <div class="wrap reveal">
      <p class="eyebrow">なぜ中立だと言えるのか</p>
      <h2>物件を売らないから、<br>点数を盛る理由がない。</h2>
      <p class="sub">住所を起点に4系統の公的データを取り寄せ、公開された配点ルールで計算します。機械学習は使いません。AIが点数を決めることもありません。</p>

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
              <text x="192" y="215">人口・世帯の推移</text>
            </g>
            <g class="f-s" font-family="IBM Plex Mono, monospace" font-size="9" letter-spacing="1">
              <text x="192" y="48">国土交通省</text>
              <text x="192" y="109">国土交通省・国土地理院</text>
              <text x="192" y="169">国土交通省</text>
              <text x="192" y="230">総務省 e-Stat</text>
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
          <p>国土交通省の成約価格、総務省の人口統計、国土地理院の地図・ハザード情報。商業施設については公的データに無いためOpenStreetMapを使用してます。出典はすべて結果画面に明示します。</p>
        </div>
        <div class="card">
          <h3>計算はルールベースで、全部見せる</h3>
          <p>点数は公開された配点ルールの計算結果です。採点に使った事例と類似度を並べ、なぜその価格なのかを再現できるようにしています。</p>
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
    <p class="soon">マンションは<a href="/mansion" style="color:inherit">こちらの診断</a>へ。管理費・修繕積立金の評価は含みません。</p>
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
      <p>URLではなく、物件ページに書かれている説明文（価格・所在地・面積・築年・駅徒歩など）をコピーして貼り付けてください。ご自身がコピーした情報を解析する形をとっています。物件チラシのPDFのアップロードにも対応しています。</p>
    </details>
    <details>
      <summary>新築でも診断できますか。</summary>
      <p>できます。新築は近隣の新築成約事例を優先し、土地相当分と建物相当分を分けて価格を推定します。中古とは類似度の重み付けを変えています。</p>
    </details>
    <details>
      <summary>マンションには対応していますか。</summary>
      <p>対応しています。マンションは所在階・向き・専有面積あたりの単価など戸建と評価軸が違うため、別の診断として用意しています。ただし管理費・修繕積立金・大規模修繕の履歴といった管理の健全性は、公的データから取得できないため評価に含まれていません。</p>
    </details>
    <details>
      <summary>点数が低い物件は、買ってはいけないということですか。</summary>
      <p>違います。点数は「その価格と条件が、公的データから見てどのあたりに位置するか」を示すものです。低い点数は、値引き交渉の材料や、事前に確認すべき項目のリストとして使ってください。最終的な判断は、現地の確認と専門家への相談のうえで行ってください。</p>
    </details>
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
  <div class="wrap">
    <p><a href="/terms">利用規約</a>　・　<a href="/privacy">プライバシーポリシー</a></p>
    <p>出典：国土交通省 不動産情報ライブラリ／総務省 e-Stat／国土地理院／Google</p>
    <p>© HOME INDEX</p>
  </div>
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

LP = (LP.replace("LP_FONT_LINK_PLACEHOLDER", LP_FONT_LINK)
      .replace("HI_SYMBOL_PLACEHOLDER", symbol_small())
      .replace("HI_WORDMARK_PLACEHOLDER", WORDMARK))

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


_TERMS_BODY = ("""
<p class="sub">最終改定日：2026年8月13日</p>
<h2>第1条（本サービス）</h2>
<p>「HOME INDEX（購入診断）」（以下「本サービス」）は、利用者が入力・貼り付けした物件情報と、
国土交通省 不動産情報ライブラリ・総務省 e-Stat・国土地理院等の公的データにもとづき、
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
<p>本サービスは、国土交通省 不動産情報ライブラリ、総務省 e-Stat（政府統計）、国土地理院、
国土数値情報、および地図・ジオコーディング事業者のデータを加工して利用しています。
各データの権利は各提供元に帰属し、その利用条件にも従います。</p>
<h2>第5条（貼り付け情報の取り扱い）</h2>
<p>物件説明の解析は、利用者ご自身が取得・入力した情報を利用者の私的利用の範囲で処理するものです。
物件情報サイト等の規約に反する形での情報取得・利用は行わないでください。</p>
<h2>第6条（変更・中断）</h2>
<p>運営者は、利用者への事前通知なく本サービスの内容を変更・中断・終了することがあります。</p>
<h2>第7条（準拠法・管轄）</h2>
<p>本規約は日本法に準拠し、本サービスに関する紛争は運営者所在地を管轄する裁判所を
第一審の専属的合意管轄とします。</p>
<h2>第8条（運営者）</h2>
<p>運営者：""" + OPERATOR + """<br>お問い合わせ：""" + CONTACT + """</p>
""")

_PRIVACY_BODY = ("""
<p class="sub">最終改定日：2026年8月13日</p>
<h2>1. 取得する情報</h2>
<p>本サービスは、診断のために利用者が入力・貼り付けした情報（物件の所在地・価格・面積・築年・
駅距離・種別、任意入力の世帯年収・頭金等）を処理します。あわせて、アクセスに伴う技術情報
（IPアドレス等）を、不正利用防止・レート制限の目的で一時的に参照する場合があります。</p>
<h2>2. 利用目的</h2>
<p>取得した情報は、(1) 診断結果の生成・表示、(2) 本サービスの品質改善、
(3) 不正・過度なアクセスの防止、の目的にのみ利用します。診断のために不要な情報は取得しません。</p>
<h2>3. 保存・安全管理</h2>
<p>入力情報は原則として診断処理のために用い、サーバー上での恒久的な保存は行いません
（不正防止のためのアクセス記録を除く）。取り扱いにあたっては適切な安全管理措置を講じます。
地図・座標情報は各提供元の利用条件に従って取り扱います。</p>
<h2>4. 第三者提供</h2>
<p>法令に基づく場合を除き、利用者の同意なく個人情報を第三者に提供しません。
診断に必要な範囲で公的データAPI等の外部サービスに対し、住所等の照会を行う場合があります。</p>
<h2>5. 外部サービス</h2>
<p>本サービスは、国土交通省 不動産情報ライブラリ・総務省 e-Stat・国土地理院・地図/ジオコーディング
事業者のAPIを利用します。これらへの照会内容は各提供元の規約・プライバシーポリシーに従います。</p>
<h2>6. お問い合わせ・開示等の請求</h2>
<p>本ポリシーに関するお問い合わせ、保有個人データの開示・訂正・削除等のご請求は、
下記までご連絡ください。</p>
<p>運営者：""" + OPERATOR + """<br>お問い合わせ：""" + CONTACT + """</p>
<h2>7. 改定</h2>
<p>本ポリシーは、必要に応じて改定することがあります。重要な変更は本ページに掲示します。</p>
""")


@app.route("/terms")
def terms():
    return _legal_page("利用規約", _TERMS_BODY)


@app.route("/privacy")
def privacy():
    return _legal_page("プライバシーポリシー", _PRIVACY_BODY)


@app.route("/")
def index():
    """トップページ。サービスの説明を置き、診断（/buy）へ送る。"""
    base = request.url_root.rstrip("/")
    return LP.replace("CANONICAL_URL", base + "/")


@app.route("/buy")
def buy():
    """購入診断（戸建）の入力フォーム。以前の / がこのURLになった。"""
    return render_template_string(FORM, v=_example_v(), listing="", banner=None)


@app.route("/resolve_city", methods=["POST"])
def resolve_city():
    """住所から市区町村コード・町名を返す（手入力時の自動補完用）。"""
    from flask import jsonify
    addr = (request.form.get("address") or "").strip()
    if not addr:
        return jsonify({})
    try:
        code, name, dist = _resolver().resolve_from_address(addr)
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
            code, cityname, dist = _resolver().resolve_from_address(p["address"])
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

@app.route("/upload_pdf", methods=["POST"])
def upload_pdf():
    from src.extract import extract_from_pdf, parse_listing_text
    f = request.files.get("pdf")
    if not f or not f.filename:
        return render_template_string(FORM, v=_example_v(), listing="",
                                      banner="PDFファイルが選ばれていません。")
    try:
        text = extract_from_pdf(f.stream)
        if not text.strip():
            return render_template_string(FORM, v=_example_v(), listing="",
                                          banner="このPDFは文字を抽出できませんでした（スキャン画像の可能性）。文字が選択できるPDFをお試しください。")
        p = parse_listing_text(text)
        if p.get("address"):
            try:
                code, cityname, dist = _resolver().resolve_from_address(p["address"])
                if code:
                    p["city"] = code
                if dist and not p.get("district"):
                    p["district"] = dist
            except Exception:
                pass
        return render_template_string(FORM, v=_v_from_parsed(p), listing=text,
                                      banner="PDFから読み取りました。内容を確認・修正してください。")
    except Exception as e:
        return render_template_string(FORM, v=_example_v(), listing="",
                                      banner=f"PDFの読み取りに失敗しました：{e}")

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


def _render_result(res, subject, sctx, down_yen, loan_years):
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
                fac_bits.append("買い物施設は付近に見当たらず")

        # 学区と将来推計人口。人口はメッシュの推計があればそちらを優先する。
        districts = []
        if getattr(en, "elementary_district", None):
            districts.append(f"小学校区 {en.elementary_district}")
        if getattr(en, "junior_district", None):
            districts.append(f"中学校区 {en.junior_district}")
        pop_label = f"{en.population:,}人" if en.population else "—"
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

    return render_template_string(
        RESULT, s=sctx, price_man=man(subject.price), age=age,
        p=price_ctx, cats=cats, d=dctx, loan=loan, warnings=res.warnings,
        enr=enr, ring_circ=round(circ, 1), ring_off=ring_off,
        grade_color=grade_color, grade_comment=grade_comment)


def _run_diagnose(f, datetime):
    address = (f.get("address") or "").strip()
    city = (f.get("city") or "").strip()
    district = (f.get("district") or "").strip()
    # 手入力で市区町村コードが空でも、住所から自動補完（保険）
    if not city and address:
        try:
            code, _nm, dist = _resolver().resolve_from_address(address)
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
    sctx = dict(address=subject.address, ptype=ptype_ja, specs=specs)
    return _render_result(res, subject, sctx,
                          to_yen(f.get("down")) or 0, loan_years)



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
 <p class="lead">項目は手で入力してください。金額は<b>万円</b>。
  <a href="/buy">戸建の診断はこちら</a></p>

 {% if banner %}<div class="banner">{{banner|safe}}</div>{% endif %}

 <div class="banner">
  <b>この診断に含まれないもの：</b>修繕積立金の<b>残高</b>、大規模修繕の履歴、管理形態、
  滞納の有無。いずれもマンションの価値を左右しますが、公的データからは取得できません。
  重要事項説明でご確認ください。
 </div>

 <form class="card" method="post" action="/mansion_diagnose">
  <label>所在地（必須）</label>
  <input name="address" value="{{v.address}}" placeholder="例）神奈川県小田原市栄町1-1-1" required>
  <div class="hint">住所を入れると市区町村コードを自動で判定します</div>

  <label>マンション名</label>
  <input name="name" value="{{v.name}}" placeholder="例）〇〇マンション">
  <div class="hint">建物の位置を正確に取るために使います。<b>成約事例を名前で検索することはできません</b>（取引価格情報は匿名化されていて建物名を含まないため）。同じ町名・同じ築年の成約を「同じ建物の可能性がある事例」として別枠で表示します。</div>

  <div class="row">
   <div><label>売出価格（万円・必須）</label>
    <input name="price" value="{{v.price}}" placeholder="例）3480" required></div>
   <div><label>専有面積（㎡・必須）</label>
    <input name="area" value="{{v.area}}" placeholder="例）70.2" required>
    <div class="hint">バルコニーは含めません</div></div>
  </div>

  <div class="row">
   <div><label>築年（西暦）</label>
    <input name="byear" value="{{v.byear}}" placeholder="例）2005"></div>
   <div><label>駅まで徒歩（分）</label>
    <input name="station" value="{{v.station}}" placeholder="例）8"></div>
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
                floor="", total_floors="", direction="不明", city="",
                district="", mfee="", rfund="", income="", down="",
                loan_years="35")


@app.route("/mansion")
def mansion():
    """マンション診断の入力フォーム。"""
    return render_template_string(MANSION_FORM, v=_mansion_example_v(),
                                  directions=DIRECTIONS, banner=None)


@app.route("/mansion_diagnose", methods=["POST"])
def mansion_diagnose():
    f = request.form

    if not _rate_ok(_client_ip()):
        return render_template_string(
            MANSION_FORM, v=_mansion_form_values(f), directions=DIRECTIONS,
            banner=(f"本日の診断回数の上限（{_RATE_LIMIT}回）に達しました。"
                    "時間をおいて再度お試しください。")), 429
    if not _SEM.acquire(timeout=25):
        return render_template_string(
            MANSION_FORM, v=_mansion_form_values(f), directions=DIRECTIONS,
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
            code, _nm, dist = _resolver().resolve_from_address(address)
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
            banner="専有面積を入力してください。㎡単価で比較するため必須です。")

    direction = (f.get("direction") or "不明").strip()
    subject = MansionSubject(
        address=address,
        name=(f.get("name") or "").strip() or None,
        price=to_yen(f.get("price")) or 0,
        build_year=to_int(f.get("byear")),
        station_walk_min=to_int(f.get("station")),
        exclusive_area_m2=area,
        floor=to_int(f.get("floor")),
        total_floors=to_int(f.get("total_floors")),
        direction=direction,
        municipality_code=city or None,
        district_name=district or None,
        management_fee=to_int(f.get("mfee")),
        repair_fund=to_int(f.get("rfund")))

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
    bits.append(f"駅徒歩{subject.station_walk_min}分"
                if subject.station_walk_min is not None else "駅徒歩不明")
    if subject.management_fee or subject.repair_fund:
        monthly = (subject.management_fee or 0) + (subject.repair_fund or 0)
        bits.append(f"管理費等 月{monthly:,}円")
    sctx = dict(address=(f"{subject.address}　{subject.name}"
                         if subject.name else subject.address),
                ptype="中古マンション",
                specs=" ・ ".join(bits))
    return _render_result(res, subject, sctx, down_yen, loan_years)

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
 <p class="lead">購入にかかる諸費用、金利が上がったときの返済額、繰上返済の効果、住宅ローン控除の見込みを、
  公的な税率と料率にもとづいて試算します。<b>物件の価格を評価するものではありません。</b></p>

 <form method="post" action="/pro/finance">
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
     <input name="land_area" value="{{v.land_area}}" placeholder="例）147.07"></div>
    <div><label>建物の床面積（㎡）</label>
     <input name="floor_area" value="{{v.floor_area}}" placeholder="例）90.47"></div>
   </div>
  </div>

  <div class="card">
   <h2>建物の新築時期</h2>
   <p class="h2sub">不動産取得税の控除額が新築時期で変わります</p>
   <div class="row">
    <div><label>新築年（西暦）</label><input name="byear" value="{{v.byear}}" placeholder="例）2005"></div>
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
 @media (max-width:560px){.wrap{padding:16px 12px}.card{padding:16px}table{font-size:12px}}
</style></head><body>
BRAND_BAR
<div class="wrap">
 <a class="back" href="/pro/finance">← 条件を変えて試算</a>

 <form method="post" action="/pro/finance.pdf" class="card" style="text-align:center">
  {% for k, val in form.items() %}<input type="hidden" name="{{k}}" value="{{val}}">{% endfor %}
  <button type="submit" style="margin-top:0">PDFレポートを保存</button>
  <p class="sub" style="margin:8px 0 0">
   この試算結果を1つのPDFにまとめます。住宅ローンの相談や家族との共有にお使いください。</p>
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
                    .replace("FONT_LINK_PLACEHOLDER", FONT_LINK)
                    .replace("BRAND_BAR", brand_bar("PRO")))
PRO_FINANCE_RESULT = (PRO_FINANCE_RESULT
                      .replace("BRAND_CSS_PLACEHOLDER", BRAND_CSS)
                      .replace("FONT_LINK_PLACEHOLDER", FONT_LINK)
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
                quake_ins=False, option_cost=False,
                deduction_cat="その他", resale=False, kosodate=False,
                prepay="", prepay_after="", prepay_kind="期間短縮型")


@app.route("/pro/finance", methods=["GET", "POST"])
def pro_finance():
    from src.finance import FCONFIG

    cats = list(FCONFIG.get("loan_deduction", {}).get("existing", {}).keys())
    cats = [c for c in cats if not c.startswith("_")]
    ps = FCONFIG.get("price_split", {})
    tmpl_kw = dict(
        categories=cats,
        nb_building=int((ps.get("new_build_building_price") or 0) / 10000),
        old_years=ps.get("old_building_hint_years", 30),
        old_ratio=int((ps.get("old_building_hint_ratio") or 0) * 100))
    if request.method == "GET":
        return render_template_string(PRO_FINANCE_FORM, v=_pro_defaults(),
                                      **tmpl_kw)

    return render_template_string(PRO_FINANCE_RESULT, **_pro_compute(request.form))


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
    pdf = build_finance_pdf(_pro_compute(request.form))
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
