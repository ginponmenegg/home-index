# -*- coding: utf-8 -*-
"""マンションのスコアリング。

新しく書いたのは資産性だけで、価格・立地・リスク・資金は戸建と同じ関数を使う。
score_price は PriceAnalysis しか見ず、score_risk は座標由来の情報しか見ず、
score_finance は LoanResult しか見ないため、そのまま通る。score_location も
subject から読むのは駅徒歩だけなので、MansionSubject がその名前を持っている。

配点は config.json の mansion_category_weights。管理は、入力してもらえる
管理費・修繕積立金だけで評価する。積立金の残高・大規模修繕の履歴・管理形態は
公的データから取れないので、依然として未評価のまま明示する。
"""
from __future__ import annotations
from dataclasses import replace
from typing import Optional, List
import datetime

from .models import MansionSubject, PriceAnalysis
from .loan import LoanResult
from .config import CONFIG
from .scoring import (CategoryScore, CriticalRisk, Diagnosis, grade_of, _clamp,
                      score_price, score_location, score_risk, score_finance,
                      _future_population_adj)

WEIGHTS = CONFIG["mansion_category_weights"]
REPAIR_GUIDE = CONFIG["mansion_repair_fund_guideline"]

# 新耐震基準は1981年6月1日以降の建築確認。築年は年単位でしか取れないため、
# 1981年築は旧耐震側に倒す（安全側）。境界の物件は結果画面で要確認と出す。
NEW_QUAKE_STANDARD_YEAR = 1982

# 向きの加減点（資産性の raw 0..1 に対して）。軽い調整にとどめる。
DIRECTION_ADJ = {"南": 0.04, "南東": 0.03, "南西": 0.03,
                 "東": 0.0, "西": 0.0,
                 "北東": -0.02, "北西": -0.02, "北": -0.04}


def is_new_quake_standard(build_year: Optional[int]) -> Optional[bool]:
    """新耐震かどうか。築年が無ければ None（不明を不明のまま返す）。"""
    if not build_year:
        return None
    return build_year >= NEW_QUAKE_STANDARD_YEAR


def _walk_raw(walk: Optional[int]) -> Optional[float]:
    if walk is None:
        return None
    if walk <= 5:
        return 0.95
    if walk <= 10:
        return 0.85
    if walk <= 15:
        return 0.72
    if walk <= 20:
        return 0.60
    return 0.48


def score_mansion_asset(subj: MansionSubject,
                        current_year: Optional[int] = None,
                        pop_change_pct: Optional[float] = None) -> CategoryScore:
    """資産性：駅徒歩と築年（新耐震か）を主に、階数・向き・将来人口で調整する。

    立地（生活利便）とは役割を分けてある。駅距離はここで主に効かせ、立地側では
    従属要素にとどめる。両方が駅距離で動くと同じことを二重に採点してしまう。
    """
    if current_year is None:
        current_year = datetime.date.today().year
    w = WEIGHTS["資産性"]
    bits: List[str] = []
    known = 0
    total_checks = 5

    # 駅徒歩（マンションは戸建より駅距離が効く）
    walk_raw = _walk_raw(subj.station_walk_min)
    if walk_raw is None:
        base = 0.6
        bits.append("駅徒歩は未入力")
    else:
        base = walk_raw
        known += 1
        bits.append(f"駅徒歩{subj.station_walk_min}分")

    # 築年・新耐震
    newq = is_new_quake_standard(subj.build_year)
    if newq is None:
        bits.append("築年は未入力")
    else:
        known += 1
        age = current_year - subj.build_year
        if not newq:
            base = base * 0.6
            bits.append(f"旧耐震（{subj.build_year}年築・築{age}年）")
        else:
            if age <= 10:
                base = min(1.0, base + 0.08)
            elif age <= 20:
                base = min(1.0, base + 0.03)
            elif age <= 35:
                base = base - 0.03
            else:
                base = base - 0.10
            bits.append(f"新耐震（{subj.build_year}年築・築{age}年）")
            if subj.build_year <= NEW_QUAKE_STANDARD_YEAR:
                bits.append("※建築確認の時期により旧耐震の可能性あり・要確認")

    # リフォーム済み。マンションには戸建のような「物件」カテゴリが無いので、
    # 内装の手入れは資産性（築年の目減り）を戻す形で効かせる。内容は見ていない。
    if getattr(subj, "renovated", False):
        base += 0.06
        bits.append("リフォーム済み(内容未評価)")

    # 所在階／総階数（軽め）
    if subj.floor is not None:
        known += 1
        if subj.floor <= 1:
            base -= 0.05
            bits.append("1階")
        elif subj.total_floors and subj.floor >= subj.total_floors:
            base += 0.03
            bits.append(f"最上階（{subj.floor}/{subj.total_floors}階）")
        else:
            tf = f"/{subj.total_floors}階" if subj.total_floors else "階"
            bits.append(f"{subj.floor}{tf}")

    # 向き（軽め）
    d = (subj.direction or "").strip()
    if d and d != "不明":
        known += 1
        adj = DIRECTION_ADJ.get(d)
        if adj is not None:
            base += adj
            bits.append(f"{d}向き")

    # 将来推計人口（250mメッシュ）。その地点で人が減るかどうかは、
    # マンションの出口（売れるか）に直結する。
    adj, pop_bit = _future_population_adj(pop_change_pct)
    if pop_bit:
        known += 1
        base += adj
        bits.append(pop_bit)

    raw = _clamp(base)
    suff = known / total_checks
    reason = "・".join(bits) if bits else "情報が不足しています"
    return CategoryScore("資産性", w, round(raw, 3), round(w * raw, 1),
                         round(suff, 2), reason, ["user"])


