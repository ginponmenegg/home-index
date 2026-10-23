# -*- coding: utf-8 -*-
"""土地の採点（100点）。

■戸建・マンションと何が違うか
戸建は「建っているもの」を評価する。土地は「これから建てるもの」を計算する。
だから配点の主役が入れ替わる。

  建てられる家 25 ／ 接道 15 ／ リスク 25 ／ 立地 15 ／ 資産性 10 ／ 資金 10

リスクが戸建(15)・マンション(15)より重いのは、土地では地盤改良が実費で
そのまま乗ってくるため。建物が建った後で分かる戸建と違い、土地は買った側が
その費用を負う。

■価格を採点しない
土地の㎡単価はばらつきが大きく（実測で四分位幅が中央値の±40〜55%）、
戸建の価格20点と同じ精度は出ない。点数にはせず、近隣成約の分布を
そのまま見せる（land_price.py）。

■居住面積水準
延床の上限が住むのに足りるかは、住生活基本計画（全国計画）の居住面積水準で
測る。私の感覚ではなく公表された水準を使う。

  一般型誘導 : 単身55㎡ / 2人以上 25㎡×世帯人数＋25㎡
  都市居住型 : 単身40㎡ / 2人以上 20㎡×世帯人数＋15㎡
  最低       : 単身25㎡ / 2人以上 10㎡×世帯人数＋10㎡
  世帯人数が4人を超える場合は、上記の面積から5%を控除する。

  出典 https://www.mlit.go.jp/hakusyo/mlit/h24/hakusho/h25/html/nss00000.html
       https://www.stat.go.jp/data/jyutaku/2008/nihon/5_2.html

水準には「3歳未満は0.25人、3歳以上6歳未満は0.5人…」という子どもの按分が
あるが、**入れていない**。フォームで聞くのは世帯人数ひとつで、年齢を
聞いていないため。按分を入れたければ年齢の入力から要る。

■頭打ち（キャップ）
再建築できない疑いがある土地は、減点では足りない。80点から15点引いて65点と
出したら「まあまあ」に読まれる。再建築不可は住宅ローンがまず通らない
（金融機関が担保として評価しない）ので、住宅を建てる目的では前提が崩れる。
だから総合点そのものに上限をかける。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple
import datetime

from .config import CONFIG
from .land import BuildCapacity, MIN_FRONTAGE_M, MIN_ROAD_WIDTH_M
from .loan import LoanResult
from .models import LandSubject
from .scoring import (CategoryScore, CriticalRisk, Diagnosis, grade_of,
                      hazard_risks, highlights, score_asset, score_finance,
                      score_location, score_risk)

WEIGHTS = CONFIG["land_category_weights"]

# 建てられる家25点の内訳
# 間口はここでは採らない。間口＝接道の長さなので、接道側で一度だけ見る。
# 同じ数字で二度減点すると、旗竿地が二重に沈む。
BUILDABLE_PARTS = {"延床": 14, "建築面積": 7, "用途地域": 4}
# 接道15点の内訳
ROAD_PARTS = {"幅員": 7, "接道長さ": 4, "道路の種類": 4}

# 世帯人数の入力が無いときに置く値。結果画面に「4人家族を想定」と明記すること。
DEFAULT_HOUSEHOLD = 4

# 再建築できない疑いがあるときの総合点の上限。
SCORE_CAP_REBUILD = 30
# 市街化調整区域のときの上限。再建築不可より緩いのは、既存宅地・分家住宅・
# 都市計画法34条11号/12号の条例指定区域など、建てられる例外が実際に多いため。
# 「建てられない」と断定はできないが、「開発許可が要る」ことは確か。
SCORE_CAP_URBANIZATION = 50


# ---- 居住面積水準 ----
def _standard(single: float, per_person: float, offset: float,
              n: Optional[int]) -> float:
    n = n or DEFAULT_HOUSEHOLD
    if n <= 1:
        return float(single)
    area = per_person * n + offset
    if n > 4:
        area *= 0.95          # 4人を超える場合は5%を控除する
    return round(area, 1)


def guided_area_m2(household: Optional[int] = None,
                   urban: bool = False) -> float:
    """誘導居住面積水準。urban=True で都市居住型。"""
    if urban:
        return _standard(40, 20, 15, household)
    return _standard(55, 25, 25, household)


def minimum_area_m2(household: Optional[int] = None) -> float:
    """最低居住面積水準。"""
    return _standard(25, 10, 10, household)


def _ramp(x: float, lo: float, hi: float, ylo: float, yhi: float) -> float:
    if hi <= lo:
        return yhi
    return ylo + (yhi - ylo) * (x - lo) / (hi - lo)


def floor_area_raw(total_floor_m2: Optional[float],
                   household: Optional[int] = None) -> Optional[float]:
    """延床の上限が住むのに足りるか。0..1。

    段ではなく坂にしてある。124㎡と125㎡で点が跳ねる設計は、境目に落ちた
    人に説明がつかない。
    """
    if not total_floor_m2 or total_floor_m2 <= 0:
        return None
    lo = minimum_area_m2(household)                 # 4人=50㎡
    mid = guided_area_m2(household, urban=True)     # 4人=95㎡
    hi = guided_area_m2(household)                  # 4人=125㎡
    t = float(total_floor_m2)
    if t >= hi:
        return 1.0
    if t >= mid:
        return round(_ramp(t, mid, hi, 0.75, 1.0), 3)
    if t >= lo:
        return round(_ramp(t, lo, mid, 0.35, 0.75), 3)
    return round(max(0.0, _ramp(t, 0.0, lo, 0.0, 0.35)), 3)


def footprint_raw(max_footprint_m2: Optional[float]) -> Optional[float]:
    """1階の広さ。ここが小さいと総三階になるか、水回りが1階に収まらない。"""
    if not max_footprint_m2 or max_footprint_m2 <= 0:
        return None
    a = float(max_footprint_m2)
    if a >= 60:
        return 1.0
    if a >= 40:
        return round(_ramp(a, 40, 60, 0.6, 1.0), 3)
    if a >= 30:
        return round(_ramp(a, 30, 40, 0.3, 0.6), 3)
    return round(max(0.0, _ramp(a, 0, 30, 0.0, 0.3)), 3)


# 用途地域。隣に何が建つかで、建てる家の日当たりと静けさが決まる。
# 部分一致で上から順に見るので、細かいほうを先に置く（近隣商業 → 商業、
# 工業専用 → 工業）。
USE_DISTRICT_RAW: List[Tuple[str, float]] = [
    ("第一種低層住居専用", 1.0),
    ("第二種低層住居専用", 1.0),
    ("田園住居", 1.0),
    ("第一種中高層住居専用", 0.85),
    ("第二種中高層住居専用", 0.85),
    ("第一種住居", 0.7),
    ("第二種住居", 0.7),
    ("準住居", 0.7),
    ("近隣商業", 0.5),
    ("商業", 0.35),
    ("準工業", 0.4),
    ("工業専用", 0.0),
    ("工業", 0.2),
]


def use_district_raw(use_district: Optional[str]) -> Optional[float]:
    if not use_district:
        return None
    for token, raw in USE_DISTRICT_RAW:
        if token in use_district:
            return raw
    return None


def score_buildable(capacity: BuildCapacity,
                    use_district: Optional[str] = None,
                    household: Optional[int] = None) -> CategoryScore:
    """建てられる家（25点）。分からない項目は埋めず、充足度を下げる。"""
    w = WEIGHTS["建てられる家"]
    src: List[str] = []
    plus: List[str] = []
    minus: List[str] = []
    bits: List[str] = []

    parts = {
        "延床": floor_area_raw(capacity.max_total_floor_m2, household),
        "建築面積": footprint_raw(capacity.max_footprint_m2),
        "用途地域": use_district_raw(use_district),
    }
    known = {k: v for k, v in parts.items() if v is not None}
    if not known:
        return CategoryScore("建てられる家", w, 0.5, round(w * 0.5, 1), 0.0,
                             "敷地面積と住所が入ると、建てられる家の大きさを計算します",
                             [], plus=[], minus=[])

    total_w = sum(BUILDABLE_PARTS[k] for k in known)
    raw = sum(BUILDABLE_PARTS[k] * v for k, v in known.items()) / total_w
    coverage = total_w / sum(BUILDABLE_PARTS.values())
    # 延床は25点中14点を持つ主役。それが計算できていないのに、
    # 残りだけで満点が出てはいけない。score_risk がハザード未確認で
    # 0.7 に頭を押さえるのと同じ扱いにする。
    if parts["延床"] is None:
        raw = min(raw, 0.7)
        bits.append("建ぺい率・容積率が取れず、延床の上限を計算できていません")

    n = household or DEFAULT_HOUSEHOLD
    if capacity.max_total_floor_m2:
        src.append("reinfolib:XKT002")
        src.append("建築基準法52条")
        hi = guided_area_m2(household)
        t = capacity.max_total_floor_m2
        bits.append(f"延床の上限 {t:.0f}㎡（{n}人世帯の誘導水準 {hi:.0f}㎡）")
        if t >= hi:
            plus.append(f"延床の上限 {t:.0f}㎡。{n}人世帯の誘導居住面積水準を満たします")
        elif t < minimum_area_m2(household):
            minus.append(f"延床の上限 {t:.0f}㎡。{n}人世帯の最低居住面積水準に届きません")
        elif t < guided_area_m2(household, urban=True):
            minus.append(f"延床の上限 {t:.0f}㎡。{n}人世帯の誘導居住面積水準に届きません")
    if capacity.far_limited_by_road:
        minus.append(
            f"指定容積率{capacity.designated_far}%のうち、前面道路の制限で"
            f"使えるのは{capacity.road_far}%まで")
    if capacity.max_footprint_m2:
        bits.append(f"建築面積の上限 {capacity.max_footprint_m2:.0f}㎡")
        if capacity.max_footprint_m2 < 40:
            minus.append(
                f"1階の上限が{capacity.max_footprint_m2:.0f}㎡。"
                "三階建てにするか、水回りの配置を詰める必要があります")
    if use_district:
        bits.append(f"用途:{use_district}")
        src.append("reinfolib:XKT002")
        if "低層住居専用" in use_district or "田園住居" in use_district:
            plus.append(f"{use_district}。周囲も低層に制限され、日当たりが守られます")
        elif "工業専用" in use_district:
            minus.append(f"{use_district}。住宅は建てられません")
        elif "商業" in use_district or "工業" in use_district:
            minus.append(f"{use_district}。隣接地に住宅以外の建物が建ち得ます")
    if capacity.setback_m2:
        minus.append(
            f"セットバックでおよそ{capacity.setback_m2}㎡が使えません（概算）")

    return CategoryScore("建てられる家", w, round(raw, 3), round(w * raw, 1),
                         round(coverage, 2), "・".join(bits),
                         sorted(set(src)), plus=plus, minus=minus)


# 道路の種類ごとの持ち点（満点4）。
ROAD_TYPE_POINTS = {
    "公道": 4.0,
    "位置指定": 3.0,
    "私道": 2.0,
    "none": 0.0,
    "unknown": 2.0,
}
ROAD_TYPE_LABEL = {
    "公道": "公道に接道",
    "位置指定": "位置指定道路に接道",
    "私道": "私道に接道",
    "none": "建築基準法の道路に接していません",
    "unknown": "道路の種類が未確認",
}


def score_road(road_width_m: Optional[float] = None,
               frontage_m: Optional[float] = None,
               road_type: str = "unknown") -> CategoryScore:
    """接道（15点）。建て替えられるか、工事ができるか。家の大きさは見ない。

    frontage_m は間口＝道路に接している長さ。整形地では同じ数字なので、
    入力も採点もここで一度だけ扱う。駐車や建物の配置が窮屈になるのも、
    工事車両が入らないのも、元は同じ一つの事実。

    幅員は「建てられる家」でも効くが、あちらは連続量として結果の延床に、
    こちらは4mという法的な線として建て替えの可否に効く。別の問いなので
    二重計上ではない。ただし幅員ひとつで合計40点中17点前後が動く。
    """
    w = WEIGHTS["接道"]
    pts = 0.0
    known_w = 0
    bits: List[str] = []
    plus: List[str] = []
    minus: List[str] = []

    # ---- 幅員 7点 ----
    mw = ROAD_PARTS["幅員"]
    if road_type == "none":
        known_w += mw
        bits.append("道路に接していません")
    elif road_width_m is None:
        pts += mw * 0.5
        bits.append("前面道路の幅員が未確認")
    else:
        known_w += mw
        if road_width_m >= MIN_ROAD_WIDTH_M:
            pts += mw
            bits.append(f"前面道路 {road_width_m}m")
            plus.append(f"前面道路{road_width_m}m。幅員4m以上を満たします")
        elif road_width_m >= 1.8:
            pts += 2.5
            bits.append(f"前面道路 {road_width_m}m（4m未満）")
            minus.append(
                f"前面道路{road_width_m}m。建築基準法42条2項の道路であれば"
                "建て替えの際にセットバックが要ります")
        else:
            bits.append(f"前面道路 {road_width_m}m（1.8m未満）")
            minus.append(
                f"前面道路{road_width_m}m。建築基準法の道路として認められない"
                "おそれがあります")

    # ---- 間口＝接道の長さ 4点 ----
    # 法43条は2m以上。2mちょうどは法を満たすが余裕がない。4m以上あれば
    # 駐車も工事車両も普通に収まる。
    cw = ROAD_PARTS["接道長さ"]
    if road_type == "none":
        known_w += cw
        bits.append("接道なし")
    elif frontage_m is None:
        pts += 2.0
        bits.append("間口が未確認")
    else:
        known_w += cw
        if frontage_m >= 4.0:
            pts += 4.0
            bits.append(f"間口 {frontage_m}m")
        elif frontage_m >= MIN_FRONTAGE_M:
            pts += 2.0
            bits.append(f"間口 {frontage_m}m")
            minus.append(
                f"間口{frontage_m}m。法43条の2mは満たしますが余裕がありません。"
                "実測で2mを割ると再建築ができなくなり、駐車と工事車両も窮屈です")
        else:
            bits.append(f"間口 {frontage_m}m（2m未満）")
            minus.append(
                f"間口{frontage_m}m。建築基準法43条の2mに足りません")

    # ---- 道路の種類 4点 ----
    tw = ROAD_PARTS["道路の種類"]
    key = road_type if road_type in ROAD_TYPE_POINTS else "unknown"
    pts += ROAD_TYPE_POINTS[key]
    bits.append(ROAD_TYPE_LABEL[key])
    if key != "unknown":
        known_w += tw
    if key == "私道":
        minus.append("私道に接道。掘削の承諾・通行の承諾・持分の有無を確認してください")
    elif key == "位置指定":
        minus.append("位置指定道路。道路位置指定の図面で幅員と範囲を確認してください")
    elif key == "公道":
        plus.append("公道に接道")

    raw = pts / w
    suff = known_w / sum(ROAD_PARTS.values())
    return CategoryScore("接道", w, round(raw, 3), round(pts, 1),
                         round(suff, 2), "・".join(bits),
                         ["user", "建築基準法42条・43条"], plus=plus, minus=minus)


# ---- 頭打ち ----
@dataclass
class ScoreCap:
    limit: int
    reasons: List[str] = field(default_factory=list)


def score_cap(road_width_m: Optional[float] = None,
              frontage_m: Optional[float] = None,
              road_type: str = "unknown",
              use_district: Optional[str] = None,
              urbanization: Optional[str] = None) -> Optional[ScoreCap]:
    """建築確認が下りない疑いがあるときの総合点の上限。

    幅員4m未満だけでは頭打ちにしない。42条2項の道路ならセットバックして
    建て替えられる。ここで止めるのは「建てられない」ほうだけ。
    """
    hard: List[str] = []
    if road_type == "none":
        hard.append("建築基準法の道路に接していない可能性")
    if frontage_m is not None and frontage_m < MIN_FRONTAGE_M:
        hard.append(f"間口（道路に接している長さ）が{frontage_m}mで、"
                    "法43条の2mに足りない")
    if road_width_m is not None and road_width_m < 1.8:
        hard.append(f"前面道路の幅員が{road_width_m}mで、法上の道路と認められないおそれ")
    if use_district and "工業専用" in use_district:
        hard.append(f"{use_district}のため住宅を建てられない")
    if hard:
        return ScoreCap(SCORE_CAP_REBUILD, hard)
    if urbanization and "調整区域" in urbanization:
        return ScoreCap(SCORE_CAP_URBANIZATION,
                        ["市街化調整区域のため、建てるには開発許可が要る"])
    return None


# ---- 土地の形と高さ（2026-10-23）-------------------------------------------
# 形は資産性から、高さ・傾きはリスクから引く。どちらも「未確認」なら引かず、
# 情報充足度だけ下げる。調べていない人を悪い土地として採点しない。
#
# 形を資産性で見るのは、建てられる家の大きさが法律上は変わらないため
# （建ぺい率・容積率は竿の部分も含めた敷地全体にかかる）。差が出るのは、
# 使いにくさと、売るときの評価。国税庁の財産評価基本通達も、不整形地を
# 20項（付表5 不整形地補正率、最小0.60）、間口の狭い宅地を20-4項
# （付表6 間口狭小補正率。普通住宅地区で間口4m未満は0.90）で低く評価する。
#
# **旗竿地でも、竿の幅（＝間口）が4m以上なら整形地と同じ扱い**にする。
# 付表6も4mで線を引いている。竿の幅が4m未満の旗竿地は、接道の「間口」で
# すでに2点引かれている（工事車両・駐車・法43条の余裕）。ここで引く3点は
# 使いにくさと売るときの評価の分で、理由が違うので二重には数えない。
#
# 高さの2mは、宅地造成及び特定盛土等規制法施行令3条（e-Gov
# 337CO0000000016）。切土で高さ2mを超える崖を生ずるものなどが規制の
# 対象になる。崖は同令1条で、勾配が30度を超える土地。擁壁や造成の費用は
# 一次情報が無いので、金額は出さない。

LAND_SHAPES = (
    ("unknown", "未確認"),
    ("regular", "整形地（ほぼ長方形）"),
    ("flag", "旗竿地（細い通路の奥に敷地がある）"),
    ("irregular", "不整形地（三角・台形・L字など）"),
)
LAND_LEVELS = (
    ("unknown", "未確認"),
    ("flat", "ほぼ平ら（道路とほぼ同じ高さ）"),
    ("step", "道路と高低差がある（2m以下）"),
    ("slope", "敷地の中が傾斜している"),
    ("cliff", "道路と2mを超える高低差・がけがある"),
)
SHAPE_DEDUCT = {"flag": 3.0, "irregular": 2.0}         # 資産性から引く点
LEVEL_DEDUCT = {"step": 2.0, "slope": 2.0, "cliff": 5.0}  # リスクから引く点
FLAG_FULL_FRONTAGE_M = 4.0     # 竿の幅がこれ以上なら整形地と同じ
LONG_POLE_M = 20.0             # 東京都の安全条例で、竿の幅の基準が変わる長さ
CLIFF_M = 2.0
# 形・高さが未確認のときに、充足度に掛ける割合
UNKNOWN_SUFF = 0.85


def effective_shape(shape: str, frontage_m: Optional[float]) -> str:
    """採点に使う形。竿の幅が4m以上の旗竿地は、整形地と同じに扱う。"""
    if shape == "flag" and frontage_m is not None \
            and frontage_m >= FLAG_FULL_FRONTAGE_M:
        return "regular"
    return shape if shape in dict(LAND_SHAPES) else "unknown"


def _minus_points(cat: CategoryScore, pts: float) -> CategoryScore:
    raw = max(0.0, cat.raw - pts / cat.weight) if cat.weight else cat.raw
    return replace(cat, raw=round(raw, 3), points=round(cat.weight * raw, 1))


def apply_shape(cat: CategoryScore, subj) -> CategoryScore:
    """資産性に、土地の形を反映する。"""
    shape = getattr(subj, "land_shape", "unknown") or "unknown"
    frontage = getattr(subj, "frontage_m", None)
    eff = effective_shape(shape, frontage)
    minus, plus = list(cat.minus), list(cat.plus)
    reason = cat.reason
    if eff == "unknown":
        return replace(cat, sufficiency=round(cat.sufficiency * UNKNOWN_SUFF, 2),
                       reason=reason + "・土地の形は未確認")
    if shape == "flag" and eff == "regular":
        reason += f"・旗竿地（竿の幅{frontage}m。4m以上なので整形地と同じ扱い）"
        return replace(cat, reason=reason)
    if eff == "regular":
        return replace(cat, reason=reason + "・整形地")
    pts = SHAPE_DEDUCT[eff]
    if eff == "flag":
        width = f"竿の幅{frontage}m" if frontage is not None else "竿の幅が未入力"
        minus.append(f"旗竿地（{width}）。奥の敷地は日当たりと駐車の配置が"
                     "限られ、売るときの評価も整形地より低くなりやすい土地です")
        reason += "・旗竿地"
    else:
        minus.append("不整形地。建物を置けない部分が出やすく、売るときの評価も"
                     "整形地より低くなりやすい土地です")
        reason += "・不整形地"
    return replace(_minus_points(cat, pts), reason=reason, minus=minus,
                   plus=plus)


def apply_level(cat: CategoryScore, subj,
                retaining: Optional[str] = None) -> CategoryScore:
    """リスクに、道路との高さ・敷地の傾きを反映する。

    retaining はPROの擁壁の答え。擁壁に検査済証があるなら、減点を半分に
    する（擁壁が確認を受けている分、崩れる心配と作り直しの心配が減る）。
    """
    level = getattr(subj, "land_level", "unknown") or "unknown"
    if level not in dict(LAND_LEVELS):
        level = "unknown"
    reason = cat.reason
    if level == "unknown":
        return replace(cat, sufficiency=round(cat.sufficiency * UNKNOWN_SUFF, 2),
                       reason=reason + "・道路との高さは未確認")
    if level == "flat":
        return replace(cat, reason=reason + "・道路とほぼ同じ高さ")
    pts = LEVEL_DEDUCT[level]
    certified = (retaining == "certified" and level in ("step", "cliff"))
    if certified:
        pts = pts / 2
    minus = list(cat.minus)
    if level == "cliff":
        minus.append("道路と2mを超える高低差か、がけがあります。擁壁の検査済証と、"
                     "都道府県のがけ条例に当たるかを確かめてください")
        reason += "・2mを超える高低差・がけ"
    elif level == "step":
        minus.append("道路と高低差があります。土留めや階段、駐車場の造成の工事が"
                     "出ることがあります")
        reason += "・道路と高低差あり"
    else:
        minus.append("敷地の中が傾斜しています。平らにする造成や、基礎を深くする"
                     "工事が出ることがあります")
        reason += "・敷地が傾斜"
    if certified:
        reason += "（擁壁の検査済証ありのため減点は半分）"
    return replace(_minus_points(cat, pts), reason=reason, minus=minus)


def level_refund(cat: CategoryScore, subj, retaining: Optional[str]
                 ) -> CategoryScore:
    """PROで擁壁に検査済証があると分かったら、高低差の減点を半分戻す。

    無料の診断は擁壁を聞いていないので、apply_level は全額引いている。
    PROの答えが来たところで、半分を戻す（apply_level(retaining=...) と
    同じ結果になる）。
    """
    level = getattr(subj, "land_level", "unknown") or "unknown"
    if retaining != "certified" or level not in ("step", "cliff"):
        return cat
    back = LEVEL_DEDUCT[level] / 2
    raw = min(1.0, cat.raw + back / cat.weight) if cat.weight else cat.raw
    return replace(cat, raw=round(raw, 3), points=round(cat.weight * raw, 1),
                   reason=cat.reason + "（擁壁の検査済証ありのため、高低差の"
                   "減点は半分）")


def shape_level_notes(subj) -> Tuple[List[str], List[CriticalRisk]]:
    """要確認の文と、重大リスク。点数には入れない注意をここで出す。"""
    confirm: List[str] = []
    risks: List[CriticalRisk] = []
    shape = getattr(subj, "land_shape", "unknown") or "unknown"
    level = getattr(subj, "land_level", "unknown") or "unknown"
    pole = getattr(subj, "pole_length_m", None)
    if shape == "unknown":
        confirm.append("土地の形: 整形地か、旗竿地・不整形地かを、販売図面か"
                       "公図で確認してください")
    if level == "unknown":
        confirm.append("高さ: 道路との高低差と、敷地の傾きを現地で確認してください")
    if shape == "flag" and pole and pole > LONG_POLE_M:
        confirm.append(
            f"路地状部分: 竿の部分が{pole}mあります。都道府県の条例で、竿の長さ"
            "に応じて必要な幅が決められていることがあります（東京都の建築安全"
            "条例では、20mを超えると幅3m以上。江戸川区の手引きによる）。"
            "市区町村の建築指導課で確認してください")
    if level == "cliff":
        risks.append(CriticalRisk(
            "高低差・がけ", "medium", "unknown",
            "道路と2mを超える高低差か、がけがあります。擁壁の検査済証、"
            "都道府県のがけ条例、宅地造成の規制区域かどうかを確かめてください。"
            "擁壁や造成の費用は、いまの診断では出せません"))
    return confirm, risks


def _reweight(cat: CategoryScore, weight: int) -> CategoryScore:
    """戸建の配点で作られたスコアを、土地の配点に載せ替える。"""
    return replace(cat, weight=weight, points=round(weight * cat.raw, 1))


def build_land_diagnosis(subj: LandSubject,
                         capacity: BuildCapacity,
                         loan: Optional[LoanResult] = None,
                         use_district: Optional[str] = None,
                         urbanization: Optional[str] = None,
                         hazard=None,
                         facility=None,
                         shops=None,
                         pop_change_pct: Optional[float] = None,
                         population_trend: Optional[str] = None,
                         current_year: Optional[int] = None) -> Diagnosis:
    if current_year is None:
        current_year = datetime.date.today().year

    cats = [
        score_buildable(capacity, use_district, subj.household_size),
        score_road(subj.road_width_m, subj.frontage_m, subj.road_type),
        apply_level(_reweight(score_risk(use_district, urbanization, hazard),
                              WEIGHTS["リスク"]), subj),
        _reweight(score_location(subj, use_district, facility, shops),
                  WEIGHTS["立地"]),
        apply_shape(_reweight(score_asset(subj, use_district,
                                          population_trend, pop_change_pct),
                              WEIGHTS["資産性"]), subj),
        _reweight(score_finance(loan), WEIGHTS["資金"]),
    ]
    total = max(0, min(100, int(round(sum(c.points for c in cats)))))

    cap = score_cap(subj.road_width_m, subj.frontage_m, subj.road_type,
                    use_district, urbanization)
    capped = False
    if cap and total > cap.limit:
        total = cap.limit
        capped = True

    grade = grade_of(total)
    suff = int(round(sum(c.sufficiency * c.weight for c in cats)
                     / sum(c.weight for c in cats) * 100))
    strengths, weaknesses = highlights(cats)
    to_confirm = [f"{c.name}: {c.reason}" for c in cats if c.sufficiency < 0.5]

    # 建築条件付きは点数に入れない。土地の欠点ではなく、買い方の制約。
    # ただし注文住宅を建てたい人には決定的なので、要確認の先頭に出す。
    if getattr(subj, "building_condition", "unknown") == "attached":
        to_confirm.insert(0, (
            "建築条件付き: 売主の指定する会社と、一定期間内に建築請負契約を"
            "結ぶ条件が付いています。設計事務所や他の工務店では建てられません。"
            "条件を外せるか、外すときの価格を売主に確認してください"))
    elif getattr(subj, "building_condition", "unknown") == "unknown":
        to_confirm.append(
            "建築条件: 建築条件付きかどうかを確認してください。"
            "条件付きなら、建てる会社を選べません")

    risks: List[CriticalRisk] = []
    if cap:
        sev = "high" if cap.limit == SCORE_CAP_REBUILD else "medium"
        for r in cap.reasons:
            risks.append(CriticalRisk("建てられない可能性", sev, "unknown", r))
        if cap.limit == SCORE_CAP_REBUILD:
            to_confirm.insert(0, (
                "接道: 建築基準法43条2項の認定・許可で建てられる場合があります。"
                "市区町村の建築指導課で確認してください"))
        else:
            to_confirm.insert(0, (
                "都市計画: 既存宅地・分家住宅・条例指定区域など、"
                "市街化調整区域でも建てられる例外があります。"
                "市区町村の都市計画課で確認してください"))
    risks.extend(hazard_risks(hazard))
    sl_confirm, sl_risks = shape_level_notes(subj)
    to_confirm.extend(sl_confirm)
    risks.extend(sl_risks)

    comment = f"総合 {total}点 / {grade}。情報充足度 {suff}%。"
    if capped:
        comment += f"建てられない可能性があるため、{cap.limit}点を上限としています。"
    comment += ("土地の価格は点数に入れていません（近隣成約の分布で別に示します）。"
                "スコアはルール計算であり、未確認項目は評価に反映していません。"
                "最終判断は現地・専門家確認を前提としてください。")

    return Diagnosis(total, grade, cats, risks, strengths, weaknesses,
                    to_confirm, suff, comment,
                    datetime.datetime.now().isoformat(timespec="seconds"))
