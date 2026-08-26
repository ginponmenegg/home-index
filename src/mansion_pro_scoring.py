# -*- coding: utf-8 -*-
"""マンションPROの詳細入力を採点に反映する層。

戸建の pro_scoring.py と同じ構え。無料のマンション診断を普通に走らせ、
その結果の CategoryScore を差し替える。無料版の挙動は変わらない。

**価格には触れない。** 仕様書§1の「点数は売るが円は売らない」の線引きのため、
ここに入る情報を analyze_mansion_price へ渡すことはしない。

マンションには戸建のような「物件」カテゴリが無いので、行き先はこうなる。
  管理    ← 積立金の残高・大規模修繕の履歴・管理形態・滞納・長期修繕計画
  資産性  ← 専有部の状態・設備の更新時期・リフォーム・認定
  リスク  ← 敷地の権利（借地権）・耐震診断

積立金の残高は入力に取らない。いくらあれば十分かの公的な目安が無く点数に
できないうえ、検討段階では重要事項調査報告書が手元に無いことがほとんどで、
答えようのない項目になるため。ただし聞く価値はあるので、仲介業者への質問文
としては必ず出す。点数は向きのはっきりした事実（修繕をやったか・計画が
あるか・滞納があるか・誰が管理しているか）で付ける。

答えられなかった項目は、そのまま「仲介業者に聞くこと」に変える。買主の
実際の困りごとは、何を聞けばいいか分からないことなので、そこを埋める。
"""
from __future__ import annotations
from dataclasses import replace
from typing import List, Optional
import datetime

from .models import MansionSubject, MansionProDetail, BuyerProfile
from .scoring import CategoryScore, CriticalRisk, Diagnosis, grade_of, _clamp
from .pro_scoring import _rebuild

# ---- 管理。加点・減点と、画面に出す言い方をまとめて持つ ----
MAJOR_REPAIR = {"recent": (0.15, "大規模修繕を直近10年以内に実施"),
                "old": (0.05, "大規模修繕の実施は10年以上前"),
                "never": (-0.15, "大規模修繕は未実施")}
LONG_TERM_PLAN = {"long": (0.12, "長期修繕計画あり（30年以上）"),
                  "short": (0.04, "長期修繕計画あり（期間が短い・不明）"),
                  "none": (-0.18, "長期修繕計画なし")}
MANAGEMENT_FORM = {"full": (0.08, "管理は全部委託"),
                   "partial": (0.03, "管理は一部委託"),
                   "self": (-0.08, "自主管理")}
MANAGER_STYLE = {"live_in": (0.05, "管理員が常駐"), "daily": (0.04, "管理員が日勤"),
                 "rounds": (0.0, "管理員は巡回"), "none": (-0.05, "管理員なし")}
ARREARS = {"none": (0.08, "管理費等の滞納なし"), "few": (-0.05, "滞納が少数あり"),
           "many": (-0.20, "滞納が多い")}
RESERVE_INCREASE = {"planned": (0.03, "積立金の値上げは計画的"),
                    "steep": (-0.10, "積立金の急な値上げが予定されている"),
                    "none": (0.0, "積立金の値上げ予定なし")}
MANAGEMENT_CERT = {"certified": (0.10, "管理計画認定あり"),
                   "applying": (0.03, "管理計画認定を申請中"),
                   "none": (0.0, None)}
COMMON_AREA = {"good": (0.06, "共用部の管理状態は良好"),
               "normal": (0.0, "共用部の管理状態は普通"),
               "concern": (-0.10, "共用部の管理状態に気になる点あり")}

MANAGEMENT_FIELDS = ("major_repair", "long_term_plan", "management_form",
                     "manager_style", "arrears", "reserve_increase",
                     "management_cert", "common_area")

# ---- 専有部 ----
UNIT_CONDITION = {"plumbing": ("給排水の不具合", 0.10),
                  "sash": ("サッシ・建具の不具合", 0.05),
                  "mold": ("結露・カビ", 0.08),
                  "tilt": ("床の傾き", 0.12)}
