# -*- coding: utf-8 -*-
"""資金計画（PRO）のオフライン単体テスト。ネットワーク不要。

既存の tests/test_logic.py と同じ形式（pytest 無しでも自走する）。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.finance import (brokerage_fee, stamp_duty, registration_tax,
                         acquisition_tax, purchase_costs, rate_scenarios,
                         prepayment, remaining_balance, affordable_loan,
                         UNKNOWN, ESTIMATED, COMPUTED)
from src.loan import monthly_payment


def test_brokerage_fee():
    # 3,480万円 → (3480万×3%+6万)×1.1 = (104.4万+6万)×1.1 = 121.44万円
    it = brokerage_fee(34_800_000)
    assert it.status == COMPUTED
    assert it.amount == 1_214_400, it.amount
    # 400万円以下は速算式の対象外 → 金額を出さない
    it2 = brokerage_fee(3_000_000)
    assert it2.status == UNKNOWN and it2.amount is None
    # 価格未入力
    assert brokerage_fee(0).amount is None


def test_stamp_duty_brackets():
    # 1,000万円超5,000万円以下 → 1万円
    assert stamp_duty(34_800_000).amount == 10_000
    # 500万円超1,000万円以下 → 5,000円
    assert stamp_duty(9_000_000).amount == 5_000
    # 5,000万円超1億円以下 → 3万円
    assert stamp_duty(60_000_000).amount == 30_000
    # 境界値：ちょうど1,000万円は「1,000万円以下」の区分
    assert stamp_duty(10_000_000).amount == 5_000
    assert stamp_duty(10_000_001).amount == 10_000


def test_registration_tax_estimates_and_flags():
    items = registration_tax(land_price=20_000_000, building_price=14_800_000,
                             loan_amount=30_000_000)
    by = {i.name: i for i in items}
    land = by["登録免許税（土地の所有権移転）"]
    # 評価額を推定した場合は estimated になり、根拠に明記される
    assert land.status == ESTIMATED
    assert "推定" in land.basis
    # 2,000万円×70% = 1,400万円 × 1.5% = 21万円
    assert land.amount == 210_000, land.amount
    # 抵当権は借入額ベース：3,000万円 × 0.1% = 3万円
    assert by["登録免許税（抵当権の設定）"].amount == 30_000
    # 実額の評価額を渡したら computed になる
    items2 = registration_tax(land_assessed=10_000_000, building_assessed=5_000_000)
    assert items2[0].status == COMPUTED
    assert items2[0].amount == 150_000


def test_unknown_items_are_not_invented():
    """出典未確認の項目は金額を作らない（第14章）。"""
    assert acquisition_tax().amount is None
    assert acquisition_tax().status == UNKNOWN
    pc = purchase_costs(34_800_000, land_price=20_000_000,
                        building_price=14_800_000, loan_amount=30_000_000)
    # 不動産取得税・司法書士報酬・火災保険は未設定なので未確認に入る
    assert "不動産取得税" in pc.unknown_items
    assert pc.is_complete is False
    # 合計は判明分のみ。未確認をゼロとして足し込んでいないこと
    assert pc.total == sum(i.amount for i in pc.items if i.amount)
    assert pc.total > 0


def test_rate_scenarios_monotonic():
    sc = rate_scenarios(30_000_000, 35, 0.0125)
    assert sc[0].label == "現在の金利" and sc[0].diff_monthly == 0
    # 金利が上がるほど月額も上がる
    monthlies = [s.monthly for s in sc]
    assert monthlies == sorted(monthlies)
    assert sc[-1].diff_monthly > 0


def test_remaining_balance_decreases():
    p, r, y = 30_000_000, 0.0125, 35
    b0 = remaining_balance(p, r, y, 0)
    b12 = remaining_balance(p, r, y, 12)
    bend = remaining_balance(p, r, y, y * 12)
    assert b0 == p
    assert b12 < p
    assert bend <= 1000  # 完済時はほぼ0


def test_prepayment_shortens_term_and_saves_interest():
    res = prepayment(30_000_000, 0.0125, 35, 3_000_000,
                     after_months=12, kind="期間短縮型")
    assert res.months_saved > 0
    assert res.interest_saved > 0
    # 期間短縮型は月額が変わらない
    assert res.new_monthly == monthly_payment(30_000_000, 0.0125, 35)
    # 返済額軽減型は月額が下がる
    res2 = prepayment(30_000_000, 0.0125, 35, 3_000_000,
                      after_months=12, kind="返済額軽減型")
    assert res2.new_monthly < res.new_monthly
    assert res2.months_saved == 0
    # 繰上返済額0なら効果も0
    res3 = prepayment(30_000_000, 0.0125, 35, 0, after_months=12)
    assert res3.months_saved == 0


def test_affordable_loan_matches_score_finance_thresholds():
    # 年収400万円以上は35%基準（score_finance と同じ）
    a = affordable_loan(8_000_000, 0.0125, 35)
    assert a.burden_limit == 35.0
    # 年収400万円未満は30%基準
    b = affordable_loan(3_000_000, 0.0125, 35)
    assert b.burden_limit == 30.0
    # 逆算の整合性：算出された借入額の月額が上限月額とほぼ一致する
    m = monthly_payment(a.max_principal, 0.0125, 35)
    assert abs(m - a.max_monthly) <= 2, (m, a.max_monthly)
    # 頭金は購入可能額に加算される
    c = affordable_loan(8_000_000, 0.0125, 35, down_payment=5_000_000)
    assert c.max_price == c.max_principal + 5_000_000


def test_free_tier_loan_untouched():
    """既存の無料版ロジックを壊していないこと。"""
    from src.loan import compute_loan
    L = compute_loan(38_800_000, down_payment=8_800_000, annual_rate=0.0125,
                     years=35, annual_income=8_000_000)
    assert L.principal == 30_000_000
    assert L.burden_ratio is not None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"[OK] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
