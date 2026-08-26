# -*- coding: utf-8 -*-
"""PROの詳細入力を採点に反映する層（仕様書 §4-A / §4-C）。

既存の診断エンジンには手を入れない。FREEの診断を普通に走らせ、その結果の
CategoryScore を差し替える形で上書きする。だから無料診断の挙動は変わらない。

**価格には触れない。** 仕様書§1の「点数は売るが円は売らない」という線引きの
ため、ここに入ってくる情報を analyze_price へ渡すことは絶対にしない。この
モジュールは PriceAnalysis を読み取りもしない。

PROの本質は情報充足度を上げること。FREEでは「未確認」として評価に入れて
いなかった項目に答えてもらい、その分だけ点数を動かし、充足度を上げる。
答えがもらえなかった項目は、これまでどおり評価に入れない。
"""
from __future__ import annotations
from dataclasses import replace
from typing import List, Optional
import datetime

from .models import SubjectProperty, ProDetail, BuyerProfile
from .scoring import CategoryScore, CriticalRisk, Diagnosis, grade_of, _clamp

# 建物内部の5項目。1つでも「気になる点あり」があれば重く見る。
CONDITION_FIELDS = ("leak", "termite", "tilt", "plumbing", "foundation")
CONDITION_LABEL = {"leak": "雨漏りの跡", "termite": "シロアリ・腐朽",
                   "tilt": "床の傾き", "plumbing": "給排水の不具合",
                   "foundation": "基礎のひび"}
# 見つかったときの重さ。構造にかかわるものほど大きく引く。
CONDITION_PENALTY = {"leak": 0.12, "termite": 0.18, "tilt": 0.15,
                     "plumbing": 0.08, "foundation": 0.15}

EQUIPMENT_FIELDS = ("water_heater", "kitchen", "bath", "electrical")
EQUIPMENT_LABEL = {"water_heater": "給湯器", "kitchen": "キッチン",
                   "bath": "浴室", "electrical": "電気設備"}
# 更新時期。給湯器は寿命が短いので、古いときの引きを大きくする。
EQUIPMENT_ADJ = {"le5": 0.02, "le10": 0.01, "gt10": -0.03}

RENO_FIELDS = (("reno_water", "水回り", 0.04), ("reno_exterior", "外壁・屋根", 0.05),
               ("reno_interior", "内装", 0.02), ("reno_pipes", "給排水管", 0.04))


def _rebuild(cat: CategoryScore, raw: float, sufficiency: float,
             reason: str, extra_source: Optional[str] = None) -> CategoryScore:
    """配点はそのままに、点数・充足度・理由だけ差し替える。"""
    raw = _clamp(raw)
    sources = list(cat.sources)
    if extra_source and extra_source not in sources:
        sources.append(extra_source)
    return replace(cat, raw=round(raw, 3), points=round(cat.weight * raw, 1),
                   sufficiency=round(_clamp(sufficiency), 2), reason=reason,
                   sources=sources)


def score_property_detail(base: CategoryScore, detail: ProDetail,
                          current_year: int) -> CategoryScore:
    """物件：FREEの築年ベースの点に、建物の中身の答えを足し引きする。"""
    raw = base.raw
    bits: List[str] = []

    # 建物内部。問題が見つかった項目は重く引き、見ていない項目は動かさない。
    concerns = [CONDITION_LABEL[f] for f in CONDITION_FIELDS
                if getattr(detail, f) == "concern"]
    clears = [f for f in CONDITION_FIELDS if getattr(detail, f) == "ok"]
    for f in CONDITION_FIELDS:
        if getattr(detail, f) == "concern":
            raw -= CONDITION_PENALTY[f]
    if clears:
        # 確認して問題が無かったことは、それ自体が価値。ただし上げ幅は控えめに。
        raw += 0.03 * len(clears)
    if concerns:
        bits.append("要注意：" + "・".join(concerns))
    if clears:
        bits.append(f"内部{len(clears)}項目は問題なし")

    # 設備の更新時期
    old_equipment = []
    for f in EQUIPMENT_FIELDS:
        v = getattr(detail, f)
        raw += EQUIPMENT_ADJ.get(v, 0.0)
        if v == "gt10":
            old_equipment.append(EQUIPMENT_LABEL[f])
    if old_equipment:
        bits.append("更新10年超：" + "・".join(old_equipment))

    # リフォームの箇所。無料版は有無だけだが、ここでは箇所ごとに見る。
    done = [(label, w) for f, label, w in RENO_FIELDS if getattr(detail, f)]
    for _label, w in done:
        raw += w
    if done:
        bits.append("リフォーム：" + "・".join(l for l, _ in done))

    if detail.quake_retrofit == "done":
        raw += 0.06
        bits.append("耐震補強済み")
    if detail.insulation == "high":
        raw += 0.04
        bits.append("断熱性能が高い")
    elif detail.insulation == "low":
        raw -= 0.03
        bits.append("断熱性能が低い")
    if detail.inspection == "done":
        bits.append("住宅診断あり")

    answered = detail.known_ratio(CONDITION_FIELDS + EQUIPMENT_FIELDS
                                  + ("quake_retrofit", "insulation"))
    # 内部を実際に見てもらえたなら、FREEの「未確認」から一気に上がる
    suff = 0.3 + 0.7 * answered
    reason = "・".join([base.reason.split("（")[0]] + bits) if bits else base.reason
    if answered < 1.0:
        reason += "（未回答の項目は評価に入れていません）"
    return _rebuild(base, raw, suff, reason, "PRO入力")


