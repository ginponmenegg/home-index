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

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# 前面道路の幅員から基準容積率を出すときの乗数（建築基準法第52条第2項）。
# 十分の四になるのは、低層住居専用・田園住居・中高層住居専用・住居・準住居。
# いずれも名前に「住居」が入るので、この一語で拾える。
# 商業・近隣商業・工業・準工業・工業専用には入らないので、十分の六になる。
RESIDENTIAL_TOKENS = ("住居",)
FAR_MULTIPLIER_RESIDENTIAL = 0.4
FAR_MULTIPLIER_OTHER = 0.6

# 1坪 = 400/121 ㎡。計量法の尺貫法の換算そのもの。
# 3.30578 や 3.305785 と丸めた値があちこちに散っていたので、ここに寄せた。
# 丸め方が違うと、同じ面積を別のところで出したときに末尾がずれる。
M2_PER_TSUBO = 400.0 / 121.0


# 道路の幅員の下限（建築基準法第42条）。接道の長さは第43条で2m以上。
MIN_ROAD_WIDTH_M = 4.0
MIN_FRONTAGE_M = 2.0

# 建ぺい率の緩和（建築基準法第53条第3項・第6項。e-Gov で確認）。
#
#   第3項「第一号又は第二号のいずれかに該当する建築物にあつては第一項各号に
#         定める数値に十分の一を加えたものを…第一号及び第二号に該当する
#         建築物にあつては…十分の二を加えたもの」
#     一号 … 防火地域内の耐火建築物等、準防火地域内の耐火・準耐火建築物等
#            （ただし建蔽率の限度が十分の八の地域は一号から除く）
#     二号 … 街区の角にある敷地又はこれに準ずる敷地で
#            **特定行政庁が指定するもの**
#
#   第6項一号「防火地域（…限度が十分の八とされている地域に限る）内にある
#            耐火建築物等」は、前各項の規定を適用しない（＝制限なし）
#
# **角地なら自動で緩和、ではない。**特定行政庁が指定した角地だけ。
# 指定されていない角地に10%を足すと、建てられない家を建てられると言うことになる。
CORNER_BONUS = 10
FIRE_BONUS = 10
COVERAGE_EXEMPT = 80    # この建ぺい率の地域＋防火地域＋耐火で制限なし


@dataclass
class BuildCapacity:
    """この土地に建てられる大きさ。分からないものは None のままにする。"""
    site_area_m2: Optional[float] = None
    coverage_ratio: Optional[int] = None       # 実際に使える建ぺい率(%)
    designated_coverage: Optional[int] = None  # 都市計画で定められた建ぺい率(%)
    coverage_relaxed: bool = False             # 法53条の緩和がかかったか
    designated_far: Optional[int] = None       # 指定容積率(%)
    road_far: Optional[int] = None             # 前面道路による基準容積率(%)
    effective_far: Optional[int] = None        # 実際に使えるほう(%)
    far_limited_by_road: bool = False          # 道路で頭打ちになったか
    max_footprint_m2: Optional[float] = None   # 建築面積の上限
    max_total_floor_m2: Optional[float] = None  # 延床の上限
    setback_m2: Optional[float] = None         # セットバックで使えなくなる面積
    floors_to_use_far: Optional[int] = None    # 容積率を使い切るのに要る階数
    notes: List[str] = field(default_factory=list)


