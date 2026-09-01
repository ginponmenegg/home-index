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
    # 強み・弱みに出す事実。reason はカテゴリの内訳をひとつなぎにした説明文で、
    # 有利な事実と不利な事実が混ざる。そのまま弱みに流すと「新耐震」「最上階」が
    # 弱みとして並ぶので、点に効いた事実だけを符号つきでここに入れる。
    plus: List[str] = field(default_factory=list)
    minus: List[str] = field(default_factory=list)


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


from . import structure


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# 画面に並べる上限。全部出すと目が滑るので、配点の重いカテゴリから拾う。
HIGHLIGHT_MAX = 6


def highlights(cats):
    """カテゴリが持つ符号つきの事実から、強み・弱みを組み立てる。

    カテゴリ単位で reason を丸ごと出す方式はやめた。reason には有利・不利が
    混ざるため、点数の伸びなかったカテゴリの説明文を弱みとして出すと、
    その中の有利な事実まで弱みとして読まれてしまう。
    """
    def pick(attr):
        out, seen = [], set()
        for c in sorted(cats, key=lambda c: -c.weight):
            for t in getattr(c, attr, []):
                # 駅距離のように複数のカテゴリが同じ事実を見ることがある。
                # 読む側には同じ一文なので、配点の重いほうで一度だけ出す。
                if t in seen:
                    continue
                seen.add(t)
                out.append(f"{c.name}: {t}")
        return out[:HIGHLIGHT_MAX]

    return pick("plus"), pick("minus")


# ---- 各カテゴリのルール ----
def score_building(subj: SubjectProperty, current_year: int) -> CategoryScore:
    src = []
    plus, minus = [], []
    # 新築戸建：築年数では満点にしない。建物性能・設備・施工は未確認として評価。
    is_new = (subj.property_type == "shinchiku_kodate") or \
        (subj.build_year is not None and current_year - subj.build_year <= 0)
    skey = structure.normalize(subj.structure)
    if is_new:
        # 新築はどの構造もまだ古びていないので、実効築年数では差が出ない。
        # それでも構造ごとに「これから何年もつか」は違うので、そこだけ
        # 控えめに見る。木造を基準に、耐用年数の長さぶんを上限0.06で加点。
        raw = 0.8
        if skey:
            raw = _clamp(raw + min(0.06, max(0.0, (
                structure.life_years(skey) - structure.BASE_LIFE) / 400.0)))
        reason = "新築（築浅）。建物性能評価・設備仕様・施工会社は未確認"
        if skey:
            reason = f"新築（築浅・{structure.label(skey)}）。" \
                     "建物性能評価・設備仕様・施工会社は未確認"
        return CategoryScore("物件", WEIGHTS["物件"], round(raw, 3),
                             round(WEIGHTS["物件"] * raw, 1),
                             0.5 if skey else 0.4, reason, ["user/URL"],
                             plus=["新築（築浅）"])
    if subj.build_year:
        age = current_year - subj.build_year
        # 構造ごとの耐用年数の違いを、木造なら何年ぶんかに換算してから
        # 同じカーブに通す（src/structure.py の説明を参照）。
        # 構造が不明なら木造として扱うので、換算しても値は変わらない。
        eff = structure.effective_age(age, skey)
        if eff <= 5:
            raw = 1.0
        elif eff <= 15:
            raw = 0.85
        elif eff <= 25:
            raw = 0.70
        elif eff <= 35:
            raw = 0.50
        elif eff <= 45:
            raw = 0.35
        else:
            raw = 0.20
        if skey and skey != "wood":
            reason = (f"築{age}年・{structure.label(skey)}"
                      f"（木造換算で築{eff:.0f}年相当）")
        elif skey:
            reason = f"築{age}年・{structure.label(skey)}"
        else:
            reason = f"築{age}年（構造未確認・木造として計算）"
        src.append("user/URL")
        # 築年はこのカテゴリの点のほとんどを決める。伸びた／落ちた理由が
        # 築年そのものなので、その一文をそのまま強み・弱みに出す。
        if raw >= 0.85:
            plus.append(reason)
        elif raw <= 0.5:
            minus.append(reason)
    else:
        raw = 0.5
        reason = f"築年不明{'・' + structure.label(skey) if skey else ''}"
    # リフォーム済みは築古の評価を持ち上げる（無料版は有無のみ・内容は未評価）
    if getattr(subj, "renovated", False):
        raw = _clamp(raw + 0.12)
        reason += "・リフォーム済み(内容未評価)"
        plus.append("リフォーム済み（内容は未評価）")
    # 建物状態(雨漏り/シロアリ等)は未取得のため充足度を抑える
    suff = 0.6 if subj.build_year else 0.3
    if skey:
        suff = min(1.0, suff + 0.1)
    reason += "（建物内部の状態は未確認）"
    return CategoryScore("物件", WEIGHTS["物件"], round(raw, 3),
                         round(WEIGHTS["物件"] * raw, 1), suff, reason, src,
                         plus=plus, minus=minus)


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
    bit = f"{v}（推定中央値比 {d:+}%）"
    plus = [bit] if v in ("割安の可能性", "概ね適正") else []
    minus = [bit] if v == "割高の可能性" else []
    return CategoryScore("価格", w, round(raw, 3), round(w * raw, 1), suff,
                         reason, ["reinfolib:XIT001"], plus=plus, minus=minus)


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