EQUIPMENT_FIELDS = ("water_heater", "kitchen", "bath")
EQUIPMENT_LABEL = {"water_heater": "給湯器", "kitchen": "キッチン", "bath": "浴室"}
EQUIPMENT_ADJ = {"le5": 0.02, "le10": 0.01, "gt10": -0.03}
RENO_FIELDS = (("reno_water", "水回り", 0.04), ("reno_interior", "内装", 0.02),
               ("reno_pipes", "給排水管", 0.04))
PERFORMANCE_ADJ = {"construction": (0.05, "建設住宅性能評価あり"),
                   "design": (0.02, "設計住宅性能評価あり"),
                   "existing": (0.04, "既存住宅性能評価あり"),
                   "none": (0.0, None)}
ASSET_FIELDS = (tuple(UNIT_CONDITION) + EQUIPMENT_FIELDS
                + ("performance_cert", "defect_insurance"))


def _apply(table, value, raw, bits):
    """表を引いて加減点し、説明を足す。未確認なら何もしない。"""
    hit = table.get(value)
    if not hit:
        return raw
    adj, label = hit
    if label:
        bits.append(label)
    return raw + adj


def score_management_detail(base: CategoryScore, detail: MansionProDetail
                            ) -> CategoryScore:
    """管理：無料版は管理費と積立金の額しか見ていない。中身を足す。"""
    raw = base.raw
    bits: List[str] = []

    for table, value in ((MAJOR_REPAIR, detail.major_repair),
                         (LONG_TERM_PLAN, detail.long_term_plan),
                         (MANAGEMENT_FORM, detail.management_form),
                         (MANAGER_STYLE, detail.manager_style),
                         (ARREARS, detail.arrears),
                         (RESERVE_INCREASE, detail.reserve_increase),
                         (MANAGEMENT_CERT, detail.management_cert),
                         (COMMON_AREA, detail.common_area)):
        raw = _apply(table, value, raw, bits)

    answered = detail.known_ratio(MANAGEMENT_FIELDS)
    suff = base.sufficiency + (1.0 - base.sufficiency) * answered
    reason = "・".join([base.reason] + bits) if bits else base.reason
    return _rebuild(base, raw, suff, reason, "PRO入力")


def score_mansion_asset_detail(base: CategoryScore, detail: MansionProDetail
                               ) -> CategoryScore:
    """資産性：専有部の状態・設備・リフォーム・認定を足す。"""
    raw = base.raw
    bits: List[str] = []

    concerns = [label for f, (label, pen) in UNIT_CONDITION.items()
                if getattr(detail, f) == "concern"]
    clears = [f for f in UNIT_CONDITION if getattr(detail, f) == "ok"]
    for f, (_label, pen) in UNIT_CONDITION.items():
        if getattr(detail, f) == "concern":
            raw -= pen
    if clears:
        raw += 0.02 * len(clears)
        bits.append(f"専有部{len(clears)}項目は問題なし")
    if concerns:
        bits.append("要注意：" + "・".join(concerns))

    old = []
    for f in EQUIPMENT_FIELDS:
        v = getattr(detail, f)
        raw += EQUIPMENT_ADJ.get(v, 0.0)
        if v == "gt10":
            old.append(EQUIPMENT_LABEL[f])
    if old:
        bits.append("更新10年超：" + "・".join(old))

    done = [(label, w) for f, label, w in RENO_FIELDS if getattr(detail, f)]
    for _label, w in done:
        raw += w
    if done:
        bits.append("リフォーム：" + "・".join(l for l, _ in done))

    perf = PERFORMANCE_ADJ.get(detail.performance_cert)
    if perf:
        raw += perf[0]
        if perf[1]:
            bits.append(perf[1])
    if detail.defect_insurance == "yes":
        raw += 0.04
        bits.append("既存住宅売買瑕疵保険の付保あり")

    answered = detail.known_ratio(ASSET_FIELDS)
    suff = base.sufficiency + (1.0 - base.sufficiency) * answered
    reason = "・".join([base.reason] + bits) if bits else base.reason
    return _rebuild(base, raw, suff, reason, "PRO入力")


