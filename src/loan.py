# -*- coding: utf-8 -*-
"""住宅ローン計算（指示書 第8章：無料版の範囲＝月々返済額まで）。

無料版：借入額・金利・返済期間・頭金・月々返済額。
詳細な資金計画・諸費用・金利シナリオは有料版（本MVPでは扱わない）。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class LoanResult:
    principal: int           # 借入額(円)
    monthly_payment: int     # 月々返済額(円)
    annual_payment: int      # 年間返済額(円)
    total_payment: int       # 総返済額(円)
    burden_ratio: Optional[float]  # 負担率(年間の支払/年収)。年収不明ならNone
    income: Optional[int] = None   # 年収(円)。年収不明ならNone
    monthly_extra: int = 0         # 管理費・修繕積立金など毎月の固定費(円/月)


def monthly_payment(principal: int, annual_rate: float, years: int) -> int:
    """元利均等の月々返済額。annual_rate は 0.0125 のような小数。"""
    n = years * 12
    if n <= 0:
        return 0
    r = annual_rate / 12.0
    if r == 0:
        return int(round(principal / n))
    pay = principal * r / (1 - (1 + r) ** (-n))
    return int(round(pay))


def compute_loan(price: int, down_payment: int = 0,
                 annual_rate: float = 0.0125, years: int = 35,
                 annual_income: Optional[int] = None,
                 monthly_extra: int = 0) -> LoanResult:
    """monthly_extra はマンションの管理費・修繕積立金のような毎月の固定費。

    住み続ける限り必ず出ていく金額なので、負担率にはこれを含めて見る。
    戸建は 0 のまま呼ばれるため、既存の結果は変わらない。
    """
    principal = max(0, price - (down_payment or 0))
    m = monthly_payment(principal, annual_rate, years)
    extra = max(0, monthly_extra or 0)
    annual = m * 12
    total = m * years * 12
    burden = None
    if annual_income and annual_income > 0:
        burden = round((annual + extra * 12) / annual_income * 100.0, 1)
    return LoanResult(principal, m, annual, total, burden, annual_income,
                      extra)