# ---- 生活利便（立地の主軸）----
# 立地と資産性が両方とも駅徒歩で動いていて、ほぼ同じ点数になっていた。
# 立地は「いま暮らしやすいか」、資産性は「将来も価値が保てるか」と役割を分け、
# 駅距離は資産性の主軸として1回だけ効かせる。立地では従属的な要素にとどめる。
LIFE_WEIGHTS = {"買い物": 0.35, "医療": 0.25, "教育": 0.25, "公共": 0.15}


def _dist_score(m, best, good, fair, poor):
    """近いほど高い。段階で切るのは、距離の1m差に意味を持たせないため。"""
    if m is None:
        return None
    if m <= best:
        return 1.0
    if m <= good:
        return 0.85
    if m <= fair:
        return 0.65
    if m <= poor:
        return 0.45
    return 0.3


def _shopping_score(shops):
    """買い物。大型商業施設（モール・百貨店）を重く見る。

    OpenStreetMapは有志の編集なので、衣料品店が supermarket として登録されて
    いる例もある。だから件数を鵜呑みにせず、種別を分けて距離で評価する。
    """
    if shops is None or not getattr(shops, "checked", False):
        return None, []
    bits = []
    big = shops.nearest_big
    daily = shops.nearest_daily
    # 大型：徒歩圏にあれば満点、車で行ける範囲までは加点を残す
    if big:
        big_raw = _dist_score(big.distance_m, 800, 1500, 2500, 4000)
        bits.append(f"大型商業施設{big.distance_m}m（{big.name or '名称不明'}）")
    else:
        big_raw = 0.35
        bits.append("大型商業施設は付近になし")
    # 日常：スーパーが徒歩圏にあるか
    if daily:
        daily_raw = _dist_score(daily.distance_m, 400, 700, 1200, 2000)
        bits.append(f"スーパー{daily.distance_m}m")
    else:
        daily_raw = 0.3
        bits.append("スーパーは付近になし")
    n = shops.count_within(1000)
    if n >= 3:
        bits.append(f"1km内に{n}店")
    return _clamp(0.5 * big_raw + 0.5 * daily_raw), bits


def _life_convenience(facility, shops):
    """生活利便を0..1で。取れなかった項目は評価に入れず、充足度だけ下げる。"""
    parts = {}
    bits = []

    shop_raw, shop_bits = _shopping_score(shops)
    if shop_raw is not None:
        parts["買い物"] = shop_raw
        bits.extend(shop_bits)

    if facility and getattr(facility, "checked", False):
        hm = facility.nearest_hospital_m
        if hm is not None:
            med = _dist_score(hm, 500, 1000, 1500, 2500)
            if facility.hospital_count_1km >= 5:
                med = min(1.0, med + 0.05)
            parts["医療"] = med
            bits.append(f"病院{hm}m・1km内{facility.hospital_count_1km}件")

        edu_vals = []
        sm = facility.nearest_school_m
        if sm is not None:
            edu_vals.append(_dist_score(sm, 500, 1000, 1500, 2500))
            bits.append(f"学校{sm}m")
        pm = facility.nearest_preschool_m
        if pm is not None:
            edu_vals.append(_dist_score(pm, 400, 800, 1200, 2000))
            bits.append(f"保育園・幼稚園{pm}m"
                        + (f"・1km内{facility.preschool_count_1km}件"
                           if facility.preschool_count_1km else ""))
        if edu_vals:
            parts["教育"] = sum(edu_vals) / len(edu_vals)

        pub_vals = []
        lm = facility.nearest_library_m
        if lm is not None:
            pub_vals.append(_dist_score(lm, 800, 1500, 2500, 4000))
            bits.append(f"図書館{lm}m")
        hallm = facility.nearest_hall_m
        if hallm is not None:
            pub_vals.append(_dist_score(hallm, 600, 1200, 2000, 3000))
        if facility.welfare_count_1km:
            pub_vals.append(min(1.0, 0.6 + 0.1 * facility.welfare_count_1km))
            bits.append(f"福祉施設1km内{facility.welfare_count_1km}件")
        if pub_vals:
            parts["公共"] = sum(pub_vals) / len(pub_vals)

    if not parts:
        return None, [], 0.0

    # 取れた項目だけで重み付き平均する（欠けた項目で薄めない）
    total_w = sum(LIFE_WEIGHTS[k] for k in parts)
    raw = sum(LIFE_WEIGHTS[k] * v for k, v in parts.items()) / total_w
    coverage = total_w / sum(LIFE_WEIGHTS.values())
    return _clamp(raw), bits, coverage


