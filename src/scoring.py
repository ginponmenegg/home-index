# -*- coding: utf-8 -*-
"""Scoring Engine（Phase F）＋ Critical Risk（指示書 第11・42・43章）。

重要：スコアは全てルール/計算で算出する。AIは点数を出さない（第13章）。
情報が不足する項目は勝手に埋めず、confidence/充足度を下げて明示する（第14章）。
仮の重み（第11章）：物件25 / 立地20 / 価格20 / リスク15 / 資金10 / 資産性10。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import datetime

from .models import SubjectProperty, PriceAnalysis
from .loan import LoanResult
from .config import CONFIG

WEIGHTS = CONFIG["category_weights"]


@dataclass
class CategoryScore:
    name: str
    weight: int
    raw: float               # 0..1
    points: float            # weight * raw
    sufficiency: float       # 0..1（情報充足度）
    reason: str
    sources: List[str] = field(default_factory=list)


@dataclass
class CriticalRisk:
    type: str
    severity: str            # high / medium / low
    status: str              # confirmed / unknown
    evidence: str


@dataclass
class Diagnosis:
    total_score: int
    grade: str
    categories: List[CategoryScore]
    critical_risks: List[CriticalRisk]
    strengths: List[str]
    weaknesses: List[str]
    to_confirm: List[str]
    data_sufficiency: int    # 全体の情報充足度(%)
    comment: str
    generated_at: str


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# ---- 各カテゴリのルール ----
def score_building(subj: SubjectProperty, current_year: int) -> CategoryScore:
    src = []
    # 新築戸建：築年数では満点にしない。建物性能・設備・施工は未確認として評価。
    is_new = (subj.property_type == "shinchiku_kodate") or \
        (subj.build_year is not None and current_year - subj.build_year <= 0)
    if is_new:
        raw = 0.8
        if subj.structure and any(s in subj.structure for s in ("ＲＣ", "RC", "ＳＲＣ", "SRC")):
            raw = min(1.0, raw + 0.03)
        reason = "新築（築浅）。建物性能評価・設備仕様・施工会社は未確認"
        return CategoryScore("物件", WEIGHTS["物件"], round(raw, 3),
                             round(WEIGHTS["物件"] * raw, 1), 0.4, reason,
                             ["user/URL"])
    if subj.build_year:
        age = current_year - subj.build_year
        if age <= 5:
            raw = 1.0
        elif age <= 15:
            raw = 0.85
        elif age <= 25:
            raw = 0.70
        elif age <= 35:
            raw = 0.50
        elif age <= 45:
            raw = 0.35
        else:
            raw = 0.20
        reason = f"築{age}年"
        src.append("user/URL")
    else:
        raw, reason = 0.5, "築年不明"
    # 構造の軽微な補正
    if subj.structure and any(s in subj.structure for s in ("ＲＣ", "RC", "ＳＲＣ", "SRC")):
        raw = _clamp(raw + 0.05)
        reason += "・RC系"
    # リフォーム済みは築古の評価を持ち上げる（無料版は有無のみ・内容は未評価）
    if getattr(subj, "renovated", False):
        raw = _clamp(raw + 0.12)
        reason += "・リフォーム済み(内容未評価)"
    # 建物状態(雨漏り/シロアリ等)は未取得のため充足度を抑える
    suff = 0.6 if subj.build_year else 0.3
    reason += "（建物内部の状態は未確認）"
    return CategoryScore("物件", WEIGHTS["物件"], round(raw, 3),
                         round(WEIGHTS["物件"] * raw, 1), suff, reason, src)


def score_price(price_a: Optional[PriceAnalysis]) -> CategoryScore:
    w = WEIGHTS["価格"]
    if not price_a or price_a.verdict == "判定不可":
        return CategoryScore("価格", w, 0.5, w * 0.5, 0.2,
                             "類似成約が不足し価格評価できず", ["reinfolib:XIT001"])
    v = price_a.verdict
    d = price_a.deviation_pct or 0.0
    if v == "割安の可能性":
        raw = 1.0
    elif v == "概ね適正":
        raw = 0.85
    else:  # 割高
        raw = _clamp(0.85 - (d / 100.0) * 1.5, 0.2, 0.85)
    reason = f"{v}（中央値比 {d:+}%・類似{price_a.comparable_count}件）"
    suff = {"high": 1.0, "mid": 0.7, "low": 0.4}.get(price_a.confidence, 0.5)
    return CategoryScore("価格", w, round(raw, 3), round(w * raw, 1), suff,
                         reason, ["reinfolib:XIT001"])


def _walk_score(m):
    # 徒歩分の段階評価。15分超を急落させず、15-20-25分でなだらかに刻む。
    if m <= 5:
        return 1.0
    if m <= 10:
        return 0.90
    if m <= 15:
        return 0.80
    if m <= 20:
        return 0.70
    if m <= 25:
        return 0.58
    return 0.45


def score_location(subj: SubjectProperty, use_district: Optional[str],
                   facility=None) -> CategoryScore:
    w = WEIGHTS["立地"]
    src = []
    walk = subj.station_walk_min
    bus = getattr(subj, "bus_min", None)
    # 徒歩分が未入力なら、周辺施設APIの最寄駅距離から推定
    if walk is None and not bus and facility and getattr(facility, "nearest_station_m", None):
        walk = max(1, round(facility.nearest_station_m / 80.0))

    if bus:
        # バス便：バス乗車＋バス停まで徒歩の合計で評価し、駅近より低めに位置づける
        total = bus + (walk or 0)
        if total <= 15:
            raw = 0.55
        elif total <= 25:
            raw = 0.45
        elif total <= 35:
            raw = 0.35
        else:
            raw = 0.25
        stop = f"・バス停徒歩{walk}分" if walk else ""
        reason = f"バス便（駅までバス{bus}分{stop}）"
        src.append("user/URL")
        suff = 0.6
    elif walk is not None:
        raw = _walk_score(walk)
        reason = f"駅徒歩{walk}分"
        src.append("user/URL")
        suff = 0.6
    else:
        raw, reason, suff = 0.5, "駅距離不明", 0.3

    if use_district:
        reason += f"・用途:{use_district}"
        src.append("reinfolib:XKT002")
        suff = min(1.0, suff + 0.05)

    checked = bool(facility and getattr(facility, "checked", False))
    if checked:
        src.append("reinfolib:XKT(facility)")
        suff = max(suff, 0.85)
        bits = []
        hm = getattr(facility, "nearest_hospital_m", None)
        if hm is not None:
            bits.append(f"病院{hm}m")
            if hm <= 1500:
                raw = min(1.0, raw + 0.05)
        sm = getattr(facility, "nearest_school_m", None)
        if sm is not None:
            bits.append(f"学校{sm}m")
            if sm <= 1000:
                raw = min(1.0, raw + 0.05)
        if getattr(facility, "hospital_count_1km", 0):
            bits.append(f"1km内医療{facility.hospital_count_1km}件")
        if bits:
            reason += "（" + "・".join(bits) + "）"
    else:
        reason += "（周辺施設は未評価）"

    return CategoryScore("立地", w, round(raw, 3), round(w * raw, 1), suff,
                         reason, src)


def score_risk(use_district: Optional[str], urbanization: Optional[str],
               hazard=None) -> CategoryScore:
    """hazard: enrichment.HazardResult（Noneなら未取得）。"""
    w = WEIGHTS["リスク"]
    src = []
    raw = 1.0  # 高いほど低リスク＝良い
    notes = []
    suff = 0.3
    if urbanization and "調整区域" in urbanization:
        raw = min(raw, 0.2)
        notes.append("市街化調整区域の可能性")
        src.append("reinfolib")

    checked = bool(hazard and getattr(hazard, "checked", False))
    if not checked:
        raw = min(raw, 0.7)   # 安全と断定できない
        notes.append("ハザード未確認（要確認）")
    else:
        src.append("reinfolib:XKT(hazard)")
        suff = 0.85
        hit = []
        fr = getattr(hazard, "flood_rank", None)
        if fr:
            if fr >= 4:
                raw = min(raw, 0.35); hit.append(f"洪水浸水{hazard.flood_label}")
            elif fr == 3:
                raw = min(raw, 0.5); hit.append(f"洪水浸水{hazard.flood_label}")
            elif fr == 2:
                raw -= 0.2; hit.append(f"洪水浸水{hazard.flood_label}")
            else:
                raw -= 0.1; hit.append(f"洪水浸水{hazard.flood_label}")
        sed = getattr(hazard, "sediment", None)
        if sed == "特別警戒区域":
            raw = min(raw, 0.3); hit.append("土砂災害特別警戒区域")
        elif sed == "警戒区域":
            raw = min(raw, 0.55); hit.append("土砂災害警戒区域")
        if getattr(hazard, "tsunami", False):
            raw -= 0.3; hit.append("津波浸水想定域")
        if getattr(hazard, "storm_surge", False):
            raw -= 0.2; hit.append("高潮浸水想定域")
        raw = max(0.0, raw)
        notes.append("・".join(hit) if hit else "指定ハザード区域に該当なし")

    reason = "・".join(notes) if notes else "重大リスクの検出なし"
    return CategoryScore("リスク", w, round(raw, 3), round(w * raw, 1), suff,
                         reason, src)


def score_finance(loan: Optional[LoanResult]) -> CategoryScore:
    w = WEIGHTS["資金"]
    if not loan or loan.burden_ratio is None:
        return CategoryScore("資金", w, 0.5, w * 0.5, 0.0,
                             "年収・頭金の入力で返済負担を評価可能", [])
    rb = loan.burden_ratio
    income = getattr(loan, "income", None)  # 年収（円）
    limit = 35 if (income is not None and income >= 4_000_000) else 30  # 400万円で基準切替
    if rb <= 20:
        raw = 1.0
    elif rb <= 25:
        raw = 0.85
    elif rb <= limit:
        raw = 0.7
    elif rb <= limit + 5:
        raw = 0.5
    else:
        raw = 0.3
    reason = f"返済負担率 {rb}%（基準{limit}%・月々{loan.monthly_payment:,}円）"
    return CategoryScore("資金", w, round(raw, 3), round(w * raw, 1), 0.9,
                         reason, ["計算"])


def score_asset(subj: SubjectProperty, use_district: Optional[str],
                population_trend: Optional[str]) -> CategoryScore:
    """資産性：駅近接性を段階評価し、ゆとりある広さ・人口動向で加減点。"""
    w = WEIGHTS["資産性"]
    src = []
    bits = []
    walk = subj.station_walk_min
    bus = getattr(subj, "bus_min", None)
    if bus:  # バス便は資産性で不利
        raw = 0.45
        bits.append("バス便")
    elif walk is not None:
        if walk <= 10:
            raw = 0.85
        elif walk <= 15:
            raw = 0.75
        elif walk <= 20:
            raw = 0.65
        elif walk <= 25:
            raw = 0.55
        else:
            raw = 0.45
        bits.append(f"駅徒歩{walk}分")
    else:
        raw = 0.55
        bits.append("駅距離不明")

    # ゆとりある広さは資産性にプラス（土地130㎡~ or 建物100㎡~）
    if (subj.land_area_m2 and subj.land_area_m2 >= 130) or \
       (subj.building_area_m2 and subj.building_area_m2 >= 100):
        raw += 0.08
        bits.append("ゆとりある広さ")

    suff = 0.4
    if population_trend:
        src.append("e-Stat")
        bits.append(f"人口{population_trend}")
        suff = 0.6
        if population_trend in ("増加", "微増"):
            raw += 0.05
        elif population_trend == "減少":
            raw -= 0.08
    else:
        bits.append("人口動向は未取得")

    raw = max(0.0, min(1.0, raw))
    return CategoryScore("資産性", w, round(raw, 3), round(w * raw, 1), suff,
                         "・".join(bits), src)


def grade_of(total: int) -> str:
    if total >= 80:
        return "A"
    if total >= 65:
        return "B"
    if total >= 50:
        return "C"
    if total >= 35:
        return "D"
    return "E"


def build_diagnosis(subj: SubjectProperty, price_a: Optional[PriceAnalysis],
                    loan: Optional[LoanResult] = None,
                    use_district: Optional[str] = None,
                    urbanization: Optional[str] = None,
                    hazard=None,
                    facility=None,
                    population_trend: Optional[str] = None,
                    current_year: Optional[int] = None) -> Diagnosis:
    if current_year is None:
        current_year = datetime.date.today().year

    cats = [
        score_building(subj, current_year),
        score_location(subj, use_district, facility),
        score_price(price_a),
        score_risk(use_district, urbanization, hazard),
        score_finance(loan),
        score_asset(subj, use_district, population_trend),
    ]
    total = int(round(sum(c.points for c in cats)))
    total = max(0, min(100, total))
    grade = grade_of(total)

    # 情報充足度（重み加重平均）
    suff = int(round(sum(c.sufficiency * c.weight for c in cats)
                     / sum(c.weight for c in cats) * 100))

    strengths = [f"{c.name}: {c.reason}" for c in cats if c.raw >= 0.8]
    weaknesses = [f"{c.name}: {c.reason}" for c in cats if c.raw <= 0.5]
    to_confirm = [f"{c.name}: {c.reason}" for c in cats if c.sufficiency < 0.5]

    # Critical Risk
    risks: List[CriticalRisk] = []
    if urbanization and "調整区域" in urbanization:
        risks.append(CriticalRisk("市街化調整区域", "high", "confirmed",
                                  "再建築・利用に制限の可能性"))
    checked = bool(hazard and getattr(hazard, "checked", False))
    if not checked:
        risks.append(CriticalRisk("ハザード未確認", "medium", "unknown",
                                  "洪水/土砂/津波等を公的データで要確認"))
    else:
        if getattr(hazard, "sediment", None) == "特別警戒区域":
            risks.append(CriticalRisk("土砂災害特別警戒区域(レッドゾーン)", "high",
                                      "confirmed", "建築制限・移転勧告等の対象になり得る"))
        elif getattr(hazard, "sediment", None) == "警戒区域":
            risks.append(CriticalRisk("土砂災害警戒区域(イエローゾーン)", "medium",
                                      "confirmed", "警戒避難体制の対象区域"))
        fr = getattr(hazard, "flood_rank", None)
        if fr and fr >= 3:
            risks.append(CriticalRisk("洪水浸水想定(大)", "high", "confirmed",
                                      f"想定浸水深 {hazard.flood_label}"))
        elif fr:
            risks.append(CriticalRisk("洪水浸水想定", "medium", "confirmed",
                                      f"想定浸水深 {hazard.flood_label}"))
        if getattr(hazard, "tsunami", False):
            risks.append(CriticalRisk("津波浸水想定域", "high", "confirmed",
                                      "津波浸水想定区域に位置"))
        if getattr(hazard, "storm_surge", False):
            risks.append(CriticalRisk("高潮浸水想定域", "medium", "confirmed",
                                      "高潮浸水想定区域に位置"))
    if price_a and price_a.verdict == "割高の可能性" and (price_a.deviation_pct or 0) >= 15:
        risks.append(CriticalRisk("価格乖離", "medium", "confirmed",
                                  f"推定中央値比 {price_a.deviation_pct:+}%"))

    comment = (f"総合 {total}点 / {grade}。情報充足度 {suff}%。"
               "スコアはルール計算であり、未確認項目は評価に反映していません。"
               "最終判断は現地・専門家確認を前提としてください。")

    return Diagnosis(total, grade, cats, risks, strengths, weaknesses,
                     to_confirm, suff, comment,
                     datetime.datetime.now().isoformat(timespec="seconds"))
