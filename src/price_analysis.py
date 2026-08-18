# -*- coding: utf-8 -*-
"""価格分析（指示書 第40・41章）。透明・再現可能。

各Comparableから「対象物件の推定価格」を復元し、外れ値を除外(IQR)したうえで
類似度で加重して推定適正価格レンジ（25/50/75パーセンタイル）を出す。
売出価格と比較して割安/概ね適正/割高を判定。適正価格は推定であり絶対値ではない。
ばらつきが大きいときは確信度を下げる。
"""
from __future__ import annotations
from typing import List, Optional, Tuple
from .models import SubjectProperty, Comparable, PriceAnalysis
from .config import CONFIG

# 価格算出は「最も似た近傍K件」を重視する（査定の考え方＝近い事例を優先）
K_NEAREST = CONFIG["k_nearest"]   # config.json で調整可
SIM_POWER = 3         # 加重の際、類似度を何乗するか（大きいほど近い事例を強く重視）
RENO_PREMIUM = CONFIG.get("reno_premium", 0.08)   # リフォーム済みの価値上乗せ率
RENO_MIN_AGE = CONFIG.get("reno_min_age", 15)     # この築年数以上で適用


def time_adjust(price: int, period_year: Optional[int], current_year: int,
                annual_rate: float) -> Tuple[int, bool]:
    """簡易な年次補正（既定 rate=0 なら補正なし・透明性優先）。"""
    if not period_year or annual_rate == 0.0:
        return price, False
    factor = (1.0 + annual_rate) ** (current_year - period_year)
    return int(round(price * factor)), True