def _future_population_adj(change_pct):
    """250mメッシュの将来推計人口の増減で、資産性を軽く調整する。

    市区町村全体の増減より、その地点で人が減るかどうかのほうが効く。
    ここは軽い加減点にとどめ、駅距離や築年を覆さない程度にする。
    """
    if change_pct is None:
        return 0.0, None
    if change_pct >= 5:
        return 0.08, f"2050年に向けて人口+{change_pct}%"
    if change_pct >= 0:
        return 0.04, f"2050年に向けて人口+{change_pct}%"
    if change_pct >= -10:
        return 0.0, f"2050年に向けて人口{change_pct}%"
    if change_pct >= -20:
        return -0.05, f"2050年に向けて人口{change_pct}%（減少）"
    return -0.10, f"2050年に向けて人口{change_pct}%（大きく減少）"


def score_location(subj: SubjectProperty, use_district: Optional[str],
                   facility=None, shops=None) -> CategoryScore:
    """立地＝いま暮らしやすいか。生活利便を主軸に、駅アクセスを従属要素にする。"""
    w = WEIGHTS["立地"]
    src = []
    plus, minus = [], []
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

    if bus:
        minus.append(f"バス便（駅までバス{bus}分）")
    elif walk is not None:
        if walk <= 10:
            plus.append(f"駅徒歩{walk}分")
        elif walk >= 21:
            minus.append(f"駅徒歩{walk}分")

    if use_district:
        reason += f"・用途:{use_district}"
        src.append("reinfolib:XKT002")
        suff = min(1.0, suff + 0.05)

    # ここまでで raw は駅アクセスの評価。生活利便が取れていれば主役を入れ替える。
    access_raw = raw
    life_raw, life_bits, coverage = _life_convenience(facility, shops)
    if life_raw is not None:
        raw = 0.65 * life_raw + 0.35 * access_raw
        src.append("reinfolib:XKT(facility)")
        if shops is not None and getattr(shops, "checked", False):
            src.append("OpenStreetMap")
        suff = max(suff, 0.5 + 0.45 * coverage)
        reason += "／" + "・".join(life_bits)
        # 施設ごとに並べると数が多くなるので、生活利便はまとめて1件にする。
        if life_raw >= 0.8:
            plus.append("周辺の生活利便施設が充実")
        elif life_raw <= 0.45:
            minus.append("周辺の生活利便施設が少ない")
    else:
        reason += "（周辺施設は未評価）"

    return CategoryScore("立地", w, round(raw, 3), round(w * raw, 1),
                         round(suff, 2), reason, src, plus=plus, minus=minus)


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

    hit = []
    checked = bool(hazard and getattr(hazard, "checked", False))
    if not checked:
        raw = min(raw, 0.7)   # 安全と断定できない
        notes.append("ハザード未確認（要確認）")
    else:
        src.append("reinfolib:XKT(hazard)")
        suff = 0.85
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
        # 地盤系。液状化は数値レベルの凡例が手元に無いので、説明文の
        # 「しやすい／しにくい」で読む。凡例を推測して逆に採点する事故を避ける。
        liq = getattr(hazard, "liquefaction", None)
        if liq:
            if "しやすい" in liq:
                raw -= 0.20 if "やや" not in liq else 0.10
                hit.append(f"液状化：{liq}")
            elif "しにくい" not in liq:
                hit.append(f"液状化：{liq}")
        if getattr(hazard, "danger_zone", None):
            raw = min(raw, 0.4)
            hit.append(f"災害危険区域（{hazard.danger_zone}）")
        if getattr(hazard, "steep_slope", False):
            raw = min(raw, 0.5)
            hit.append("急傾斜地崩壊危険区域")
        if getattr(hazard, "landslide_zone", False):
            raw = min(raw, 0.5)
            hit.append("地すべり防止地区")
        if getattr(hazard, "embankment", None):
            raw -= 0.10
            hit.append(f"大規模盛土造成地（{hazard.embankment}）")
        raw = max(0.0, raw)
        notes.append("・".join(hit) if hit else "指定ハザード区域に該当なし")

    reason = "・".join(notes) if notes else "重大リスクの検出なし"
    plus, minus = [], []
    if urbanization and "調整区域" in urbanization:
        minus.append("市街化調整区域の可能性")
    if checked:
        # 未確認のときは何も言わない。該当なしと言えるのは、調べた場合だけ。
        if hit:
            minus.extend(hit)
        else:
            plus.append("指定のハザード区域に該当なし")
    return CategoryScore("リスク", w, round(raw, 3), round(w * raw, 1), suff,
                         reason, src, plus=plus, minus=minus)


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
    plus = [f"返済負担率 {rb}%（基準{limit}%に対して余裕がある）"] if rb <= 25 else []
    minus = [f"返済負担率 {rb}%（基準{limit}%を超えています）"] if rb > limit else []
    return CategoryScore("資金", w, round(raw, 3), round(w * raw, 1), 0.9,
                         reason, ["計算"], plus=plus, minus=minus)


