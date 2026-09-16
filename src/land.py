# -*- coding: utf-8 -*-
"""土地に何が建てられるか。建築基準法の計算だけを持つ。

■なぜ別ファイルか
戸建・マンションの採点は「建っているものを評価する」。土地は「これから
建てるものを計算する」。同じ scoring に混ぜると、どちらの話をしているか
読めなくなる。

■ここで扱う二つの数字
建ぺい率と容積率は、用途地域に紐づいて公的データに入っている（XKT002 の
u_building_coverage_ratio_ja / u_floor_area_ratio_ja）。住所から自動で取れる。

ただし容積率には、指定容積率とは別に**前面道路の幅員による上限**がある。
建築基準法第52条第2項（e-Gov 325AC0000000201 で確認）。

  「前面道路（前面道路が二以上あるときは、その幅員の最大のもの）の幅員が
   十二メートル未満である建築物の容積率は、当該前面道路の幅員のメートルの
   数値に、次の各号に掲げる区分に従い、当該各号に定める数値を乗じたもの
   以下でなければならない」

乗数は、低層住居専用・田園住居・中高層住居専用・住居・準住居のいずれも
十分の四。それ以外は十分の六（特定行政庁が別に定める場合を除く）。
指定容積率と比べて小さいほうが、実際に使える上限になる。

指定容積率200%の土地でも、前面道路が4mなら 4×0.4＝160% までしか使えない。
広告に出ているのは指定容積率のほうなので、ここで落ちることを知らない人が多い。

**前面道路が2つ以上あるときは、いちばん広いほうで計算する。**入力を求める
ときにそう書くこと。狭いほうを入れると、使えるはずの容積率より小さく出る。

■接道の話
道路は建築基準法第42条で幅員4m以上のものと定義され、第43条が
「建築物の敷地は、道路に二メートル以上接しなければならない」と定める。
幅員4m未満の道（第42条第2項の道路）は、中心から2m下がった線が境界線と
みなされ、その分は敷地として使えない（セットバック）。
私道かどうかは再建築の可否そのものではないが、掘削の承諾や持分の有無が
実務では効く。

■書かないもの
高さ制限（絶対高さ・斜線・日影）、角地の建ぺい率緩和、防火地域の緩和は
扱わない。いずれも地点ごとの条件が要り、公的データからは取れない。
取れないものを推定で埋めると、この計算の意味が無くなる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# 前面道路の幅員から基準容積率を出すときの乗数（建築基準法第52条第2項）。
# 十分の四になるのは、低層住居専用・田園住居・中高層住居専用・住居・準住居。
# いずれも名前に「住居」が入るので、この一語で拾える。
# 商業・近隣商業・工業・準工業・工業専用には入らないので、十分の六になる。
RESIDENTIAL_TOKENS = ("住居",)
FAR_MULTIPLIER_RESIDENTIAL = 0.4
FAR_MULTIPLIER_OTHER = 0.6

# 道路の幅員の下限（建築基準法第42条）。接道の長さは第43条で2m以上。
MIN_ROAD_WIDTH_M = 4.0
MIN_FRONTAGE_M = 2.0


@dataclass
class BuildCapacity:
    """この土地に建てられる大きさ。分からないものは None のままにする。"""
    site_area_m2: Optional[float] = None
    coverage_ratio: Optional[int] = None       # 指定建ぺい率(%)
    designated_far: Optional[int] = None       # 指定容積率(%)
    road_far: Optional[int] = None             # 前面道路による基準容積率(%)
    effective_far: Optional[int] = None        # 実際に使えるほう(%)
    far_limited_by_road: bool = False          # 道路で頭打ちになったか
    max_footprint_m2: Optional[float] = None   # 建築面積の上限
    max_total_floor_m2: Optional[float] = None  # 延床の上限
    setback_m2: Optional[float] = None         # セットバックで使えなくなる面積
    notes: List[str] = field(default_factory=list)


def far_multiplier(use_district: Optional[str]) -> float:
    """前面道路の幅員に掛ける乗数。住居系は4/10、それ以外は6/10。

    **用途地域が分からないときは住居系の4/10を使う。**この乗数は上限を
    決めるものなので、大きいほうを当てると「建てられない家を建てられる」
    と言うことになる。分からないときは厳しいほうに寄せる。
    注文住宅の土地は住居系が大半でもある。
    """
    if use_district is None:
        return FAR_MULTIPLIER_RESIDENTIAL
    if any(t in use_district for t in RESIDENTIAL_TOKENS):
        return FAR_MULTIPLIER_RESIDENTIAL
    return FAR_MULTIPLIER_OTHER


def setback_area_m2(site_area_m2: Optional[float], frontage_m: Optional[float],
                    road_width_m: Optional[float]) -> Optional[float]:
    """幅員4m未満のとき、セットバックで使えなくなる面積の概算。

    道の中心から2mまで下がる（両側に家がある一般的な場合）。
    後退の奥行は (4 - 幅員) / 2、それに間口を掛ける。
    向かいが崖や川なら一方後退で倍になるが、それは外から分からない。
    概算であることを notes に書くこと。
    """
    if not road_width_m or road_width_m >= MIN_ROAD_WIDTH_M:
        return None
    if not frontage_m or frontage_m <= 0:
        return None
    depth = (MIN_ROAD_WIDTH_M - road_width_m) / 2.0
    lost = round(depth * frontage_m, 1)
    if site_area_m2:
        lost = min(lost, round(site_area_m2 * 0.5, 1))
    return lost


def build_capacity(site_area_m2: Optional[float],
                   coverage_ratio: Optional[int],
                   designated_far: Optional[int],
                   use_district: Optional[str] = None,
                   road_width_m: Optional[float] = None,
                   frontage_m: Optional[float] = None) -> BuildCapacity:
    """建てられる建築面積と延床の上限。分からないところは埋めない。"""
    c = BuildCapacity(site_area_m2=site_area_m2, coverage_ratio=coverage_ratio,
                      designated_far=designated_far)

    # ---- 前面道路による容積率の上限（法第52条第2項）----
    if road_width_m and road_width_m < 12.0:
        m = far_multiplier(use_district)
        c.road_far = int(round(road_width_m * m * 100))
        c.notes.append(
            f"前面道路{road_width_m}mによる基準容積率は"
            f"{c.road_far}%（幅員×{m:.1f}）")

    # 法52条2項は「これを超えてはならない」であって「これだけ建ててよい」
    # ではない。指定容積率が分からないまま道路の数値を採ると、上限を
    # 許可と読み替えることになる。幅員6mの道に接した土地というだけで
    # 「容積率360%」と出してしまう。だから指定容積率が無いときは計算しない。
    if designated_far:
        c.effective_far = min(designated_far, c.road_far or designated_far)
        c.far_limited_by_road = (c.road_far is not None
                                 and c.road_far < designated_far)
        if c.far_limited_by_road:
            c.notes.append(
                f"指定容積率{designated_far}%より道路の{c.road_far}%が小さいので、"
                f"使えるのは{c.road_far}%まで")
    elif c.road_far:
        c.notes.append(
            f"指定容積率が分からないため、延床の上限は出していません"
            f"（前面道路による上限は{c.road_far}%です）")

    # ---- セットバック ----
    lost = setback_area_m2(site_area_m2, frontage_m, road_width_m)
    if lost:
        c.setback_m2 = lost
        c.notes.append(
            f"幅員が4m未満なので、およそ{lost}㎡がセットバックで使えません"
            "（道の中心から2m下がる前提の概算）")

    usable = site_area_m2
    if usable and lost:
        usable = max(0.0, usable - lost)

    if usable and coverage_ratio:
        c.max_footprint_m2 = round(usable * coverage_ratio / 100.0, 1)
    if usable and c.effective_far:
        c.max_total_floor_m2 = round(usable * c.effective_far / 100.0, 1)
    return c


def meets_road_requirement(road_width_m: Optional[float]) -> Optional[bool]:
    """建築基準法第43条の幅員を満たすか。分からなければ None。"""
    if road_width_m is None:
        return None
    return road_width_m >= MIN_ROAD_WIDTH_M


def tsubo(m2: Optional[float]) -> Optional[float]:
    """坪に直す。打ち合わせは坪で進むことが多い。"""
    if m2 is None:
        return None
    return round(m2 / 3.30578, 1)
