# -*- coding: utf-8 -*-
"""類似取引の抽出（指示書 第25章）。ルールベースで透明・機械学習は使わない。

XIT001 には緯度経度が無いため、位置は「市区町村コード＋町名(DistrictName)」で近似する
（Phase B の実データで確認した制約）。
"""
from __future__ import annotations
from typing import List, Optional, Dict
from .models import SubjectProperty, Transaction, Comparable
from .config import CONFIG

# ---- 重み・ルール（config.json で上書き可能） ----
DEFAULT_WEIGHTS = CONFIG["sim_weights"]
NEWBUILD_WEIGHTS = CONFIG["newbuild_sim_weights"]
NEIGHBOR_RADIUS_M = CONFIG["neighbor_radius_m"]

# 中古戸建の抽出対象（新築も近隣の宅地成約を基準にする）
DETACHED_TYPES = ("宅地(土地と建物)",)

# 新築の類似は「新築相当の成約」に限定：成約年−築年 が この値以下（年粒度のため
# 11ヶ月ルールを『築1年以内相当』で近似）。config.json で調整可。
NEWBUILD_MAX_AGE = CONFIG["newbuild_max_age"]


def is_newbuild_txn(t, max_age: int = NEWBUILD_MAX_AGE) -> bool:
    if t.build_year is None or t.period_year is None:
        return False
    age = t.period_year - t.build_year
    return 0 <= age <= max_age


def _ratio_similarity(a: Optional[float], b: Optional[float],
                      full: float = 0.10, zero: float = 0.50) -> float:
    """差の割合で減衰。±full以内=1.0、±zeroで0.5、それ以上は逓減。"""
    if not a or not b or a <= 0 or b <= 0:
        return 0.0
    diff = abs(a - b) / ((a + b) / 2.0)
    if diff <= full:
        return 1.0
    # full..zero を 1.0..0.5 に線形、その先は0.5から更に逓減
    if diff <= zero:
        return 1.0 - 0.5 * (diff - full) / (zero - full)
    return max(0.0, 0.5 * (1.0 - (diff - zero) / zero))


def _year_similarity(a: Optional[int], b: Optional[int],
                     full: int = 3, half: int = 10) -> float:
    if not a or not b:
        return 0.0
    d = abs(a - b)
    if d <= full:
        return 1.0
    if d <= half:
        return 1.0 - 0.5 * (d - full) / (half - full)
    return max(0.0, 0.5 * (1.0 - (d - half) / half))


def _location_similarity(subj: SubjectProperty, txn: Transaction) -> float:
    if subj.municipality_code and txn.municipality_code:
        if subj.municipality_code != txn.municipality_code:
            return 0.0  # 別の市区町村は対象外
    # 同一町名は最優先
    if subj.district_name and txn.district_name and \
            subj.district_name == txn.district_name:
        return 1.0
    # 実距離が分かる場合は距離で評価（近いほど高い）
    dm = txn.distance_m
    if dm is not None:
        if dm <= 500:
            return 0.9
        if dm <= 1000:
            return 0.75
        if dm <= 1500:
            return 0.55
        if dm <= 2000:
            return 0.4
        return 0.15
    return 0.3  # 距離不明・別町名


def _recency_similarity(txn: Transaction, current_year: int) -> float:
    if not txn.period_year:
        return 0.0
    d = current_year - txn.period_year
    if d <= 0:
        return 1.0
    # 直近を強く優先：1年ごとに0.18減（約5.5年で0近辺）。
    # 「同エリアで古い」より「隣接エリアで新しい／同エリアで新しく面積差あり」を上位に。
    return max(0.0, 1.0 - d * 0.18)


def similarity(subj: SubjectProperty, txn: Transaction, current_year: int,
               weights: Dict[str, float] = None) -> (float, Dict[str, float]):
    w = weights or DEFAULT_WEIGHTS
    parts = {
        "location": _location_similarity(subj, txn),
        "land_area": _ratio_similarity(subj.land_area_m2, txn.land_area_m2),
        "building_area": _ratio_similarity(subj.building_area_m2, txn.building_area_m2),
        "build_year": _year_similarity(subj.build_year, txn.build_year),
        "recency": _recency_similarity(txn, current_year),
        "city_planning": 1.0 if (subj.city_planning and txn.city_planning
                                 and subj.city_planning == txn.city_planning) else 0.0,
        "structure": 1.0 if (subj.structure and txn.structure
                             and subj.structure == txn.structure) else 0.0,
    }
    score = sum(w.get(k, 0.0) * v for k, v in parts.items())
    return score, parts


def extract_comparables(subj: SubjectProperty, txns: List[Transaction],
                        current_year: int,
                        weights: Dict[str, float] = None,
                        min_score: float = 0.30,
                        top_n: int = 20,
                        detached_only: bool = True,
                        max_year_gap: int = 25,
                        newbuild_only: bool = False,
                        radius_m: Optional[int] = NEIGHBOR_RADIUS_M) -> List[Comparable]:
    """同種フィルタ→類似度→上位N件を返す。

    max_year_gap: 対象と築年が この年数を超えて離れた取引は除外（査定で古すぎる
    事例を使わないのと同じ。対象/取引の築年が不明な場合は除外しない）。
    newbuild_only: True のとき「新築相当の成約」だけを対象にする（新築戸建の査定用）。
    """
    candidates: List[Comparable] = []
    for t in txns:
        if detached_only and t.type not in DETACHED_TYPES:
            continue
        if not t.trade_price or t.trade_price <= 0:
            continue
        # 新築査定：新築相当の成約のみ
        if newbuild_only and not is_newbuild_txn(t):
            continue
        # 地理的範囲：同町 or 近接（実距離）に限定。距離が判明していて範囲外なら除外。
        same_town = bool(subj.district_name and t.district_name
                         and subj.district_name == t.district_name)
        if radius_m is not None and not same_town and t.distance_m is not None \
                and t.distance_m > radius_m:
            continue
        # 築年が離れすぎた事例を除外（新築査定時はnewbuild条件が優先されるので緩和）
        if (not newbuild_only and max_year_gap is not None and subj.build_year
                and t.build_year
                and abs(subj.build_year - t.build_year) > max_year_gap):
            continue
        score, parts = similarity(subj, t, current_year, weights)
        if score < min_score:
            continue
        candidates.append(Comparable(
            txn=t, similarity_score=round(score, 4),
            similarity_breakdown={k: round(v, 3) for k, v in parts.items()},
            subject_price_estimate=None, price_basis="", time_adjusted=False))
    candidates.sort(key=lambda c: c.similarity_score, reverse=True)
    return candidates[:top_n]
