# -*- coding: utf-8 -*-
"""注文住宅の資金の流れ（つなぎ融資・総額）。ネットワーク不要。

つなぎ融資は、注文住宅で唯一「誰も先に教えてくれない」費用。
ここの計算を間違えると、土地を決済したあとに困る人が出る。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.finance import UNKNOWN
from src.land_finance import (DAYS_PER_YEAR, DEFAULT_FRAME_PCT,
                              DEFAULT_START_PCT, bridge_loan, cash_timeline,
                              land_total)

LAND = 18_000_000
BUILDING = 25_000_000


def _b(**kw):
    base = dict(land_price=LAND, building_price=BUILDING,
                settlement="2026-04-10", completion="2027-02-28",
                rate_pct=2.8)
    base.update(kw)
    return bridge_loan(**base)


# ---- 利息そのもの ----
def test_the_interest_is_daily_and_simple():
    """利息 = 実行額 × 年利 × 日数 ÷ 365。手で検算できること。"""
    b = _b(own_funds=0)
    land = b.drawdowns[0]
    assert land.amount == LAND
    assert land.days == (datetime.date(2027, 2, 28)
                         - datetime.date(2026, 4, 10)).days == 324
    expected = round(LAND * 0.028 * 324 / DAYS_PER_YEAR)
    assert land.interest == expected


def test_a_longer_build_costs_more_interest():
    short = _b(completion="2026-10-31")
    long = _b(completion="2027-08-31")
    assert long.interest > short.interest


def test_own_funds_are_spent_on_the_earliest_drawdown_first():
    """自己資金は、いちばん早い実行に充てる。利息が乗る期間が長いため。"""
    b = _b(own_funds=3_000_000)
    assert b.drawdowns[0].amount == LAND - 3_000_000
    assert b.interest < _b(own_funds=0).interest


def test_enough_own_funds_removes_a_drawdown_entirely():
    b = _b(own_funds=LAND)
    assert all("土地代金" not in x.name for x in b.drawdowns)


def test_the_building_payments_follow_the_stated_split():
    b = _b(own_funds=0)
    start = [x for x in b.drawdowns if x.name == "着工金"][0]
    framing = [x for x in b.drawdowns if x.name == "上棟時の中間金"][0]
    assert start.amount == int(BUILDING * DEFAULT_START_PCT / 100)
    assert framing.amount == int(BUILDING * DEFAULT_FRAME_PCT / 100)
    # 既定値を使ったことを必ず言う。請負契約書で決まるものなので。
    assert any("請負契約書" in a for a in b.assumed)


def test_a_given_split_is_used_without_the_assumption_notice():
    b = _b(own_funds=0, start_pct=10, framing_pct=40,
           start="2026-07-01", framing="2026-11-01")
    start = [x for x in b.drawdowns if x.name == "着工金"][0]
    assert start.amount == int(BUILDING * 0.10)
    assert not b.assumed


def test_the_dates_we_guessed_are_declared():
    b = _b(own_funds=0)          # 着工日・上棟日は未入力
    assert any("着工日が未入力" in a for a in b.assumed)
    assert any("上棟日が未入力" in a for a in b.assumed)


# ---- 分からないときは計算しない ----
def test_without_dates_nothing_is_computed():
    b = bridge_loan(LAND, BUILDING, rate_pct=2.8)
    assert b.drawdowns == []
    assert b.interest == 0
    assert any("決済予定日" in n for n in b.notes)


def test_a_completion_before_settlement_is_refused():
    b = _b(completion="2026-01-01")
    assert b.drawdowns == []
    assert any("完成予定日" in n for n in b.notes)


def test_a_missing_rate_is_said_out_loud_rather_than_guessed():
    b = _b(rate_pct=0)
    assert b.interest == 0
    assert any("金利が未入力" in n for n in b.notes)
    # 金利を勝手に置かない。置いたら、その数字が独り歩きする。


# ---- 総額 ----
def test_the_total_only_adds_what_it_knows():
    t = land_total(LAND, BUILDING, bridge_interest=500_000)
    assert t.total == LAND + BUILDING + 500_000
    # 付帯・外構は未入力なので合計に入れず、名前だけ残す
    assert "付帯工事費" in t.unknown
    assert "外構工事費" in t.unknown


def test_the_costs_with_no_public_source_are_never_given_a_number():
    """地盤改良・解体・引き込みに金額を出さない。

    公的な統計が無いことを確かめた（docs/土地PRO_設計.md 第7章）。
    世に出ている相場は工務店や比較サイトの自社見積りで、一次情報ではない。
    """
    t = land_total(LAND, BUILDING)
    for name in ("地盤改良", "古家の解体", "上下水道・ガスの引き込み"):
        hit = [i for i in t.items if i.name.startswith(name)]
        assert hit, name
        assert hit[0].amount is None, name
        assert hit[0].status == UNKNOWN, name


def test_a_cost_known_to_be_unnecessary_is_dropped():
    t = land_total(LAND, BUILDING, needs={"古家の解体": False})
    assert not [i for i in t.items if i.name.startswith("古家の解体")]


def test_a_cost_known_to_be_needed_loses_the_uncertain_label():
    t = land_total(LAND, BUILDING, needs={"地盤改良": True})
    hit = [i for i in t.items if i.name.startswith("地盤改良")][0]
    assert hit.name == "地盤改良"          # 「要否も未確認」が付かない
    assert hit.amount is None             # それでも金額は出さない


def test_entered_budgets_are_counted():
    t = land_total(LAND, BUILDING, extra_work=3_000_000,
                   exterior=1_500_000, other_costs=2_000_000)
    assert t.total == LAND + BUILDING + 3_000_000 + 1_500_000 + 2_000_000
    assert "付帯工事費" not in t.unknown


# ---- 時系列 ----
def _timeline(**kw):
    b = _b(**{k: v for k, v in kw.items() if k in ("own_funds",)})
    return b, cash_timeline(b, LAND, BUILDING,
                            own_funds=kw.get("own_funds", 0),
                            deposit=kw.get("deposit"),
                            contract_date="2026-03-01",
                            settlement="2026-04-10",
                            completion="2027-02-28")


def test_the_timeline_runs_in_date_order():
    _, events = _timeline(own_funds=3_000_000, deposit=1_000_000)
    dates = [e.date for e in events if e.date]
    assert dates == sorted(dates)


def test_the_deposit_is_flagged_as_cash_before_any_loan():
    """手付金は融資の実行前に払う。ここで詰まる人が多い。"""
    _, events = _timeline(deposit=1_000_000)
    first = events[0]
    assert first.name == "土地の手付金"
    assert first.source == "自己資金"
    assert "現金" in first.note


def test_the_last_event_is_the_loan_paying_everything_off():
    b, events = _timeline(own_funds=3_000_000, deposit=1_000_000)
    last = events[-1]
    assert last.source == "住宅ローン"
    # 建物の残金 ＋ つなぎ融資の元本
    prepaid = sum(x.amount for x in b.drawdowns[1:])
    assert last.amount == (BUILDING - prepaid) + b.principal
