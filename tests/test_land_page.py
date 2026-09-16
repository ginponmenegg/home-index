# -*- coding: utf-8 -*-
"""土地診断の画面（入力・結果）。ネットワーク不要（SHINDAN_MOCK）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SHINDAN_MOCK", "1")

import app as webapp  # noqa: E402

FORM = dict(address="千葉県船橋市前原西6-1", price="1800", area="120",
            budget="2500", household="4", road_width="4",
            road_type="公道", frontage="8", station="12", coverage="60",
            far="200", city="12204", district="前原西", income="800",
            down="500", loan_years="35")


@pytest.fixture
def client():
    return webapp.app.test_client()


def _post(client, **kw):
    f = dict(FORM)
    f.update(kw)
    return client.post("/land_diagnose", data=f).get_data(as_text=True)


def test_the_form_opens(client):
    h = client.get("/land").get_data(as_text=True)
    assert "/land_diagnose" in h
    assert "前面道路の幅員" in h
    assert "建物の予算" in h
    assert "世帯人数" in h
    # 間口と接道の長さは整形地では同じ数字。一度しか聞かない。
    assert "接道の長さ" not in h
    assert h.count('name="frontage"') == 1
    # 幅員は「いちばん広いほう」を入れてもらわないと、使える容積率を
    # 小さく出してしまう。フォームでそう言う。
    assert "いちばん広いほう" in h


def test_the_form_says_the_price_is_not_scored(client):
    h = client.get("/land").get_data(as_text=True)
    assert "土地の価格は点数に入れていません" in h


def test_a_diagnosis_comes_back_with_the_buildable_size(client):
    h = _post(client)
    assert "延床の上限 192㎡" in h          # 120㎡ × 160%（道路4m×0.4）
    assert "58.1坪" in h
    assert "建築面積の上限（1階）" in h


def test_the_road_limit_is_explained_where_it_bites(client):
    h = _post(client)
    assert "前面道路で容積率が頭打ちになっています" in h
    assert "建築基準法52条2項" in h
    # 広い道路なら出さない
    assert "前面道路で容積率が頭打ち" not in _post(client, road_width="8")


def test_the_six_categories_are_shown(client):
    h = _post(client)
    for name in ("建てられる家", "接道", "リスク", "立地", "資産性", "資金"):
        assert name in h
    assert "価格評価" not in h              # 土地は価格を採点しない


def test_an_unbuildable_plot_is_stopped_at_the_top_of_the_page(client):
    h = _post(client, frontage="1.5")
    assert "家を建てられない可能性があります" in h
    assert "住宅ローンがまず通らない" in h
    assert "43条2項" in h                   # 逃げ道も必ず出す


def test_a_narrow_road_alone_does_not_trigger_the_stop(client):
    # 42条2項の道路ならセットバックして建て替えられる。
    h = _post(client, road_width="3")
    assert "家を建てられない可能性があります" not in h
    assert "セットバック" in h


def test_the_total_is_land_plus_building(client):
    h = _post(client)
    assert "総額 4,300万円" in h
    # 注文住宅で最も多い誤解。必ず添える。
    assert "外構・地盤改良・付帯工事・諸費用が" in h


def test_raw_exception_text_never_reaches_the_page(client):
    """接続エラーの文字列をそのまま画面に出さない。

    パイプラインの warnings には例外を文字列にしたものが混ざる。
    読む人には意味が無いうえ、APIのURLや内部の作りが漏れる。
    """
    h = _post(client)
    for leak in ("SSLError", "HTTPSConnectionPool", "Traceback",
                 "ex-api/external", "Max retries"):
        assert leak not in h, leak


def test_severity_is_shown_in_japanese(client):
    h = _post(client)
    assert "（medium）" not in h
    assert "（high）" not in h


def test_the_strengths_and_weaknesses_are_labelled(client):
    """色分けだけでは、どちらが強みか分からない。"""
    h = _post(client)
    assert ("◎ 強み" in h) or ("△ 弱み" in h)


def test_the_edit_button_brings_the_answers_back(client):
    h = client.post("/land/edit", data=FORM).get_data(as_text=True)
    assert 'value="1800"' in h and 'value="120"' in h
    assert 'value="公道" selected' in h or '"公道" selected' in h


def test_the_land_page_is_reachable_from_everywhere(client):
    for path in ("/", "/buy", "/mansion"):
        assert 'href="/land"' in client.get(path).get_data(as_text=True), path
    assert b"<loc>http://localhost/land</loc>" in \
        client.get("/sitemap.xml").data
