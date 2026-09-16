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


# ============================================================
# B群 ── 建てられる形を決める規制
# ============================================================
# 角地緩和だけは、ここで点を足し引きしない。建ぺい率そのものが変わるので、
# build_capacity に corner_designated を渡して計算し直す。結果は延床と
# 建築面積の数字に出る（land.coverage_with_relaxations）。

RULE_CHOICES: Dict[str, List[Tuple[str, str]]] = {
    "corner": [(UNKNOWN, "未確認"),
               ("designated", "角地で、特定行政庁の指定がある"),
               ("corner_only", "角地だが、指定があるか分からない"),
               ("no", "角地ではない")],
    "fire_zone": [(UNKNOWN, "未確認"),
                  ("none", "防火の指定はない"),
                  ("art22", "法22条区域"),
                  ("quasi", "準防火地域"),
                  ("fire", "防火地域")],
    "height_district": [(UNKNOWN, "未確認"),
                        ("none", "高度地区の指定はない"),
                        ("h3", "第三種高度地区"),
                        ("h2", "第二種高度地区"),
                        ("h1", "第一種高度地区")],
    "district_plan": [(UNKNOWN, "未確認"),
                      ("no", "地区計画・建築協定はない"),
                      ("yes", "地区計画または建築協定がある")],
    "scenic": [(UNKNOWN, "未確認"),
               ("no", "風致地区・景観地区ではない"),
               ("yes", "風致地区または景観地区")],
}

RULE_LABELS = {"corner": "角地かどうか", "fire_zone": "防火の指定",
               "height_district": "高度地区", "district_plan": "地区計画・建築協定",
               "scenic": "風致地区・景観地区"}

RULE_SECTIONS = [
    ("建てられる形を決める規制",
     "都市計画の話です。市区町村の都市計画課か、重要事項説明書で分かります。"
     "**角地の指定があるかどうかで、建てられる大きさが変わります。**",
     ["corner", "fire_zone", "height_district", "district_plan", "scenic"]),
]

# 建てられる家（25点）の raw への足し引き。角地はここに入れない
# （建ぺい率そのものが変わるため、延床と建築面積の数字に出る）。
RULE_ADJUST: Dict[Tuple[str, str], float] = {
    ("fire_zone", "none"): 0.02,
    ("fire_zone", "art22"): 0.0,
    ("fire_zone", "quasi"): -0.01,
    ("fire_zone", "fire"): -0.03,
    ("height_district", "none"): 0.02,
    ("height_district", "h3"): -0.02,
    ("height_district", "h2"): -0.04,
    ("height_district", "h1"): -0.06,
    ("district_plan", "no"): 0.02,
    ("district_plan", "yes"): -0.06,
    ("scenic", "no"): 0.01,
    ("scenic", "yes"): -0.05,
}

RULE_PLUS_TEXT = {
    ("corner", "designated"): "特定行政庁が指定した角地。建ぺい率が10%上がります",
    ("district_plan", "no"): "地区計画・建築協定の制限がありません",
    ("height_district", "none"): "高度地区の指定がありません",
}
RULE_MINUS_TEXT = {
    ("corner", "corner_only"): "角地ですが、特定行政庁の指定があるか"
                               "分かりません。指定がなければ建ぺい率は"
                               "上がりません（法53条3項2号）",
    ("fire_zone", "fire"): "防火地域。建物を耐火構造にする必要があり、"
                           "工事費が上がります。ただし耐火建築物にすれば"
                           "建ぺい率が10%上がります（法53条3項1号）",
    ("fire_zone", "quasi"): "準防火地域。外壁や開口部に制限がかかり、"
                            "工事費に響きます。耐火・準耐火建築物にすれば"
                            "建ぺい率が10%上がります",
    ("height_district", "h1"): "第一種高度地区。北側の斜線制限が厳しく、"
                               "北側の屋根を削ることになります",
    ("height_district", "h2"): "第二種高度地区。北側に斜線制限がかかります",
    ("height_district", "h3"): "第三種高度地区。北側に斜線制限がかかります",
    ("district_plan", "yes"): "地区計画または建築協定があります。外壁の後退、"
                              "高さ、屋根の形、色まで決まっていることがあり、"
                              "設計が始まってから気づくと計画のやり直しになります",
    ("scenic", "yes"): "風致地区・景観地区。建ぺい率や高さ、外観に"
                       "上乗せの制限がかかります",
}

