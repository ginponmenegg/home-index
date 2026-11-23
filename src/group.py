"""HOME INDEX 住まいのサービス（グループのページ群、2026-11-23）。

注文住宅の依頼先さがし・住宅ローンの比較・火災保険の見積もりを、成果報酬型の
広告（ASPのリンク）でつなぐ。住み替え・空き家・相続は、相談の窓口
（src/referral.py）へ案内する。

■決まりごと（本体の中立の約束と対になっている）
- 採点のコード（scoring / pipeline ほか）は、このモジュールを import しない
  （tests/test_group.py で固定）。広告で点数が変わる経路を持たない。
- 広告であることを、ページの頭と、リンクの1つずつに書く（景品表示法の
  ステルスマーケティング規制）。リンクには rel="sponsored nofollow" を付ける。
- 保険は「見積もりサービスの広告」まで。保険の募集・比較・推奨はしない。
  住宅ローンも「比較サービスの広告」まで。申込みの取り次ぎはしない。
  特定の保険会社や銀行を勧める文は書かない。
- 本体からの導線は、点数の説明より下に、小さく置く。「広告を含みます」と書く。
- 外へ出るリンクは /go/<key> を通して件数だけ数える。誰が押したかは残さない。
- 広告のリンク（https）が1つも設定されていない分野のページは出さない（404）。
  全部が未設定なら、入口のページも導線も出ない。

■設定
group_offers.json に広告を並べる。url はASPの管理画面で発行されたリンク。
"""
from __future__ import annotations

import html
import json
import os
from typing import Dict, List, Optional

_CONF = (os.environ.get("GROUP_OFFERS_FILE")
         or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "group_offers.json"))

BRAND = "HOME INDEX 住まいのサービス"

# 分野ごとのページの中身。数字は書かない（一次情報の無い相場は出さない）。
CATEGORIES: Dict[str, dict] = {
    "chumon": {
        "title": "注文住宅の依頼先をさがす",
        "short": "注文住宅の依頼先さがし",
        "lead": ("土地が決まりかけたら、建てる会社を比べ始める時期です。"
                 "間取りの提案と見積もりを、何社かから取り寄せて比べます。"),
        "stance": ("HOME INDEX は、特定のハウスメーカーや工務店を勧めません。"
                   "下のリンクは、資料や見積もりを一括で請求できるサービスの広告です。"),
        "checks": [
            ("本体工事費に何が含まれるか",
             "付帯工事・外構・地盤改良・設計料が別になっていないかを、見積もりの"
             "内訳で確かめます。", "/guide/tochi-sagashi-chumon-jutaku",
             "注文住宅の土地の探し方"),
            ("建築条件付きの土地ではないか",
             "条件付きなら、建てる会社は売主が決めた会社に限られます。",
             "/guide/kenchiku-jouken-tsuki-tochi", "建築条件付土地"),
            ("その土地に、どの広さまで建てられるか",
             "建ぺい率・容積率と前面道路で、建てられる延床の上限が決まります。",
             "/land", "土地を診断する"),
            ("つなぎ融資が要るか",
             "土地の代金と着工金は、住宅ローンの実行より先に払うことが多いため、"
             "その間の借入と利息を見ておきます。", "/guide/tsunagi-yushi-risoku",
             "注文住宅のつなぎ融資"),
        ],
    },
    "loan": {
        "title": "住宅ローンを比べる",
        "short": "住宅ローンの比較",
        "lead": ("金利だけでなく、手数料・保証料・団信・繰上返済の条件まで含めて"
                 "比べると、総額の差が見えてきます。"),
        "stance": ("HOME INDEX は、住宅ローンの申込みの取り次ぎや、特定の金融機関の"
                   "推奨をしません。下のリンクは、比較サービスの広告です。"),
        "checks": [
            ("金利が上がったら、毎月いくらになるか",
             "変動金利は、5年ルールなどで返済額の上がり方が緩やかでも、利息は"
             "増えます。", "/guide/kinri-joushou-5nen-rule", "5年ルールの中身"),
            ("年収に対して、返済の割合はどれくらいか",
             "返済負担率で、借りすぎていないかを確かめます。",
             "/guide/hensai-futanritsu", "返済負担率"),
            ("手数料と保証料",
             "諸費用の中でも大きな項目です。借入額に比例するものがあります。",
             "/guide/chuko-shohiyou-uchiwake", "中古戸建の諸費用の内訳"),
            ("ローン特約の期限が、本審査に間に合うか",
             "売買契約の前に、特約の期限と審査の日程を合わせておきます。",
             "/guide/jutaku-loan-tokuyaku", "住宅ローン特約"),
        ],
    },
    "hoken": {
        "title": "火災保険・地震保険の見積もりを取る",
        "short": "火災保険の見積もり",
        "lead": ("引渡しの日から補償が始まるように、決済の前に決めておきます。"
                 "補償の範囲と、建物の構造で保険料が変わります。"),
        "stance": ("HOME INDEX は、保険の募集・比較・推奨をしません。下のリンクは、"
                   "複数の保険会社から見積もりを取り寄せられるサービスの広告です。"
                   "補償の内容は、保険会社の説明で確かめてください。"),
        "checks": [
            ("水災の補償を付けるか",
             "ハザードマップの想定浸水深と、建物の階数で考えます。",
             "/guide/kouzui-shinsui-fukasa", "想定浸水深とは"),
            ("地震保険を付けるか",
             "地震保険は、火災保険とセットで入る保険です。単独では入れません。",
             "/guide/jiban-ekijoka-morido", "液状化と盛土"),
            ("土砂災害の区域に入っていないか",
             "区域の種類で、建物の被害の想定が違います。",
             "/guide/dosha-saigai-keikai-kuiki", "土砂災害警戒区域"),
        ],
    },
}

