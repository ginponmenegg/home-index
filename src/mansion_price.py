# -*- coding: utf-8 -*-
"""マンションの価格分析（㎡単価ベース）。

戸建は「土地＋建物」を復元して比べるが、マンションは専有面積あたりの単価で
比べるほうが素直なので、そこだけ別扱いにする。考え方（近さ・築年・時期で
似た事例を選ぶ／外れ値を落とす／ばらつきが大きければ確信度を下げる）は
price_analysis.py と同じで、集計の道具もそちらから借りている。

出力は既存の PriceAnalysis にそろえてあるので、score_price と結果テンプレートは
戸建と共通のものがそのまま使える。
"""
from __future__ import annotations
from typing import List, Optional

from .models import MansionSubject, Transaction, Comparable, PriceAnalysis
from .config import CONFIG
# 集計の道具は戸建版と共有する（重複させると片方だけ直す事故が起きる）
from .price_analysis import (_percentile, _weighted_percentile, _iqr_filter,
                             time_adjust, SIM_POWER)

# 不動産情報ライブラリ XIT001 の Type。マンションはこの区分で入ってくる
# （小田原市の実データで確認：宅地(土地と建物)561 / 宅地(土地)293 /
#  中古マンション等217 / 林地10 / 農地3）。
MANSION_TYPES = ("中古マンション等",)

# 実データで見えた注意点：同一市内でも築年で㎡単価が5〜6倍違う
# （1975年築 11.4万円/㎡ に対し 2009年築 69.3万円/㎡）。
# 築年を軽く見ると推定が壊れるので、類似度の build_year と max_year_gap は
# 下げないこと。

SIM_WEIGHTS = CONFIG["mansion_sim_weights"]
K_NEAREST = CONFIG["k_nearest"]
NEIGHBOR_RADIUS_M = CONFIG["neighbor_radius_m"]
MAX_YEAR_GAP = CONFIG["max_year_gap"]


def txn_area_m2(t: Transaction) -> Optional[float]:
    """取引レコードの専有面積。

    XIT001 のマンションは面積が Area に入る（正規化時に land_area_m2 へ渡る）。
    小田原市2024-2025の実データ217件で確認したところ、217件すべてが Area 側で、
    TotalFloorArea は0件だった。将来変わっても落ちないよう両方を見ている。
    どちらも無ければ None を返し、その事例は使わない（埋めない）。
    """
    for v in (t.land_area_m2, t.building_area_m2):
        if v and v > 0:
            return v
    return None


def is_mansion_txn(t: Transaction) -> bool:
    """マンションの成約かどうか。

    区分は「中古マンション等」で入ってくるが、表記が揺れても拾えるよう
    「マンション」を含むかで判定する。宅地・林地・農地はこれで落ちる。
    """
    return bool(t.type and "マンション" in t.type)


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


def _area_similarity(a: Optional[float], b: Optional[float]) -> float:
    """専有面積の近さ。同じ㎡単価でも狭い住戸は割高になりやすいので軽く見る。"""
    if not a or not b or a <= 0 or b <= 0:
        return 0.0
    diff = abs(a - b) / ((a + b) / 2.0)
    if diff <= 0.10:
        return 1.0
    if diff <= 0.40:
        return 1.0 - 0.5 * (diff - 0.10) / 0.30
    return max(0.0, 0.5 * (1.0 - (diff - 0.40) / 0.40))


def _location_similarity(subj: MansionSubject, t: Transaction,
                         radius_m: Optional[float]) -> float:
    """同一市区町村を前提に、町名一致と距離で近さを測る。"""
    if subj.municipality_code and t.municipality_code \
            and subj.municipality_code != t.municipality_code:
        return 0.0
    if subj.district_name and t.district_name \
            and subj.district_name == t.district_name:
        return 1.0
    if t.distance_m is not None:
        if radius_m and t.distance_m > radius_m:
            return 0.0
        # 500m以内はほぼ同等、そこから2kmまでで落とす
        if t.distance_m <= 500:
            return 0.9
        return max(0.2, 0.9 - 0.7 * (t.distance_m - 500) / 1500.0)
    # 距離が付いていない（町名のジオコーディングができなかった）場合は市内扱い
    return 0.35 if radius_m is None else 0.0


def _recency_similarity(period_year: Optional[int], current_year: int) -> float:
    if not period_year:
        return 0.0
    d = max(0, current_year - period_year)
    return max(0.0, 1.0 - 0.18 * d)


def extract_mansion_comparables(subj: MansionSubject, txns: List[Transaction],
                                current_year: int,
                                radius_m: Optional[float] = NEIGHBOR_RADIUS_M,
                                max_year_gap: int = MAX_YEAR_GAP
                                ) -> List[Comparable]:
    """マンションの成約から類似事例を選ぶ。似ていない事例は最初から落とす。"""
    out: List[Comparable] = []
    for t in txns:
        if not is_mansion_txn(t) or not t.trade_price:
            continue
        area = txn_area_m2(t)
        if not area:
            continue
        if subj.build_year and t.build_year \
                and abs(subj.build_year - t.build_year) > max_year_gap:
            continue
        loc = _location_similarity(subj, t, radius_m)
        if loc <= 0.0:
            continue
        parts = {
            "location": loc,
            "build_year": _year_similarity(subj.build_year, t.build_year),
            "recency": _recency_similarity(t.period_year, current_year),
            "area": _area_similarity(subj.exclusive_area_m2, area),
        }
        score = sum(SIM_WEIGHTS.get(k, 0.0) * v for k, v in parts.items())
        if score <= 0.0:
            continue
        out.append(Comparable(txn=t, similarity_score=round(score, 4),
                              similarity_breakdown={k: round(v, 3)
                                                    for k, v in parts.items()},
                              subject_price_estimate=None, price_basis="area",
                              time_adjusted=False))
    out.sort(key=lambda c: c.similarity_score, reverse=True)
    return out


