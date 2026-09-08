# -*- coding: utf-8 -*-
"""無料とPROのちがいを、どこで見せるか。ネットワーク不要。

■なぜ要るか
料金表（/plan）はメニューにも、トップにも、結果画面にも無かった。着ける
のはマイページとPROのロック画面からだけ。つまり「まだ払っていない人」に
だけ見えないところに、払う理由が置かれていた。

■見本の資金計画（/sample/finance）
PROの3つのうち、入力したその場で価値が完結するのは資金計画だけ。中身を
見せずに売ると、買う理由が言葉の説明しか残らない。この計算は外部APIを
使わない純計算なので、/sample の診断と違って必ず出る。だから公開できる。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp

FREE_INPUT = {"address": "神奈川県小田原市城山1-2-3", "price": "3880",
              "land": "147", "building": "90", "byear": "2005",
              "station": "12", "ptype": "chuko_kodate", "income": "600",
              "down": "300", "loan_years": "35"}


def _client():
    return webapp.app.test_client()


# ---- 料金表への導線 -------------------------------------------------------

def test_the_price_page_is_in_the_menu():
    """メニューに無いページは、無いのと同じ。"""
    if not webapp.accounts_on():
        return                      # アカウント機能ごと隠している構成
    assert "/plan" in dict(webapp.MENU_ITEMS)


def test_the_free_result_says_what_pro_adds():
    """無料の結果に、無料とPROの差を並べる。"""
    h = _client().post("/diagnose", data=FREE_INPUT).data.decode("utf-8")
    assert "無料でここまで／PROでここまで" in h
    assert "/sample/finance" in h, "資金計画の見本へ行けること"


def test_the_comparison_is_not_shown_to_a_pro_member():
    """PRO診断の結果に「PROならこうです」を出さない。

    最初 finance_carry で出し分けようとして失敗した。あれはPRO診断の
    結果にも入っている。無料診断の目印は handover のほう。
    """
    h = _client().post("/pro/diagnose",
                       data=dict(FREE_INPUT, leak="ok")).data.decode("utf-8")
    assert "無料でここまで／PROでここまで" not in h


def test_the_pitch_stays_out_of_the_shared_image():
    """共有される画像は診断結果であって、広告ではない。

    #report をまるごと画像にしているので、印を付けないとPROの案内まで
    写り込む。html2canvas はこの属性の要素を飛ばす。
    """
    h = _client().post("/diagnose", data=FREE_INPUT).data.decode("utf-8")
    i = h.find("無料でここまで／PROでここまで")
    assert i > 0
    assert "data-html2canvas-ignore" in h[i - 200:i]


# ---- 資金計画の見本 -------------------------------------------------------

def test_the_finance_sample_opens_without_an_account():
    h = _client().get("/sample/finance")
    assert h.status_code == 200
    t = h.get_data(as_text=True)
    assert "詳細な資金計画（見本）" in t
    assert "実在の売り出し物件ではなく" in t


def test_the_finance_sample_shows_every_assumption():
    """数字だけを見せると、自分の物件の額として持ち帰られる。"""
    t = _client().get("/sample/finance").get_data(as_text=True)
    assert "この見本の前提" in t
    for word in ("固定資産税評価額", "頭金", "世帯年収", "繰上返済"):
        assert word in t, word


def test_the_finance_sample_actually_computes():
    """外部APIに依存しないので、いつ開いても中身が揃っているはず。"""
    t = _client().get("/sample/finance").get_data(as_text=True)
    for word in ("仲介手数料", "登録免許税", "不動産取得税",
                 "住宅ローン控除", "引渡日の精算"):
        assert word in t, word


def test_the_sample_pdf_opens_in_place():
    """スマホでダウンロードにすると別アプリへ移る。見せるのが目的なので開く。"""
    r = _client().get("/sample/finance.pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data[:4] == b"%PDF"
    assert r.headers["Content-Disposition"].startswith("inline;")


def test_the_sample_is_reachable_from_the_pages_that_sell_it():
    c = _client()
    for path in ("/pro", "/plan"):
        h = c.get(path).get_data(as_text=True)
        assert "/sample/finance" in h, path


def test_the_handover_date_is_written_in_japanese():
    """見本の中で 2026-11-01 と 2026年11月1日 が混ざらないこと。"""
    from src.finance import proration
    p = proration("2026-11-01", "0101", tax_yearly=130000)
    assert p.handover == "2026年11月1日"


def test_the_sample_is_linked_from_every_page_footer():
    """課金の有無で消えないこと。誰でも見られる公開ページなので。"""
    h = _client().get("/").get_data(as_text=True)
    assert "/sample/finance" in h


def test_the_free_result_can_go_straight_to_the_finance_plan():
    """無料 → PRO診断 → 資金計画 の一本道だと、いちばん見せたいものに
    無料から辿り着けない。finance_carry は長らくPRO側にしか渡していなかった。
    """
    h = _client().post("/diagnose", data=FREE_INPUT).data.decode("utf-8")
    assert 'action="/pro/finance_start"' in h
    i = h.find('action="/pro/finance_start"')
    seg = h[i:i + 2000]
    for k in ("price", "byear", "down", "income", "loan_years", "land_area"):
        assert f'name="{k}"' in seg, k


def test_the_mansion_result_can_too():
    h = _client().post("/mansion_diagnose", data=dict(
        address="東京都新宿区西新宿2丁目", price="5000", area="70",
        byear="2010", income="700", loan_years="35")).data.decode("utf-8")
    assert 'action="/pro/finance_start"' in h


def test_the_sample_numbers_hang_together():
    """評価額と年税額を適当に置くと、実務を知っている人にはすぐ分かる。

    ・土地の評価額は実勢の6割前後（公示価格の7割が目安のため）
    ・年税額は、小規模住宅用地1/6・都計税1/3で積んだ額に近いこと
    """
    import app
    S = app.SAMPLE_FINANCE
    land_price = int(S["land_price"]) * 10000
    land_assessed = int(S["land_assessed"]) * 10000
    ratio = land_assessed / land_price
    assert 0.55 <= ratio <= 0.72, ratio

    la, ba = land_assessed, int(S["building_assessed"]) * 10000
    tax = (la / 6 * 0.014 + la / 3 * 0.003) + (ba * 0.014 + ba * 0.003)
    got = int(S["tax_yearly"])
    assert abs(got - tax) / tax < 0.12, (got, tax)


def test_the_sample_settlement_is_worth_showing():
    """秋に月をずらすだけだと精算金が1万円台になり、「決済当日に別途現金が
    要る」という話がいちばん伝わらない額で出る。3月引渡しに固定した。
    """
    import app
    d = app._next_march_first()
    import datetime
    assert (d.month, d.day) == (3, 1)
    assert d > datetime.date.today()