def _estimate_subject_price(subj: SubjectProperty, comp: Comparable,
                            current_year: int, annual_rate: float
                            ) -> Optional[Comparable]:
    """1取引から対象物件の価格を復元。建物延床単価を優先、無ければ土地単価。"""
    t = comp.txn
    price, adjusted = time_adjust(t.trade_price, t.period_year,
                                  current_year, annual_rate)
    est = None
    basis = ""
    if t.building_area_m2 and subj.building_area_m2 and t.building_area_m2 > 0:
        unit = price / t.building_area_m2
        est = int(round(unit * subj.building_area_m2))
        basis = "building"
    elif t.land_area_m2 and subj.land_area_m2 and t.land_area_m2 > 0:
        unit = price / t.land_area_m2
        est = int(round(unit * subj.land_area_m2))
        basis = "land"
    else:
        return None
    comp.subject_price_estimate = est
    comp.price_basis = basis
    comp.time_adjusted = adjusted
    return comp


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    frac = k - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def _weighted_percentile(pairs: List[Tuple[float, float]], pct: float) -> float:
    """pairs=[(value, weight)] の加重パーセンタイル(pct:0..100)。"""
    if not pairs:
        return 0.0
    pairs = sorted(pairs, key=lambda x: x[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        return _percentile([v for v, _ in pairs], pct)
    target = total * pct / 100.0
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return v
    return pairs[-1][0]


def _iqr_filter(comps: List[Comparable]) -> Tuple[List[Comparable], int]:
    """推定価格のIQRで外れ値を除外。返り値=(残ったcomps, 除外件数)。"""
    vals = [float(c.subject_price_estimate) for c in comps]
    if len(vals) < 4:
        return comps, 0
    q1 = _percentile(vals, 25)
    q3 = _percentile(vals, 75)
    iqr = q3 - q1
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    kept = [c for c in comps if lo <= c.subject_price_estimate <= hi]
    return kept, len(comps) - len(kept)


def _median_unit_prices(comps: List[Comparable]) -> Tuple[Optional[int], Optional[int]]:
    b, l = [], []
    for c in comps:
        t = c.txn
        if t.building_area_m2 and t.building_area_m2 > 0 and t.trade_price:
            b.append(t.trade_price / t.building_area_m2)
        if t.land_area_m2 and t.land_area_m2 > 0 and t.trade_price:
            l.append(t.trade_price / t.land_area_m2)
    bm = int(_percentile(b, 50)) if b else None
    lm = int(_percentile(l, 50)) if l else None
    return bm, lm


def analyze_price(subj: SubjectProperty, comparables: List[Comparable],
                  current_year: int, annual_rate: float = 0.0,
                  min_count: int = 5, k_nearest: int = K_NEAREST) -> PriceAnalysis:
    # 推定価格を復元
    estimates: List[Comparable] = []
    for c in comparables:
        e = _estimate_subject_price(subj, c, current_year, annual_rate)
        if e and e.subject_price_estimate:
            estimates.append(e)

    if not estimates:
        return PriceAnalysis(None, None, None, "判定不可", None, "low", 0,
                             "類似成約が得られませんでした（情報不足）。", [])

    # 完全重複（同一価格・面積・築年・町名）を除去
    seen = set()
    deduped = []
    for c in estimates:
        t = c.txn
        key = (t.trade_price, t.land_area_m2, t.building_area_m2,
               t.build_year, t.district_name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    # 類似度の高い順に並べ、最類似のK件だけを価格算出に使う（k近傍）
    deduped.sort(key=lambda c: c.similarity_score, reverse=True)
    working = deduped[:max(3, k_nearest)]

    # 外れ値除外（IQR）
    filtered, trimmed = _iqr_filter(working)
    if len(filtered) < 3:  # 絞りすぎたら戻す
        filtered, trimmed = working, 0

    n = len(filtered)
    # 加重は類似度のSIM_POWER乗＝近い事例を強く重視
    pairs = [(float(c.subject_price_estimate), c.similarity_score ** SIM_POWER)
             for c in filtered]
    low = int(_weighted_percentile(pairs, 25))
    mid = int(_weighted_percentile(pairs, 50))
    high = int(_weighted_percentile(pairs, 75))

    bm, lm = _median_unit_prices(filtered)

    # リフォーム済み補正：類似成約は原則リフォーム前提でないため、
    # 築古のリフォーム済み物件は推定価格に価値を上乗せ（無料版は有無のみで一律）。
    reno_applied = False
    age = (current_year - subj.build_year) if subj.build_year else None
    if getattr(subj, "renovated", False) and (age is None or age >= RENO_MIN_AGE):
        f = 1.0 + RENO_PREMIUM
        low = int(round(low * f))
        mid = int(round(mid * f))
        high = int(round(high * f))
        reno_applied = True

    dispersion = round((high - low) / mid * 100.0, 1) if mid > 0 else None

    # 売出価格との比較
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

    # 確信度：件数・平均類似度・ばらつきを総合
    avg_sim = sum(c.similarity_score for c in filtered) / n
    if n >= min_count and avg_sim >= 0.6:
        conf = "high"
    elif n >= max(3, min_count // 2) and avg_sim >= 0.45:
        conf = "mid"
    else:
        conf = "low"
    # ばらつきが大きければ格下げ（レンジ幅が中央値の一定割合超）
    if dispersion is not None:
        if dispersion > 25 and conf == "high":
            conf = "mid"
        if dispersion > 45 and conf in ("high", "mid"):
            conf = "low"

    note = (f"類似成約 {n} 件（外れ値除外 {trimmed} 件）・平均類似度 {avg_sim:.2f}"
            f"・レンジ幅 {dispersion}%。"
            "推定適正価格は絶対値ではなく、類似事例からの推定レンジです。")
    if reno_applied:
        note += (f" リフォーム済みのため推定価格に+{int(RENO_PREMIUM*100)}%"
                 "を加味（無料版は有無のみ・内容は未評価）。")
    if n < min_count:
        note += f" 件数が少ない({n}<{min_count})ため参考値として扱ってください。"
    if dispersion is not None and dispersion > 60:
        note += " レンジ幅が広く、物件条件のばらつきが大きい可能性があります。"

    pa = PriceAnalysis(low, mid, high, verdict, deviation, conf, n, note,
                       sorted(filtered, key=lambda c: c.similarity_score,
                              reverse=True))
    pa.used_count = len(working)
    pa.trimmed_outliers = trimmed
    pa.dispersion_pct = dispersion
    pa.unit_building_median = bm
    pa.unit_land_median = lm
    return pa
