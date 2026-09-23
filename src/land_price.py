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

from .citycode import same_town
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

    same = [t for t in land if same_town(district_name, t.district_name)]
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
        same = [t for t in land if same_town(district_name, t.district_name)]
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


# ---- 条件を揃えた分布（PRO）----
#
# 無料の分布は「同じ町の土地の成約」を全部まとめたもの。角地も旗竿地も、
# 私道も公道も、40㎡も200㎡も同じ袋に入っている。だからばらつきが
# 中央値の±24%まで広がる（船橋市前原西・実測）。
#
# PROでは、対象地と条件の近いものだけに絞る。比べる相手が近くなる。
#
# **ただし「絞れば正確になる」とは言わない。** 件数が減るぶん、数字は
# 少数の成約に振り回されやすくなる。だから絞った件数を必ず並べて出し、
# 絞る前の分布も消さずに残す。読む人が両方を見て決められるようにする。

# 絞り込みの条件。**大事な順**に並べる。件数が足りないときは、
# 後ろから順に外していく。
#
# 道路の種類を先頭に置いたのは、実測で私道と公道の差が最も大きかったため
# （船橋市三咲は成約の62%が私道で、坪単価の中央値が36万円。前原西の
# 97万円と比べると、駅距離だけでは説明がつかない差になる）。
MATCH_FILTERS = ("road_type", "road_width", "ratio", "area")

# 幅員の帯。実測値そのものではなく帯で合わせる（4.0mと4.2mを別物に
# しても意味がない）。
def road_band(w):
    if w is None:
        return None
    if w < 4.0:
        return "4m未満"
    if w < 6.0:
        return "4〜6m"
    return "6m以上"


AREA_TOLERANCE = 0.30      # 面積は±30%まで同規模とみなす


@dataclass
class MatchedMarket:
    """条件を揃えた分布。絞る前の分布と並べて見せること。"""
    count: int = 0                      # 絞ったあとの件数
    pool: int = 0                       # 絞る前の件数
    unit_low: Optional[int] = None
    unit_mid: Optional[int] = None
    unit_high: Optional[int] = None
    spread_pct: Optional[int] = None
    applied: List[str] = field(default_factory=list)   # 効かせた条件
    dropped: List[str] = field(default_factory=list)   # 外した条件
    notes: List[str] = field(default_factory=list)


MATCH_LABEL = {"road_type": "道路の種類", "road_width": "前面道路の幅員",
               "ratio": "建ぺい率・容積率", "area": "敷地面積"}


def _matches(t: Transaction, key: str, subj: dict) -> Optional[bool]:
    """1件が条件に合うか。相手の項目が空なら None（判定不能）。"""
    if key == "road_type":
        if not t.road_type or not subj.get("road_type"):
            return None
        mine = "私道" in subj["road_type"]
        theirs = "私道" in t.road_type
        return mine == theirs
    if key == "road_width":
        a, b = road_band(t.road_width_m), road_band(subj.get("road_width_m"))
        if a is None or b is None:
            return None
        return a == b
    if key == "ratio":
        if not subj.get("coverage_ratio") or not t.coverage_ratio:
            return None
        if t.coverage_ratio != subj["coverage_ratio"]:
            return False
        if subj.get("floor_area_ratio") and t.floor_area_ratio:
            return t.floor_area_ratio == subj["floor_area_ratio"]
        return True
    if key == "area":
        mine = subj.get("land_area_m2")
        if not mine or not t.land_area_m2:
            return None
        return abs(t.land_area_m2 - mine) / mine <= AREA_TOLERANCE
    return None


def _keep(rows, keys, subj):
    """判定不能（相手の項目が空）は落とさない。空欄を不一致として扱うと、
    埋まっている物件だけが残って、分布が偏る。"""
    out = []
    for t in rows:
        if all(_matches(t, k, subj) is not False for k in keys):
            out.append(t)
    return out


def analyze_matched_market(txns: List[Transaction], subj: dict,
                           district_name: Optional[str] = None,
                           subject_price: Optional[int] = None,
                           subject_area_m2: Optional[float] = None
                           ) -> MatchedMarket:
    """対象地と条件の近い成約だけで、㎡単価の分布を出す。

    subj は対象地の条件（road_type / road_width_m / coverage_ratio /
    floor_area_ratio / land_area_m2）。
    """
    m = MatchedMarket()
    land = [t for t in txns if is_land_txn(t) and t.trade_price
            and t.land_area_m2 and t.land_area_m2 > 0]
    same = [t for t in land if same_town(district_name, t.district_name)]
    pool = same if len(same) >= MIN_SAMPLES else land
    m.pool = len(pool)
    if m.pool < MIN_SAMPLES:
        m.notes.append("絞り込みの前に、比べられる成約が足りません")
        return m

    keys = list(MATCH_FILTERS)
    rows = _keep(pool, keys, subj)
    while len(rows) < MIN_SAMPLES and keys:
        m.dropped.insert(0, MATCH_LABEL[keys.pop()])
        rows = _keep(pool, keys, subj)
    if len(rows) < MIN_SAMPLES:
        m.count = len(rows)
        m.notes.append(
            f"条件を揃えると{len(rows)}件しか残らないため、"
            f"絞った分布は出していません（{MIN_SAMPLES}件から）")
        return m

    m.applied = [MATCH_LABEL[k] for k in keys]
    m.count = len(rows)
    units = [t.trade_price / t.land_area_m2 for t in rows]
    m.unit_low = int(_pct(units, 0.25))
    m.unit_mid = int(_pct(units, 0.50))
    m.unit_high = int(_pct(units, 0.75))
    if m.unit_mid:
        m.spread_pct = int(round(100 * (m.unit_high - m.unit_low) / 2 / m.unit_mid))
    if m.dropped:
        m.notes.append(
            "件数が足りなかったため、" + "・".join(m.dropped)
            + "の条件は外しています")
    return m