def score_mansion_risk_detail(base: CategoryScore, detail: MansionProDetail
                              ) -> CategoryScore:
    """リスク：敷地の権利と耐震診断を足す。"""
    raw = base.raw
    bits: List[str] = []

    if detail.land_right == "leasehold":
        raw -= 0.15
        bits.append("借地権")
    elif detail.land_right == "ownership":
        bits.append("所有権")

    if detail.quake_diagnosis == "need":
        raw = min(raw, 0.35)
        bits.append("耐震診断で要補強とされている")
    elif detail.quake_diagnosis in ("ok", "done"):
        raw += 0.08
        bits.append("耐震診断済み（問題なし・補強済み）"
                    if detail.quake_diagnosis == "ok" else "耐震補強済み")
    elif detail.quake_diagnosis == "never":
        bits.append("耐震診断は未実施")

    answered = detail.known_ratio(("land_right", "quake_diagnosis"))
    suff = base.sufficiency + (1.0 - base.sufficiency) * answered
    reason = "・".join([base.reason] + bits) if bits else base.reason
    return _rebuild(base, raw, suff, reason, "PRO入力")


def mansion_pro_risks(detail: MansionProDetail,
                      subj: MansionSubject) -> List[CriticalRisk]:
    """PROの回答から出てくる重大リスクと、契約前に確認すべきこと。"""
    out: List[CriticalRisk] = []
    if detail.long_term_plan == "none":
        out.append(CriticalRisk(
            "長期修繕計画がない", "high", "confirmed",
            "いつ・いくらかけて何を直すかが決まっていません。必要な時期に修繕が"
            "できず、一時金の徴収や資産価値の下落につながります"))
    if detail.major_repair == "never" and subj.build_year:
        age = datetime.date.today().year - subj.build_year
        if age >= 15:
            out.append(CriticalRisk(
                "築15年超で大規模修繕が未実施", "high", "confirmed",
                f"築{age}年で一度も大規模修繕が行われていません。"
                "実施予定と資金の裏付けを総会議事録で確認してください"))
    if detail.arrears == "many":
        out.append(CriticalRisk(
            "管理費等の滞納が多い", "high", "confirmed",
            "予定どおり積立金が集まらず、修繕計画が崩れる恐れがあります。"
            "滞納額と回収の状況を確認してください"))
    if detail.management_form == "self":
        out.append(CriticalRisk(
            "自主管理", "medium", "confirmed",
            "管理会社に委託せず区分所有者が運営しています。会計や修繕の"
            "実務が担われているか、住宅ローンの審査で不利にならないかを"
            "確認してください"))
    if detail.reserve_increase == "steep":
        out.append(CriticalRisk(
            "積立金の急な値上げが予定されている", "medium", "confirmed",
            "購入後の月々の負担が変わります。値上げ幅と時期を確認し、"
            "資金計画に織り込んでください"))
    if detail.land_right == "leasehold":
        out.append(CriticalRisk(
            "借地権", "high", "confirmed",
            "土地は所有できません。地代・更新料・残存期間と、住宅ローンが"
            "使えるかを確認してください"))
    if detail.quake_diagnosis == "need":
        out.append(CriticalRisk(
            "耐震診断で要補強", "high", "confirmed",
            "補強工事の予定と費用負担、実施までの見通しを確認してください"))
    return out


