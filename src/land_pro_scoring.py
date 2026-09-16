# -*- coding: utf-8 -*-
"""PROの土地診断で聞く「現地と書類」を、採点に反映する層。

■どこを動かすか
無料診断の**リスク**カテゴリ（25点）を上書きする。配点そのものは変えない。
戸建PRO（pro_scoring.py）と同じ考え方で、FREEの診断を普通に走らせ、
その結果の CategoryScore を差し替える。無料側の挙動は変わらない。

■動かす条件
**土地の事実だけで点を動かす。買う人が調べたかどうかでは動かさない。**

  地盤調査を実施済みで「改良不要」  → 土地の事実。加点する
  地盤調査を実施済みで「改良要」    → 土地の事実。減点する
  地盤調査が未実施                 → 土地の話ではない。点は動かさない

未実施・未確認は減点せず、情報充足度も上げない（＝要確認に出る）。
ここを混ぜると、「調べていない人」を「悪い土地」として採点することになる。

■金額は出さない
地盤改良・解体・引き込みの費用は、公的な統計が無いことを確かめた
（docs/土地PRO_設計.md 第7章）。要否だけをここで判定し、金額は
land_finance.land_total 側で「まだ誰にも分からない」として扱う。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from .scoring import CategoryScore

UNKNOWN = "unknown"


@dataclass
class LandProDetail:
    """PROで追加入力してもらう、現地と書類の答え。既定は全て未確認。"""
    water: str = UNKNOWN          # 上水道の引き込み
    sewer: str = UNKNOWN          # 下水道
    gas: str = UNKNOWN            # ガス
    ground: str = UNKNOWN         # 地盤調査
    retaining: str = UNKNOWN      # 擁壁
    level: str = UNKNOWN          # 道路との高低差
    oldhouse: str = UNKNOWN       # 古家
    leftovers: str = UNKNOWN      # 残置物・工作物


# 選択肢。値は英字にしておく（URLやDBに日本語が混ざるのを避ける）。
CHOICES: Dict[str, List[Tuple[str, str]]] = {
    "water": [(UNKNOWN, "未確認"),
              ("done20", "引き込み済み（口径20mm以上）"),
              ("done13", "引き込み済み（口径13mm）"),
              ("main_only", "前面道路に本管はあるが未引込"),
              ("none", "前面道路に本管がない")],
    "sewer": [(UNKNOWN, "未確認"),
              ("connected", "公共下水に接続済み"),
              ("main_only", "前面道路に本管はあるが未接続"),
              ("septic", "公共下水がなく、浄化槽が要る")],
    "gas": [(UNKNOWN, "未確認"),
            ("city_done", "都市ガス引き込み済み"),
            ("city_main", "前面道路に都市ガスの本管がある"),
            ("lpg", "都市ガスがなく、プロパンになる")],
    "ground": [(UNKNOWN, "未確認"),
               ("ok", "調査済み・改良は不要"),
               ("needed", "調査済み・改良が要る"),
               ("not_done", "まだ調査していない")],
    "retaining": [(UNKNOWN, "未確認"),
                  ("none", "擁壁はない"),
                  ("certified", "擁壁あり・検査済証がある"),
                  ("uncertified", "擁壁あり・検査済証がない"),
                  ("unclear", "擁壁あり・検査済証があるか分からない")],
    "level": [(UNKNOWN, "未確認"),
              ("flat", "道路とほぼ同じ高さ"),
              ("higher", "道路より高い"),
              ("lower", "道路より低い")],
    "oldhouse": [(UNKNOWN, "未確認"),
                 ("none", "古家はない"),
                 ("seller", "古家あり・解体は売主の負担"),
                 ("buyer", "古家あり・解体は買主の負担")],
    "leftovers": [(UNKNOWN, "未確認"),
                  ("none", "残置物・工作物はない"),
                  ("exists", "残置物・工作物がある")],
}

LABELS = {"water": "上水道の引き込み", "sewer": "下水道", "gas": "ガス",
          "ground": "地盤調査", "retaining": "擁壁",
          "level": "道路との高低差", "oldhouse": "古家",
          "leftovers": "残置物・工作物"}

# 画面の並び。見出しと、そこで何を見ているかの説明。
SECTIONS = [
    ("ライフライン",
     "前面道路に本管が来ているか、敷地の中まで引き込んであるか。"
     "市区町村の水道課とガス事業者で分かります。",
     ["water", "sewer", "gas"]),
    ("地盤と造成",
     "買ったあとに費用が出るかどうかが、ここでほぼ決まります。",
     ["ground", "retaining", "level"]),
    ("敷地の上にあるもの",
     "古家や物置が残っていると、解体と処分が要ります。",
     ["oldhouse", "leftovers"]),
]

# 回答ごとの、リスクの raw への足し引き。
#   正の数 … 土地として有利な事実
#   0      … 点は動かさない（未確認・未実施を含む）
#   負の数 … 土地として不利な事実
# 調べていないことを減点にしない。それは充足度の話。
ADJUST: Dict[Tuple[str, str], float] = {
    ("water", "done20"): 0.05,
    ("water", "done13"): 0.0,
    ("water", "main_only"): -0.05,
    ("water", "none"): -0.15,
    ("sewer", "connected"): 0.05,
    ("sewer", "main_only"): -0.03,
    ("sewer", "septic"): -0.08,
    ("gas", "city_done"): 0.03,
    ("gas", "city_main"): 0.0,
    ("gas", "lpg"): -0.02,
    ("ground", "ok"): 0.10,
    ("ground", "needed"): -0.08,
    ("ground", "not_done"): 0.0,
    ("retaining", "none"): 0.05,
    ("retaining", "certified"): 0.0,
    ("retaining", "uncertified"): -0.15,
    ("retaining", "unclear"): -0.05,
    ("level", "flat"): 0.03,
    ("level", "higher"): -0.03,
    ("level", "lower"): -0.08,
    ("oldhouse", "none"): 0.0,
    ("oldhouse", "seller"): 0.0,
    ("oldhouse", "buyer"): -0.05,
    ("leftovers", "none"): 0.0,
    ("leftovers", "exists"): -0.03,
}

# 画面に出す一言。強み（plus）と弱み（minus）に流す。
PLUS_TEXT = {
    ("water", "done20"): "上水道が口径20mm以上で引き込み済み",
    ("sewer", "connected"): "公共下水に接続済み",
    ("gas", "city_done"): "都市ガスが引き込み済み",
    ("ground", "ok"): "地盤調査済みで、改良は不要と出ています",
    ("retaining", "none"): "擁壁がありません",
    ("level", "flat"): "道路とほぼ同じ高さ（造成の費用が出にくい）",
}
MINUS_TEXT = {
    ("water", "done13"): "上水道の口径が13mm。水回りが複数あると20mmへの"
                         "増径が要り、道路の掘削を伴います",
    ("water", "main_only"): "上水道が未引込。前面道路から敷地まで引く工事が要ります",
    ("water", "none"): "前面道路に上水道の本管がありません。"
                       "引けるか、どこから引くかを水道課で確認してください",
    ("sewer", "main_only"): "下水道が未接続。接続の工事が要ります",
    ("sewer", "septic"): "公共下水がなく浄化槽が要ります。設置費と、"
                         "住み始めてからの保守点検の費用がかかります",
    ("gas", "lpg"): "都市ガスがなくプロパンになります。毎月の料金が"
                    "都市ガスより高くなるのが一般的です",
    ("ground", "needed"): "地盤調査で改良が要ると出ています",
    ("retaining", "uncertified"): "擁壁に検査済証がありません。"
                                  "建て替えの際にやり替えを求められることがあります",
    ("retaining", "unclear"): "擁壁の検査済証があるか分かりません",
    ("level", "higher"): "道路より高い土地。土留めや階段の工事が出ます",
    ("level", "lower"): "道路より低い土地。雨水の排水にポンプが要ることがあり、"
                        "大雨のときに水が集まりやすくなります",
    ("oldhouse", "buyer"): "古家の解体が買主の負担です",
    ("leftovers", "exists"): "残置物・工作物があります。処分の負担を"
                             "どちらが持つか、契約前に決めてください",
}

# 未確認・未実施のときに「確認すること」へ出す一文。
CONFIRM_TEXT = {
    "water": "上水道: 前面道路の本管の有無と、敷地までの引き込み・口径を"
             "市区町村の水道課で確認してください",
    "sewer": "下水道: 公共下水に接続できるかを市区町村の下水道課で"
             "確認してください",
    "gas": "ガス: 都市ガスの本管があるかをガス事業者に確認してください",
    "ground": "地盤: 地盤調査をするまで、改良が要るかも費用も決まりません。"
              "売主が調査済みでないか、まず聞いてください",
    "retaining": "擁壁: 擁壁の有無と、あれば検査済証を確認してください。"
                 "検査済証がないものは、建て替えのときに問題になります",
    "level": "高低差: 道路との高低差を現地で確認してください",
    "oldhouse": "古家: 古家の有無と、解体費をどちらが負担するかを"
                "契約条件で確認してください",
    "leftovers": "残置物: 物置・塀・井戸などが残っていないかを確認してください",
}

# 未実施・未確認でも、それが「答え」であるもの。充足度には数えない。
NOT_ANSWERED = {UNKNOWN, "not_done", "unclear"}

# 足し引きの合計に、上下の枠をはめる。
#
# 枠が無いと、8項目の減点がそのまま積み上がって、リスク25点が1.5点まで
# 落ちた。実際に起きたのは「未引込・浄化槽・プロパン・改良要・無検査擁壁・
# 低い土地・買主解体・残置物」の土地で、これは確かに悪いが、壊滅ではない。
# 数百万円の出費とひと手間であって、再建築不可のような前提の崩壊ではない。
#
# 上げ幅を下げ幅より小さくしているのは、引込済み・平坦がこの国の土地では
# 普通だから。普通であることに大きな加点はしない。
MAX_UP = 0.15
MAX_DOWN = -0.35


def detail_from(values: Dict[str, str]) -> LandProDetail:
    """フォームの値から LandProDetail を作る。知らない値は未確認に倒す。"""
    kw = {}
    for key, choices in CHOICES.items():
        v = (values.get(key) or UNKNOWN).strip()
        kw[key] = v if v in dict(choices) else UNKNOWN
    return LandProDetail(**kw)


def answered_ratio(detail: LandProDetail) -> float:
    """はっきり答えが返ってきた項目の割合。"""
    vals = [getattr(detail, k) for k in CHOICES]
    got = [v for v in vals if v not in NOT_ANSWERED]
    return len(got) / len(vals) if vals else 0.0


def cost_needs(detail: LandProDetail) -> Dict[str, Optional[bool]]:
    """金額の出せない費目が要るかどうか。True/False/None（不明）。

    land_finance.land_total に渡す。True でも金額は出さない。
    """
    ground = {"needed": True, "ok": False}.get(detail.ground)
    demolition = {"buyer": True, "none": False,
                  "seller": False}.get(detail.oldhouse)
    lines = [detail.water, detail.sewer, detail.gas]
    if any(v in ("main_only", "none", "septic", "lpg") for v in lines):
        utilities = True
    elif all(v in ("done20", "done13", "connected", "city_done")
             for v in lines):
        utilities = False
    else:
        utilities = None
    return {"地盤改良": ground, "古家の解体": demolition,
            "上下水道・ガスの引き込み": utilities}


def score_land_site(base: CategoryScore,
                    detail: LandProDetail) -> Tuple[CategoryScore, List[str]]:
    """リスクのカテゴリを、現地と書類の答えで上書きする。

    base は無料診断が出した「リスク」の CategoryScore。
    戻り値は (上書きしたカテゴリ, 確認すること)。CategoryScore に項目を
    生やして返すより、素直に2つ返すほうが読める。
    """
    plus, minus, confirm = list(base.plus), list(base.minus), []
    bits = []
    delta = 0.0

    for key in CHOICES:
        v = getattr(detail, key)
        delta += ADJUST.get((key, v), 0.0)
        if (key, v) in PLUS_TEXT:
            plus.append(PLUS_TEXT[(key, v)])
        if (key, v) in MINUS_TEXT:
            minus.append(MINUS_TEXT[(key, v)])
        if v in NOT_ANSWERED:
            confirm.append(CONFIRM_TEXT[key])
        else:
            bits.append(f"{LABELS[key]}:{dict(CHOICES[key])[v]}")

    raw = base.raw + max(MAX_DOWN, min(MAX_UP, delta))

    # ハザードが未確認のとき、score_risk は raw を 0.7 で頭打ちにしている
    # （安全と断定できないため）。**水道やガスの答えで、その上限を
    # 押し上げてはいけない。** 別の話なので、天井はそのまま残す。
    # 確認できたかどうかは、出典に hazard が入っているかで見る。
    # 文章を読むと、言い回しを変えたときに静かに壊れる。
    hazard_checked = any("hazard" in (x or "") for x in base.sources)
    ceiling = 1.0 if hazard_checked else 0.7
    raw = max(0.0, min(ceiling, raw))

    # 充足度は「足す」のではなく「分母を広げる」。
    #
    # 加算にしていたら、無料で0.9まで来ている人が8問すべて未確認でも
    # 1.00に張り付いた。PROは聞く項目が増えるのだから、答えていなければ
    # 充足度は無料より下がるのが正しい。戸建PROが property_fields() で
    # 分母を広げているのと同じ考え方。
    ratio = answered_ratio(detail)
    suff = 0.5 * base.sufficiency + 0.5 * ratio
    reason = base.reason
    if bits:
        reason += "／" + "・".join(bits)

    cat = replace(base, raw=round(raw, 3),
                  points=round(base.weight * raw, 1),
                  sufficiency=round(suff, 2), reason=reason,
                  plus=plus, minus=minus)
    return cat, confirm