def same_building_candidates(subj: MansionSubject,
                             comparables: List[Comparable]
                             ) -> List[Comparable]:
    """同じ建物の別住戸である可能性が高い事例を拾う。

    取引価格情報に建物名は無い（実データで全項目を確認済み）。個人が特定
    されないよう匿名化されていて、場所は町名までしか出ない。だからマンション名
    そのもので突き合わせることはできない。

    代わりに「町名が一致し、築年が完全に一致する」事例を集める。同じ町に
    同じ年に建ったマンションはそう多くないので、同一建物である可能性は高い。
    ただし可能性であって断定ではないので、呼び出し側は必ずその旨を表示する。
    """
    if not subj.district_name or not subj.build_year:
        return []
    out = [c for c in comparables
           if c.txn.district_name == subj.district_name
           and c.txn.build_year == subj.build_year]
    out.sort(key=lambda c: c.similarity_score, reverse=True)
    return out


def analyze_mansion_price(subj: MansionSubject, comparables: List[Comparable],
                          current_year: int, annual_rate: float = 0.0,
                          min_count: int = 5,
                          k_nearest: int = K_NEAREST) -> PriceAnalysis:
    """専有面積㎡単価から推定価格レンジを出し、売出価格と比べる。

    事例が取れないときは価格を断定せず「判定不可」を返す。
    """
    if not subj.exclusive_area_m2 or subj.exclusive_area_m2 <= 0:
        return PriceAnalysis(None, None, None, "判定不可", None, "low", 0,
                             "専有面積が未入力のため㎡単価で比較できません。", [])

    estimates: List[Comparable] = []
    for c in comparables:
        area = txn_area_m2(c.txn)
        if not area or not c.txn.trade_price:
            continue
        price, adjusted = time_adjust(c.txn.trade_price, c.txn.period_year,
                                      current_year, annual_rate)
        unit = price / area
        c.subject_price_estimate = int(round(unit * subj.exclusive_area_m2))
        c.price_basis = "area"
        c.time_adjusted = adjusted
        estimates.append(c)

    if not estimates:
        return PriceAnalysis(None, None, None, "判定不可", None, "low", 0,
                             "近隣のマンション成約が得られませんでした（情報不足）。", [])

    # 完全重複（同一価格・面積・築年・町名）を除去
    seen = set()
    deduped = []
    for c in estimates:
        t = c.txn
        key = (t.trade_price, txn_area_m2(t), t.build_year, t.district_name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    deduped.sort(key=lambda c: c.similarity_score, reverse=True)
    working = deduped[:max(3, k_nearest)]

    filtered, trimmed = _iqr_filter(working)
    if len(filtered) < 3:
        filtered, trimmed = working, 0

    n = len(filtered)
    pairs = [(float(c.subject_price_estimate), c.similarity_score ** SIM_POWER)
             for c in filtered]
    low = int(_weighted_percentile(pairs, 25))
    mid = int(_weighted_percentile(pairs, 50))
    high = int(_weighted_percentile(pairs, 75))

    units = [c.txn.trade_price / txn_area_m2(c.txn) for c in filtered]
    unit_median = int(_percentile(units, 50)) if units else None

    dispersion = round((high - low) / mid * 100.0, 1) if mid > 0 else None

    verdict = "判定不可"
    deviation = None
    if subj.price and mid > 0:
        deviation = round((subj.price - mid) / mid * 100.0, 1)
        if subj.price < low:
            verdict = "割安の可能性"
        elif subj.price > high:
            verdict = "割高の可能性"
        else:
            verdict = "概ね適正"

    avg_sim = sum(c.similarity_score for c in filtered) / n
    if n >= min_count and avg_sim >= 0.6:
        conf = "high"
    elif n >= max(3, min_count // 2) and avg_sim >= 0.45:
        conf = "mid"
    else:
        conf = "low"
    if dispersion is not None:
        if dispersion > 25 and conf == "high":
            conf = "mid"
        if dispersion > 45 and conf in ("high", "mid"):
            conf = "low"

    note = (f"マンション成約 {n} 件（外れ値除外 {trimmed} 件）・平均類似度 {avg_sim:.2f}"
            f"・レンジ幅 {dispersion}%。専有面積㎡単価の中央値から推定しています。"
            "推定適正価格は絶対値ではなく、類似事例からの推定レンジです。")
    if n < min_count:
        note += f" 件数が少ない({n}<{min_count})ため参考値として扱ってください。"
    if dispersion is not None and dispersion > 60:
        note += " レンジ幅が広く、階数・向き・管理状態などのばらつきが大きい可能性があります。"

    pa = PriceAnalysis(low, mid, high, verdict, deviation, conf, n, note,
                       sorted(filtered, key=lambda c: c.similarity_score,
                              reverse=True))
    pa.used_count = len(working)
    pa.trimmed_outliers = trimmed
    pa.dispersion_pct = dispersion
    # 専有面積の㎡単価。戸建の「建物㎡単価」の枠をそのまま使う（表示も共通）
    pa.unit_building_median = unit_median
    return pa
