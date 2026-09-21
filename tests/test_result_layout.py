# -*- coding: utf-8 -*-
"""結果画面の読む順と、画面に出す言葉。

外部レビューで3つ言われた。
・点数のつぎに読みたい「要点」が、PROの案内2枚とローンの下にあった
・確信度 mid、[medium]、unknown といった英語がそのまま出ていた
・本文13px・注記12px は小さい

点数の計算は何も変えていない。出し方だけの話。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402

HOUSE = {"address": "神奈川県小田原市城山1-2-3", "price": "3880",
         "land": "147", "building": "90", "byear": "2005",
         "station": "12", "ptype": "chuko_kodate", "income": "600",
         "down": "300", "loan_years": "35"}

# 欄の名前は area / total_floors。exclusive と書いていたため入力エラーに
# なり、返ってきた入力画面を結果画面だと思って調べていた（入力画面にも
# 「管理費・修繕積立金」の文字があるので、テストは通ってしまっていた）。
FLAT = {"address": "神奈川県小田原市栄町1-1-1", "price": "3480",
        "area": "70", "byear": "2010", "station": "8",
        "floor": "5", "total_floors": "10", "mfee": "12000",
        "rfund": "13000", "income": "600", "down": "300",
        "loan_years": "35"}


def _client():
    os.environ["SHINDAN_MOCK"] = "1"
    return webapp.app.test_client()


def _house():
    return _client().post("/diagnose", data=HOUSE).get_data(as_text=True)


def _flat():
    h = _client().post("/mansion_diagnose", data=FLAT).get_data(as_text=True)
    # 入力エラーで返ってきた入力画面を、結果画面と取り違えないこと
    assert "スコア内訳" in h, "結果画面になっていない（入力が弾かれている）"
    return h


def _house_page():
    h = _house()
    assert "スコア内訳" in h, "結果画面になっていない"
    return h


# ---- 読む順 ---------------------------------------------------------------

def test_the_summary_comes_before_the_evidence():
    """点数 → 要点 → 根拠。要点がいちばん下にあった。"""
    h = _house()
    score = h.index("情報充足度")
    summary = h.index(">強み<")
    price = h.index("価格評価")
    breakdown = h.index("スコア内訳")
    assert score < summary < price < breakdown


def test_the_summary_is_above_the_pro_cards():
    """PROの案内をまたいだ先に要点を置かない。"""
    h = _house()
    assert h.index(">強み<") < h.index("契約の前に、つぶしておくこと")


def test_the_risk_and_the_summary_are_one_card():
    """重大リスクと強み弱みは同じ「要点」。間にカードの境目を入れない。"""
    h = _house()
    i = h.index("重大リスク")
    assert 'class="card"' not in h[i:h.index(">強み<")], "間にカードが挟まっている"


# ---- 言葉 -----------------------------------------------------------------

def test_the_confidence_is_not_english():
    h = _house()
    assert "確信度 mid" not in h and "確信度 high" not in h
    assert "推定の確かさ" in h
    for word in ("ふつう", "高い", "低い"):
        if word in h:
            break
    else:
        raise AssertionError("確かさが日本語で出ていない")


def test_the_severity_is_not_a_bracketed_english_word():
    h = _house()
    for bad in ("[medium]", "[high]", "[low]", "（unknown）"):
        assert bad not in h, bad


def test_the_jargon_in_the_price_card_is_replaced():
    h = _house()
    assert "レンジ幅" not in h and "㎡単価(中央)" not in h
    assert "価格のばらつき" in h
    assert "参考にした成約" in h
    assert "1㎡あたりの中央値" in h


def test_the_flat_management_category_says_what_it_looks_at():
    """「管理」だと管理組合の運営まで見ていると読める。

    実際に見ているのは修繕積立金の水準と管理費の額だけ。
    """
    h = _flat()
    assert "管理費・修繕積立金" in h
    # 採点のキーは変えていない
    from src.config import CONFIG
    assert "管理" in CONFIG["mansion_category_weights"]


# ---- 色 -------------------------------------------------------------------

def test_the_weakness_is_the_red_one():
    """強みが赤、弱みが水色で、直感と逆だった。"""
    css = webapp._RESULT_CSS
    assert "ul.strong li{color:#0ea5e9}" in css, "強みは青"
    assert "ul.weak li{color:#dc2626}" in css, "弱みは赤"


def test_the_headings_still_say_which_is_which():
    """色を入れ替えただけで終わらせない。色が見えない人にも伝わること。"""
    h = _house()
    assert ">強み<" in h and ">弱み<" in h


# ---- 文字サイズ -----------------------------------------------------------

def test_the_body_text_is_not_thirteen_pixels():
    css = webapp._RESULT_CSS
    assert ".muted{color:var(--sub);font-size:14px}" in css
    assert "li{margin:3px 0;font-size:15px" in css
    assert "font-size:13px;margin-top:6px" not in css


def test_the_phone_tables_are_not_twelve_pixels():
    css = webapp._RESULT_CSS
    i = css.index("@media (max-width:560px)")
    assert "table{font-size:13px}" in css[i:], "スマホの表が12pxのまま"


# ---- 土地の結果画面にも同じCSSが配られている -------------------------------

def test_the_land_result_gets_the_same_styling():
    """LAND_RESULT は RESULT の <style> を借りている。

    片方だけ直すと、サイトの中で見た目がばらける。
    """
    assert webapp._RESULT_CSS in webapp.LAND_RESULT
    assert "h2::before" in webapp.LAND_RESULT


# ---- 名前のぶつかり -------------------------------------------------------

def test_no_display_table_is_defined_twice():
    """同じ名前の言い換え表を2回定義しない。

    リスクの状態を _STATUS_JA という名前で足したら、資金計画にある
    同名の表（unknown → 「未確認」）が後ろで定義されていて、そちらが
    勝った。画面には「ハザード未確認（未確認）」と二重に出た。
    落ちも例外も出ないので、見るまで気づけない。
    """
    import ast
    import collections

    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    seen = collections.Counter()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id.endswith("_JA"):
                seen[t.id] += 1
    dupes = [k for k, v in seen.items() if v > 1]
    assert not dupes, f"同じ名前で2回定義されている: {dupes}"


def test_an_unknown_risk_does_not_say_it_twice():
    h = _house()
    assert "（未確認）" not in h, "項目名と同じことを括弧でもう一度言っている"


# ---- 注記 -----------------------------------------------------------------

def test_the_house_result_does_not_print_raw_exceptions():
    """土地の結果だけが注記を絞っていた。

    戸建とマンションは例外の文字列をそのまま出していて、接続先の
    ホスト名・ポート・SSLの内部メッセージが読む人に見えていた。
    読む人には意味が無く、こちらの作りが漏れる。
    """
    for html in (_house(), _flat()):
        for leak in ("HTTPSConnectionPool", "CERTIFICATE_VERIFY_FAILED",
                     "Traceback", "msearch.gsi.go.jp", "SSLError"):
            assert leak not in html, leak


def test_a_long_string_cannot_widen_the_page():
    """URLのような切れ目の無い文字列が入っても、横スクロールを出さない。"""
    assert "overflow-wrap:anywhere" in webapp._RESULT_CSS