def score_asset(subj: SubjectProperty, use_district: Optional[str],
                population_trend: Optional[str],
                pop_change_pct: Optional[float] = None) -> CategoryScore:
    """資産性＝将来も価値が保てるか。駅近接性を主軸に、広さと人口見通しで加減点。

    立地（生活利便）と役割を分けている。駅距離はここで主に効かせ、立地側では
    従属要素にとどめる。両方が駅距離で動くと、同じことを二重に採点してしまう。
    """
    w = WEIGHTS["資産性"]
    src = []
    bits = []
    plus, minus = [], []
    walk = subj.station_walk_min
    bus = getattr(subj, "bus_min", None)
    if bus:  # バス便は資産性で不利
        raw = 0.45
        bits.append("バス便")
        minus.append(f"バス便（駅までバス{bus}分）")
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
        if walk <= 10:
            plus.append(f"駅徒歩{walk}分")
        elif walk >= 21:
            minus.append(f"駅徒歩{walk}分")
    else:
        raw = 0.55
        bits.append("駅距離不明")

    # ゆとりある広さは資産性にプラス（土地130㎡~ or 建物100㎡~）
    if (subj.land_area_m2 and subj.land_area_m2 >= 130) or \
       (subj.building_area_m2 and subj.building_area_m2 >= 100):
        raw += 0.08
        bits.append("ゆとりある広さ")
        plus.append("ゆとりある広さ")

    suff = 0.4
    # 250mメッシュの将来推計人口があればそちらを優先する。市区町村全体の
    # 増減より、その地点で人が減るかどうかのほうが資産性に効く。
    adj, pop_bit = _future_population_adj(pop_change_pct)
    if pop_bit:
        src.append("reinfolib:XKT013")
        bits.append(pop_bit)
        raw += adj
        suff = 0.75
        if adj > 0:
            plus.append(pop_bit)
        elif adj < 0:
            minus.append(pop_bit)
    elif population_trend:
        src.append("e-Stat")
        bits.append(f"人口{population_trend}")
        suff = 0.6
        if population_trend in ("増加", "微増"):
            raw += 0.05
            plus.append(f"人口{population_trend}")
        elif population_trend == "減少":
            raw -= 0.08
            minus.append(f"人口{population_trend}")
    else:
        bits.append("人口動向は未取得")

    raw = max(0.0, min(1.0, raw))
    return CategoryScore("資産性", w, round(raw, 3), round(w * raw, 1), suff,
                         "・".join(bits), src, plus=plus, minus=minus)


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
                    current_year: Optional[int] = None,
                    shops=None,
                    pop_change_pct: Optional[float] = None) -> Diagnosis:
    if current_year is None:
        current_year = datetime.date.today().year

    cats = [
        score_building(subj, current_year),
        score_location(subj, use_district, facility, shops),
        score_price(price_a),
        score_risk(use_district, urbanization, hazard),
        score_finance(loan),
        score_asset(subj, use_district, population_trend, pop_change_pct),
    ]
    total = int(round(sum(c.points for c in cats)))
    total = max(0, min(100, total))
    grade = grade_of(total)

    # 情報充足度（重み加重平均）
    suff = int(round(sum(c.sufficiency * c.weight for c in cats)
                     / sum(c.weight for c in cats) * 100))

    strengths, weaknesses = highlights(cats)
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
