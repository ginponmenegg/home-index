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
from .scoring import (CategoryScore, CriticalRisk, Diagnosis, grade_of,
                      _clamp, highlights)

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

# 省エネ基準への適合。2025年4月から新築は適合が義務。義務を満たしただけの
# 「適合」に加点はせず、上回った場合だけ足す。下回っていれば引く。
ENERGY_ADJ = {"zeh": (0.05, "ZEH水準の省エネ性能"),
              "meets": (0.0, "省エネ基準に適合"),
              "below": (-0.05, "省エネ基準に適合していない")}

RENO_FIELDS = (("reno_water", "水回り", 0.04), ("reno_exterior", "外壁・屋根", 0.05),
               ("reno_interior", "内装", 0.02), ("reno_pipes", "給排水管", 0.04))

# 公的な認定・評価。いずれも第三者の検査や基準に裏付けられているので、
# 自己申告の項目より重く見てよい。ただし積み上がりすぎないよう幅は抑える。
CERT_FIELDS = ("long_term_excellent", "performance_cert", "quake_grade",
               "defect_insurance")
# 新築で聞く認定。既存住宅売買瑕疵保険は中古の制度なので外す。
CERT_NEW_FIELDS = ("long_term_excellent", "performance_cert", "quake_grade")
PERFORMANCE_ADJ = {"construction": (0.06, "建設住宅性能評価あり"),
                   "design": (0.03, "設計住宅性能評価あり"),
                   "existing": (0.05, "既存住宅性能評価あり"),
                   "none": (0.0, None)}
QUAKE_GRADE_ADJ = {"g3": (0.06, "耐震等級3"), "g2": (0.03, "耐震等級2"),
                   "g1": (0.0, "耐震等級1（建築基準法と同等）")}


def score_certifications(detail: ProDetail):
    """認定・評価による加点と、その説明。合計の上限は設けている。"""
    adj = 0.0
    bits: List[str] = []
    if detail.long_term_excellent == "yes":
        adj += 0.08
        bits.append("長期優良住宅の認定あり")
    perf = PERFORMANCE_ADJ.get(detail.performance_cert)
    if perf:
        adj += perf[0]
        if perf[1]:
            bits.append(perf[1])
    grade = QUAKE_GRADE_ADJ.get(detail.quake_grade)
    if grade:
        adj += grade[0]
        bits.append(grade[1])
    if detail.defect_insurance == "yes":
        adj += 0.05
        bits.append("既存住宅売買瑕疵保険の付保あり")
    return min(adj, 0.22), bits


def _rebuild(cat: CategoryScore, raw: float, sufficiency: float,
             reason: str, extra_source: Optional[str] = None,
             plus: Optional[List[str]] = None,
             minus: Optional[List[str]] = None) -> CategoryScore:
    """配点はそのままに、点数・充足度・理由だけ差し替える。

    強み・弱みは無料診断で付いたものを土台にして、PROの回答から分かった
    ことを足す。置き換えないのは、築年や駅距離のように、PROの入力では
    変わらない事実まで消してしまうのを避けるため。
    """
    raw = _clamp(raw)
    sources = list(cat.sources)
    if extra_source and extra_source not in sources:
        sources.append(extra_source)
    return replace(cat, raw=round(raw, 3), points=round(cat.weight * raw, 1),
                   sufficiency=round(_clamp(sufficiency), 2), reason=reason,
                   sources=sources,
                   plus=list(cat.plus) + list(plus or []),
                   minus=list(cat.minus) + list(minus or []))


def property_fields(newbuild: bool = False):
    """物件の評価で見る項目。種別によって変える。

    新築に「給湯器はいつ交換したか」と聞いても答えようがない。それでも
    分母に入れておくと、いくら答えても情報充足度が上がりきらない。PROは
    充足度を上げるサービスなので、聞かない項目は分母からも外す。
    """
    if newbuild:
        return ("insulation", "energy_saving") + CERT_NEW_FIELDS
    # 省エネ基準は中古でも聞く。2025年4月以降に建った家は適合しているはずで、
    # それ以前でも上位の性能を取っている家がある。分からなければ未確認のまま。
    return (CONDITION_FIELDS + EQUIPMENT_FIELDS
            + ("quake_retrofit", "insulation", "energy_saving") + CERT_FIELDS)