def repair_fund_band(total_floors: Optional[int],
                     floor_area_m2: Optional[float] = None) -> dict:
    """修繕積立金の目安レンジ（円/㎡・月）。出典は国土交通省のガイドライン。

    資料は地上20階で分け、20階未満はさらに建築延床面積で4区分している。
    延床面積は購入検討者が普通は知らないので入力に取っておらず、その場合は
    4区分の幅を包絡した値を返す（exact=False）。広く取るぶん判定は甘くなり、
    「明らかに低いか」しか見ない。延床面積が分かるなら区分を特定できるよう、
    引数だけ用意してある。

    前提の違いは承知して使うこと。この目安は「計画期間全体における修繕積立金の
    平均額」であって、いま毎月いくら払っているかではない。均等積立方式なら
    ほぼ一致するが、段階増額積立方式なら現在額は平均額を下回る。
    """
    if total_floors and total_floors >= 20:
        b = REPAIR_GUIDE["over_20f"]
        return {"low": b["low"], "high": b["high"], "avg": b["avg"],
                "exact": True}
    bands = REPAIR_GUIDE["under_20f_bands"]
    if floor_area_m2:
        for b in bands:
            cap = b.get("max_floor_area_m2")
            if cap is None or floor_area_m2 < cap:
                return {"low": b["low"], "high": b["high"],
                        "avg": b["avg"], "exact": True}
    return {"low": min(b["low"] for b in bands),
            "high": max(b["high"] for b in bands),
            "avg": None, "exact": False}

def score_mansion_management(subj: MansionSubject) -> CategoryScore:
    """管理：修繕積立金が専有面積に対して妥当な水準かを見る。

    管理費の「適正額」には公的な目安が無い（規模と共用設備で大きく変わる）ので、
    高い安いの判定はしない。月々いくら出ていくかの内訳として扱う。
    """
    w = WEIGHTS["管理"]
    src = ["国土交通省 修繕積立金ガイドライン"]
    fee = subj.management_fee
    fund = subj.repair_fund
    area = subj.exclusive_area_m2

    if not fund or not area or area <= 0:
        bits = []
        if fee:
            bits.append(f"管理費 月{fee:,}円")
        bits.append("修繕積立金が未入力のため水準を判定できません")
        return CategoryScore("管理", w, 0.5, round(w * 0.5, 1), 0.1,
                             "・".join(bits), src)

    unit = fund / area                      # 円/㎡・月
    band = repair_fund_band(subj.total_floors)
    span = f"目安 {band['low']}〜{band['high']}円/㎡"

    # 幅の下限値は、マンション長寿命化促進税制と管理計画の認定で「著しく低額で
    # ないか」が問われる線として使われている。ただし資料自身が「幅に収まって
    # いないからといって直ちに不適切とは限らない」と断っているので、下回った
    # ことだけを理由に大きくは減点せず、確認を促す扱いにする。
    if unit < band["low"]:
        raw = 0.5
        judge = f"{span}の下限を下回る"
    elif unit <= band["high"]:
        raw = 0.85
        judge = f"{span}の範囲"
    else:
        raw = 0.75
        judge = f"{span}を上回る"
    bits = [f"修繕積立金 月{fund:,}円（{unit:.0f}円/㎡）＝{judge}"]
    if fee:
        bits.append(f"管理費 月{fee:,}円")
        bits.append(f"合計 月{fee + fund:,}円")
    # 残高も履歴も見ていない以上、満点の充足度は名乗れない
    suff = 0.6 if fee else 0.45
    return CategoryScore("管理", w, round(raw, 3), round(w * raw, 1),
                         suff, "・".join(bits), src)