def coverage_with_relaxations(coverage_ratio: Optional[int],
                              corner_designated: Optional[bool] = None,
                              fire_relaxation: bool = False
                              ) -> Tuple[Optional[int], List[str]]:
    """建ぺい率に、法53条の緩和を反映する。戻り値は (建ぺい率, 注記)。

    corner_designated は「特定行政庁が指定した角地か」。角地であることと、
    指定されていることは別なので、True にするのは指定が確認できたときだけ。

    fire_relaxation は「防火地域に耐火建築物等を建てる／準防火地域に
    耐火・準耐火建築物等を建てる」場合。土地の段階では**建てるものを
    まだ選べる**ので、こちらが勝手に True にはしない。
    """
    notes: List[str] = []
    if not coverage_ratio:
        return coverage_ratio, notes

    if fire_relaxation and coverage_ratio == COVERAGE_EXEMPT:
        notes.append(
            f"建ぺい率{COVERAGE_EXEMPT}%の地域に、防火地域内の耐火建築物を"
            "建てる場合は建ぺい率の制限を受けません（法53条6項1号）")
        return 100, notes

    out = coverage_ratio
    if fire_relaxation:
        out += FIRE_BONUS
        notes.append(f"防火・準防火の緩和で建ぺい率＋{FIRE_BONUS}%（法53条3項1号）")
    if corner_designated:
        out += CORNER_BONUS
        notes.append(
            f"特定行政庁が指定した角地なので建ぺい率＋{CORNER_BONUS}%"
            "（法53条3項2号）")
    out = min(100, out)
    return out, notes


def floors_to_use_far(max_total_floor_m2: Optional[float],
                      max_footprint_m2: Optional[float]) -> Optional[int]:
    """容積率を使い切るのに要る階数。

    建ぺい率60%・容積率200%の土地は、200÷60 で**4階建て**にしないと容積を
    使い切れない。注文住宅は2階建てが多いので、広告の容積率をそのまま
    「建てられる広さ」と読むと実際より大きく見える。
    """
    if not max_total_floor_m2 or not max_footprint_m2:
        return None
    return int(math.ceil(max_total_floor_m2 / max_footprint_m2 - 1e-9))


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
                   frontage_m: Optional[float] = None,
                   corner_designated: Optional[bool] = None,
                   fire_relaxation: bool = False,
                   usable_area_m2: Optional[float] = None) -> BuildCapacity:
    """建てられる建築面積と延床の上限。分からないところは埋めない。"""
    designated_coverage = coverage_ratio
    coverage_ratio, relax_notes = coverage_with_relaxations(
        coverage_ratio, corner_designated, fire_relaxation)
    c = BuildCapacity(site_area_m2=site_area_m2, coverage_ratio=coverage_ratio,
                      designated_coverage=designated_coverage,
                      coverage_relaxed=(coverage_ratio != designated_coverage),
                      designated_far=designated_far)
    c.notes.extend(relax_notes)

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
    if usable_area_m2:
        # 測量図に後退後の面積があるなら、概算より実測を採る。
        usable = usable_area_m2
        if site_area_m2:
            c.setback_m2 = round(site_area_m2 - usable_area_m2, 1) or None
        c.notes.append(
            f"測量図の実測（セットバック後の有効面積 {usable_area_m2}㎡）で"
            "計算しています。概算は使っていません")
    elif usable and lost:
        usable = max(0.0, usable - lost)

    if usable and coverage_ratio:
        c.max_footprint_m2 = round(usable * coverage_ratio / 100.0, 1)
    if usable and c.effective_far:
        c.max_total_floor_m2 = round(usable * c.effective_far / 100.0, 1)
    # 絶対高さ（法55条）。第一種・第二種低層住居専用地域と田園住居地域は
    # 10mか12m（都市計画でどちらかが定まる）。用途地域は無料でも取れるので、
    # ここは無料の診断でも出す。
    if use_district and any(t in use_district
                            for t in ("低層住居専用", "田園住居")):
        c.notes.append(
            f"{use_district}は建物の高さが10mまたは12mまでに制限されます"
            "（法55条。どちらかは都市計画で決まります）。3階建ては"
            "設計次第で入らないことがあります")

    c.floors_to_use_far = floors_to_use_far(c.max_total_floor_m2,
                                            c.max_footprint_m2)
    if c.floors_to_use_far and c.floors_to_use_far >= 3:
        c.notes.append(
            f"容積率を使い切るには{c.floors_to_use_far}階建てが要ります"
            "（延床の上限 ÷ 建築面積の上限）。注文住宅は2階建てが多いので、"
            "実際に建てる家はこれより小さくなるのが普通です")
    return c


def meets_road_requirement(road_width_m: Optional[float]) -> Optional[bool]:
    """建築基準法第43条の幅員を満たすか。分からなければ None。"""
    if road_width_m is None:
        return None
    return road_width_m >= MIN_ROAD_WIDTH_M