def score_property_detail(base: CategoryScore, detail: ProDetail,
                          current_year: int,
                          newbuild: bool = False) -> CategoryScore:
    """物件：FREEの築年ベースの点に、建物の中身の答えを足し引きする。"""
    raw = base.raw
    bits: List[str] = []
    plus: List[str] = []
    minus: List[str] = []

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
        minus.extend(concerns)
    if clears:
        bits.append(f"内部{len(clears)}項目は問題なし")
        plus.append(f"建物内部の{len(clears)}項目は問題なし")

    # 設備の更新時期
    old_equipment = []
    for f in EQUIPMENT_FIELDS:
        v = getattr(detail, f)
        raw += EQUIPMENT_ADJ.get(v, 0.0)
        if v == "gt10":
            old_equipment.append(EQUIPMENT_LABEL[f])
    if old_equipment:
        bits.append("更新10年超：" + "・".join(old_equipment))
        minus.append("更新から10年超：" + "・".join(old_equipment))

    # リフォームの箇所。無料版は有無だけだが、ここでは箇所ごとに見る。
    done = [(label, w) for f, label, w in RENO_FIELDS if getattr(detail, f)]
    for _label, w in done:
        raw += w
    if done:
        bits.append("リフォーム：" + "・".join(l for l, _ in done))
        plus.append("リフォーム：" + "・".join(l for l, _ in done))

    cert_adj, cert_bits = score_certifications(detail)
    raw += cert_adj
    bits.extend(cert_bits)
    # 認定・評価は第三者の検査に裏付けられた事実なので、そのまま強みに出す。
    # ただし耐震等級1は建築基準法と同等というだけなので、強みにはしない。
    plus.extend(b for b in cert_bits if not b.startswith("耐震等級1"))

    if detail.quake_retrofit == "done":
        raw += 0.06
        bits.append("耐震補強済み")
        plus.append("耐震補強済み")
    if detail.insulation == "high":
        raw += 0.04
        bits.append("断熱性能が高い")
        plus.append("断熱性能が高い")
    elif detail.insulation == "low":
        raw -= 0.03
        bits.append("断熱性能が低い")
        minus.append("断熱性能が低い")
    if detail.inspection == "done":
        bits.append("住宅診断あり")

    en = ENERGY_ADJ.get(detail.energy_saving)
    if en:
        raw += en[0]
        bits.append(en[1])
        if en[0] > 0:
            plus.append(en[1])
        elif en[0] < 0:
            minus.append(en[1])

    answered = detail.known_ratio(property_fields(newbuild))
    # 無料診断の充足度を出発点にして、答えた分だけ上げる。0から計算し直すと、
    # 未回答のままPROに来たときに無料診断より低く出てしまう。
    suff = base.sufficiency + (1.0 - base.sufficiency) * answered
    reason = "・".join([base.reason.split("（")[0]] + bits) if bits else base.reason
    if answered < 1.0:
        reason += "（未回答の項目は評価に入れていません）"
    return _rebuild(base, raw, suff, reason, "PRO入力", plus, minus)


def score_risk_detail(base: CategoryScore, detail: ProDetail) -> CategoryScore:
    """リスク：接道・再建築可否・境界・越境を足す（§4-C）。"""
    raw = base.raw
    bits: List[str] = []
    plus: List[str] = []
    minus: List[str] = []

    if detail.rebuildable == "no":
        raw = min(raw, 0.15)
        bits.append("再建築不可")
        minus.append("再建築不可")
    elif detail.rebuildable == "yes":
        bits.append("再建築可")
        plus.append("再建築可")

    if detail.road_width == "none":
        raw = min(raw, 0.2)
        bits.append("未接道")
        minus.append("未接道")
    elif detail.road_width == "lt4":
        raw -= 0.15
        bits.append("接道の幅員4m未満（セットバックの可能性）")
        minus.append("接道の幅員4m未満（セットバックの可能性）")
    elif detail.road_width == "ge4":
        bits.append("接道4m以上")
        plus.append("接道4m以上")

    if detail.boundary == "unfixed":
        raw -= 0.08
        bits.append("境界未確定")
        minus.append("境界未確定")
    elif detail.boundary == "fixed":
        bits.append("境界確定済み")
        plus.append("境界確定済み")

    if detail.encroachment == "exists":
        raw -= 0.08
        bits.append("越境あり")
        minus.append("越境あり")
    elif detail.encroachment == "none":
        bits.append("越境なし")
        plus.append("越境なし")

    answered = detail.known_ratio(("road_width", "rebuildable", "boundary",
                                   "encroachment"))
    suff = base.sufficiency + (1.0 - base.sufficiency) * answered
    reason = "・".join([base.reason] + bits) if bits else base.reason
    return _rebuild(base, raw, suff, reason, "PRO入力", plus, minus)


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
    if detail.long_term_excellent == "yes":
        out.append(CriticalRisk(
            "長期優良住宅の認定の承継", "low", "unknown",
            "中古では、認定を引き継ぐのに承継の手続きが必要です。認定通知書と"
            "維持保全の記録が残っているか、承継が可能かを確認してください。"
            "住宅ローン控除の限度額や登録免許税・不動産取得税の扱いに関わります"))
    # 旧耐震の境界。年単位の築年では判定しきれない。
    if subj.build_year and 1981 <= subj.build_year <= 1983:
        out.append(CriticalRisk(
            "耐震基準の境界にあたる築年", "medium", "unknown",
            f"{subj.build_year}年築は、建築確認を受けた日によって新耐震か"
            "旧耐震かが分かれます。確認済証の日付を確認してください"))
    return out


