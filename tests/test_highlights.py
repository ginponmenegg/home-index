# -*- coding: utf-8 -*-
"""強み・弱みの選び方。ネットワーク不要。

強み・弱みは以前、カテゴリの点数がしきい値を超えたら、そのカテゴリの
説明文（reason）を丸ごと並べていた。reason は有利な事実と不利な事実を
ひとつなぎにした文なので、点数の伸びなかったカテゴリでは
「新耐震・最上階（6/6階）・南向き」が弱みの欄に並んでしまっていた。

いまは各スコアラーが、点に効いた事実だけを plus / minus に符号つきで
持ち、highlights() がそこから組み立てる。reason は従来どおり
カテゴリの内訳として別に表示している。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import MansionSubject
from src.scoring import CategoryScore, highlights, HIGHLIGHT_MAX
from src.mansion_scoring import build_mansion_diagnosis


def _mansion(**kw):
    base = dict(address="神奈川県小田原市城山1-2-3", price=34_800_000,
                exclusive_area_m2=70.0)
    base.update(kw)
    return MansionSubject(**base)


def _diag(subj, **kw):
    return build_mansion_diagnosis(subj, None, None, current_year=2026, **kw)


# ---- 報告のあった不具合 ---------------------------------------------------

def test_favourable_facts_never_land_in_weaknesses():
    """新耐震・最上階・南向きが弱みに出ない。

    駅から遠く人口も減る物件では資産性の点が伸びないが、だからといって
    その物件の有利な事実まで弱みとして読ませてはいけない。
    """
    d = _diag(_mansion(build_year=2000, station_walk_min=25,
                       floor=6, total_floors=6, direction="南"),
              pop_change_pct=-15)
    joined = " ".join(d.weaknesses)
    for good in ("新耐震", "最上階", "南向き"):
        assert good not in joined, f"「{good}」が弱みに出ている：{d.weaknesses}"
    # 有利な事実は強みのほうに出る
    assert any("最上階" in x for x in d.strengths)
    assert any("南向き" in x for x in d.strengths)
    # 点を落としている事実は弱みに出る
    assert any("駅徒歩25分" in x for x in d.weaknesses)
    assert any("人口" in x for x in d.weaknesses)


def test_unfavourable_facts_never_land_in_strengths():
    d = _diag(_mansion(build_year=1978, station_walk_min=22,
                       floor=1, total_floors=5, direction="北"))
    joined = " ".join(d.strengths)
    for bad in ("旧耐震", "1階", "北向き"):
        assert bad not in joined, f"「{bad}」が強みに出ている：{d.strengths}"
    assert any("旧耐震" in x for x in d.weaknesses)


def test_new_quake_standard_alone_is_not_a_strength():
    """新耐震は最低条件。点を押し上げているのは築浅であることのほう。"""
    old = _diag(_mansion(build_year=1990, station_walk_min=8))   # 築36年
    assert not any("新耐震" in x for x in old.strengths)
    new = _diag(_mansion(build_year=2016, station_walk_min=8))   # 築10年
    assert any("新耐震" in x for x in new.strengths)


# ---- highlights() のふるまい ----------------------------------------------

def _cat(name, weight, plus=(), minus=()):
    return CategoryScore(name, weight, 0.5, 0.0, 0.5, "", [],
                         plus=list(plus), minus=list(minus))


def test_same_fact_is_shown_once_from_the_heavier_category():
    """駅距離は立地と資産性の両方が見る。読む側には同じ一文なので1回だけ。"""
    cats = [_cat("立地", 20, plus=["駅徒歩5分"]),
            _cat("資産性", 10, plus=["駅徒歩5分"])]
    strengths, _ = highlights(cats)
    assert strengths == ["立地: 駅徒歩5分"]


def test_highlights_are_capped():
    cats = [_cat("立地", 20, minus=[f"項目{i}" for i in range(20)])]
    _, weaknesses = highlights(cats)
    assert len(weaknesses) == HIGHLIGHT_MAX


def test_categories_without_notable_facts_produce_nothing():
    """出す事実が無ければ空。無理に埋めない。"""
    assert highlights([_cat("価格", 20)]) == ([], [])
