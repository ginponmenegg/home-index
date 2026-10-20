# -*- coding: utf-8 -*-
"""PROの手取り・教育費（src/household.py）。ネットワーク不要。

2026-10-20、世帯年収だけで返済負担を見ると、片働きと共働きの手取りの
差も、子どもの学費も見えないので、PROでは年収を本人と配偶者に分けて
受け、手取りで資金の点をつけることにした。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import household as hh  # noqa: E402

MAN = 10_000


# ---- 手取り ---------------------------------------------------------------

def test_take_home_by_hand_for_6m_single_earner():
    """年収600万円・配偶者なし。式を手で追った値と一致する。

    社会保険料 600万×(9.90%/2＋18.3%/2＋0.23%/2＋0.5%) ＝ 882,900円
    給与所得   600万−(600万×20%＋44万) ＝ 436万円 → 基礎控除104万円
    所得税     (436万−882,900−104万)＝2,437,000 ×10%−97,500 ＝146,200 ×1.021
    住民税     (436万−882,900−43万)＝3,047,000 ×10% ＋5,000
    """
    t = hh.take_home(6_000_000)
    assert t.social == 882_900
    assert t.income_tax == int(146_200 * 1.021)
    assert t.resident_tax == 304_700 + 5_000
    assert t.net == 6_000_000 - 882_900 - int(146_200 * 1.021) - 309_700


def test_same_household_income_different_take_home():
    """世帯年収1,000万円でも、共働きのほうが手取りは多い。"""
    one = hh.household_take_home(10_000_000, 0)
    two = hh.household_take_home(6_000_000, 4_000_000)
    assert one.income == two.income == 10_000_000
    assert two.net - one.net > 400_000        # 年40万円以上の差
    # 差は世帯年収が下がると小さくなる（配偶者控除が効く）
    d600 = (hh.household_take_home(3_000_000, 3_000_000).net
            - hh.household_take_home(6_000_000, 0).net)
    assert 0 < d600 < two.net - one.net


def test_spouse_deduction_is_used_only_when_the_spouse_earns_little():
    assert hh.household_take_home(10_000_000, 0).earners[0].spouse_deduction == 380_000
    assert hh.household_take_home(6_000_000, 4_000_000).earners[0].spouse_deduction == 0
    # 本人の合計所得が1,000万円を超えると使えない
    assert hh.household_take_home(13_000_000, 0).earners[0].spouse_deduction == 0
    # 配偶者がいない（空欄）なら判定しない
    assert hh.household_take_home(10_000_000, None).earners[0].spouse_deduction == 0


def test_a_dependent_spouse_pays_no_social_insurance():
    h = hh.household_take_home(6_000_000, 1_200_000)
    assert h.earners[1].social == 0
    h = hh.household_take_home(6_000_000, 1_300_000)
    assert h.earners[1].social > 0


def test_care_insurance_only_from_40():
    assert hh.take_home(6_000_000, over40=True).social > hh.take_home(6_000_000).social


# ---- 教育費 ---------------------------------------------------------------

def test_ages_are_read_loosely():
    assert hh.parse_ages("4, 7") == [4, 7]
    assert hh.parse_ages("４歳と７歳") == [4, 7]
    assert hh.parse_ages("") is None
    assert hh.parse_ages("なし") is None


def test_education_runs_until_the_youngest_turns_22():
    p = hh.education_plan([4, 7], "public", "national")
    assert len(p.years) == 18
    assert p.peak.offset == 14 and p.peak.ages == [18, 21]
    assert p.total == sum(y.net for y in p.years)
    # 私立大学のほうが重い
    assert hh.education_plan([4, 7], "public", "private").total > p.total
    assert hh.education_plan([4, 7], "public", "none").total < p.total


def test_child_allowance_counts_the_third_child():
    # 3人目は月3万円。上の子が22歳以下なら数える
    assert hh.child_allowance([10, 7, 1]) == (10_000 + 10_000 + 30_000) * 12
    assert hh.child_allowance([1]) == 15_000 * 12
    # 18歳以上には払わないが、3人目の数え方には入る
    assert hh.child_allowance([20, 10, 5]) == (10_000 + 30_000) * 12


# ---- 採点 -----------------------------------------------------------------

def test_bands_on_take_home():
    assert hh.net_band(25.0)[1] == "最高"
    assert hh.net_band(25.1)[1] == "高"
    assert hh.net_band(30.0)[1] == "高"
    assert hh.net_band(35.0)[1] == "中"
    assert hh.net_band(35.1)[1] == "低"


def test_left_over_below_living_cost_lowers_one_band(monkeypatch):
    house = hh.household_take_home(6_000_000, 0)
    cfg = dict(hh._cfg())
    cfg["living"] = {"by_size": {"4": 400_000}}
    monkeypatch.setattr(hh, "_cfg", lambda: cfg)
    c = hh.check(house, 1_000_000, children_ages=[4, 7])
    assert c.household_size == 4
    assert c.lowered and c.raw < hh.net_band(c.net_ratio)[0]
    cfg["living"] = {"by_size": {"4": 100_000}}
    c = hh.check(house, 1_000_000, children_ages=[4, 7])
    assert not c.lowered and c.raw == hh.net_band(c.net_ratio)[0]


def test_children_without_ages_are_said_not_guessed():
    house = hh.household_take_home(6_000_000, 0)
    c = hh.check(house, 1_200_000, children=2)
    assert c.ages_missing and c.education is None


# ---- 画面 -----------------------------------------------------------------

def _client():
    os.environ["SHINDAN_MOCK"] = "1"
    import app
    return app.app.test_client()


BASE = dict(address="神奈川県平塚市田村5丁目12-3", price="4500", byear="2010",
            land="120", building="100", station="10", loan_years="35",
            down="300")


def test_pro_result_shows_take_home_when_income_is_split():
    h = _client().post("/pro/diagnose", data=dict(
        BASE, income_self="600", income_spouse="400",
        children="2", children_ages="4, 7")).get_data(as_text=True)
    assert "手取りで見た返済と教育費" in h
    assert "住宅ローン控除は入れていません" in h


def test_pro_result_without_split_stays_as_before():
    h = _client().post("/pro/diagnose", data=dict(BASE, income="1000")
                       ).get_data(as_text=True)
    assert "手取りで見た返済" not in h


def test_self_employed_is_not_given_a_take_home():
    h = _client().post("/pro/diagnose", data=dict(
        BASE, income_self="800", employment="jieigyo")).get_data(as_text=True)
    assert "手取りで見た返済" not in h


def test_free_income_lands_in_the_self_field():
    import app
    v = app._pro_form_values({"income": "700"})
    assert v["income_self"] == "700"
    assert app._hh_income({"income_self": "600", "income_spouse": "400"}) == 10_000_000
    assert app._hh_income({"income": "800"}) == 8_000_000


def test_every_pro_form_asks_for_the_split():
    c = _client()
    for path in ("/pro/diagnose", "/pro/mansion", "/pro/land", "/pro/finance"):
        g = c.get(path).get_data(as_text=True)
        assert 'name="income_self"' in g and 'name="children_ages"' in g, path
        assert "HH_FIELDS" not in g, path


def test_family_inputs_are_never_saved():
    from src.saved import NEVER_SAVE
    for k in ("income_self", "income_spouse", "children_ages",
              "over40_self", "over40_spouse"):
        assert k in NEVER_SAVE


def test_living_cost_comes_from_the_survey_table():
    """家計調査2025 第3-1表（勤労者世帯・世帯主60歳未満）の消費支出−住居−教育。"""
    assert hh.living_cost_monthly(4) == 370_755 - 17_457 - 35_739
    assert hh.living_cost_monthly(2) == 307_100 - 28_420 - 1_838
    assert hh.living_cost_monthly(8) == hh.living_cost_monthly(6)   # 6人以上
    assert hh.living_cost_monthly(1) is None                         # 表に無い


def test_land_pro_shows_take_home():
    import app
    v = app._land_pro_defaults()
    v.update(address="神奈川県平塚市田村5丁目12-3", price="2500", area="130",
             budget="2200", income_self="700", income_spouse="0",
             children_ages="1")
    h = _client().post("/pro/land", data=v).get_data(as_text=True)
    assert "手取りで見た返済と教育費" in h
    assert "世帯の手取り" in h and "年540" in h