# 答えられなかった項目を、そのまま仲介業者への質問に変える。
# 戸建の建物の中の状態は、売主が記入する「物件状況報告書（告知書）」に
# 書かれていることが多い。どこを見れば分かるかまで書いておく。
# 並びは、答えが返ってこなかったときの影響が大きい順。建て替えられるか、
# 敷地の境界がはっきりしているか、が最初に来る。
AGENT_QUESTIONS = {
    "rebuildable": "再建築は可能ですか。制限がある場合、その理由を教えてください。",
    "road_width": "前面道路の幅員は何メートルですか。セットバックは必要ですか。",
    "boundary": "隣地との境界は確定していますか。確定測量図はありますか。",
    "encroachment": "塀・屋根・配管などの越境はありますか。覚書はありますか。",
    "foundation": "基礎にひび割れはありますか。補修した箇所はありますか。",
    "termite": "シロアリの被害や駆除の履歴はありますか。防蟻処理はいつ行いましたか。",
    "leak": "雨漏りの跡はありますか。物件状況報告書（告知書）にどう記載されていますか。",
    "termite": "シロアリの被害や駆除の履歴はありますか。防蟻処理はいつ行いましたか。",
    "tilt": "床の傾きを指摘されたことはありますか。",
    "plumbing": "給排水管の不具合や漏水の履歴はありますか。配管の更新はしていますか。",
    "quake_retrofit": "耐震補強工事は行っていますか。行っている場合、内容と時期を教えてください。",
    "inspection": "住宅診断（インスペクション）は実施済みですか。未実施の場合、契約前に実施できますか。",
    "water_heater": "給湯器はいつ交換しましたか。",
    "kitchen": "キッチンはいつ交換・改修しましたか。",
    "bath": "浴室はいつ交換・改修しましたか。",
    "electrical": "分電盤や屋内配線の更新はしていますか。",
    "insulation": "断熱材の仕様や断熱等性能等級は分かりますか。",
    "long_term_excellent": "長期優良住宅の認定を受けていますか。受けている場合、認定を承継できますか。",
    "performance_cert": "住宅性能評価書は残っていますか。設計と建設のどちらですか。",
    "quake_grade": "耐震等級はいくつですか。証明する書類はありますか。",
    "defect_insurance": "既存住宅売買瑕疵保険に加入できる物件ですか。",
    "energy_saving": "省エネ基準に適合していますか。ZEH水準の場合は、"
                     "それを示す書類を見せていただけますか。",
}

# 入力には取らないが、戸建では必ず見ておきたい書類
ALWAYS_ASK = [
    "物件状況報告書（告知書）と設備表を見せていただけますか。"
    "雨漏り・シロアリ・給排水の不具合について、売主の申告が書かれています。",
    "建築確認済証と検査済証は残っていますか。",
]

# 新築で必ず聞くこと。物件状況報告書は中古で売主が過去の不具合を申告する
# 書類なので、新築では外す。
ALWAYS_ASK_NEW = [
    "建築確認済証と検査済証は残っていますか。",
]

# 敷地と法規は種別を問わず聞く。建て替えと売却に効く話なので、新築でも
# 確かめておくものになる。
RISK_QUESTION_FIELDS = ("rebuildable", "road_width", "boundary",
                        "encroachment")


def agent_questions(detail: ProDetail, subj: SubjectProperty,
                    newbuild: bool = False) -> List[str]:
    """未回答の項目から、仲介業者や売主に聞くべきことを組み立てる。

    聞くのは、その種別で実際に入力してもらう項目だけ。新築に
    「給湯器はいつ交換しましたか」と聞いても、相手も答えに困る。
    """
    # 一度に多くの答えが得られる書類の依頼を先に置く。個別に聞くより早い。
    out = list(ALWAYS_ASK_NEW if newbuild else ALWAYS_ASK)
    # 新旧の耐震基準が築年だけでは決まらない年は、確認済証の日付を聞く
    if subj.build_year and 1981 <= subj.build_year <= 1983:
        out.append(f"{subj.build_year}年築は、建築確認を受けた日によって"
                   "新耐震か旧耐震かが分かれます。確認済証の日付を教えてください。")
    asked = set(property_fields(newbuild)) | set(RISK_QUESTION_FIELDS)
    if not newbuild:
        asked.add("inspection")
    out += [q for f, q in AGENT_QUESTIONS.items()
            if f in asked and getattr(detail, f, "unknown") == "unknown"]
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

    newbuild = (getattr(subj, "property_type", "") == "shinchiku_kodate")
    cats = []
    for c in diagnosis.categories:
        if c.name == "物件":
            cats.append(score_property_detail(c, detail, current_year,
                                              newbuild))
        elif c.name == "リスク":
            cats.append(score_risk_detail(c, detail))
        else:
            cats.append(c)

    total = max(0, min(100, int(round(sum(c.points for c in cats)))))
    suff = int(round(sum(c.sufficiency * c.weight for c in cats)
                     / sum(c.weight for c in cats) * 100))

    risks = list(diagnosis.critical_risks) + pro_critical_risks(detail, subj,
                                                                current_year)
    strengths, weaknesses = highlights(cats)
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