PLACES = ("result", "land", "finance", "hub", "page")


def _load() -> list:
    try:
        with open(_CONF, encoding="utf-8") as f:
            return list(json.load(f).get("offers") or [])
    except (OSError, ValueError):
        return []


def _live(o: dict) -> bool:
    url = (o.get("url") or "").strip()
    return bool((o.get("key") or "").strip() and (o.get("name") or "").strip()
                and o.get("category") in CATEGORIES and url.startswith("https://"))


def offers(category: Optional[str] = None) -> List[dict]:
    return [o for o in _load() if _live(o)
            and (category is None or o["category"] == category)]


def offer(key: str) -> Optional[dict]:
    for o in offers():
        if o["key"] == key:
            return o
    return None


def categories() -> List[str]:
    live = {o["category"] for o in offers()}
    return [c for c in CATEGORIES if c in live]


def enabled() -> bool:
    return bool(offers())


AD_NOTE = ("このページは HOME INDEX のグループのサービスです。成果報酬型の広告を"
           "含みます。広告の掲載と報酬は、診断の点数に関係しません。")

CSS = """
.gad{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;
  border:1px solid #8A6512;color:#8A6512;border-radius:5px;padding:1px 6px;margin-right:6px}
.gnote{background:#FBF6EA;border-left:3px solid #8A6512;border-radius:6px;
  padding:10px 12px;font-size:13px;line-height:1.8;color:#5b4410;margin:0 0 14px}
.gcard{border:1px solid #E1E5EB;border-radius:12px;padding:14px 16px;margin:10px 0;background:#fff}
.gcard h3{margin:0 0 6px;font-size:16px}
.gcard p{margin:0 0 10px;font-size:14px;line-height:1.8}
.gbtn{display:block;text-align:center;background:#111;color:#fff;border-radius:10px;
  padding:13px;font-size:15px;font-weight:700;text-decoration:none}
.gchk{list-style:none;padding:0;margin:0}
.gchk li{border-top:1px solid #E1E5EB;padding:12px 0}
.gchk li b{display:block;font-size:15px;margin-bottom:3px}
.gchk li span{font-size:14px;line-height:1.8;color:#333}
.gchk li a{font-size:13px}
.gcats a{display:block;border:1px solid #E1E5EB;border-radius:12px;padding:14px 16px;
  margin:10px 0;text-decoration:none;color:#111;background:#fff}
.gcats a b{display:block;font-size:16px;margin-bottom:4px}
.gcats a span{font-size:13px;color:#5E6670;line-height:1.7}
.gbridge{border:1px dashed #C9CED6;border-radius:12px;padding:12px 14px;margin:14px 0;
  background:#FAFAF8;font-size:13px;line-height:1.8}
.gbridge a{font-weight:700}
"""


def bridge(place: str) -> str:
    """本体の画面の下に置く小さな導線。広告であることを書く。"""
    cats = categories()
    want = {"land": ("chumon", "loan", "hoken"),
            "result": ("loan", "hoken"),
            "finance": ("loan", "hoken")}.get(place, ())
    show = [c for c in want if c in cats]
    if not show:
        return ""
    p = place if place in PLACES else "page"
    links = "　・　".join(
        f'<a href="/services/{c}?from={p}">{html.escape(CATEGORIES[c]["short"])}</a>'
        for c in show)
    return (f"<style>{CSS}</style><div class=\"gbridge\" data-html2canvas-ignore>"
            f"<span class=\"gad\">広告を含みます</span><b>{BRAND}</b>"
            f"<br>{links}<br><span style=\"color:#5E6670\">グループの別サービスです。"
            "点数には関係しません。</span></div>")


def offer_card(o: dict, place: str = "page") -> str:
    esc = html.escape
    p = place if place in PLACES else "page"
    note = f"<p>{esc(o['note'])}</p>" if (o.get("note") or "").strip() else ""
    adv = (f"<p style=\"font-size:12px;color:#5E6670\">広告主：{esc(o['advertiser'])}</p>"
           if (o.get("advertiser") or "").strip() else "")
    return (f"<div class=\"gcard\"><h3><span class=\"gad\">広告</span>"
            f"{esc(o['name'])}</h3>{note}"
            f"<a class=\"gbtn\" href=\"/go/{esc(o['key'])}?from={p}\" "
            "rel=\"sponsored nofollow noopener\" target=\"_blank\">"
            f"{esc(o['name'])}のサイトへ（広告）</a>{adv}</div>")
