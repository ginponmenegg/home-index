# -*- coding: utf-8 -*-
"""詳細な資金計画（PRO版）。既存の loan.py は変更しない。

方針（PROJECT BRIEF 第13・14章）：
- 税率・料率は finance_config.json に外出しし、コードに数値を埋め込まない。
- 出典が確認できていない項目は金額を出さず status="unknown" を返す。
  「たぶんこのくらい」を作らない。
- 各項目は計算根拠(basis)と出典(source)を必ず持つ。

無料版との棲み分け：loan.py は月々返済額・返済負担率まで。本モジュールは
諸費用・金利シナリオ・繰上返済・適正借入額を扱う（第8章）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import os
import json
import math

from .loan import monthly_payment

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "finance_config.json")


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


FCONFIG = _load()

UNKNOWN = "unknown"        # 出典未確認のため金額を出さない
ESTIMATED = "estimated"    # 推定値（前提を basis に明記）
COMPUTED = "computed"      # 確認済みの根拠から計算


@dataclass
class CostItem:
    """諸費用の1項目。金額が出せない場合 amount=None・status=unknown。"""
    name: str
    amount: Optional[int]
    basis: str                  # 計算根拠（利用者に見せる）
    status: str                 # computed / estimated / unknown
    source: Optional[str] = None
    note: str = ""


@dataclass
class PurchaseCosts:
    """諸費用の総額と内訳。未確認項目があることを隠さない。"""
    items: List[CostItem] = field(default_factory=list)
    total: int = 0              # 金額が判明した項目の合計のみ
    unknown_items: List[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.unknown_items


# ---------------- 個別の費用項目 ----------------
def brokerage_fee(price: int, cfg: dict = None) -> CostItem:
    """仲介手数料（宅建業法の上限額）。税込料率で段階計算する。

    200万円以下5.5% / 200万円超400万円以下4.4% / 400万円超3.3%。
    800万円以下は低廉な空家等の特例（上限33万円）に触れる可能性を注記する。
    """
    c = (cfg or FCONFIG).get("brokerage", {})
    if not price or price <= 0:
        return CostItem("仲介手数料", None, "売買価格が未入力", UNKNOWN)
    tiers = c.get("tiers")
    if not tiers:
        return CostItem("仲介手数料", None, "料率が未設定", UNKNOWN,
                        c.get("source"), c.get("note", ""))
    amount = 0.0
    lower = 0
    parts = []
    for upper, rate in tiers:
        cap = price if upper is None else min(price, upper)
        if cap <= lower:
            continue
        span = cap - lower
        amount += span * rate
        parts.append(f"{span:,}円×{rate * 100:.1f}%")
        lower = cap
        if lower >= price:
            break
    amount = int(round(amount))
    basis = f"{' ＋ '.join(parts)} ＝ {amount:,}円（税込）"

    note = c.get("note", "")
    vh = c.get("vacant_house_special", {})
    limit, cap_amt = vh.get("price_limit"), vh.get("cap")
    if limit and cap_amt and price <= limit:
        note = (f"売買価格が{limit:,}円以下のため、低廉な空家等の特例により"
                f"最大{cap_amt:,}円（税込）まで請求され得ます。"
                f"{vh.get('note', '')} " + note)
    return CostItem("仲介手数料", amount, basis, COMPUTED, c.get("source"), note)


def loan_guarantee_fee(loan_amount: Optional[int], cfg: dict = None) -> CostItem:
    """住宅ローン保証料（一括前払い方式）。"""
    c = (cfg or FCONFIG).get("loan_guarantee", {})
    rate = c.get("rate")
    if not loan_amount or rate is None:
        return CostItem("住宅ローン保証料", None, "借入額または料率が未設定",
                        UNKNOWN, c.get("source"), c.get("note", ""))
    amount = int(round(loan_amount * rate))
    return CostItem("住宅ローン保証料", amount,
                    f"借入額 {loan_amount:,}円 × {rate * 100:.1f}%",
                    ESTIMATED, c.get("source"), c.get("note", ""))


def _flat_item(name: str, key: str, cfg: dict = None) -> CostItem:
    c = (cfg or FCONFIG).get(key, {})
    flat = c.get("flat")
    if flat is None:
        return CostItem(name, None, "金額が未設定", UNKNOWN, c.get("source"),
                        c.get("note", ""))
    return CostItem(name, int(flat), f"定額 {int(flat):,}円", ESTIMATED,
                    c.get("source"), c.get("note", ""))


def stamp_duty(price: int, cfg: dict = None) -> CostItem:
    """売買契約書に貼る印紙税。契約金額の区分による定額。"""
    c = (cfg or FCONFIG).get("stamp_duty", {})
    table = c.get("table_reduced")
    if not price or price <= 0 or not table:
        return CostItem("印紙税", None, "契約金額または税額表が未設定", UNKNOWN,
                        c.get("source"))
    amount = None
    for upper, tax in table:
        if upper is None or price <= upper:
            amount = tax
            break
    if amount is None:
        return CostItem("印紙税", None, "税額表の範囲外", UNKNOWN, c.get("source"))
    limit = c.get("reduced_until")
    return CostItem(
        "印紙税", int(amount),
        f"契約金額 {price:,}円 の区分による定額（軽減後）",
        COMPUTED, c.get("source"),
        f"軽減措置は{limit}までに作成される契約書が対象です。" if limit else "")


def _assessed(value: Optional[int], price_part: Optional[int],
              ratio: Optional[float]) -> Tuple[Optional[int], bool]:
    """固定資産税評価額。実額があれば優先、無ければ比率で推定。
    戻り値=(評価額, 推定したか)。"""
    if value:
        return int(value), False
    if price_part and ratio:
        return int(round(price_part * ratio)), True
    return None, False


def registration_tax(land_price: Optional[int] = None,
                     building_price: Optional[int] = None,
                     loan_amount: Optional[int] = None,
                     land_assessed: Optional[int] = None,
                     building_assessed: Optional[int] = None,
                     residential: bool = True,
                     cfg: dict = None) -> List[CostItem]:
    """登録免許税（所有権移転・抵当権設定）。課税標準は固定資産税評価額。

    購入検討段階では評価額が不明なことが多いため、売買価格からの推定を許す。
    推定した場合は status=estimated とし、前提を basis に明記する。
    """
    conf = (cfg or FCONFIG)
    c = conf.get("registration_tax", {})
    ratios = conf.get("assessed_value_ratio", {})
    out: List[CostItem] = []

    def _tax(label, part_cfg, assessed, estimated, part_name):
        rate = part_cfg.get("reduced") if residential else part_cfg.get("standard")
        used = "軽減税率" if residential else "本則税率"
        if rate is None:
            rate = part_cfg.get("standard")
            used = "本則税率"
        if rate is None:
            return CostItem(label, None, "税率が未設定", UNKNOWN, c.get("source"),
                            part_cfg.get("note", ""))
        if assessed is None:
            return CostItem(label, None,
                            f"{part_name}の固定資産税評価額が不明", UNKNOWN,
                            c.get("source"))
        amount = int(round(assessed * rate))
        basis = f"{part_name}の評価額 {assessed:,}円 × {rate * 100:.1f}%（{used}）"
        if estimated:
            basis += "　※評価額は売買価格からの推定"
        return CostItem(label, amount, basis,
                        ESTIMATED if estimated else COMPUTED, c.get("source"),
                        part_cfg.get("requirement", ""))

    la, la_est = _assessed(land_assessed, land_price, ratios.get("land"))
    out.append(_tax("登録免許税（土地の所有権移転）", c.get("land_transfer", {}),
                    la, la_est, "土地"))

    ba, ba_est = _assessed(building_assessed, building_price,
                           ratios.get("building"))
    out.append(_tax("登録免許税（建物の所有権移転）", c.get("building_transfer", {}),
                    ba, ba_est, "建物"))

    m = c.get("mortgage", {})
    rate = m.get("reduced") if residential else m.get("standard")
    if rate is None or not loan_amount:
        out.append(CostItem("登録免許税（抵当権の設定）", None,
                            "税率または借入額が未設定", UNKNOWN, c.get("source"),
                            m.get("note", "")))
    else:
        out.append(CostItem(
            "登録免許税（抵当権の設定）", int(round(loan_amount * rate)),
            f"借入額 {loan_amount:,}円 × {rate * 100:.1f}%", COMPUTED,
            c.get("source"), m.get("note", "")))
    return out


def _ymd(s: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """'1981-07-01' -> (1981, 7, 1)。None や不正値は None。"""
    if not s:
        return None
    try:
        y, m, d = str(s).split("-")
        return int(y), int(m), int(d)
    except (ValueError, AttributeError):
        return None


def _in_span(date: Tuple[int, int, int], lo: Optional[str],
             hi: Optional[str]) -> bool:
    lo_t, hi_t = _ymd(lo), _ymd(hi)
    if lo_t and date < lo_t:
        return False
    if hi_t and date > hi_t:
        return False
    return True


def _band_value(table: list, year: int, month: Optional[int],
                day: Optional[int]) -> Tuple[Optional[int], bool]:
    """新築時期の区分表から該当額を引く。

    月日が不明な場合、その年に該当し得る区分が複数あれば
    「不利側（額の小さい方）」を採る（運営方針）。
    戻り値=(額, 月日不明のため不利側を採ったか)。
    """
    if month and day:
        for lo, hi, val in table:
            if _in_span((year, month, day), lo, hi):
                return int(val), False
        return None, False
    # 月日不明：その年と重なる区分を全部集める
    cands = []
    for lo, hi, val in table:
        lo_t, hi_t = _ymd(lo), _ymd(hi)
        if (lo_t is None or lo_t[0] <= year) and (hi_t is None or hi_t[0] >= year):
            cands.append(int(val))
    if not cands:
        return None, False
    return min(cands), len(cands) > 1


def acquisition_tax(building_assessed: Optional[int] = None,
                    land_assessed: Optional[int] = None,
                    land_area_m2: Optional[float] = None,
                    floor_area_m2: Optional[float] = None,
                    build_year: Optional[int] = None,
                    build_month: Optional[int] = None,
                    build_day: Optional[int] = None,
                    quake_conforming: Optional[bool] = None,
                    residential_land: bool = True,
                    cfg: dict = None) -> List[CostItem]:
    """不動産取得税（建物・土地）。

    建物：(評価額 − 控除額) × 3%。耐震基準不適合の場合は控除ではなく税額から減額。
    土地：評価額 × 1/2（宅地）× 3% − 軽減額。
          軽減額 = max(45,000円, 1㎡単価 × min(床面積×2, 200㎡) × 3%)
    """
    c = (cfg or FCONFIG).get("acquisition_tax", {})
    src = c.get("source")
    juris = c.get("jurisdiction")
    prefix = f"【{juris}基準】" if juris else ""
    rates = c.get("rates", {})
    out: List[CostItem] = []

    r_b = rates.get("residential_building")
    r_l = rates.get("land")
    if r_b is None or r_l is None:
        return [CostItem("不動産取得税", None, "税率が未設定", UNKNOWN, src,
                         c.get("note", ""))]

    # ---- 床面積要件 ----
    fmin, fmax = c.get("floor_area_min"), c.get("floor_area_max")
    eligible = True
    area_note = ""
    if floor_area_m2 is None:
        eligible = False
        area_note = "床面積が未入力のため軽減の適用可否を判定できません"
    elif fmin and floor_area_m2 < fmin:
        eligible = False
        area_note = f"床面積が{fmin}㎡未満のため軽減の対象外"
    elif fmax and floor_area_m2 > fmax:
        eligible = False
        area_note = f"床面積が{fmax}㎡超のため軽減の対象外"

    # ---- 建物 ----
    if building_assessed is None or build_year is None:
        out.append(CostItem("不動産取得税（建物）", None,
                            "建物の評価額または新築時期が不明", UNKNOWN, src))
    elif not eligible:
        tax = int(round(building_assessed * r_b))
        out.append(CostItem(
            "不動産取得税（建物）", tax,
            f"{prefix}評価額 {building_assessed:,}円 × {r_b*100:.0f}%（軽減なし）",
            ESTIMATED, src, area_note))
    elif quake_conforming is False:
        # 耐震基準不適合：控除は無く、税額から定額を減額
        tbl = c.get("reduction_nonconforming", {}).get("table", [])
        red, ambiguous = _band_value(tbl, build_year, build_month, build_day)
        tax = int(round(building_assessed * r_b))
        final = max(0, tax - (red or 0))
        basis = (f"{prefix}評価額 {building_assessed:,}円 × {r_b*100:.0f}% "
                 f"＝ {tax:,}円 − 軽減 {(red or 0):,}円（耐震基準不適合）")
        if ambiguous:
            basis += "　※新築の月日が不明なため不利側（軽減額の小さい方）で試算"
        out.append(CostItem("不動産取得税（建物）", final, basis, ESTIMATED, src,
                            "取得後に耐震改修し証明を受けた場合の軽減です。"))
    else:
        key = "after_s57" if build_year >= 1982 else "before_s56_certified"
        tbl = c.get("deduction_conforming", {}).get(key, [])
        ded, ambiguous = _band_value(tbl, build_year, build_month, build_day)
        if ded is None:
            out.append(CostItem("不動産取得税（建物）", None,
                                f"{build_year}年築は控除額の区分表の範囲外",
                                UNKNOWN, src))
        else:
            base = max(0, building_assessed - ded)
            tax = int(round(base * r_b))
            basis = (f"{prefix}(評価額 {building_assessed:,}円 − 控除 {ded:,}円)"
                     f" × {r_b*100:.0f}%")
            if ambiguous:
                basis += "　※新築の月日が不明なため不利側（控除額の小さい方）で試算"
            note = ""
            if build_year <= 1981:
                note = "耐震基準適合証明（取得日前2年以内の調査）が必要です。"
            out.append(CostItem("不動産取得税（建物）", tax, basis, ESTIMATED,
                                src, note))

    # ---- 土地 ----
    if land_assessed is None:
        out.append(CostItem("不動産取得税（土地）", None,
                            "土地の評価額が不明", UNKNOWN, src))
        return out

    half = 0.5 if residential_land else 1.0
    taxable = int(round(land_assessed * half))
    land_tax = int(round(taxable * r_l))
    lr = c.get("land_reduction", {})
    reduction = 0
    detail = ""
    if eligible and land_area_m2 and floor_area_m2 and land_area_m2 > 0:
        unit = taxable / land_area_m2          # 1㎡単価（1/2適用後）
        cap = lr.get("floor_area_cap_m2", 200)
        mult = lr.get("floor_area_multiplier", 2)
        target = min(floor_area_m2 * mult, cap)
        calc = int(round(unit * target * lr.get("rate", r_l)))
        flat = int(lr.get("flat", 0))
        reduction = max(flat, calc)
        detail = (f"　軽減 = max({flat:,}円, 1㎡単価 {int(unit):,}円 × "
                  f"{target:.0f}㎡ × {lr.get('rate', r_l)*100:.0f}% "
                  f"＝ {calc:,}円) ＝ {reduction:,}円")
    final = max(0, land_tax - reduction)
    basis = (f"{prefix}評価額 {land_assessed:,}円"
             f"{' × 1/2（宅地）' if residential_land else ''}"
             f" × {r_l*100:.0f}% ＝ {land_tax:,}円{detail}")
    out.append(CostItem("不動産取得税（土地）", final, basis, ESTIMATED, src,
                        area_note if not eligible else ""))
    return out


def judicial_scrivener(cfg: dict = None) -> CostItem:
    """司法書士報酬。登録免許税とあわせて登記費用を構成する。"""
    c = (cfg or FCONFIG).get("judicial_scrivener", {})
    flat = c.get("flat")
    if flat is None:
        return CostItem("司法書士報酬", None, "報酬額が未設定", UNKNOWN,
                        c.get("source"), c.get("note", ""))
    return CostItem("司法書士報酬", int(flat), f"定額 {int(flat):,}円",
                    ESTIMATED, c.get("source"), c.get("note", ""))


def fire_insurance(earthquake: bool = False, cfg: dict = None) -> CostItem:
    """火災保険料。地震保険の有無で金額帯が変わる。"""
    c = (cfg or FCONFIG).get("fire_insurance", {})
    key = "with_earthquake" if earthquake else "without_earthquake"
    band = c.get(key, {})
    lo, hi = band.get("low"), band.get("high")
    term = c.get("term_years")
    if lo is None or hi is None:
        return CostItem("火災保険料", None, "相場が未設定", UNKNOWN,
                        c.get("source"), c.get("note", ""))
    label = "火災保険料（地震保険あり）" if earthquake else "火災保険料（地震保険なし）"
    mid = int((lo + hi) / 2)
    term_txt = f"{term}年一括の" if term else ""
    return CostItem(label, mid,
                    f"{term_txt}目安 {lo:,}〜{hi:,}円 の中央値",
                    ESTIMATED, c.get("source"), c.get("note", ""))


def purchase_costs(price: int,
                   land_price: Optional[int] = None,
                   building_price: Optional[int] = None,
                   loan_amount: Optional[int] = None,
                   land_assessed: Optional[int] = None,
                   building_assessed: Optional[int] = None,
                   land_area_m2: Optional[float] = None,
                   floor_area_m2: Optional[float] = None,
                   build_year: Optional[int] = None,
                   build_month: Optional[int] = None,
                   build_day: Optional[int] = None,
                   quake_conforming: Optional[bool] = None,
                   earthquake_insurance: bool = False,
                   new_build: bool = False,
                   option_cost: bool = False,
                   residential: bool = True,
                   cfg: dict = None) -> PurchaseCosts:
    """購入諸費用の一式。判明した項目だけを合計し、未確認は明示する。

    new_build=True のときだけ表題登記・保存登記を計上する（中古では通常発生しない）。
    option_cost=True のときだけオプション費用を計上する（任意項目）。
    """
    conf = cfg or FCONFIG
    ratios = conf.get("assessed_value_ratio", {})
    # 評価額は実額優先。無ければ売買価格から推定（推定した旨は各項目に出る）。
    la, _ = _assessed(land_assessed, land_price, ratios.get("land"))
    ba, _ = _assessed(building_assessed, building_price, ratios.get("building"))

    items: List[CostItem] = [
        brokerage_fee(price, cfg),
        stamp_duty(price, cfg),
    ]
    items += registration_tax(land_price, building_price, loan_amount,
                              land_assessed, building_assessed, residential, cfg)
    if new_build:
        items.append(_flat_item("表題登記費用", "title_registration", cfg))
        items.append(_flat_item("所有権保存登記費用", "preservation_registration", cfg))
    items.append(judicial_scrivener(cfg))
    items += acquisition_tax(ba, la, land_area_m2, floor_area_m2,
                             build_year, build_month, build_day,
                             quake_conforming, residential, cfg)
    items.append(loan_guarantee_fee(loan_amount, cfg))
    items.append(fire_insurance(earthquake_insurance, cfg))
    if option_cost:
        items.append(_flat_item("オプション費用", "option_cost", cfg))

    total = sum(i.amount for i in items if i.amount)
    unknown = [i.name for i in items if i.amount is None]
    return PurchaseCosts(items, total, unknown)


_REGISTRATION_NAMES = ("司法書士報酬", "表題登記費用", "所有権保存登記費用")


def registration_cost_total(costs: PurchaseCosts) -> Optional[int]:
    """登記費用（登録免許税＋司法書士報酬＋表題/保存登記）の小計。合算表示用。"""
    vals = [i.amount for i in costs.items
            if i.amount is not None
            and (i.name.startswith("登録免許税") or i.name in _REGISTRATION_NAMES)]
    return sum(vals) if vals else None


# ---------------- 金利シナリオ ----------------
@dataclass
class RateScenario:
    label: str
    annual_rate: float
    monthly: int
    total: int
    diff_monthly: int      # 基準シナリオとの差額


def rate_scenarios(principal: int, years: int, base_rate: float,
                   deltas: List[float] = None) -> List[RateScenario]:
    """金利が変動した場合の返済額。将来予測ではなく「いくらになるか」の試算。"""
    if deltas is None:
        deltas = [0.0, 0.005, 0.01, 0.02]
    base = monthly_payment(principal, base_rate, years)
    out = []
    for d in deltas:
        r = base_rate + d
        m = monthly_payment(principal, r, years)
        label = "現在の金利" if d == 0 else f"+{d * 100:.1f}%"
        out.append(RateScenario(label, r, m, m * years * 12, m - base))
    return out


# ---------------- 繰上返済 ----------------
@dataclass
class PrepaymentResult:
    kind: str                  # 期間短縮型 / 返済額軽減型
    amount: int                # 繰上返済額
    months_saved: int          # 短縮月数
    interest_saved: int        # 軽減される利息
    new_monthly: int           # 返済後の月額


def remaining_balance(principal: int, annual_rate: float, years: int,
                      paid_months: int) -> int:
    """元利均等返済で paid_months 回返済した後の残高。"""
    n = years * 12
    r = annual_rate / 12.0
    if r == 0:
        return max(0, int(round(principal * (n - paid_months) / n)))
    m = monthly_payment(principal, annual_rate, years)
    bal = principal * (1 + r) ** paid_months - m * (((1 + r) ** paid_months - 1) / r)
    return max(0, int(round(bal)))


def prepayment(principal: int, annual_rate: float, years: int,
               prepay_amount: int, after_months: int = 12,
               kind: str = "期間短縮型") -> PrepaymentResult:
    """繰上返済の効果。after_months 回返済した時点で prepay_amount を投入する。"""
    n = years * 12
    r = annual_rate / 12.0
    m = monthly_payment(principal, annual_rate, years)
    bal = remaining_balance(principal, annual_rate, years, after_months)
    remain_months = n - after_months
    interest_before = m * remain_months - bal

    new_bal = max(0, bal - max(0, prepay_amount))
    if kind == "返済額軽減型":
        remain_years = max(1, remain_months // 12)
        new_m = monthly_payment(new_bal, annual_rate, remain_years)
        interest_after = new_m * remain_months - new_bal
        return PrepaymentResult(kind, prepay_amount, 0,
                                max(0, int(interest_before - interest_after)),
                                int(new_m))
    # 期間短縮型：月額は据え置き、返済回数が減る
    if r == 0:
        new_months = int(round(new_bal / m)) if m else 0
    elif new_bal <= 0:
        new_months = 0
    elif m <= new_bal * r:
        new_months = remain_months
    else:
        new_months = int(math.ceil(
            -math.log(1 - new_bal * r / m) / math.log(1 + r)))
    new_months = min(new_months, remain_months)
    interest_after = m * new_months - new_bal
    return PrepaymentResult(kind, prepay_amount,
                            max(0, remain_months - new_months),
                            max(0, int(interest_before - interest_after)), int(m))


# ---------------- 住宅ローン減税 ----------------
@dataclass
class LoanDeduction:
    """住宅ローン控除の試算。実際の控除額は納税額が上限になる点に注意。"""
    category: str              # 長期優良・低炭素 / ZEH水準省エネ / 省エネ基準適合 / その他
    limit: Optional[int]       # 借入限度額
    years: Optional[int]       # 控除期間
    yearly: List[int] = field(default_factory=list)   # 各年の控除額
    total: int = 0
    status: str = UNKNOWN
    basis: str = ""
    source: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def loan_deduction(principal: int, annual_rate: float, years: int,
                   category: str = "その他",
                   is_resale: bool = False,
                   is_kosodate: bool = False,
                   annual_income: Optional[int] = None,
                   floor_area_m2: Optional[float] = None,
                   build_year: Optional[int] = None,
                   cfg: dict = None) -> LoanDeduction:
    """住宅ローン控除の最大額。各年末残高と借入限度額の小さい方に控除率を掛ける。

    実際の控除額はその人の所得税・住民税の範囲内に限られるため、ここで出るのは
    「制度上の上限」。個別の税額計算には踏み込まない（§8-6）。
    """
    c = (cfg or FCONFIG).get("loan_deduction", {})
    rate = c.get("rate")
    table = c.get("resale" if is_resale else "existing", {})
    band = table.get(category)
    src = c.get("source")
    notes: List[str] = []

    if rate is None or not band:
        return LoanDeduction(category, None, None, [], 0, UNKNOWN,
                             "控除率または区分が未設定", src)

    # 要件チェック（満たさない場合は算出しない）
    income_limit = c.get("income_limit")
    if annual_income is not None and income_limit and annual_income > income_limit:
        return LoanDeduction(category, None, None, [], 0, UNKNOWN,
                             f"合計所得金額が{income_limit:,}円を超えるため対象外", src)

    fmin = c.get("floor_area_min")
    if annual_income is not None and c.get("high_income_threshold") and \
            annual_income > c["high_income_threshold"]:
        fmin = c.get("floor_area_min_high_income", fmin)
    if floor_area_m2 is not None and fmin and floor_area_m2 < fmin:
        return LoanDeduction(category, None, None, [], 0, UNKNOWN,
                             f"床面積が{fmin}㎡未満のため対象外", src)

    cert_before = _ymd(c.get("quake_cert_required_before"))
    if build_year and cert_before and build_year <= cert_before[0]:
        notes.append(c.get("quake_cert_note", ""))

    limit = band.get("limit")
    if is_kosodate and band.get("limit_kosodate"):
        limit = band["limit_kosodate"]
        notes.append("子育て世帯・若者夫婦世帯の上乗せを適用しています。")
    elif is_kosodate:
        notes.append("この区分には子育て世帯の上乗せがありません。")
    n_years = band.get("years")
    if limit is None or n_years is None:
        return LoanDeduction(category, None, None, [], 0, UNKNOWN,
                             "借入限度額または控除期間が未設定", src)

    yearly = []
    for y in range(1, n_years + 1):
        bal = remaining_balance(principal, annual_rate, years, y * 12)
        yearly.append(int(round(min(bal, limit) * rate)))
    total = sum(yearly)
    basis = (f"{category}（{'買取再販' if is_resale else '既存住宅'}）"
             f"／借入限度額 {limit:,}円 × 控除率 {rate*100:.1f}% × {n_years}年")
    notes.append("実際の控除額は、その年の所得税・住民税の額が上限になります。")
    return LoanDeduction(category, limit, n_years, yearly, total,
                         ESTIMATED, basis, src, notes)


# ---------------- 適正借入額の逆算 ----------------
@dataclass
class Affordability:
    annual_income: int
    burden_limit: float        # 返済負担率の上限(%)
    max_monthly: int
    max_principal: int
    max_price: int             # 頭金を足した購入可能額
    note: str


def affordable_loan(annual_income: int, annual_rate: float, years: int,
                    down_payment: int = 0,
                    burden_limit: Optional[float] = None) -> Affordability:
    """年収から逆算した借入可能額。scoring.score_finance と同じ基準を使う。

    年収400万円以上は35%、未満は30%（既存の score_finance と揃える）。
    """
    if burden_limit is None:
        burden_limit = 35.0 if annual_income >= 4_000_000 else 30.0
    max_annual = annual_income * burden_limit / 100.0
    max_monthly = int(max_annual / 12)
    n = years * 12
    r = annual_rate / 12.0
    if r == 0:
        principal = max_monthly * n
    else:
        principal = max_monthly * (1 - (1 + r) ** (-n)) / r
    principal = int(round(principal))
    return Affordability(
        annual_income, burden_limit, max_monthly, principal,
        principal + (down_payment or 0),
        f"返済負担率 {burden_limit:.0f}% を上限とした場合の試算です。"
        "金融機関の審査基準・他の借入・諸費用は考慮していません。")