# 答えられなかった項目を、そのまま仲介業者への質問に変える。
# いつ分かるかで段階が違うので、聞く相手と手段もあわせて書く。
AGENT_QUESTIONS = {
    "major_repair": "大規模修繕は過去に何回、直近はいつ実施されましたか。次回の予定時期も教えてください。",
    "long_term_plan": "長期修繕計画は作成されていますか。作成されている場合、計画期間は何年ですか。",
    "reserve_increase": "修繕積立金の値上げ予定はありますか。ある場合は時期と値上げ後の金額を教えてください。",
    "arrears": "管理費・修繕積立金の滞納はありますか。重要事項調査報告書で確認できますか。",
    "management_cert": "マンション管理計画認定、またはマンション管理適正評価を受けていますか。",
    "management_form": "管理形態は全部委託ですか、一部委託ですか、自主管理ですか。",
    "manager_style": "管理員の勤務形態は常駐・日勤・巡回のどれですか。",
    "quake_diagnosis": "耐震診断は実施されていますか。実施済みの場合、結果と補強の有無を教えてください。",
    "land_right": "敷地の権利は所有権ですか、借地権ですか。",
    "performance_cert": "住宅性能評価書は残っていますか。",
    "defect_insurance": "既存住宅売買瑕疵保険に加入できる物件ですか。",
}

# 入力には取らないが、聞く価値があるので必ず質問に入れるもの
ALWAYS_ASK = [
    "修繕積立金の残高は現在いくらですか。総戸数と築年数に対して十分な水準か、"
    "管理会社の見解も教えてください。",
    "直近の総会議事録を見せていただけますか。修繕や管理費の議題が分かります。",
]


def agent_questions(detail: MansionProDetail,
                    subj: MansionSubject) -> List[str]:
    """未回答の項目から、仲介業者に聞くべきことを組み立てる。"""
    # 一度に多くの答えが得られる依頼を先に置く。個別に聞くより早い。
    out = list(ALWAYS_ASK)
    # 旧耐震のときだけ意味を持つ質問は、条件を見て足す
    if subj.build_year and subj.build_year < 1982 \
            and detail.quake_diagnosis == "unknown":
        out.append(f"{subj.build_year}年築で旧耐震基準にあたります。"
                   "耐震診断や補強工事の予定はありますか。")
    out += [q for f, q in AGENT_QUESTIONS.items()
            if getattr(detail, f, "unknown") == "unknown"]
    return out


def apply_pro_mansion(diagnosis: Diagnosis, detail: MansionProDetail,
                      subj: MansionSubject,
                      buyer: Optional[BuyerProfile] = None) -> Diagnosis:
    """無料のマンション診断に、PROの回答を重ねた新しい診断を返す。"""
    cats = []
    for c in diagnosis.categories:
        if c.name == "管理":
            cats.append(score_management_detail(c, detail))
        elif c.name == "資産性":
            cats.append(score_mansion_asset_detail(c, detail))
        elif c.name == "リスク":
            cats.append(score_mansion_risk_detail(c, detail))
        else:
            cats.append(c)

    total = max(0, min(100, int(round(sum(c.points for c in cats)))))
    suff = int(round(sum(c.sufficiency * c.weight for c in cats)
                     / sum(c.weight for c in cats) * 100))

    # 無料版は「積立金残高と修繕履歴が未確認」を必ず出しているが、
    # PROで答えてもらえたなら、その指摘はもう当たらないので外す。
    answered_all = all(getattr(detail, f) != "unknown"
                       for f in ("major_repair", "management_form", "arrears"))
    risks = [r for r in diagnosis.critical_risks
             if not (answered_all and r.type == "積立金残高と修繕履歴が未確認")]
    risks += mansion_pro_risks(detail, subj)

    strengths = [f"{c.name}: {c.reason}" for c in cats if c.raw >= 0.8]
    weaknesses = [f"{c.name}: {c.reason}" for c in cats if c.raw <= 0.5]
    to_confirm = [f"{c.name}: {c.reason}" for c in cats if c.sufficiency < 0.5]

    comment = (f"総合 {total}点 / {grade_of(total)}。情報充足度 {suff}%"
               f"（無料診断では {diagnosis.data_sufficiency}%）。"
               "PROで追加された情報は管理・資産性・リスクにのみ反映しており、"
               "推定価格レンジは無料診断と同じ計算です。"
               "スコアはルール計算であり、未回答の項目は反映していません。"
               "最終判断は現地・専門家確認を前提としてください。")

    return Diagnosis(total, grade_of(total), cats, risks, strengths, weaknesses,
                     to_confirm, suff, comment,
                     datetime.datetime.now().isoformat(timespec="seconds"))
