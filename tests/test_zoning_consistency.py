# -*- coding: utf-8 -*-
"""本番で実住所を1件通して見つかった3つ。

見本（千葉県船橋市前原西6丁目）の結果に、こう出ていた。

    敷地 125㎡ ・ **商業地域** ・ 前面道路 4.0m
    指定建ぺい率 **60%** ／ 指定容積率 200%

商業地域の建ぺい率は法53条1項4号で**十分の八**と決まっていて、他の値は
選べない。つまりこの組み合わせは存在しない。地点を含むポリゴンに名前が
無く、名前だけ隣の面から借りていたため、**別の土地の用途地域**が出ていた。

あわせて、同じ画面の中で食い違っていたものが2つ。
・採点は「津波浸水想定を確認していません」、注記は「津波…に該当なし」
・「大型商業施設は付近になし」（OpenStreetMap に無い＝存在しない、ではない）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.land import COVERAGE_CHOICES, coverage_fits_district  # noqa: E402


# ---- 法53条1項の表 --------------------------------------------------------

@pytest.mark.parametrize("district,coverage,want", [
    # 四号：商業地域は十分の八しかない
    ("商業地域", 80, True),
    ("商業地域", 60, False),
    ("商業地域", 50, False),
    # 三号：近隣商業は6か8
    ("近隣商業地域", 60, True),
    ("近隣商業地域", 80, True),
    ("近隣商業地域", 50, False),
    # 一号：低層・中高層・田園住居・工業専用は3〜6
    ("第一種低層住居専用地域", 30, True),
    ("第一種低層住居専用地域", 60, True),
    ("第一種低層住居専用地域", 80, False),
    ("工業専用地域", 40, True),
    # 二号：住居系・準工業は5,6,8
    ("第一種住居地域", 80, True),
    ("第一種住居地域", 40, False),
    ("準工業地域", 60, True),
    # 五号：工業地域は5か6
    ("工業地域", 50, True),
    ("工業地域", 80, False),
])
def test_the_law_table_matches_article_53(district, coverage, want):
    assert coverage_fits_district(district, coverage) is want


def test_the_longer_name_is_matched_first():
    """「商業地域」は「近隣商業地域」の一部でもある。

    短いほうが先に当たると、近隣商業の60%を「商業地域なのに60%」として
    矛盾と判定してしまう。
    """
    assert coverage_fits_district("近隣商業地域", 60) is True
    names = list(COVERAGE_CHOICES)
    assert names.index("近隣商業地域") < names.index("商業地域")
    # 住居系も同じ。第一種住居地域と第一種中高層住居専用地域を取り違えない
    assert coverage_fits_district("第一種住居地域", 80) is True
    assert coverage_fits_district("第一種中高層住居専用地域", 80) is False


def test_what_it_does_not_know_is_not_a_contradiction():
    """**分からないものを False にしない。**名前を捨てる判断に使うので、
    確かに矛盾しているときだけ False を返す。
    """
    assert coverage_fits_district(None, 60) is None
    assert coverage_fits_district("商業地域", None) is None
    assert coverage_fits_district("用途地域の指定のない区域", 60) is None
    assert coverage_fits_district("知らない地域", 60) is None


# ---- 借りた名前を捨てる ---------------------------------------------------

def test_a_borrowed_name_that_contradicts_the_ratio_is_dropped():
    """用途地域は表示だけの話ではない。前面道路による容積率の係数
    （法52条2項）と高さ制限（法55条）に効く。
    """
    from src import enrichment
    e = enrichment.Enrichment()

    class _T:
        def result(self):
            return "商業地域", 60, 200

    # gather の中の該当部分と同じ判定
    e.use_district, e.coverage_ratio, e.floor_area_ratio = _T().result()
    assert enrichment.coverage_fits_district(
        e.use_district, e.coverage_ratio) is False


def test_the_far_multiplier_depends_on_the_name():
    """名前が違えば、前面道路4mで使える容積率が160%と240%に分かれる。

    「表示に使うだけ」では済まないことの確認。
    """
    from src.land import far_multiplier
    assert far_multiplier("商業地域") != far_multiplier("第一種住居地域")
    assert 4.0 * far_multiplier("第一種住居地域") * 100 == 160
    assert 4.0 * far_multiplier("商業地域") * 100 == 240


# ---- 同じ画面の中で食い違わない -------------------------------------------

def test_a_blocked_hazard_layer_is_not_called_clear():
    """採点が「確認していません」と言っているものを、注記が
    「該当なし」に含めない。
    """
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "enrichment.py"),
        encoding="utf-8").read()
    i = src.index("ハザード：洪水/土砂/津波/高潮の指定区域に該当なし")
    head = src[max(0, i - 700):i]
    assert "e.hazard.restricted" in head, "未取得の層を見ずに断定している"
    assert "見た範囲では指定区域に該当なし" in src


def test_shops_are_not_declared_absent_in_the_score_reason():
    """OpenStreetMap は有志が作る地図。載っていないことは、
    存在しないことではない。
    """
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "scoring.py"),
        encoding="utf-8").read()
    assert "は付近になし" not in src
    assert "OpenStreetMap では見つからず" in src
