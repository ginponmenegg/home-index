# -*- coding: utf-8 -*-
"""「調べた結果、該当しなかった」と「調べられていない」を混ぜない。

外部のレビューで指摘された。結果画面の防災カードが緑で
「指定区域に該当なし」と出しながら、下の採点では
「この地域では津波浸水想定を確認していません」と書いていた。
読む人には、どちらなのか分からない。

採点側（score_risk）は先に直してあったが、表示カードが取り残されていた。
判定が _render_result の中に埋まっていて、単体で試せなかったため。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
from src.enrichment import HazardResult  # noqa: E402
from src.scoring import score_risk  # noqa: E402


def _hz(**kw):
    restricted = kw.pop("restricted", [])
    h = HazardResult(checked=kw.pop("checked", True))
    for k, v in kw.items():
        setattr(h, k, v)
    h.restricted = list(restricted)
    return h


def _labels(items):
    return [f"{a}:{b}" for a, b, _c in items]


# ---- 表示カード ----
def test_a_blocked_layer_is_never_folded_into_clear():
    items = webapp._hazard_items(_hz(restricted=["津波浸水想定"]))
    text = " ".join(_labels(items))
    assert "指定区域に該当なし" not in text, "見ていない層があるのに断定している"
    assert "津波浸水想定" in text
    assert "未取得" in text


def test_it_still_says_what_it_did_look_at():
    """全部調べて該当なしなら、そう言ってよい。"""
    items = webapp._hazard_items(_hz())
    assert _labels(items) == ["防災:指定区域に該当なし"]


def test_a_partial_check_says_so_in_words():
    """見た層は該当なし、見ていない層がある、という状態を言い分ける。"""
    items = webapp._hazard_items(_hz(restricted=["土砂災害警戒区域"]))
    text = " ".join(_labels(items))
    assert "見た範囲では該当なし" in text
    assert "土砂災害警戒区域:未取得（提供元の利用条件）" in text


def test_nothing_checked_at_all_is_its_own_state():
    items = webapp._hazard_items(_hz(checked=False))
    assert _labels(items) == ["防災:未取得（要確認）"]


def test_a_real_hit_is_shown_with_the_blocked_layer_beside_it():
    items = webapp._hazard_items(
        _hz(sediment="特別警戒区域", restricted=["津波浸水想定"]))
    text = " ".join(_labels(items))
    assert "土砂災害:特別警戒区域" in text
    assert "津波浸水想定:未取得" in text
    assert "該当なし" not in text


def test_the_blocked_layers_are_greyed_not_green():
    """未取得を緑にしない。点数の高さと、確かめられたかは別のこと。"""
    items = webapp._hazard_items(_hz(restricted=["津波浸水想定"]))
    kinds = {a: c for a, _b, c in items}
    assert kinds["津波浸水想定"] == "muted"


# ---- 画面と採点が食い違わない ----
def test_the_card_and_the_score_tell_the_same_story():
    """同じハザードから、カードと採点が別々のことを言わないこと。"""
    hz = _hz(restricted=["津波浸水想定"])
    card = " ".join(_labels(webapp._hazard_items(hz)))
    score = score_risk(None, None, hz)

    said_clear_on_card = "指定区域に該当なし" in card
    said_clear_in_score = "該当なし" in score.reason and "確認していません" not in score.reason
    assert said_clear_on_card == said_clear_in_score

    # どちらも「見ていない層がある」と言うこと
    assert "未取得" in card
    assert "確認していません" in score.reason


def test_both_agree_when_everything_was_checked():
    hz = _hz()
    card = " ".join(_labels(webapp._hazard_items(hz)))
    score = score_risk(None, None, hz)
    assert "指定区域に該当なし" in card
    assert "該当なし" in score.reason


# ---- 無いと断定しない ----
def test_shops_are_not_declared_absent():
    """OpenStreetMap は有志が作る地図で、載っていないことは存在しないこと

    ではない。「付近に見当たらず」は、実在しないと読める。
    """
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
    assert "商業施設は付近に見当たらず" not in src
    assert "OpenStreetMap の範囲では見つかりませんでした" in src


# ---- 説明が実装と合っている ----
def test_the_about_page_does_not_claim_a_rule_it_does_not_follow():
    """「取得できなかった項目は採点に反映しません」と書いていたが、

    実際には災害リスクは点数の上限を下げ、年収は中立の点を置いている。
    どちらも「反映しない」ではない。
    """
    c = webapp.app.test_client()
    h = c.get("/about").get_data(as_text=True)
    assert "採点には反映しません" not in h
    assert "点数の上限を" in h
    assert "情報充足度を下げます" in h


def test_the_copy_guide_matches_the_forms():
    """フォームは住所と価格で診断できるのに、5項目必要だと書いていた。"""
    c = webapp.app.test_client()
    h = c.get("/copy-guide").get_data(as_text=True)
    assert "徒歩分の5つだけ" not in h
    assert "所在地と価格だけで診断できます" in h
    # 土地から開いた人の戻り先も用意する
    assert "土地の診断にもどる" in h


def test_the_land_form_lead_matches_its_required_fields():
    """冒頭で4つと言いながら、建物の予算に必須の表示が無かった。"""
    c = webapp.app.test_client()
    h = c.get("/land").get_data(as_text=True)
    assert "4つで診断できます" not in h
    assert "3つで診断できます" in h
