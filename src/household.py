# -*- coding: utf-8 -*-
"""家計の手取りと教育費（PRO・詳細な資金計画）。

無料版の返済負担率は、税込の世帯年収で割る（金融機関の審査と同じ定義）。
ただ同じ世帯年収1,000万円でも、片働きと共働きでは手取りが年に数十万円
違う。子どもがいれば、学費が何年にもわたって出ていく。PROでは、

  1. 本人と配偶者の年収から、それぞれの手取りを出して足す
  2. 返済が手取りの何%かで、資金の点の段を決める
  3. 手取りから返済と教育費を引いて、月にいくら残るかを出し、
     同じ人数の世帯の平均的な生活費（家計調査）と比べる

税率・料率・学費・生活費は finance_config.json の "household" に置く。
コードに数値を埋め込まない（finance.py と同じ方針）。

前提（画面にもそのまま出す）
- 給与所得（会社員・公務員）。自営業は計算しない
- 社会保険料は協会けんぽの全国平均の料率。年収に料率を掛ける
- 所得税・住民税は、基礎控除・配偶者控除・配偶者特別控除・社会保険料控除
  だけを引く。住宅ローン控除・生命保険料控除・扶養控除などは入れない
- 教育費は文部科学省の調査の平均。0〜2歳の保育料と大学の下宿代は入れない

入力された年収・子どもの年齢は保存しない（診断のほかの入力と同じ）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .finance import FCONFIG


def _cfg() -> dict:
    return FCONFIG.get("household", {})


# ---------------------------------------------------------------- 手取り
@dataclass
class TakeHome:
    income: int              # 年収（額面）
    social: int              # 社会保険料（本人負担）
    income_tax: int          # 所得税（復興特別所得税を含む）
    resident_tax: int        # 住民税
    spouse_deduction: int = 0  # 使った配偶者控除・配偶者特別控除（所得税）

    @property
    def net(self) -> int:
        return self.income - self.social - self.income_tax - self.resident_tax


def _salary_deduction(x: int, c: dict) -> int:
    """給与所得控除。表は [上限, 率, 加算] と、最低額・最高額。"""
    t = c["salary_deduction"]
    for upper, rate, add in t["bands"]:
        if x <= upper:
            return max(t["min"], int(x * rate + add))
    return t["max"]


def employment_income(x: int) -> int:
    """給与所得（＝合計所得金額。給与だけの人の場合）。"""
    if x <= 0:
        return 0
    return max(0, x - _salary_deduction(x, _cfg()))


def _basic(g: int, table) -> int:
    for upper, amount in table:
        if upper is None or g <= upper:
            return amount
    return 0


def _spouse(g_self: int, g_sp: int, table: dict) -> int:
    """配偶者控除・配偶者特別控除。

    table["self_upper"] は本人の合計所得の区分の上限（900万・950万・1,000万）。
    table["rows"] は [配偶者の合計所得の上限, [区分ごとの控除額]]。
    本人が上限を超えれば0、配偶者が表の最後を超えても0。
    """
    col = None
    for i, upper in enumerate(table["self_upper"]):
        if g_self <= upper:
            col = i
            break
    if col is None:
        return 0
    for upper, amounts in table["rows"]:
        if g_sp <= upper:
            return amounts[col]
    return 0


def _income_tax(taxable: int, c: dict) -> int:
    t = max(0, taxable // 1000 * 1000)          # 課税所得は千円未満切り捨て
    for upper, rate, minus in c["income_tax_bands"]:
        if upper is None or t <= upper:
            base = max(0, t * rate - minus)
            return int(base * (1 + c["reconstruction_rate"]))
    return 0


def social_insurance(x: int, over40: bool = False) -> int:
    """社会保険料（本人負担）。料率は労使折半の前の全体の率を半分にする。"""
    if x <= 0:
        return 0
    s = _cfg()["social"]
    health_base = min(x, s["health_cap_annual"])
    pension_base = min(x, s["pension_cap_annual"])
    v = health_base * s["health"] / 2
    if over40:
        v += health_base * s["care"] / 2
    v += health_base * s["child_support"] / 2
    v += pension_base * s["pension"] / 2
    v += x * s["employment"]
    return int(round(v))


def take_home(income: int, over40: bool = False,
              spouse_income: Optional[int] = None,
              insured: bool = True) -> TakeHome:
    """ひとり分の手取り。spouse_income を渡すと、配偶者控除を判定する。

    配偶者控除は、所得の高いほうの人が使う前提（呼び出し側で決める）。
    insured=False は、家族の扶養に入っていて社会保険料がかからない人。
    """
    c = _cfg()
    x = max(0, int(income or 0))
    if x == 0:
        return TakeHome(0, 0, 0, 0)
    soc = social_insurance(x, over40) if insured else 0
    g = employment_income(x)
    sp_tax = sp_res = 0
    if spouse_income is not None:
        g_sp = employment_income(spouse_income)
        sp_tax = _spouse(g, g_sp, c["spouse_income_tax"])
        sp_res = _spouse(g, g_sp, c["spouse_resident_tax"])
    it = _income_tax(g - soc - _basic(g, c["basic_income_tax"]) - sp_tax, c)
    r = c["resident"]
    taxable_r = max(0, g - soc - r["basic"] - sp_res) // 1000 * 1000
    rt = int(taxable_r * r["rate"]) + (r["per_capita"] if g > r["exempt_income"]
                                       else 0)
    return TakeHome(x, soc, it, rt, sp_tax)


@dataclass
class Household:
    earners: List[TakeHome]

    @property
    def income(self) -> int:
        return sum(e.income for e in self.earners)

    @property
    def net(self) -> int:
        return sum(e.net for e in self.earners)


def household_take_home(self_income: Optional[int],
                        spouse_income: Optional[int] = None,
                        self_over40: bool = False,
                        spouse_over40: bool = False,
                        married: Optional[bool] = None) -> Household:
    """本人と配偶者の手取り。配偶者控除は、所得の高いほうが使う。

    married が None のときは、配偶者の年収が入力されていれば（0でも）
    配偶者がいるとみなす。
    """
    a = max(0, int(self_income or 0))
    b = spouse_income
    if married is None:
        married = b is not None
    if not married:
        return Household([take_home(a, self_over40)])
    b = max(0, int(b or 0))
    # 収入の少ないほうが年収130万円未満なら、扶養に入っているとして
    # 社会保険料を0にする（健康保険の被扶養者の認定基準）。勤め先で
    # 加入している人もいるので、画面の前提に書く。
    dep = _cfg()["social"]["dependent_limit"]
    if a >= b:
        return Household([take_home(a, self_over40, spouse_income=b),
                          take_home(b, spouse_over40, spouse_income=None,
                                    insured=b >= dep)])
    return Household([take_home(a, self_over40, spouse_income=None,
                                insured=a >= dep),
                      take_home(b, spouse_over40, spouse_income=a)])


# ---------------------------------------------------------------- 教育費
SCHOOL_ROUTES = {"public": "すべて公立", "private_jhs": "中学から私立"}
UNIV_ROUTES = {"national": "国公立", "private": "私立", "none": "大学は考えない"}


def parse_ages(text: Optional[str]) -> Optional[List[int]]:
    """「4, 7」「4歳と7歳」などから年齢の並びを取る。読めなければ None。"""
    import re
    import unicodedata
    if not text:
        return None
    s = unicodedata.normalize("NFKC", str(text))
    nums = [int(n) for n in re.findall(r"\d+", s)]
    nums = [n for n in nums if 0 <= n <= 30]
    return nums or None


def _stage(age: int) -> Optional[str]:
    if 3 <= age <= 5:
        return "kindergarten"
    if 6 <= age <= 11:
        return "elementary"
    if 12 <= age <= 14:
        return "junior_high"
    if 15 <= age <= 17:
        return "high"
    if 18 <= age <= 21:
        return "university"
    return None


def school_cost(age: int, school: str, univ: str) -> int:
    """その年齢の1年分の学費（学習費総額。塾・習い事を含む）。"""
    e = _cfg()["education"]
    st = _stage(age)
    if st is None:
        return 0
    if st == "university":
        if univ == "none":
            return 0
        u = e["university"][univ]
        return u["first_year"] if age == 18 else u["later_years"]
    private = (school == "private_jhs" and st in ("junior_high", "high"))
    return e["school"][st]["private" if private else "public"]


def child_allowance(ages_this_year: Sequence[int]) -> int:
    """その年の児童手当の合計（年額）。

    第3子以降の数え方は、22歳の年度末までの子を上から数える。ここでは
    年齢で近似し、22歳以下を数える。
    """
    a = _cfg()["child_allowance"]
    counted = sorted([x for x in ages_this_year if 0 <= x <= a["count_until"]],
                     reverse=True)
    total = 0
    for nth, age in enumerate(counted, start=1):
        if age > a["paid_until"]:
            continue
        if nth >= 3:
            total += a["third_plus"] * 12
        elif age < 3:
            total += a["under3"] * 12
        else:
            total += a["from3"] * 12
    return total


@dataclass
class EducationYear:
    offset: int          # 何年後か（0＝今年）
    ages: List[int]
    gross: int           # 学費
    allowance: int       # 児童手当
    net: int             # 差し引き


@dataclass
class EducationPlan:
    years: List[EducationYear] = field(default_factory=list)
    school: str = "public"
    univ: str = "private"

    @property
    def total(self) -> int:
        return sum(y.net for y in self.years)

    @property
    def average(self) -> int:
        """末っ子が大学を出るまでを均した年額（その間、毎年同じ額を
        積み立てて払う前提）。"""
        return int(self.total / len(self.years)) if self.years else 0

    @property
    def peak(self) -> Optional[EducationYear]:
        return max(self.years, key=lambda y: y.net) if self.years else None


def education_plan(ages: Sequence[int], school: str = "public",
                   univ: str = "private") -> EducationPlan:
    """今年から、いちばん下の子が22歳になる前の年までの教育費。"""
    school = school if school in SCHOOL_ROUTES else "public"
    univ = univ if univ in UNIV_ROUTES else "private"
    plan = EducationPlan(school=school, univ=univ)
    ages = [a for a in ages if 0 <= a <= 21]
    if not ages:
        return plan
    span = 22 - min(ages)
    for t in range(span):
        now = [a + t for a in ages]
        gross = sum(school_cost(a, school, univ) for a in now)
        allow = child_allowance(now)
        plan.years.append(EducationYear(t, now, gross, allow,
                                        max(0, gross - allow)))
    return plan


# ---------------------------------------------------------------- 生活費
def living_cost_monthly(household_size: int) -> Optional[int]:
    """同じ人数の勤労者世帯の、住居と教育を除いた消費支出（月額）。

    家計調査の世帯人員別（勤労者世帯のうち世帯主が60歳未満）。6人以上は
    6の値を使う。1人は表に無いので None。
    """
    t = _cfg().get("living", {}).get("by_size") or {}
    if not t or not household_size:
        return None
    sizes = sorted(int(k) for k in t)
    if int(household_size) < sizes[0]:
        return None            # 単身はこの表に無い。2人の値で比べると厳しすぎる
    n = min(sizes[-1], int(household_size))
    best = min(sizes, key=lambda k: abs(k - n))
    return int(t[str(best)])


# ---------------------------------------------------------------- 採点
NET_BANDS = ((25, 1.0, "最高"), (30, 0.8, "高"), (35, 0.6, "中"))
NET_LOW = 0.3


def net_band(ratio: float) -> Tuple[float, str]:
    for upper, raw, label in NET_BANDS:
        if ratio <= upper:
            return raw, label
    return NET_LOW, "低"


def _one_lower(raw: float) -> float:
    steps = [r for _u, r, _l in NET_BANDS] + [NET_LOW]
    i = steps.index(raw) if raw in steps else len(steps) - 1
    return steps[min(i + 1, len(steps) - 1)]


@dataclass
class HouseholdCheck:
    household: Household
    annual_outflow: int            # 年間の返済（他の借入・管理費等を含む）
    net_ratio: float               # 手取りに対する割合（%）
    raw: float
    band: str
    gross_ratio: Optional[float] = None
    education: Optional[EducationPlan] = None
    household_size: Optional[int] = None
    living: Optional[int] = None   # 同じ人数の世帯の生活費（月）
    left_avg: Optional[int] = None  # 教育費を均した場合に、月に残る額
    left_peak: Optional[int] = None  # 教育費がいちばん重い年に、月に残る額
    short_years: List[int] = field(default_factory=list)  # 残る額が生活費を下回る年
    lowered: bool = False          # 残る額が生活費を下回り、1段下げた
    ages_missing: bool = False     # 子どもがいるのに年齢が無い
    notes: List[str] = field(default_factory=list)


def check(household: Household, annual_outflow: int,
          children_ages: Optional[Sequence[int]] = None,
          children: Optional[int] = None,
          household_size: Optional[int] = None,
          school: str = "public", univ: str = "private",
          gross_ratio: Optional[float] = None) -> Optional[HouseholdCheck]:
    """手取りで見た返済と、教育費を払ったあとに残る額。"""
    net = household.net
    if net <= 0 or annual_outflow <= 0:
        return None
    ratio = round(annual_outflow / net * 100, 1)
    raw, band = net_band(ratio)
    out = HouseholdCheck(household, annual_outflow, ratio, raw, band,
                         gross_ratio=gross_ratio)

    ages = list(children_ages or [])
    if children and len(ages) < children:
        out.ages_missing = True
    plan = education_plan(ages, school, univ) if ages else None
    out.education = plan

    n_children = max(len(ages), children or 0)
    size = household_size or (len(household.earners) + n_children)
    out.household_size = size
    out.living = living_cost_monthly(size)

    edu_avg = plan.average if plan else 0
    out.left_avg = int((net - annual_outflow - edu_avg) / 12)
    if plan and plan.years:
        pk = plan.peak
        out.left_peak = int((net - annual_outflow - pk.net) / 12)
        if out.living:
            out.short_years = [y.offset for y in plan.years
                               if (net - annual_outflow - y.net) / 12
                               < out.living]
    if out.living and out.left_avg < out.living:
        out.raw = _one_lower(out.raw)
        out.lowered = True
    return out


def finance_category(base, hc: Optional[HouseholdCheck]):
    """資金の点を、手取りで見た段に差し替える。配点（weight）は変えない。

    hc が無い（年収が分かれていない・自営業など）ときは base をそのまま返す。
    """
    if hc is None:
        return base
    from .scoring import CategoryScore
    raw = hc.raw
    reason = (f"返済は手取りの{hc.net_ratio}%（段：{hc.band}）。"
              f"世帯の手取り 年{hc.household.net / 10000:,.0f}万円")
    plus, minus = [], []
    if hc.net_ratio <= 25:
        plus.append(f"返済は手取りの{hc.net_ratio}%（25%以下）")
    elif hc.net_ratio > 35:
        minus.append(f"返済は手取りの{hc.net_ratio}%（35%を超えています）")
    elif hc.net_ratio > 30:
        minus.append(f"返済は手取りの{hc.net_ratio}%（30%を超えています）")
    if hc.lowered:
        reason += ("。教育費を均して払うと、暮らしに回せる額が同じ人数の"
                   "世帯の平均を下回るため1段下げています")
        minus.append(f"返済と教育費を払うと、月に残る額（約{hc.left_avg / 10000:,.1f}万円）"
                     f"が{hc.household_size}人世帯の平均的な生活費"
                     f"（約{hc.living / 10000:,.1f}万円）を下回ります")
    elif hc.short_years:
        a, b = hc.short_years[0], hc.short_years[-1]
        span = f"{a}年後" if a == b else f"{a}〜{b}年後"
        minus.append(f"教育費がいちばん重い時期（{span}）は、月に残る額が"
                     f"{hc.household_size}人世帯の平均的な生活費を下回ります")
    # 上限（30%/35%・税込年収）を超えていれば、今までどおり弱みに残す
    for m in base.minus:
        if "借入の上限" in m:
            minus.append(m)
    suff = base.sufficiency
    if hc.ages_missing:
        suff = min(suff, 0.7)
        reason += "。お子さまの年齢が分からないため、教育費は見込んでいません"
    return CategoryScore(base.name, base.weight, round(raw, 3),
                         round(base.weight * raw, 1), suff, reason,
                         list(base.sources) + ["家計の試算"],
                         plus=plus, minus=minus)


def with_finance(diagnosis, hc: Optional[HouseholdCheck]):
    """診断の「資金」だけを差し替えた写しを返す（合計は呼び出し側で出し直す）。"""
    import copy
    if hc is None:
        return diagnosis
    d = copy.copy(diagnosis)
    d.categories = [finance_category(c, hc) if c.name == "資金" else c
                    for c in diagnosis.categories]
    return d