def to_m2(value: Optional[float], unit: str = "m2") -> Optional[float]:
    """入力された面積を㎡に直す。unit は "m2" か "tsubo"。

    土地の広告は坪で書かれることが多く、㎡に直すのを利用者にやらせていた。
    掛け算を1回させるだけだが、そこで間違えると建ぺい率・容積率の計算が
    まるごとずれる。こちらで直す。
    """
    if value is None:
        return None
    return value * M2_PER_TSUBO if unit == "tsubo" else value


def tsubo(m2: Optional[float]) -> Optional[float]:
    """坪に直す。打ち合わせは坪で進むことが多い。"""
    if m2 is None:
        return None
    return round(m2 / M2_PER_TSUBO, 1)


# ---- 図解 ------------------------------------------------------------------
# 「前面道路4.0m」「間口9.0m」が、どれくらいなのかは、何度も土地を見た人
# にしか浮かばない。買う人はたいてい初めてなので、縮尺を合わせて絵にする。
#
# ただし**土地の形は分かっていない**。手元にあるのは面積と間口だけなので、
# 奥行きは面積÷間口で割り戻している。旗竿かもしれないし台形かもしれない。
# だから縮尺だけは必ず本物に合わせ、形は断定しないと画面側に書く。
# 道路だけ別の縮尺で描くと「4mか6mか」を見る意味が無くなるので、
# 道路・敷地・建築面積はすべて同じ scale で描く。

DIAGRAM_W = 560          # 図全体の横幅（px）
LOT_MAX_W = 260          # 敷地の横幅の上限
LOT_MAX_H = 230          # 敷地の高さの上限
LOT_TOP = 30             # 上の余白（「上が奥」の注記を置く）
FRONT_GAP = 26           # 敷地と道路のあいだ。間口の寸法線を置く
ROAD_LABEL_MIN_H = 34    # これより細い道路は、文字を中に入れられない
# 間口の狭い土地では建築面積の箱も細くなる。数字が入らないまま置くと、
# 左の「奥行き 31.2 m」と隣り合って一つの数に見える。
FOOT_NUM_MIN_W = 90
BAND_MIN_H = 5           # セットバックの帯が消えない最小の高さ


@dataclass
class SiteDiagram:
    """縮尺を合わせた座標（px）。単位つきの数字は画面で使う。"""
    scale: float                         # px / m
    width: int
    height: int
    lot: Tuple[float, float, float, float]          # x, y, w, h
    road: Optional[Tuple[float, float, float, float]] = None
    footprint: Optional[Tuple[float, float, float, float]] = None
    setback: Optional[Tuple[float, float, float, float]] = None
    road_label_inside: bool = True
    footprint_number_inside: bool = True
    frontage_m: Optional[float] = None
    depth_m: Optional[float] = None
    road_width_m: Optional[float] = None
    setback_depth_m: Optional[float] = None


