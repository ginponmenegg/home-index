# -*- coding: utf-8 -*-
"""構造（木造・鉄骨・RC）の扱い。ネットワーク不要。

耐用年数は国税庁「主な減価償却資産の耐用年数表」<建物> の
「店舗用・住宅用のもの」に載っている値。こちらで決めた数字ではないので、
テストで固定して、うっかり書き換えたら気づけるようにしておく。
https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/pdf/2100_01.pdf
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import structure as st
from src.models import SubjectProperty
from src.scoring import score_building
from src.comparable import _same_structure
from src.extract import parse_listing_text


# ---- 耐用年数（公表値の固定） ---------------------------------------------

def test_life_years_match_the_published_table():
    """国税庁の表のとおりであること。"""
    assert st.LIFE["wood"] == 22          # 木造・合成樹脂造
    assert st.LIFE["heavy_steel"] == 34   # 金属造 骨格材4mm超
    assert st.LIFE["rc"] == 47            # RC・SRC（住宅用）
    assert st.LIFE["other"] == 38         # れんが造・石造・ブロック造
    # 軽量鉄骨だけは表に無い。厚みで19年と27年に分かれるため中間を採る。
    assert st.LIFE["light_steel"] == 23
    assert st.BASE_LIFE == st.LIFE["wood"], "基準は木造"


# ---- 表記ゆれ -------------------------------------------------------------

def test_normalize_common_notations():
    f = st.normalize
    assert f("木造") == f("木造軸組") == f("2×4") == f("ツーバイフォー") == "wood"
    assert f("軽量鉄骨造") == f("軽鉄") == "light_steel"
    assert f("重量鉄骨造") == "heavy_steel"
    assert f("ＲＣ") == f("RC造") == f("鉄筋コンクリート造") == "rc"
    assert f("SRC") == f("鉄骨鉄筋コンクリート造") == "rc", "SRCをRCより先に見る"
    assert f("ブロック造") == "other"
    assert f(None) is None and f("") is None and f("よくわからない") is None


def test_bare_steel_is_read_as_the_shorter_lived_one():
    """「鉄骨造」とだけあるときは軽量として読む。

    軽量(23年)か重量(34年)かは判別できない。重量と読むと根拠なく建物の
    点数を上げてしまうので、低いほうに寄せる。
    """
    assert st.normalize("鉄骨造") == "light_steel"
    assert st.normalize("S造") == "light_steel"
    assert st.normalize("重量鉄骨造") == "heavy_steel", "明記があれば重量"


# ---- 実効築年数 -----------------------------------------------------------

def test_effective_age_is_relative_to_wood():
    assert st.effective_age(30, "wood") == 30.0
    assert round(st.effective_age(30, "rc"), 1) == 14.0
    assert round(st.effective_age(30, "heavy_steel"), 1) == 19.4
    assert st.effective_age(None, "rc") is None


def test_unknown_structure_is_treated_as_wood():
    """構造が不明でも点数は動かない。戸建の大半が木造のため。"""
    assert st.effective_age(30, None) == 30.0
    assert st.life_years(None) == st.LIFE["wood"]


# ---- 採点 -----------------------------------------------------------------

def _house(age, structure=None, year=2026):
    return SubjectProperty(property_type="chuko_kodate", price=1, address="x",
                           build_year=year - age, structure=structure)


def test_structure_changes_the_building_score():
    """同じ築年数でも、構造が長持ちするほど建物の評価が高い。"""
    age = 35
    wood = score_building(_house(age, "wood"), 2026).points
    heavy = score_building(_house(age, "heavy_steel"), 2026).points
    rc = score_building(_house(age, "rc"), 2026).points
    assert wood < heavy <= rc, (wood, heavy, rc)


def test_unknown_structure_scores_the_same_as_wood():
    """構造を選ばなくても、これまでと同じ点数のままであること。

    大半が木造なので、ここが動くと過去の診断と比べられなくなる。
    """
    for age in (5, 15, 25, 35, 50):
        a = score_building(_house(age), 2026)
        b = score_building(_house(age, "wood"), 2026)
        assert a.points == b.points, age


def test_reason_names_the_structure_and_the_conversion():
    c = score_building(_house(30, "rc"), 2026)
    assert "RC" in c.reason and "築30年" in c.reason
    assert "木造換算" in c.reason, "換算したことを隠さない"
    # 構造が不明なら、木造として計算したと明記する
    assert "構造未確認" in score_building(_house(30), 2026).reason


def test_knowing_the_structure_raises_sufficiency():
    """分かっている項目が増えたぶん、情報充足度は上がる。"""
    known = score_building(_house(20, "rc"), 2026).sufficiency
    unknown = score_building(_house(20), 2026).sufficiency
    assert known > unknown


def test_new_build_gets_only_a_small_structure_bonus():
    """新築はどの構造もまだ古びていないので、差は小さくとどめる。"""
    wood = score_building(
        SubjectProperty(property_type="shinchiku_kodate", price=1, address="x",
                        build_year=2026, structure="wood"), 2026)
    rc = score_building(
        SubjectProperty(property_type="shinchiku_kodate", price=1, address="x",
                        build_year=2026, structure="rc"), 2026)
    assert 0 < rc.raw - wood.raw <= 0.07


# ---- 類似物件の突き合わせ -------------------------------------------------

def test_comparable_matching_bridges_the_two_notations():
    """フォームの区分値と、取引データの文字列を突き合わせられること。"""
    assert _same_structure("wood", "木造") == 1.0
    assert _same_structure("rc", "ＲＣ") == 1.0
    assert _same_structure("wood", "ＲＣ") == 0.0
    # 片方でも不明なら加点しない
    assert _same_structure("wood", None) == 0.0
    assert _same_structure(None, "木造") == 0.0


# ---- 貼り付けからの読み取り -----------------------------------------------

def test_structure_is_read_from_pasted_text():
    def s(t):
        return parse_listing_text(t)["structure"]
    assert s("中古一戸建て 構造：木造 価格3,500万円") == "wood"
    assert s("構造・階建 軽量鉄骨造2階建") == "light_steel"
    assert s("鉄骨鉄筋コンクリート造 5階建") == "rc"
    assert s("価格3000万円 土地120㎡") is None, "書いていなければ不明のまま"
