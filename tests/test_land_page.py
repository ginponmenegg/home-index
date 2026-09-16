# -*- coding: utf-8 -*-
"""土地診断の画面（入力・結果）。ネットワーク不要（SHINDAN_MOCK）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402

FORM = dict(address="千葉県船橋市前原西6-1", price="1800", area="120",
            budget="2500", household="4", road_width="4",
            road_type="公道", frontage="8", station="12", coverage="60",
            far="200", city="12204", district="前原西", income="800",
            down="500", loan_years="35")


@pytest.fixture(autouse=True)
def mock_mode():
    """毎回このテストの中で立てる。

    import 時に一度だけ立てると、先に走る test_accounts が後始末で
    SHINDAN_MOCK を消したあとに走ったとき、実際のAPIを叩きにいって
    成約0件になる。順番に依存しない形にしておく。
    """
    keep = os.environ.get("SHINDAN_MOCK")
    os.environ["SHINDAN_MOCK"] = "1"
    # 1日40回の上限はモジュール変数なので、テストをまたいで貯まる。
    # 土地のテストが増えたときに上限に当たり、診断の代わりにフォームが
    # 返ってきて、関係のないアサーションが落ちた。毎回まっさらにする。
    webapp._RATE.clear()
    yield
    if keep is None:
        os.environ.pop("SHINDAN_MOCK", None)
    else:
        os.environ["SHINDAN_MOCK"] = keep


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


def test_the_form_links_to_both_other_diagnoses(client):
    h = client.get("/land").get_data(as_text=True)
    assert "戸建の診断はこちら" in h
    assert "マンションの診断はこちら" in h
    assert "建売" not in h          # 土地から見たら戸建は戸建


# ---- 近隣の土地取引（モックの宅地(土地)で描く）----
MARKET = dict(FORM, address="神奈川県小田原市南町1-1", city="14206",
              district="南町")


def test_the_neighbourhood_prices_are_shown_in_tsubo_and_m2(client):
    """土地の打ち合わせは坪単価で進む。㎡単価だけだと読み替えが要る。"""
    h = client.post("/land_diagnose", data=MARKET).get_data(as_text=True)
    assert "坪単価" in h
    assert "㎡単価" in h
    assert "中央値" in h
    assert "この土地の坪単価" in h
    assert "点数には使っていません" in h


def test_the_neighbourhood_card_says_something_even_with_no_sales(client):
    """分布が出せないときに、見出しだけ出して中身が空になってはいけない。"""
    h = client.post("/land_diagnose",
                    data=dict(MARKET, district="存在しない町",
                              city="99999")).get_data(as_text=True)
    if "近隣の土地取引" in h:
        i = h.index("近隣の土地取引")
        assert len(h[i:i + 600].strip()) > 100


def test_the_neighbourhood_traits_are_not_claimed_about_this_plot(client):
    h = client.post("/land_diagnose", data=MARKET).get_data(as_text=True)
    assert "私道に接していた割合" in h
    assert "対象地がそうだという意味ではありません" in h


# ---- 建築条件付き ----
def test_a_conditional_plot_is_called_out_but_not_docked(client):
    """建築条件付きは点数に入れない。土地の欠点ではなく買い方の制約。

    ただし注文住宅を探している人には決定的なので、はっきり出す。
    """
    plain = client.post("/land_diagnose",
                        data=dict(FORM, condition="none")).get_data(as_text=True)
    tied = client.post("/land_diagnose",
                       data=dict(FORM, condition="attached")).get_data(as_text=True)

    def score(h):
        import re
        return int(re.search(r'<b style="color:[^"]+">(\d+)</b><small>点', h).group(1))

    assert score(plain) == score(tied)          # 点は動かさない
    assert "この土地は建築条件付きです" in tied
    assert "他の工務店では" in tied
    assert "点数に入れていません" in tied
    assert "この土地は建築条件付きです" not in plain


def test_an_unanswered_building_condition_is_asked_about(client):
    h = client.post("/land_diagnose",
                    data=dict(FORM, condition="unknown")).get_data(as_text=True)
    assert "建築条件付きかどうかを確認" in h


def test_the_form_asks_about_the_building_condition(client):
    h = client.get("/land").get_data(as_text=True)
    assert 'name="condition"' in h
    assert "建てる会社を選べません" in h


# ---- メニューの並び ----
def test_the_three_diagnoses_stay_together_and_in_order(client):
    """診断の3つは、上から戸建・マンション・土地で、続けて並ぶこと。

    番号で /plan と /mypage を差し込んでいたため、土地を足したときに
    戸建・マンションと土地の間に割り込んで並びが崩れた。
    """
    hrefs = [h for h, _ in webapp.MENU_ITEMS]
    i = hrefs.index("/buy")
    assert hrefs[i:i + 3] == ["/buy", "/mansion", "/land"]


def test_the_land_page_is_reachable_from_everywhere(client):
    for path in ("/", "/buy", "/mansion"):
        assert 'href="/land"' in client.get(path).get_data(as_text=True), path
    assert b"<loc>http://localhost/land</loc>" in \
        client.get("/sitemap.xml").data


# ---- 持ち出せること（印刷・画像）----
def test_the_things_to_check_can_be_printed_on_their_own(client):
    """役所や現地に持っていける一覧にする。"""
    h = _post(client)
    assert 'id="ask"' in h
    assert "この一覧だけ印刷する" in h
    assert "asklist" in h
    assert "@media print" in h


def test_the_result_can_be_saved_as_an_image(client):
    h = _post(client)
    assert "saveReport" in h
    assert "html2canvas.min.js" in h
    assert "🔗 共有する" in h


def test_the_footer_is_still_there(client):
    """フッターを '</div></body></html>' の一致で足していたため、

    画像保存のボタンを加えて末尾の形が変わった瞬間に、一致しなくなって
    黙って消えた。印で差し込む形にしてある。
    """
    h = _post(client)
    assert "出典：国土交通省" in h
    assert "LAND_FOOTER_PLACEHOLDER" not in h


def test_the_land_result_carries_its_own_disclaimer(client):
    h = _post(client)
    assert "免責" in h
    assert "斜線制限" in h          # 計算に入れていないものを名指しする