def _reweight(cat: CategoryScore, weight: int) -> CategoryScore:
    """戸建用の配点で作られたスコアを、マンションの配点に載せ替える。"""
    return replace(cat, weight=weight, points=round(weight * cat.raw, 1))


def build_mansion_diagnosis(subj: MansionSubject,
                            price_a: Optional[PriceAnalysis],
                            loan: Optional[LoanResult] = None,
                            use_district: Optional[str] = None,
                            urbanization: Optional[str] = None,
                            hazard=None,
                            facility=None,
                            current_year: Optional[int] = None,
                            shops=None,
                            pop_change_pct: Optional[float] = None) -> Diagnosis:
    if current_year is None:
        current_year = datetime.date.today().year

    cats = [
        _reweight(score_price(price_a), WEIGHTS["価格"]),
        score_mansion_asset(subj, current_year, pop_change_pct),
        score_mansion_management(subj),
        _reweight(score_location(subj, use_district, facility, shops),
                  WEIGHTS["立地"]),
        _reweight(score_risk(use_district, urbanization, hazard), WEIGHTS["リスク"]),
        _reweight(score_finance(loan), WEIGHTS["資金"]),
    ]
    total = max(0, min(100, int(round(sum(c.points for c in cats)))))
    grade = grade_of(total)
    suff = int(round(sum(c.sufficiency * c.weight for c in cats)
                     / sum(c.weight for c in cats) * 100))

    strengths = [f"{c.name}: {c.reason}" for c in cats if c.raw >= 0.8]
    weaknesses = [f"{c.name}: {c.reason}" for c in cats if c.raw <= 0.5]
    to_confirm = [f"{c.name}: {c.reason}" for c in cats if c.sufficiency < 0.5]

    risks: List[CriticalRisk] = []
    if is_new_quake_standard(subj.build_year) is False:
        risks.append(CriticalRisk(
            "旧耐震基準", "high", "confirmed",
            f"{subj.build_year}年築。耐震診断・改修の履歴と、住宅ローン控除の"
            "適用可否を確認してください"))
    if not (hazard and getattr(hazard, "checked", False)):
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
    if price_a and price_a.verdict == "割高の可能性" \
            and (price_a.deviation_pct or 0) >= 15:
        risks.append(CriticalRisk("価格乖離", "medium", "confirmed",
                                  f"推定中央値比 {price_a.deviation_pct:+}%"))
    # 積立金が目安を大きく下回るのは、将来の値上げや一時金徴収に直結する。
    if subj.repair_fund and subj.exclusive_area_m2:
        unit = subj.repair_fund / subj.exclusive_area_m2
        band = repair_fund_band(subj.total_floors)
        if unit < band["low"]:
            risks.append(CriticalRisk(
                "修繕積立金が目安の下限を下回る", "medium", "confirmed",
                f"月{subj.repair_fund:,}円＝{unit:.0f}円/㎡。国土交通省の目安は"
                f"{band['low']}〜{band['high']}円/㎡です。下限を下回る額は、"
                "管理計画の認定で「著しく低額でない特段の理由」を求められる"
                "水準にあたります。ただし目安は計画期間全体の平均額なので、"
                "当初を低く抑えて段階的に上げる方式なら現在額が下回ること自体は"
                "起こります。長期修繕計画の内容と積立方式を確認してください"))
    # 額が分かっても、貯まっているか・使われたかは分からない。そこは必ず残す。
    risks.append(CriticalRisk(
        "積立金残高と修繕履歴が未確認", "medium", "unknown",
        "積立金の残高、大規模修繕の実施履歴、管理形態、滞納の有無は公的データから"
        "取得できず、この診断に含まれていません。重要事項説明で必ず確認してください"))

    comment = (f"総合 {total}点 / {grade}。情報充足度 {suff}%。"
               "管理は入力された管理費・修繕積立金だけで評価しており、"
               "積立金残高・修繕履歴・管理形態は含まれていません。"
               "スコアはルール計算であり、未確認項目は反映していません。"
               "最終判断は現地・専門家確認を前提としてください。")

    return Diagnosis(total, grade, cats, risks, strengths, weaknesses,
                     to_confirm, suff, comment,
                     datetime.datetime.now().isoformat(timespec="seconds"))