RULE_CONFIRM_TEXT = {
    "corner": "角地: 角地の建ぺい率の緩和は、特定行政庁が指定した角地だけに"
              "かかります（法53条3項2号）。指定があるかを建築指導課で"
              "確認してください",
    "fire_zone": "防火: 防火地域・準防火地域・法22条区域のどれかを"
                 "都市計画課で確認してください。工事費に直接効きます",
    "height_district": "高度地区: 指定があるかを都市計画課で確認してください。"
                       "北側の斜線が厳しいと、思った形の家になりません",
    "district_plan": "地区計画: 地区計画・建築協定の有無を都市計画課で"
                     "確認してください。外壁後退や意匠の制限がかかります",
    "scenic": "風致地区: 風致地区・景観地区かどうかを確認してください",
}

RULE_NOT_ANSWERED = {UNKNOWN, "corner_only"}
RULE_MAX_UP = 0.08
RULE_MAX_DOWN = -0.20


def rule_detail_from(values: Dict[str, str]) -> Dict[str, str]:
    """フォームの値からB群の答えを取り出す。知らない値は未確認に倒す。"""
    out = {}
    for key, choices in RULE_CHOICES.items():
        v = (values.get(key) or UNKNOWN).strip()
        out[key] = v if v in dict(choices) else UNKNOWN
    return out


def corner_designated(rules: Dict[str, str]) -> Optional[bool]:
    """建ぺい率の緩和をかけてよいか。**指定が確認できたときだけ True。**

    「角地だが指定は不明」を True にすると、建てられない家を建てられると
    言うことになる。
    """
    v = rules.get("corner")
    if v == "designated":
        return True
    if v == "no":
        return False
    return None


def score_land_rules(base: CategoryScore,
                     rules: Dict[str, str]) -> Tuple[CategoryScore, List[str]]:
    """建てられる家のカテゴリを、都市計画の答えで上書きする。

    角地はここで足し引きしない。建ぺい率が変わるので、延床と建築面積の
    数字そのものが動く（呼び出し側が build_capacity をやり直す）。
    """
    plus, minus, confirm = list(base.plus), list(base.minus), []
    bits = []
    delta = 0.0

    for key in RULE_CHOICES:
        v = rules.get(key, UNKNOWN)
        delta += RULE_ADJUST.get((key, v), 0.0)
        if (key, v) in RULE_PLUS_TEXT:
            plus.append(RULE_PLUS_TEXT[(key, v)])
        if (key, v) in RULE_MINUS_TEXT:
            minus.append(RULE_MINUS_TEXT[(key, v)])
        if v in RULE_NOT_ANSWERED:
            confirm.append(RULE_CONFIRM_TEXT[key])
        else:
            bits.append(f"{RULE_LABELS[key]}:{dict(RULE_CHOICES[key])[v]}")

    raw = base.raw + max(RULE_MAX_DOWN, min(RULE_MAX_UP, delta))
    raw = max(0.0, min(1.0, raw))

    answered = sum(1 for k in RULE_CHOICES
                   if rules.get(k, UNKNOWN) not in RULE_NOT_ANSWERED)
    ratio = answered / len(RULE_CHOICES)
    suff = 0.6 * base.sufficiency + 0.4 * ratio

    reason = base.reason
    if bits:
        reason += "／" + "・".join(bits)
    cat = replace(base, raw=round(raw, 3),
                  points=round(base.weight * raw, 1),
                  sufficiency=round(suff, 2), reason=reason,
                  plus=plus, minus=minus)
    return cat, confirm
