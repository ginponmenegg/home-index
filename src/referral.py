"""提携の不動産会社への相談窓口（2026-11-20）。

物件探しや購入の相談を希望した人を、本人の申込みで、提携の不動産会社に
つなぐ。HOME INDEX は仲介をしない。仲介・媒介は提携先が行う。

■決まりごと（画面の約束と対になっている。崩すと約束が嘘になる）
- 採点のコード（scoring / pro_scoring / land_scoring / mansion_scoring /
  pipeline ほか）は、このモジュールを import しない。提携先が誰かで点数が
  変わる経路を、作りとして持たない（tests/test_referral.py で固定）。
- 窓口は、結果の点数の説明より下に出す。結果の上部やリスクの説明の途中に
  出さない。
- 申し込まれるまで、利用者の情報は提携先に渡さない。渡す前に、提携先の
  会社名と渡す項目を示して同意を取る（個人情報保護法27条の第三者提供）。
  診断の点数、年収・頭金などの家計の入力は渡さない。
- 「提携」であることを、窓口を出すその場所に書く。紹介料を受け取るように
  なったら、同じ場所に書く（景品表示法のステルスマーケティング規制）。
- 提携先の対応地域の外の物件には、窓口を出さない。
- ほかの不動産会社で申込み・契約に進んでいる人は受け付けない。
- 1つの地域に2社以上あるときは、こちらで割り振らない。全部を見せて
  本人に選んでもらう。

■提携先の設定
referral_partners.json に並べる。通知先のメールアドレスはリポジトリに
置かず、各提携先の notify_env に書いた環境変数の名前から読む。
会社名・免許番号・通知先のどれかが欠けている提携先は、無いものとして扱う。
有効な提携先が1社も無ければ、窓口は一切出ない（/soudan も404）。
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import List, Optional

# 手元で見た目を確かめるときだけ、REFERRAL_PARTNERS_FILE で別の設定を読ませる。
_CONF = (os.environ.get("REFERRAL_PARTNERS_FILE")
         or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "referral_partners.json"))

PREFS = ("東京都", "神奈川県")

# 住所に都県名が書かれていないとき（「横浜市…」など）の手がかり。
_CITY_HINTS = {
    "神奈川県": ("横浜市", "川崎市", "相模原市", "横須賀市", "藤沢市", "茅ヶ崎市",
             "茅ケ崎市", "平塚市", "鎌倉市", "小田原市", "厚木市", "大和市",
             "海老名市", "座間市", "綾瀬市", "伊勢原市", "秦野市", "逗子市",
             "三浦市", "南足柄市"),
    "東京都": ("千代田区", "中央区", "港区", "新宿区", "文京区", "台東区", "墨田区",
            "江東区", "品川区", "目黒区", "大田区", "世田谷区", "渋谷区", "中野区",
            "杉並区", "豊島区", "北区", "荒川区", "板橋区", "練馬区", "足立区",
            "葛飾区", "江戸川区", "八王子市", "立川市", "武蔵野市", "三鷹市",
            "青梅市", "府中市", "昭島市", "調布市", "町田市", "小金井市",
            "小平市", "日野市", "東村山市", "国分寺市", "国立市", "福生市",
            "狛江市", "東大和市", "清瀬市", "東久留米市", "武蔵村山市",
            "多摩市", "稲城市", "羽村市", "あきる野市", "西東京市"),
}

# 申込みフォームの選択肢。値はここで決めたものしか受け付けない。
STAGES = {"search": "まだ物件は決まっていない（探したい）",
          "found": "気になる物件がある"}
OTHERS = {"none": "どこにも相談していない",
          "viewed": "ほかの会社で内見した（申込みはしていない）",
          "applied": "ほかの会社で申込み・契約に進んでいる"}
WHENS = ("3か月以内", "半年以内", "1年以内", "まだ決めていない")
KINDS = {"buy": "中古戸建", "mansion": "マンション", "land": "土地", "": "—"}
FROMS = ("result", "lp", "page", "")

RETENTION_DAYS = 365       # 申込みの記録は1年で消す（プライバシーポリシー）


def _load() -> list:
    try:
        with open(_CONF, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return list(data.get("partners") or [])


def notify_address(p: dict) -> str:
    env = (p.get("notify_env") or "").strip()
    return (os.environ.get(env) or "").strip() if env else ""


def _complete(p: dict) -> bool:
    return bool((p.get("key") or "").strip() and (p.get("name") or "").strip()
                and (p.get("license") or "").strip() and p.get("areas")
                and notify_address(p))


def partners() -> List[dict]:
    """有効な提携先。毎回読み直す（設定を変えたら再起動なしで効く）。"""
    return [p for p in _load() if _complete(p)]


def enabled() -> bool:
    return bool(partners())


def prefecture(address: Optional[str]) -> Optional[str]:
    a = re.sub(r"\s", "", address or "")
    if not a:
        return None
    for pref in PREFS:
        if a.startswith(pref):
            return pref
    for pref, cities in _CITY_HINTS.items():
        if any(a.startswith(c) for c in cities):
            return pref
    return None


def partners_for_pref(pref: Optional[str]) -> List[dict]:
    if not pref:
        return []
    return [p for p in partners() if pref in p["areas"]]


def partners_for(address: Optional[str]) -> List[dict]:
    return partners_for_pref(prefecture(address))


def served_prefs() -> List[str]:
    out = []
    for p in partners():
        for a in p["areas"]:
            if a not in out:
                out.append(a)
    return out


def area_label() -> str:
    return "・".join(x[:-1] if x.endswith(("都", "県", "府")) else x
                    for x in served_prefs())


def fee_line(ps: List[dict]) -> str:
    """紹介料についての一文。受け取る提携先が1社でもあれば、そう書く。"""
    paid = [p["name"] for p in ps if p.get("referral_fee")]
    if not paid:
        return "HOME INDEX は紹介料を受け取っていません。"
    return ("HOME INDEX は、" + "、".join(paid)
            + "から紹介料を受け取ることがあります。点数には関係しません。")


def relation_lines(ps: List[dict]) -> List[str]:
    """運営者との関係（勤務先など）。書かれていれば、そのまま出す。"""
    return [f'{p["name"]}：{p["relation"]}' for p in ps
            if (p.get("relation") or "").strip()]


CSS = """
.refer{border:1px solid #111;border-radius:14px;padding:18px 18px 16px;margin:16px 0;background:#fff}
.refer .rtag{display:inline-block;font-size:11px;letter-spacing:.08em;border:1px solid #111;
  border-radius:6px;padding:2px 8px;margin:0 0 8px}
.refer h2{margin:0 0 8px;font-size:18px;line-height:1.5}
.refer p{margin:0 0 10px;font-size:14px;line-height:1.8}
.refer ul.rfacts{list-style:none;padding:0;margin:0 0 12px;display:grid;gap:6px}
.refer ul.rfacts li{font-size:13px;padding:8px 10px;background:#f6f6f4;border-radius:8px}
.refer ul.rfacts li b{margin-right:4px}
.refer .rbtn{display:block;width:100%;text-align:center;background:#111;color:#fff;border:0;
  border-radius:10px;padding:14px;font-size:15px;font-weight:700;cursor:pointer;text-decoration:none}
.refer .rwho{font-size:12px;color:#666;margin:10px 0 0}
"""


def result_block(address: Optional[str], kind: str = "buy") -> str:
    """診断の結果に出す窓口。対応地域の外、または提携先が無ければ空文字。"""
    ps = partners_for(address)
    if not ps:
        return ""
    pref = prefecture(address)
    what = "この土地の購入" if kind == "land" else "この物件の購入"
    names = "、".join(p["name"] for p in ps)
    rel = "".join(f"<br>{html.escape(r)}" for r in relation_lines(ps))
    return f"""
<style>{CSS}</style>
<div class="refer" data-html2canvas-ignore>
 <span class="rtag">提携の不動産会社（{html.escape(area_label())}）</span>
 <h2>{what}や、物件探しを相談する</h2>
 <p>HOME INDEX は物件を売りません。仲介もしません。購入の相談や物件探しを
  希望される方にだけ、ご本人のお申込みで、{html.escape(pref)}の物件を扱う
  提携の不動産会社をご案内しています。</p>
 <ul class="rfacts">
  <li><b>点数は同じ計算です。</b>提携しているかどうかで、点数は変わりません。</li>
  <li><b>申し込むまで、情報は渡しません。</b>この診断の内容も、勝手には渡しません。</li>
  <li><b>{html.escape(fee_line(ps))}</b></li>
 </ul>
 <form method="post" action="/soudan">
  <input type="hidden" name="address" value="{html.escape(address or '')}">
  <input type="hidden" name="kind" value="{html.escape(kind if kind in KINDS else '')}">
  <input type="hidden" name="from" value="result">
  <button type="submit" class="rbtn">提携の不動産会社に相談する（相談は無料）</button>
 </form>
 <p class="rwho">提携先：{html.escape(names)}。仲介は提携先が行い、契約に進んだ場合の
  仲介手数料は提携先との取り決めになります。{rel}</p>
</div>
"""


# ---- 申込みの検証 ---------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate(form) -> tuple[Optional[dict], List[str]]:
    """フォームを確かめる。問題が無ければ (申込み, [])、あれば (None, 理由)。

    画面のJavaScriptでも止めているが、ここでもう一度確かめる
    （同意の無い申込みや、対応地域の外のものを送らないため）。
    """
    g = lambda k: (form.get(k) or "").strip()
    errs = []
    if g("website"):                       # 人には見えない欄（機械の投稿よけ）
        errs.append("送信できませんでした。")
        return None, errs
    address, area_pref, area = g("address")[:120], g("pref"), g("area")[:80]
    stage, other, when = g("stage"), g("other"), g("when")
    if stage not in STAGES:
        errs.append("いまの状況を選んでください。")
    if other not in OTHERS:
        errs.append("ほかの不動産会社とのやりとりを選んでください。")
    elif other == "applied":
        errs.append("ほかの会社で申込みや契約に進んでいる物件は、ご案内できません。")
    if when not in WHENS:
        errs.append("時期を選んでください。")
    pref = prefecture(address) if address else (area_pref if area_pref in PREFS else None)
    if address and not pref:
        errs.append("この物件は、提携先の対応地域の外です。")
    elif not address and not pref:
        errs.append("探している都県を選んでください。")
    ps = partners_for_pref(pref)
    if pref and not ps:
        errs.append("この地域を扱う提携先がありません。")
    key = g("partner")
    chosen = [p for p in ps if p["key"] == key] if len(ps) > 1 else ps[:1]
    if ps and not chosen:
        errs.append("相談する会社を選んでください。")
    name, email, tel = g("name")[:60], g("email")[:120], g("tel")[:30]
    if not name:
        errs.append("お名前を入れてください。")
    if not _EMAIL_RE.match(email):
        errs.append("メールアドレスを確かめてください。")
    if tel and not re.fullmatch(r"[0-9０-９\-－()（）+ ]{6,20}", tel):
        errs.append("電話番号を確かめてください。")
    if g("agree") != "on":
        errs.append("提携先への提供に同意していただく必要があります。")
    if errs:
        return None, errs
    return {
        "partner": chosen[0], "pref": pref, "address": address,
        "area": area, "kind": g("kind") if g("kind") in KINDS else "",
        "stage": stage, "other": other, "when": when, "name": name,
        "email": email, "tel": tel, "note": g("note")[:1000],
        "came_from": g("from") if g("from") in FROMS else "",
    }, []


def shared_rows(r: dict) -> List[tuple]:
    """提携先に渡す項目。画面の確認とメールで同じものを使う。"""
    rows = [("相談の種類", KINDS.get(r["kind"], "—") if r["address"] else "物件探し"),
            ("物件", r["address"]) if r["address"] else
            ("探している地域", f'{r["pref"]} {r["area"]}'.strip()),
            ("いまの状況", STAGES[r["stage"]]),
            ("ほかの会社", OTHERS[r["other"]]),
            ("時期", r["when"]), ("お名前", r["name"]),
            ("メール", r["email"]), ("電話", r["tel"] or "—"),
            ("相談したいこと", r["note"] or "—")]
    return rows


def mail_html(r: dict) -> str:
    rows = "".join(f"<tr><th align=left style='padding:4px 12px 4px 0'>{html.escape(k)}"
                   f"</th><td>{html.escape(v)}</td></tr>" for k, v in shared_rows(r))
    return (f"<p>{html.escape(r['partner']['name'])} ご担当者様</p>"
            "<p>HOME INDEX の相談窓口から、次のお申込みがありました。"
            "ご本人は、この内容を貴社に提供することに同意しています。</p>"
            f"<table>{rows}</table>"
            "<p>診断の点数、年収・頭金などの家計の入力は、お渡ししていません。<br>"
            "HOME INDEX</p>")


def mail_text(r: dict) -> str:
    lines = [f"{r['partner']['name']} ご担当者様", "",
             "HOME INDEX の相談窓口から、次のお申込みがありました。",
             "ご本人は、この内容を貴社に提供することに同意しています。", ""]
    lines += [f"{k}：{v}" for k, v in shared_rows(r)]
    lines += ["", "診断の点数、年収・頭金などの家計の入力は、お渡ししていません。",
              "HOME INDEX"]
    return "\n".join(lines)


def subject(r: dict) -> str:
    where = r["address"] or f'{r["pref"]}{r["area"]}'
    m = re.match(r"^(.*?[市区町村])", where)
    return f"【HOME INDEX】相談のお申込み（{(m.group(1) if m else where)[:20]}）"


# ---- 記録 -----------------------------------------------------------------
def store(r: dict) -> None:
    """申込みを残す。DBが無ければ何もしない（メールだけで届く）。"""
    from src import db
    if not db.enabled():
        return
    try:
        db.run("INSERT INTO referrals (created_at, partner_key, pref, address, area,"
               " kind, stage, other, when_, name, email, tel, note, came_from, status)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (db.now(), r["partner"]["key"], r["pref"], r["address"], r["area"],
                r["kind"], r["stage"], r["other"], r["when"], r["name"], r["email"],
                r["tel"], r["note"], r["came_from"], "sent"))
    except Exception as e:
        print(f"[referral] 記録できませんでした: {e}")
    _purge_once_a_day()


_purged_on: Optional[str] = None


def _purge_once_a_day() -> None:
    global _purged_on
    import datetime
    today = datetime.date.today().isoformat()
    if _purged_on == today:
        return
    _purged_on = today
    purge()


def purge() -> int:
    from src import db
    import datetime
    if not db.enabled():
        return 0
    limit = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=RETENTION_DAYS)).replace(microsecond=0).isoformat()
    try:
        db.run("DELETE FROM referrals WHERE created_at < ?", (limit,))
    except Exception as e:
        print(f"[referral] 古い申込みを消せませんでした: {e}")
    return 0