def score_risk_detail(base: CategoryScore, detail: ProDetail) -> CategoryScore:
    """リスク：接道・再建築可否・境界・越境を足す（§4-C）。"""
    raw = base.raw
    bits: List[str] = []

    if detail.rebuildable == "no":
        raw = min(raw, 0.15)
        bits.append("再建築不可")
    elif detail.rebuildable == "yes":
        bits.append("再建築可")

    if detail.road_width == "none":
        raw = min(raw, 0.2)
        bits.append("未接道")
    elif detail.road_width == "lt4":
        raw -= 0.15
        bits.append("接道の幅員4m未満（セットバックの可能性）")
    elif detail.road_width == "ge4":
        bits.append("接道4m以上")

    if detail.boundary == "unfixed":
        raw -= 0.08
        bits.append("境界未確定")
    elif detail.boundary == "fixed":
        bits.append("境界確定済み")

    if detail.encroachment == "exists":
        raw -= 0.08
        bits.append("越境あり")
    elif detail.encroachment == "none":
        bits.append("越境なし")

    answered = detail.known_ratio(("road_width", "rebuildable", "boundary",
                                   "encroachment"))
    suff = base.sufficiency + (1.0 - base.sufficiency) * answered
    reason = "・".join([base.reason] + bits) if bits else base.reason
    return _rebuild(base, raw, suff, reason, "PRO入力")


def pro_critical_risks(detail: ProDetail, subj: SubjectProperty,
                       current_year: int) -> List[CriticalRisk]:
    """PROの入力から出てくる重大リスクと、契約前に確認すべきこと。"""
    out: List[CriticalRisk] = []
    if detail.rebuildable == "no":
        out.append(CriticalRisk(
            "再建築不可", "high", "confirmed",
            "建て替えができない土地です。住宅ローンが組めない、将来売却しにくい"
            "といった影響があります。再建築不可となっている理由を確認してください"))
    if detail.road_width == "none":
        out.append(CriticalRisk(
            "未接道", "high", "confirmed",
            "建築基準法の道路に接していない可能性があります。再建築の可否と"
            "併せて確認してください"))
    elif detail.road_width == "lt4":
        out.append(CriticalRisk(
            "接道の幅員が4m未満", "medium", "confirmed",
            "セットバックが必要になり、建て替え時に敷地として使える面積が"
            "減る場合があります"))
    for f in CONDITION_FIELDS:
        if getattr(detail, f) == "concern":
            out.append(CriticalRisk(
                CONDITION_LABEL[f], "high" if f in ("termite", "foundation", "tilt")
                else "medium", "confirmed",
                "気になる点ありと回答されています。補修費用の見積もりを取り、"
                "価格交渉の材料にできるか検討してください"))
    if detail.boundary == "unfixed":
        out.append(CriticalRisk(
            "境界未確定", "medium", "confirmed",
            "隣地との境界が確定していません。確定測量を売主負担で行うよう"
            "交渉できる場合があります"))
    if detail.encroachment == "exists":
        out.append(CriticalRisk(
            "越境あり", "medium", "confirmed",
            "越境の覚書があるか、将来の是正について取り決めがあるかを"
            "確認してください"))
    # 旧耐震の境界。年単位の築年では判定しきれない。
    if subj.build_year and 1981 <= subj.build_year <= 1983:
        out.append(CriticalRisk(
            "耐震基準の境界にあたる築年", "medium", "unknown",
            f"{subj.build_year}年築は、建築確認を受けた日によって新耐震か"
            "旧耐震かが分かれます。確認済証の日付を確認してください"))
    return out


def apply_pro(diagnosis: Diagnosis, detail: ProDetail,
              subj: SubjectProperty,
              buyer: Optional[BuyerProfile] = None,
              current_year: Optional[int] = None) -> Diagnosis:
    """FREEの診断結果に、PROの詳細入力を重ねた新しい診断を返す。

    元の Diagnosis は変更しない。FREEとPROを並べて見せられるようにするため。
    """
    if current_year is None:
        current_year = datetime.date.today().year

    cats = []
    for c in diagnosis.categories:
        if c.name == "物件":
            cats.append(score_property_detail(c, detail, current_year))
        elif c.name == "リスク":
            cats.append(score_risk_detail(c, detail))
        else:
            cats.append(c)

    total = max(0, min(100, int(round(sum(c.points for c in cats)))))
    suff = int(round(sum(c.sufficiency * c.weight for c in cats)
                     / sum(c.weight for c in cats) * 100))

    risks = list(diagnosis.critical_risks) + pro_critical_risks(detail, subj,
                                                                current_year)
    strengths = [f"{c.name}: {c.reason}" for c in cats if c.raw >= 0.8]
    weaknesses = [f"{c.name}: {c.reason}" for c in cats if c.raw <= 0.5]
    to_confirm = [f"{c.name}: {c.reason}" for c in cats if c.sufficiency < 0.5]

    comment = (f"総合 {total}点 / {grade_of(total)}。情報充足度 {suff}%"
               f"（無料診断では {diagnosis.data_sufficiency}%）。"
               "PROで追加された情報は物件評価とリスクにのみ反映しており、"
               "推定価格レンジは無料診断と同じ計算です。"
               "スコアはルール計算であり、未回答の項目は反映していません。"
               "最終判断は現地・専門家確認を前提としてください。")

    return Diagnosis(total, grade_of(total), cats, risks, strengths, weaknesses,
                     to_confirm, suff, comment,
                     datetime.datetime.now().isoformat(timespec="seconds"))
