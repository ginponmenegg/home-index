# -*- coding: utf-8 -*-
"""PROの土地診断で聞く「現地と書類」の採点。ネットワーク不要。

この層でいちばん大事なのは、**調べていない人を悪い土地として採点しない**こと。
点を動かすのは土地の事実だけで、調べたかどうかは充足度の話。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.land_pro_scoring import (ADJUST, CHOICES, MAX_DOWN, MAX_UP,
                                  answered_ratio, cost_needs, detail_from,
                                  score_land_site)
from src.scoring import CategoryScore

GOOD = dict(water="done20", sewer="connected", gas="city_done", ground="ok",
            retaining="none", level="flat", oldhouse="none", leftovers="none")
BAD = dict(water="none", sewer="septic", gas="lpg", ground="needed",
           retaining="uncertified", level="lower", oldhouse="buyer",
           leftovers="exists")


def _base(hazard_checked=True, raw=1.0, suff=0.9):
    return CategoryScore("リスク", 25, raw, round(25 * raw, 1), suff,
                         "ハザード",
                         ["reinfolib:XKT(hazard)"] if hazard_checked else [])


def _score(answers, **kw):
    return score_land_site(_base(**kw), detail_from(answers))


# ---- 調べていないことを減点にしない ----
def test_leaving_everything_unanswered_never_moves_the_score():
    cat, confirm = _score({})
    assert cat.points == _base().points
    assert len(confirm) == len(CHOICES)      # 代わりに確認先を全部出す


def test_a_survey_not_yet_done_is_not_a_penalty():
    """地盤調査が未実施なのは、土地の性質ではなく段取りの話。"""
    assert ADJUST[("ground", "not_done")] == 0.0
    cat, confirm = _score(dict(GOOD, ground="not_done"))
    done, _ = _score(GOOD)
    assert cat.points >= done.points - 2.5   # ok の加点ぶんだけ下がる
    assert any("地盤調査をするまで" in c for c in confirm)


def test_facts_about_the_land_do_move_the_score():
    good, _ = _score(GOOD)
    bad, _ = _score(BAD)
    assert bad.points < good.points


# ---- 積み上がりすぎない ----
def test_the_penalties_are_bounded():
    """8項目の減点が積み上がって、リスクが1.5点まで落ちたことがある。

    未引込・浄化槽・プロパン・改良要・無検査擁壁・低い土地・買主解体・
    残置物の土地は確かに悪いが、壊滅ではない。数百万円の出費とひと手間で
    あって、再建築不可のような前提の崩壊ではない。
    """
    assert sum(ADJUST[("water", "none")] for _ in range(1)) < 0
    bad, _ = _score(BAD)
    assert bad.raw == round(_base().raw + MAX_DOWN, 3)
    assert bad.points > 15                   # 25 * 0.65


def test_the_bonuses_are_bounded_more_tightly_than_the_penalties():
    # 引込済み・平坦はこの国の土地では普通。普通に大きな加点はしない。
    assert 0 < MAX_UP < abs(MAX_DOWN)


# ---- ハザードの天井を押し上げない ----
def test_good_plumbing_cannot_undo_an_unchecked_hazard():
    """score_risk はハザード未確認のとき raw を 0.7 で頭打ちにしている。

    水道やガスの答えで、その上限を押し上げてはいけない。別の話なので。
    """
    cat, _ = _score(GOOD, hazard_checked=False, raw=0.7, suff=0.3)
    assert cat.raw <= 0.7


def test_the_ceiling_lifts_once_the_hazards_were_checked():
    cat, _ = _score(GOOD, hazard_checked=True, raw=0.9)
    assert cat.raw > 0.9


# ---- 充足度は分母が広がる ----
def test_answering_nothing_lowers_sufficiency_below_the_free_diagnosis():
    """PROは聞く項目が増える。答えていなければ、無料より下がるのが正しい。"""
    cat, _ = _score({})
    assert cat.sufficiency < _base().sufficiency


def test_answering_everything_raises_it():
    assert _score(GOOD)[0].sufficiency > _score({})[0].sufficiency


def test_an_unclear_answer_does_not_count_as_answered():
    # 「擁壁あり・検査済証があるか分からない」は答えたことにしない
    assert answered_ratio(detail_from(dict(GOOD, retaining="unclear"))) < 1.0


# ---- 言葉 ----
def test_the_uncertified_retaining_wall_is_spelled_out():
    cat, _ = _score(BAD)
    assert any("検査済証がありません" in m for m in cat.minus)


def test_the_thirteen_millimetre_pipe_is_explained_not_just_flagged():
    cat, _ = _score(dict(GOOD, water="done13"))
    assert any("増径" in m for m in cat.minus)


def test_an_unknown_value_falls_back_to_unconfirmed():
    d = detail_from({"water": "でたらめ"})
    assert d.water == "unknown"


# ---- 費用の要否 ----
def test_the_costs_are_derived_from_the_answers():
    assert cost_needs(detail_from(BAD)) == {
        "地盤改良": True, "古家の解体": True, "上下水道・ガスの引き込み": True}
    assert cost_needs(detail_from(GOOD)) == {
        "地盤改良": False, "古家の解体": False, "上下水道・ガスの引き込み": False}


def test_unanswered_costs_stay_unknown_rather_than_assumed_absent():
    assert cost_needs(detail_from({})) == {
        "地盤改良": None, "古家の解体": None, "上下水道・ガスの引き込み": None}


def test_a_seller_paid_demolition_is_not_the_buyers_cost():
    assert cost_needs(detail_from(dict(GOOD, oldhouse="seller")))["古家の解体"] \
        is False


# ============================================================
# B群 ── 建てられる形を決める規制
# ============================================================
from src.land_pro_scoring import (RULE_CHOICES, RULE_MAX_DOWN, RULE_MAX_UP,
                                  corner_designated, rule_detail_from,
                                  score_land_rules)


def _rules(**kw):
    return rule_detail_from(kw)


def _base_build(raw=1.0, suff=0.8):
    return CategoryScore("建てられる家", 25, raw, round(25 * raw, 1), suff,
                         "延床の上限 240㎡", [])


def test_a_corner_without_the_designation_is_not_relaxed():
    """指定が確認できたときだけ True。ここを緩めると、建てられない家を
    建てられると言うことになる。"""
    assert corner_designated(_rules(corner="designated")) is True
    assert corner_designated(_rules(corner="no")) is False
    assert corner_designated(_rules(corner="corner_only")) is None
    assert corner_designated(_rules()) is None


def test_the_corner_is_not_scored_as_a_delta():
    """角地は建ぺい率そのものを変える。点の足し引きにはしない。"""
    from src.land_pro_scoring import RULE_ADJUST
    assert not [k for k in RULE_ADJUST if k[0] == "corner"]


def test_a_district_plan_costs_points_and_says_why():
    cat, _ = score_land_rules(_base_build(), _rules(district_plan="yes"))
    assert cat.points < _base_build().points
    assert any("外壁の後退" in m for m in cat.minus)


def test_the_strictest_height_district_costs_the_most():
    def pts(v):
        return score_land_rules(_base_build(raw=0.8),
                                _rules(height_district=v))[0].points
    assert pts("h1") < pts("h2") < pts("h3") < pts("none")


def test_the_fire_zone_mentions_the_relaxation_it_unlocks():
    cat, _ = score_land_rules(_base_build(), _rules(fire_zone="fire"))
    assert any("耐火建築物にすれば" in m for m in cat.minus)


def test_the_rule_adjustments_are_bounded():
    worst = _rules(fire_zone="fire", height_district="h1",
                   district_plan="yes", scenic="yes")
    cat, _ = score_land_rules(_base_build(raw=0.9), worst)
    assert cat.raw == round(0.9 + RULE_MAX_DOWN, 3)
    assert 0 < RULE_MAX_UP < abs(RULE_MAX_DOWN)


def test_unanswered_rules_come_back_as_things_to_check():
    cat, confirm = score_land_rules(_base_build(), _rules())
    assert cat.points == _base_build().points      # 減点しない
    assert len(confirm) == len(RULE_CHOICES)


def test_a_corner_with_no_designation_still_asks_about_it():
    _, confirm = score_land_rules(_base_build(), _rules(corner="corner_only"))
    assert any("特定行政庁が指定した角地" in c for c in confirm)


# ============================================================
# C群 ── 買えるかどうか・いつ買えるか
# ============================================================
from src.land_pro_scoring import (LEGAL_CHOICES, LEGAL_MAX_DOWN, LEGAL_MAX_UP,
                                  farmland_procedure, heritage_notice,
                                  legal_detail_from, score_land_access,
                                  score_land_asset, score_land_heritage)


def _legal(**kw):
    return legal_detail_from(kw)


def _base_named(name, weight, raw=1.0, suff=0.8):
    return CategoryScore(name, weight, raw, round(weight * raw, 1), suff,
                         name, [])


# ---- 農地法（条文で確認済み）----
def test_farmland_in_an_urban_zone_only_needs_a_notification():
    """農地法4条1項7号・5条1項6号。市街化区域内は農業委員会への届出。"""
    got = farmland_procedure(_legal(chimoku="nochi"), "市街化区域")
    assert "届け出れば" in got
    assert "許可は要りません" in got


def test_farmland_outside_the_urban_zone_needs_permission():
    got = farmland_procedure(_legal(chimoku="nochi"), "市街化調整区域")
    assert "許可" in got
    assert "届け出れば" not in got
    # 許可が下りない土地があることも言う。ここを黙ると、買ってから困る。
    assert "許可が下りない土地" in got


def test_an_unknown_zone_says_both_and_asks():
    got = farmland_procedure(_legal(chimoku="nochi"), None)
    assert "届出" in got and "許可" in got
    assert "確認してください" in got


def test_nothing_is_said_when_the_land_is_not_farmland():
    assert farmland_procedure(_legal(chimoku="takuchi"), "市街化区域") is None
    assert farmland_procedure(_legal(), "市街化区域") is None


# ---- 文化財保護法93条 ----
def test_a_heritage_site_gets_the_sixty_day_lead_time():
    got = heritage_notice(_legal(maizo="yes"))
    assert "60日前" in got
    assert "文化財保護法93条" in got
    assert heritage_notice(_legal(maizo="no")) is None
    assert heritage_notice(_legal()) is None


def test_a_heritage_site_also_costs_points_on_risk():
    plain, _ = score_land_heritage(_base_named("リスク", 25), _legal(maizo="no"))
    hit, _ = score_land_heritage(_base_named("リスク", 25), _legal(maizo="yes"))
    assert hit.points < plain.points


# ---- 私道のときだけ聞く ----
def test_the_private_road_questions_are_skipped_on_a_public_road():
    """公道なら掘削の承諾も持分も関係がない。

    関係のない項目を「未確認」として並べると、読む人は自分に要ることだと
    思ってしまう。
    """
    base = _base_named("接道", 15)
    cat, confirm = score_land_access(base, _legal(kussaku="none"), "公道")
    assert cat is base            # 触らない
    assert confirm == []


def test_a_missing_dig_consent_costs_the_most_on_a_private_road():
    base = _base_named("接道", 15)
    written, _ = score_land_access(base, _legal(kussaku="written"), "私道")
    verbal, _ = score_land_access(base, _legal(kussaku="verbal"), "私道")
    none, _ = score_land_access(base, _legal(kussaku="none"), "私道")
    assert none.points < verbal.points < written.points
    assert any("工事ができない" in m for m in none.minus)


def test_a_verbal_consent_says_why_paper_matters():
    cat, _ = score_land_access(_base_named("接道", 15),
                               _legal(kussaku="verbal"), "私道")
    assert any("所有者が変わると引き継がれない" in m for m in cat.minus)


def test_a_designated_road_counts_as_private_for_these_questions():
    cat, confirm = score_land_access(_base_named("接道", 15),
                                     _legal(), "位置指定")
    assert confirm                # 未確認なら確認先を出す


# ---- 資産性 ----
def test_an_unfixed_boundary_costs_asset_value():
    fixed, _ = score_land_asset(_base_named("資産性", 10),
                                _legal(boundary="fixed"))
    unfixed, _ = score_land_asset(_base_named("資産性", 10),
                                  _legal(boundary="unfixed"))
    assert unfixed.points < fixed.points
    assert any("売るときに確定測量" in m for m in unfixed.minus)


def test_an_encroachment_is_the_heaviest_asset_penalty():
    from src.land_pro_scoring import ASSET_ADJUST
    assert ASSET_ADJUST[("encroach", "exists")] < ASSET_ADJUST[("chimoku", "nochi")]


def test_the_legal_adjustments_are_bounded():
    worst = _legal(chimoku="sanrin", boundary="unfixed", encroach="exists")
    cat, _ = score_land_asset(_base_named("資産性", 10, raw=0.9), worst)
    assert cat.raw == round(0.9 + LEGAL_MAX_DOWN, 3)
    assert 0 < LEGAL_MAX_UP < abs(LEGAL_MAX_DOWN)


def test_unanswered_legal_questions_do_not_dock_the_score():
    base = _base_named("資産性", 10)
    cat, confirm = score_land_asset(base, _legal())
    assert cat.points == base.points
    assert len(confirm) == 3


def test_an_unknown_legal_value_falls_back_to_unconfirmed():
    assert legal_detail_from({"chimoku": "でたらめ"})["chimoku"] == "unknown"
