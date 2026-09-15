# -*- coding: utf-8 -*-
"""近隣の土地取引から、㎡単価の分布を出す。

■なぜ「推定価格」を出さないか
土地の㎡単価はばらつきが大きい。実測（2026-09-15・不動産情報ライブラリ）で、
四分位の幅が船橋市で中央値の±55%、小田原市で±40%。同じ町でも、角地か、
間口が広いか、形がいびつか、道路が何mかで大きく動く。

戸建でさえ価格は100点中20点にとどめている。土地でそれ以上の精度は出ない。
だからここは**点数を付けない**。近隣でいくらで売買されているかの分布を
そのまま見せて、判断は読む人に返す。

■代わりに条件をそろえる
XIT001 の 宅地(土地) には、幅員・道路の種類・土地の形状・建ぺい率・容積率が
入っている（実測で96〜100%が埋まっている）。戸建やマンションの成約より項目が
濃い。同じ町名というだけで並べるのではなく、幅員や形状で絞った分布も出せる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .models import Transaction

LAND_TYPE = "宅地(土地)"

# 分布を出すのに要る最低件数。これを下回ったら数字を出さない。
# 3件の中央値は、真ん中の1件そのもの。分布とは呼べない。
MIN_SAMPLES = 5


@dataclass
class LandMarket:
    """近隣の土地取引の分布。点数には使わない。"""
    count: int = 0
    district: Optional[str] = None
    unit_low: Optional[int] = None      # ㎡単価の第1四分位
    unit_mid: Optional[int] = None      # 中央値
    unit_high: Optional[int] = None     # 第3四分位
    subject_unit: Optional[int] = None  # 対象地の㎡単価
    spread_pct: Optional[int] = None    # ばらつき（四分位幅÷中央値）
    years: List[int] = field(default_factory=list)
    # 近隣の傾向。対象地の話ではないので、そう分かる言葉で出すこと。
    private_road_pct: Optional[int] = None    # 私道に接する割合
    narrow_road_pct: Optional[int] = None     # 幅員4m未満の割合
    irregular_pct: Optional[int] = None       # 不整形の割合
    notes: List[str] = field(default_factory=list)


def is_land_txn(t: Transaction) -> bool:
    return (t.type or "") == LAND_TYPE


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] * (1 - (k - lo)) + v[hi] * (k - lo)


def analyze_land_market(txns: List[Transaction],
                        district_name: Optional[str] = None,
                        subject_price: Optional[int] = None,
                        subject_area_m2: Optional[float] = None) -> LandMarket:
    """町名で絞って㎡単価の分布を出す。足りなければ市区町村まで広げる。"""
    m = LandMarket()
    land = [t for t in txns if is_land_txn(t) and t.trade_price
            and t.land_area_m2 and t.land_area_m2 > 0]
    if not land:
        m.notes.append("近隣の土地取引が見つかりませんでした")
        return m

    same = [t for t in land if district_name and t.district_name == district_name]
    if len(same) >= MIN_SAMPLES:
        use, m.district = same, district_name
    else:
        use, m.district = land, None
        if district_name:
            m.notes.append(
                f"{district_name}の取引が{len(same)}件しか無いため、"
                "市区町村全体で見ています")

    if len(use) < MIN_SAMPLES:
        m.count = len(use)
        m.notes.append(
            f"取引が{len(use)}件しかないため、分布を出していません"
            f"（{MIN_SAMPLES}件から）")
        return m

    units = [t.trade_price / t.land_area_m2 for t in use]
    m.count = len(use)
    m.unit_low = int(_pct(units, 0.25))
    m.unit_mid = int(_pct(units, 0.50))
    m.unit_high = int(_pct(units, 0.75))
    if m.unit_mid:
        m.spread_pct = int(round(100 * (m.unit_high - m.unit_low) / 2 / m.unit_mid))
    m.years = sorted({t.period_year for t in use if t.period_year})

    if subject_price and subject_area_m2 and subject_area_m2 > 0:
        m.subject_unit = int(subject_price / subject_area_m2)

    return m


def _share(rows, hit) -> Optional[int]:
    got = [r for r in rows if hit(r) is not None]
    if len(got) < MIN_SAMPLES:
        return None
    return int(round(100 * sum(1 for r in got if hit(r)) / len(got)))


def add_neighbourhood_traits(m: LandMarket, txns: List[Transaction],
                             district_name: Optional[str] = None) -> LandMarket:
    """近隣の土地の傾向を足す。**対象地の話ではない。**

    XIT001 の 宅地(土地) は、幅員・道路の種類・形状がほぼ埋まっている
    （実測で96〜100%）。「この町の成約の31%が私道接道だった」のような形で、
    何を確かめるべきかの見当をつけるために使う。
    対象地がそうだという意味には決してならないので、画面の言葉に気をつけること。
    """
    land = [t for t in txns if is_land_txn(t)]
    if district_name:
        same = [t for t in land if t.district_name == district_name]
        if len(same) >= MIN_SAMPLES:
            land = same
    if len(land) < MIN_SAMPLES:
        return m

    m.private_road_pct = _share(
        land, lambda t: ("私道" in t.road_type) if t.road_type else None)
    m.narrow_road_pct = _share(
        land, lambda t: (t.road_width_m < 4.0) if t.road_width_m else None)
    m.irregular_pct = _share(
        land, lambda t: ("不整形" in t.land_shape) if t.land_shape else None)
    return m
