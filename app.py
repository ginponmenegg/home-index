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
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, request, render_template_string  # noqa: E402
from src.models import SubjectProperty  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402
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

app = Flask(__name__)

# ブランド：HOME INDEX（シンボル＝家×棒グラフ / モノクロ #111111・#E5E5E5）
# 欧文は Jost（Futura系ジオメトリックサンセリフ）。未読込環境では端末フォントへ退避。
FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link href="https://fonts.googleapis.com/css2?family=Jost:wght@300;700'
             '&display=swap" rel="stylesheet">')


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
    # --- 結果画像用の小ロックアップ ---
    '.hi-lock-sm{margin-bottom:10px}'
    '.hi-lock-sm .hi-sym{width:22px;height:22px}'
    '.hi-lock-sm .hi-wm{font-size:13px}')


def brand_bar(chip="購入診断"):
    """ページ最上部の固定ヘッダーバー（全ページ共通）。"""
    return ('<div class="hi-bar"><div class="hi-bar-in">'
            f'<a class="hi-lock" href="/">{symbol_small()}{WORDMARK}</a>'
            f'<span class="chip">{chip}</span>'
            '</div></div>')


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
 <p class="lead">物件説明を貼り付けると自動で項目を埋めます。内容を確認・修正して診断してください。金額は<b>万円</b>。物件の評価 × ご自身の属性で、あなたに合っている物件かを診断します。</p>

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
    <div class="hint">※ スキャン（画像）のPDFは読み取れません。文字がコピーできるPDFをお使いください。
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
  <p class="muted">土地 {{s.land}}㎡ ・ 建物 {{s.building}}㎡ ・ 築{{age}}年 ・ 駅徒歩{{s.station}}分</p>
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
    {% if loan.burden %}<span class="muted" style="font-size:14px">／ 返済負担率 {{loan.burden}}%</span>{% endif %}</p>
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
</body></html></body></html>
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
    price_ctx = dict(has=bool(p and p.verdict != "判定不可"))
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
    loan = dict(principal=man(L.principal), down=man(to_yen(f.get("down")) or 0),
                rate="1.25", years=str(loan_years), monthly=f"{L.monthly_payment:,}円",
                burden=L.burden_ratio)
    age = (datetime.date.today().year - subject.build_year) if subject.build_year else "—"

    # 表示用の subject（正しい項目名・日本語化・欠損は—）
    ptype_ja = {"chuko_kodate": "中古戸建", "shinchiku_kodate": "新築戸建"}.get(
        subject.property_type, subject.property_type)
    dash = lambda v: v if v is not None else "—"
    sctx = dict(address=subject.address, ptype=ptype_ja,
                land=dash(subject.land_area_m2), building=dash(subject.building_area_m2),
                station=dash(subject.station_walk_min))

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
        enr = dict(use_district=en.use_district or "—",
                   population=(f"{en.population:,}人" if en.population else "—"),
                   trend=en.population_trend or "—", hazard_items=items,
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


def open_browser():
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Timer(1.3, open_browser).start()
    print("ブラウザで http://127.0.0.1:5000 を開いてください（自動で開きます）")
    app.run(host="127.0.0.1", port=5000, debug=False)
