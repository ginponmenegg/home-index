# -*- coding: utf-8 -*-
"""販売図面PDFの取り込み。ネットワーク不要。

PDFの文字は、図面の見た目どおりには並ばない。左に間取り図・右に物件概要
という作りをテキストにすると、図の寸法が表の途中に入り込み、ラベルは
均等割付で字間が開き、周辺環境の「徒歩○分」や資金計画の「○○万円」が
交通欄・価格欄より先に現れる。

ここで固定しているのは、実際に pdfplumber が返した並びそのもの
（scratchpad で図面に近いPDFを作って取り出した結果）。空欄になるより、
もっともらしい誤った値が入るほうが危ないので、そこを重点的に見る。
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extract import (_despace, _normalize, parse_listing_text,
                         parse_mansion_text, extract_from_pdf)


# ---- 均等割付（字間）------------------------------------------------------

def test_despace_joins_letter_spaced_runs():
    """3文字以上続いた1文字は字間とみなしてつなげる。"""
    assert _despace("土 地 面 積 1 2 0 . 0 0 m 2") == "土地面積120.00m2"
    assert _despace("所 在 地 神 奈 川 県") == "所在地神奈川県"


def test_despace_leaves_real_word_breaks_alone():
    """単語として分かれているものは触らない。"""
    assert _despace("洋室 6.0帖 1階") == "洋室 6.0帖 1階"
    assert _despace("中古一戸建 価格 3,500万円") == "中古一戸建 価格 3,500万円"
    # 1文字が2つまでなら字間と決めつけない
    assert _despace("南 東 向き") == "南 東 向き"


# ---- 図面をテキストにしたときの並び ---------------------------------------

SPACED = "\n".join([                       # 均等割付された概要表
    "中古一戸建 価格 3,500万円",
    "所 在 地 神 奈 川 県 小 田 原 市 城 山 一 丁 目 2 番 3 号",
    "交 通 J R 東 海 道 本 線 小 田 原 駅 徒 歩 2 0 分",
    "土 地 面 積 1 2 0 . 0 0 m 2 ( 3 6 . 3 0 坪 )",
    "建 物 面 積 9 5 . 0 0 m 2 ( 2 8 . 7 3 坪 )",
    "築 年 月 平 成 1 7 年 3 月",
    "構 造 木 造 2 階 建",
])

INTERLEAVED = "\n".join([                  # 間取り図の寸法が割り込む
    "中古一戸建 価格 3,500万円",
    "所在地 3.640 神奈川県小田原市城山一丁目2番3号",
    "交通 洋室6.0帖 JR東海道本線 小田原駅 徒歩20分",
    "土地面積 7.280 120.00m2(36.30坪)",
    "建物面積 2.730 95.00m2(28.73坪)",
    "築年月 5.460 平成17年3月",
])

FACILITIES_FIRST = "\n".join([             # 周辺環境が交通より先に来る
    "中古一戸建 価格 3,500万円",
    "＜周辺環境＞ 城山小学校 徒歩8分 スーパー 徒歩4分",
    "交通 JR東海道本線 小田原駅 徒歩20分",
    "土地面積 120.00m2(36.30坪)",
])

PAYMENT_FIRST = "\n".join([                # 資金計画が価格より先に来る
    "中古一戸建",
    "＜お支払い例＞ 自己資金 500万円 月々 9.8万円",
    "価格 3,500万円",
    "土地面積 120.00m2(36.30坪)",
])


@pytest.mark.parametrize("label,text", [
    ("均等割付", SPACED),
    ("図の寸法が割り込む", INTERLEAVED),
])
def test_layout_noise_does_not_hide_the_values(label, text):
    p = parse_listing_text(text)
    assert p["price_man"] == 3500, label
    assert p["land"] == 120.00, label
    assert p["building"] == 95.00, label
    assert p["byear"] == 2005, label
    assert p["station"] == 20, label
    assert p["city"] == "14206", label


def test_walking_time_to_a_school_is_not_the_walk_to_the_station():
    """周辺環境の徒歩分を駅徒歩として読むと、駅に近い物件として採点される。"""
    p = parse_listing_text(FACILITIES_FIRST)
    assert p["station"] == 20, "小学校の徒歩8分を拾っている"


def test_the_down_payment_is_not_the_asking_price():
    """資金計画の金額を価格として読むと、そのまま価格評価に流れる。"""
    p = parse_listing_text(PAYMENT_FIRST)
    assert p["price_man"] == 3500, "自己資金500万円を価格として拾っている"


def test_price_label_wins_over_an_earlier_amount():
    p = parse_listing_text("諸費用 250万円\n販売価格 4,180万円")
    assert p["price_man"] == 4180


def test_price_without_a_label_still_works():
    """貼り付けテキストは価格の見出しが無いことが多い。従来どおり読む。"""
    p = parse_listing_text("戸建 神奈川県小田原市栄町 3200万円 土地120㎡ 徒歩10分")
    assert p["price_man"] == 3200
    assert p["station"] == 10, "駅の記載が無ければ従来どおり徒歩分を拾う"


def test_area_is_read_from_the_label_line_only():
    """別の行の数字を面積として掴まない。"""
    p = parse_listing_text("土地面積\n120.00m2\n建物面積\n95.00m2")
    assert (p["land"], p["building"]) == (120.00, 95.00)
    # 単位の付かない数字は面積ではない
    assert parse_listing_text("土地面積 3.640")["land"] is None


def test_mansion_pdf_layout_is_handled_too():
    p = parse_mansion_text("\n".join([
        "中古マンション 価格 3,480万円",
        "＜周辺環境＞ 〇〇小学校 徒歩12分",
        "交 通 小 田 急 江 ノ 島 線 鵠 沼 海 岸 駅 徒 歩 8 分",
        "専 有 面 積 7 0 . 0 0 m 2",
        "所在階/階建 2階/5階建",
    ]))
    assert p["price_man"] == 3480
    assert p["area"] == 70.00
    assert p["station"] == 8
    assert (p["floor"], p["total_floors"]) == (2, 5)


# ---- PDFから取り出すところまで通す ----------------------------------------

def _spaced_pdf() -> bytes:
    """均等割付のラベルを持つ1枚ものを組む。実物の図面はこの形が多い。"""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    def txt(x, y, s, size=8, space=0.0):
        to = c.beginText(x * mm, (297 - y) * mm)
        to.setFont("HeiseiKakuGo-W5", size)
        if space:
            to.setCharSpace(space)      # 均等割付
        to.textOut(s)
        c.drawText(to)

    txt(15, 20, "中古一戸建", 14)
    txt(120, 20, "価格 3,500万円", 14)
    y = 40
    for lab, val in (("所在地", "神奈川県小田原市城山一丁目2番3号"),
                     ("交通", "JR東海道本線 小田原駅 徒歩20分"),
                     ("土地面積", "120.00m2(36.30坪)"),
                     ("建物面積", "95.00m2(28.73坪)"),
                     ("築年月", "平成17年3月"),
                     ("構造", "木造2階建")):
        txt(108, y, lab, 8, space=5.0)
        txt(140, y, val, 8)
        y += 6
    c.showPage()
    c.save()
    return buf.getvalue()


def test_reading_a_letter_spaced_pdf_end_to_end(tmp_path):
    pytest.importorskip("pdfplumber")
    path = tmp_path / "zumen.pdf"
    path.write_bytes(_spaced_pdf())
    text = extract_from_pdf(str(path))
    # 画面の確認欄にもこの文字列が出るので、読める形で返っていること
    assert "土地面積" in text, f"字間がほどけていない：{text!r}"
    p = parse_listing_text(text)
    assert p["price_man"] == 3500
    assert (p["land"], p["building"]) == (120.00, 95.00)
    assert p["byear"] == 2005
    assert p["station"] == 20
    assert p["structure"] == "wood"


# ---- 実物の販売図面2枚で分かったこと ---------------------------------------
#
# 値と番地は伏せてある（tests/test_placeholders.py を参照）。写しているのは
# 崩れ方の形だけで、地名は市区町村コードの解決を検証するために残している。

def test_kangxi_radicals_are_read_as_kanji():
    """PDFのフォントによっては、漢字が部首の符号位置で入ってくる。

    「⽊」は木ではなく KANGXI RADICAL TREE（U+2F4A）。見た目は同じでも
    別の文字なので、そのままではどのラベルにも一致しない。
    """
    assert _normalize("⼟地⾯積") == "土地面積"
    assert _normalize("築年⽉") == "築年月"
    # NFKCは旧字体に開くことがある。「売⼾建住宅」が戶のままだと戸建に当たらない
    assert _normalize("売⼾建住宅") == "売戸建住宅"
    # CJK部首補助にはNFKCが手を付けない字がある（⻄ は U+2EC4）
    assert _normalize("温⽔⻄") == "温水西"


RADICAL_SHEET = "\n".join([                # レインズ形式のinfo sheet
    "売⼾建住宅 所",
    "在",
    "地",
    "3,480万円",
    "価",
    "格",
    "物",
    "件 所 神奈川県厚⽊市温⽔⻄2丁⽬",
    "⼩⽥急⼩⽥原線 本厚⽊",
    "交",
    "通 バス14分 バス停 ⽑利台⼀丁⽬ 停歩6分",
    "⼟",
    "地 公簿 180.00㎡ 私道⾯積",
    "⾯ (共有持分)",
    "積",
    "構造・規模 ＲＣ 2階建 地下1階",
    "築年⽉ 2004年11⽉ 駐⾞場有 無料",
    "間取り 建物⾯積 築年⽉ 備 考",
    "4SLDK 190.00㎡ 2004年11⽉",
])


def test_a_sheet_written_in_radicals_still_parses():
    p = parse_listing_text(RADICAL_SHEET)
    assert p["price_man"] == 3480
    assert p["land"] == 180.00, "縦組みの見出しの脇に残る「公簿」から読む"
    assert p["building"] == 190.00, "下段の一覧表は、見出しの行の次の行に値が並ぶ"
    assert p["byear"] == 2004
    assert p["structure"] == "rc"
    assert p["ptype"] == "chuko_kodate", "「売戸建住宅」＋築年 → 中古戸建"
    assert p["city"] == "14212"
    assert p["district"] == "温水西"
    assert (p["bus"], p["station"]) == (14, 6), "バス14分＋バス停まで徒歩6分"


def test_a_header_row_hands_its_value_to_the_right_column():
    """見出しだけの行の次に値が並ぶ表。列の番号で対応させる。"""
    p = parse_listing_text("間取り 土地面積 建物面積 築年月\n"
                           "4SLDK 120.00m2 95.00m2 2010年3月")
    assert (p["land"], p["building"]) == (120.00, 95.00)


def test_koubo_is_read_as_the_land_area():
    assert parse_listing_text("地 公簿 180.00m2 私道面積")["land"] == 180.00


# ---- 自社作成の図面 -------------------------------------------------------

def test_the_address_stops_at_a_bullet():
    """図面は項目を■で並べる。止めないと後ろが全部住所になる。"""
    p = parse_listing_text(
        "■所在地／神奈川県秦野市南矢名■交通/小田急線「東海大学前駅」徒歩20分")
    assert p["address"] == "神奈川県秦野市南矢名"
    assert p["station"] == 20


def test_the_agents_address_is_not_the_property_address():
    """図面の隅には仲介業者の住所がある。物件の所在地と取り違えない。

    空欄になるのとは違い、別の市区町村の成約データで採点してしまう。
    """
    p = parse_listing_text("\n".join([
        "■所在地／神奈川県秦野市南矢名",
        "〒254-0824 平塚市花水台1-2-3 担当：〇〇",
    ]))
    assert p["city"] == "14211", "秦野市。平塚市を拾っていない"
    assert p["district"] == "南矢名", "花水台は業者の所在地"


def test_square_metres_beat_a_rounded_tsubo_headline():
    """見出しの坪表記は丸めてある。同じ図面の正確な㎡を採る。"""
    p = parse_listing_text("土地面積151坪超\n■敷地面積／500.00㎡（151.25坪）")
    assert p["land"] == 500.00
    # 坪しか無ければ従来どおり換算する
    assert abs(parse_listing_text("土地 100坪")["land"] - 330.58) < 0.1


def test_a_price_split_across_three_lines():
    """価格だけ大きく組まれ、数字と「万円」が別の行に落ちる図面がある。"""
    p = parse_listing_text("\n".join([
        "販売価格 ■所在地／神奈川県秦野市南矢名",
        "3,980",
        "万円 ■建ぺい率／50% ■容積率／100%",
    ]))
    assert p["price_man"] == 3980


def test_a_bare_number_is_not_taken_as_a_price_without_a_price_context():
    """桁区切りの数字だけの行を価格とみなすのは、価格の記載がある図面に限る。"""
    assert parse_listing_text("物件番号 1,234")["price_man"] is None