def site_diagram(site_area_m2: Optional[float],
                 frontage_m: Optional[float],
                 road_width_m: Optional[float] = None,
                 footprint_m2: Optional[float] = None,
                 setback_m2: Optional[float] = None
                 ) -> Optional[SiteDiagram]:
    """敷地・前面道路・建築面積を、同じ縮尺で置いた座標を返す。

    面積か間口が無ければ描かない。無いものを線で埋めると、
    「分からない」が「こういう形」に化ける。
    """
    if not site_area_m2 or not frontage_m or frontage_m <= 0:
        return None
    if site_area_m2 <= 0:
        return None
    depth_m = site_area_m2 / frontage_m

    # 間口と奥行きの両方が枠に収まる縮尺。道路もこの縮尺で描く。
    scale = min(LOT_MAX_W / frontage_m, LOT_MAX_H / depth_m)
    lot_w, lot_h = frontage_m * scale, depth_m * scale
    lot_x = (DIAGRAM_W - lot_w) / 2.0
    lot = (round(lot_x, 1), float(LOT_TOP), round(lot_w, 1), round(lot_h, 1))

    d = SiteDiagram(scale=round(scale, 3), width=DIAGRAM_W, height=0, lot=lot,
                    frontage_m=round(frontage_m, 1),
                    depth_m=round(depth_m, 1),
                    road_width_m=road_width_m)

    # ---- セットバックで使えなくなる帯（敷地の道路側）----
    if setback_m2 and setback_m2 > 0:
        d.setback_depth_m = round(setback_m2 / frontage_m, 2)
        band_h = max(BAND_MIN_H, d.setback_depth_m * scale)
        d.setback = (lot[0], round(lot[1] + lot_h - band_h, 1),
                     lot[2], round(band_h, 1))

    # ---- 建築面積。置き場所ではなく広さのイメージなので中央に置く ----
    if footprint_m2 and footprint_m2 > 0 and footprint_m2 <= site_area_m2:
        k = (footprint_m2 / site_area_m2) ** 0.5
        fw, fh = lot_w * k, lot_h * k
        d.footprint = (round(lot_x + (lot_w - fw) / 2.0, 1),
                       round(LOT_TOP + (lot_h - fh) / 2.0, 1),
                       round(fw, 1), round(fh, 1))
        d.footprint_number_inside = fw >= FOOT_NUM_MIN_W

    # ---- 前面道路 ----
    bottom = LOT_TOP + lot_h + FRONT_GAP
    if road_width_m and road_width_m > 0:
        road_h = road_width_m * scale
        d.road = (40.0, round(bottom, 1), float(DIAGRAM_W - 80),
                  round(road_h, 1))
        d.road_label_inside = road_h >= ROAD_LABEL_MIN_H
        bottom += road_h
    d.height = int(round(bottom + 14))
    return d


# ---- 用途地域と建ぺい率の突き合わせ ----------------------------------------
# 法第53条第1項（e-Gov 325AC0000000201 で確認）は、都市計画で定められる
# 指定建ぺい率の選択肢を用途地域ごとに決めている。**商業地域は十分の八**で、
# 他の値は選べない。だから「商業地域・指定建ぺい率60%」という組み合わせは
# 存在しない。
#
# なぜ要るか。不動産情報ライブラリの面は、地点を含むポリゴンに建ぺい率と
# 容積率だけ入っていて用途地域の名前が空、ということがある（船橋市前原西
# 6丁目が実例）。名前だけ隣の面から借りると、**別の土地の用途地域**を
# この土地のものとして出すことになる。用途地域は前面道路による容積率の
# 係数（法52条2項）と高さ制限（法55条）に効くので、表示だけの話ではない。
#
# 3項の緩和（角地・防火地域で+1/10）は指定建ぺい率そのものではないので、
# ここでは見ない。APIが返すのは都市計画で定めた値。
COVERAGE_CHOICES = {
    "第一種低層住居専用地域": (30, 40, 50, 60),
    "第二種低層住居専用地域": (30, 40, 50, 60),
    "第一種中高層住居専用地域": (30, 40, 50, 60),
    "第二種中高層住居専用地域": (30, 40, 50, 60),
    "田園住居地域": (30, 40, 50, 60),
    "工業専用地域": (30, 40, 50, 60),
    "第一種住居地域": (50, 60, 80),
    "第二種住居地域": (50, 60, 80),
    "準住居地域": (50, 60, 80),
    "準工業地域": (50, 60, 80),
    "近隣商業地域": (60, 80),
    "商業地域": (80,),
    "工業地域": (50, 60),
}


def coverage_fits_district(use_district: Optional[str],
                           coverage_ratio: Optional[int]) -> Optional[bool]:
    """指定建ぺい率が、その用途地域で取りうる値か。

    どちらかが無ければ None（分からない）。知らない用途地域名も None。
    **分からないものを False にしない。**名前を捨てる判断に使うので、
    確かに矛盾しているときだけ False を返す。
    """
    if not use_district or not coverage_ratio:
        return None
    for name, allowed in COVERAGE_CHOICES.items():
        if name in use_district:
            return int(coverage_ratio) in allowed
    return None
