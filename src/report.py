# -*- coding: utf-8 -*-
"""資金計画・契約前チェックのPDF（PRO）。

日本語は ReportLab の Adobe-Japan1 CID フォントで描く（本文はゴシック
HeiseiKakuGo-W5、表題は明朝 HeiseiMin-W3）。システムライブラリを
必要としないため Windows でも Render でも同じ動作になる。

注意：CIDフォントはPDFに埋め込まれない。閲覧側に日本語フォントが無い環境では
代替フォントで表示される。完全な再現が要る場合はTTFの埋め込みが必要（要検討）。

■見た目（2026-11-23）
サイトとパンフレットに揃えた。表紙の帯は紺（#0C1B2A）、要の数字は
カードで大きく、節には番号を振る。表は見出し行に色を付け、行を交互に
塗る。中身（項目・数字・文言）は変えていない。テストは文言で確かめている
（tests/test_finance.py・test_land_shape.py）。
"""
from __future__ import annotations
from typing import Optional
import datetime
import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether, PageBreak, Flowable)

# フォントは fonts/ の BIZ UDPゴシック・BIZ UDP明朝を埋め込む（2026-11-23）。
# Google Fonts 配布の SIL Open Font License 1.1（fonts/OFL-*.txt）。
# CIDフォント（埋め込まれない標準フォント）は約物の前後が空いて見えたため。
# ファイルが無い環境では、従来のCIDフォントに戻す（PDFが作れなくならない）。
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "fonts")
_TTF = {"BIZUDPGothic": "BIZUDPGothic-Regular.ttf",
        "BIZUDPGothic-Bold": "BIZUDPGothic-Bold.ttf",
        "BIZUDPMincho": "BIZUDPMincho-Regular.ttf"}
FONT = "BIZUDPGothic"
FONT_B = "BIZUDPGothic-Bold"
SERIF = "BIZUDPMincho"
_registered = False

# サイトの色（docs/pamphlet/pamphlet.css と app.py の変数）
NAVY = colors.HexColor("#0C1B2A")
NAVY2 = colors.HexColor("#14395C")
PAPER = colors.HexColor("#F0EEE9")
PAPER_DIM = colors.HexColor("#B9BCBD")
PIN = colors.HexColor("#E0A83F")
INK = colors.HexColor("#14181D")
SUB = colors.HexColor("#5E6670")
LINE = colors.HexColor("#E1E5EB")
HEAD = colors.HexColor("#EEF2F6")       # 表の見出し行
ZEBRA = colors.HexColor("#F8F9FB")      # 表の偶数行
CARD = colors.HexColor("#F6F4EF")       # 数字のカード
WRITE = colors.HexColor("#FCFBF8")      # 書き込み欄
GOOD = colors.HexColor("#2E6F4E")
WARN = colors.HexColor("#8A6512")
NOTE_BG = colors.HexColor("#FBF6EA")
BLACK = INK
SOFT = ZEBRA


def _ensure_font():
    global _registered, FONT, FONT_B, SERIF
    if _registered:
        return
    try:
        for name, file in _TTF.items():
            pdfmetrics.registerFont(TTFont(name, os.path.join(_FONT_DIR, file)))
        # <b> で太字が使えるように、同じ書体の家族として登録する
        registerFontFamily("BIZUDPGothic", normal="BIZUDPGothic",
                           bold="BIZUDPGothic-Bold", italic="BIZUDPGothic",
                           boldItalic="BIZUDPGothic-Bold")
    except Exception as e:
        print(f"[report] 埋め込みフォントを読めません。標準フォントで作ります: {e}")
        FONT = FONT_B = "HeiseiKakuGo-W5"
        SERIF = "HeiseiMin-W3"
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
        pdfmetrics.registerFont(UnicodeCIDFont(SERIF))
    _registered = True


def _styles():
    base = dict(fontName=FONT, textColor=INK, leading=14)
    st = {
        "title": ParagraphStyle("t", fontName=SERIF, fontSize=18, leading=24,
                                textColor=INK, spaceAfter=4),
        "sub": ParagraphStyle("s", fontSize=8.5, textColor=SUB, fontName=FONT,
                              leading=13, spaceAfter=6),
        "h2": ParagraphStyle("h", fontSize=11, spaceBefore=10, spaceAfter=5,
                             **base),
        "h3": ParagraphStyle("h3", fontSize=9.5, leading=14, fontName=FONT_B,
                             textColor=NAVY2, spaceBefore=4, spaceAfter=2),
        "body": ParagraphStyle("b", fontSize=9, **base),
        "lead": ParagraphStyle("l", fontSize=9, fontName=FONT, textColor=INK,
                               leading=15),
        "cell": ParagraphStyle("c", fontSize=8.3, fontName=FONT, textColor=INK,
                               leading=12),
        "cellhead": ParagraphStyle("ch", fontSize=7.5, fontName=FONT_B,
                                   textColor=NAVY2, leading=10),
        "cellheadr": ParagraphStyle("chr", fontSize=7.5, fontName=FONT_B,
                                    textColor=NAVY2, leading=10, alignment=TA_RIGHT),
        "cellsub": ParagraphStyle("cs", fontSize=7, fontName=FONT,
                                  textColor=SUB, leading=10),
        "num": ParagraphStyle("n", fontSize=8.8, fontName=FONT, textColor=INK,
                              leading=12, alignment=TA_RIGHT),
        "numbig": ParagraphStyle("nb", fontSize=10, fontName=FONT_B, textColor=INK,
                                 leading=13, alignment=TA_RIGHT),
        "note": ParagraphStyle("no", fontSize=7.5, fontName=FONT, textColor=SUB,
                               leading=11.5),
        "callout": ParagraphStyle("co", fontSize=8, fontName=FONT, textColor=WARN,
                                  leading=12.5),
        "cardk": ParagraphStyle("ck", fontSize=7.2, fontName=FONT, textColor=SUB,
                                leading=10),
        "cardv": ParagraphStyle("cv", fontSize=14, fontName=FONT_B, textColor=INK,
                                leading=18),
        "cardvs": ParagraphStyle("cvs", fontSize=11.5, fontName=FONT_B, textColor=INK,
                                 leading=15),
        "qnum": ParagraphStyle("qn", fontSize=8, fontName=FONT_B, textColor=NAVY2,
                               leading=12, alignment=TA_CENTER),
    }
    # 日本語は語の切れ目に空白が無いので、どの文字でも折り返せるようにする
    # （既定のままだと「※」だけが1行に残ることがあった）。
    for v in st.values():
        v.wordWrap = "CJK"
    return st


# ---- 部品 -----------------------------------------------------------------

class _Hero(Flowable):
    """表紙の帯。紺の角丸の箱に、種類・表題・副題を置く。"""

    def __init__(self, width, kicker, title, sub, tag="PRO"):
        super().__init__()
        self.width, self.kicker, self.title = width, kicker, title
        self.sub, self.tag = sub, tag
        self.height = 34 * mm

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        w, h = self.width, self.height
        c.saveState()
        c.setFillColor(NAVY)
        c.roundRect(0, 0, w, h, 3 * mm, stroke=0, fill=1)
        # 左上の細い金の線（サイトの「検討中の物件」のピンの色）
        c.setFillColor(PIN)
        c.rect(7 * mm, h - 8.2 * mm, 8 * mm, 0.7 * mm, stroke=0, fill=1)
        c.setFillColor(PAPER_DIM)
        c.setFont(FONT, 7.5)
        c.drawString(17 * mm, h - 8.8 * mm, f"HOME INDEX　{self.kicker}")
        # 右上の PRO の札
        if self.tag:
            tw = c.stringWidth(self.tag, FONT, 7) + 5 * mm
            c.setStrokeColor(PIN)
            c.setLineWidth(0.6)
            c.roundRect(w - 7 * mm - tw, h - 10.2 * mm, tw, 5 * mm, 2.5 * mm,
                        stroke=1, fill=0)
            c.setFillColor(PIN)
            c.setFont(FONT, 7)
            c.drawCentredString(w - 7 * mm - tw / 2, h - 8.6 * mm, self.tag)
        c.setFillColor(PAPER)
        c.setFont(SERIF, 19)
        c.drawString(7 * mm, h - 19.5 * mm, self.title)
        c.setFillColor(PAPER_DIM)
        c.setFont(FONT, 8)
        y = h - 26 * mm
        for line in self.sub.split("\n")[:2]:
            c.drawString(7 * mm, y, line)
            y -= 4.3 * mm
        c.restoreState()


class _Section(Flowable):
    """節の見出し。番号の札・表題・細い線。"""

    def __init__(self, width, num, title):
        super().__init__()
        self.width, self.num, self.title = width, num, title
        self.height = 11 * mm

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        y = 3.2 * mm
        c.setFillColor(NAVY)
        c.roundRect(0, y - 1.6 * mm, 8.5 * mm, 6.2 * mm, 1.4 * mm, stroke=0, fill=1)
        c.setFillColor(PAPER)
        c.setFont(FONT_B, 7.8)
        c.drawCentredString(4.25 * mm, y, f"{self.num:02d}")
        c.setFillColor(INK)
        c.setFont(FONT_B, 12)
        c.drawString(11.5 * mm, y - 0.3 * mm, self.title)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(0, 0, self.width, 0)
        c.setStrokeColor(PIN)
        c.setLineWidth(1.2)
        c.line(0, 0, 8.5 * mm, 0)
        c.restoreState()


class _Counter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


def _section(story, W, counter, title, keep=None):
    """節の見出しを置く。keep を渡すと、見出しと最初の塊を同じページに置く。"""
    story.append(Spacer(1, 7))
    head = _Section(W, counter.next(), title)
    if keep:
        story.append(KeepTogether([head, Spacer(1, 4)] + list(keep)))
    else:
        story.append(head)
        story.append(Spacer(1, 4))


def _callout(text, st, W, color=WARN, bg=NOTE_BG):
    """左に色の線を引いた注意書きの箱。"""
    p = Paragraph(text, ParagraphStyle("cx", parent=st["callout"], textColor=color))
    t = Table([[p]], colWidths=[W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _cards(items, st, W, big=True):
    """要の数字を横に並べたカード。items は (見出し, 値) の列。"""
    n = len(items)
    gap = 3 * mm
    cw = (W - gap * (n - 1)) / n
    cells, widths = [], []
    for i, (k, v) in enumerate(items):
        cells.append([Paragraph(k, st["cardk"]),
                      Paragraph(v, st["cardv"] if big else st["cardvs"])])
        widths.append(cw)
        if i < n - 1:
            cells.append("")
            widths.append(gap)
    t = Table([cells], colWidths=widths)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 0),
             ("RIGHTPADDING", (0, 0), (-1, -1), 0),
             ("TOPPADDING", (0, 0), (-1, -1), 0),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]
    t.setStyle(TableStyle(style))
    # 各カードの中身は小さな表にして、背景と余白を付ける
    for i in range(0, len(cells), 2):
        inner = Table([[cells[i][0]], [cells[i][1]]], colWidths=[cw])
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CARD),
            ("LINEABOVE", (0, 0), (-1, 0), 1.6, NAVY),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (0, 0), 6),
            ("BOTTOMPADDING", (0, 0), (0, 0), 1),
            ("TOPPADDING", (0, 1), (0, 1), 1),
            ("BOTTOMPADDING", (0, 1), (0, 1), 7),
        ]))
        cells[i] = inner
    t = Table([cells], colWidths=widths)
    t.setStyle(TableStyle(style))
    return t


def _kv_table(rows, st, width, strong=(), ratio=0.58):
    """見出しと値の2列。strong に入れた見出しの行は太めに目立たせる。"""
    data = []
    for k, v in rows:
        if k in strong:
            data.append([Paragraph(k, st["h3"]), Paragraph(v, st["numbig"])])
        else:
            data.append([Paragraph(k, st["body"]), Paragraph(v, st["num"])])
    t = Table(data, colWidths=[width * ratio, width * (1 - ratio)])
    style = [
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i in range(len(data)):
        if i % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
        if rows[i][0] in strong:
            style.append(("BACKGROUND", (0, i), (-1, i), HEAD))
    t.setStyle(TableStyle(style))
    return t


def _header_style(t, ncols, zebra=True):
    rows = len(t._cellvalues)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY2),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if zebra:
        for i in range(2, rows, 2):
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def _status(s: str, st) -> Paragraph:
    """諸費用の区分（計算／推定など）を色の付いた札にする。"""
    color = {"計算": "#2E6F4E", "推定": "#8A6512"}.get(s, "#5E6670")
    return Paragraph(f"<font color='{color}'>● {s}</font>", st["cellsub"])


def _page_decor(name: str):
    """各ページの上の細い線と、下の帯（名前・注意・ページ番号）。"""
    def _draw(canv, _doc):
        canv.saveState()
        w, h = A4
        canv.setStrokeColor(NAVY)
        canv.setLineWidth(2)
        canv.line(0, h - 1, w, h - 1)
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.5)
        canv.line(18 * mm, 14 * mm, w - 18 * mm, 14 * mm)
        canv.setFont(FONT, 7)
        canv.setFillColor(NAVY2)
        canv.drawString(18 * mm, 9.5 * mm, "HOME INDEX")
        canv.setFillColor(SUB)
        canv.drawString(18 * mm + canv.stringWidth("HOME INDEX　", FONT, 7),
                        9.5 * mm,
                        f"{name}　参考情報であり、最終判断は専門家の確認を前提としてください。")
        canv.drawRightString(w - 18 * mm, 9.5 * mm, f"{canv.getPageNumber()}")
        canv.restoreState()
    return _draw


def _doc(buf, title):
    return SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=20 * mm,
        title=title, author="HOME INDEX")


# 契約前チェックの題。診断から持ち回したときだけ、この名前の1冊になる。
CHECK_TITLE = "契約前チェック"


def _diagnosis_pages(story, d, st, W, counter):
    """契約前チェックの前半（物件の要点・聞くこと・重説で読む欄）を置く。

    d は結果画面が署名して持ち回した辞書（app.py の _render_result）。
    引き継ぎが無ければ何もしない（資金計画だけのPDFになる）。
    点数をここで計算し直さないのは、外部APIを二度叩かないためと、
    画面に出したものと1点でもずれさせないため。

    2026-10-23、PROの目玉を「重要事項説明の日に持っていく1冊」にした。
    聞くことには回答を書き込む欄を付け、重説で読む欄を資金計画の前に置く
    （当日に開く順番に並べる）。
    """
    if not d:
        return
    today = datetime.date.today().strftime("%Y年%m月%d日")
    story.append(_Hero(W, "重要事項説明の前に開く1冊", CHECK_TITLE,
                       f"{d.get('title', '')}\n作成日 {today}"))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "重要事項説明と契約の前に、手元に置いておくための1冊です。"
        + (d.get("contents") or
           "1. この物件の要点　2. 仲介業者に聞くこと（回答を書き込む欄つき）　"
           "3. 重要事項説明書で読む欄　4. 諸費用まで含めた資金計画、の順に"
           "並べています。"), st["lead"]))
    if d.get("total") is None:
        story.append(Spacer(1, 5))
        story.append(_callout(
            "これは見本です。実在の物件ではないため、点数と注意することの節は"
            "省いています。", st, W))
    else:
        story.append(Spacer(1, 7))
        story.append(_cards([("総合", f"{d.get('total')}点（{d.get('grade')}）"),
                             ("情報充足度", f"{d.get('suff')}％")], st, W))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "未確認の項目は評価に入れていません。点数はルール計算であり、"
            "物件の価値や安全性を保証するものではありません。", st["note"]))

    cats = d.get("cats") or []
    if cats:
        rows = [(c[0], f"{c[1]} / {c[2]}") for c in cats]
        _section(story, W, counter, "カテゴリ別の評価",
                 keep=[_kv_table(rows, st, W)])
        for c in cats:
            story.append(Paragraph(f"<font color='#14395C'>{c[0]}</font>　{c[3]}",
                                   st["note"]))

    risks = d.get("risks") or []
    if risks:
        _section(story, W, counter, "重大リスク")
        for r in risks:
            done = r[2] == "confirmed"
            mark = "確認済" if done else "未確認"
            story.append(_callout(
                f"<b>{r[1]}</b>（{mark}）　{r[3]}", st, W,
                color=(colors.HexColor("#A0332B") if r[0] == "high" else WARN)))
            story.append(Spacer(1, 3))

    ask = d.get("ask") or []
    if ask:
        _section(story, W, counter, d.get("ask_title") or "仲介業者に聞くこと")
        story.append(Paragraph(
            "まだ分かっていない項目から組み立てています。答えを聞いたら、"
            "右の欄に書き込んでください。書類で確かめたものには印を。",
            st["note"]))
        story.append(Spacer(1, 4))
        data = [[Paragraph("", st["cellhead"]), Paragraph("聞くこと", st["cellhead"]),
                 Paragraph("回答・メモ", st["cellhead"])]]
        for i, q in enumerate(ask, 1):
            data.append([Paragraph(f"□ {i}", st["qnum"]),
                         Paragraph(q, st["cell"]), Paragraph("", st["cell"])])
        t = Table(data, colWidths=[W * 0.08, W * 0.50, W * 0.42], repeatRows=1)
        t = _header_style(t, 3, zebra=False)
        # 手で書き込めるよう、行に余白を取る（スマホで見るより、印刷して
        # 重要事項説明の場に持っていく使い方を想定している）
        t.setStyle(TableStyle([
            ("BACKGROUND", (2, 1), (2, -1), WRITE),
            ("LINEAFTER", (1, 0), (1, -1), 0.3, LINE),
            ("TOPPADDING", (0, 1), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 12)]))
        story.append(t)

    disc = d.get("disc") or []
    if disc:
        _section(story, W, counter, "重要事項説明書で読む欄")
        story.append(Paragraph(
            "契約の直前に読み上げられます。先に読む場所を絞るためのものです。"
            "どこに何が書かれるかまでで、良し悪しは書いていません。",
            st["note"]))
        story.append(Spacer(1, 4))
        data = [[Paragraph("読む欄", st["cellhead"]), Paragraph("なぜ", st["cellhead"])]]
        for where, law, why in disc:
            data.append([Paragraph(f"□ {where}<br/><font size=6.8 color='#5E6670'>"
                                   f"{law}</font>", st["cell"]),
                         Paragraph(why, st["cellsub"])])
        story.append(_header_style(
            Table(data, colWidths=[W * 0.38, W * 0.62], repeatRows=1), 2))

    story.append(PageBreak())


def build_land_check_pdf(d: dict) -> bytes:
    """土地の契約前チェック（2026-10-23）。

    土地のPROには資金計画の画面が無い。代わりに、結果画面に出した
    建てられる家・総額の積み上げ・つなぎ融資・支払いの時系列を、
    結果画面が署名して持ち回した値のまま載せる（計算し直さない）。
    """
    _ensure_font()
    st = _styles()
    buf = io.BytesIO()
    doc = _doc(buf, f"{CHECK_TITLE}（土地）｜HOME INDEX PRO")
    W = doc.width - 12          # フレームの左右の余白（各6pt）を除いた幅
    story = []
    counter = _Counter()
    _diagnosis_pages(story, d, st, W, counter)

    procs = d.get("procedures") or []
    if procs:
        _section(story, W, counter, "着工までに要る手続き")
        data = [[Paragraph("□", st["qnum"]), Paragraph(p, st["cell"])] for p in procs]
        t = Table(data, colWidths=[W * 0.06, W * 0.94])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story.append(t)

    cap = d.get("cap") or []
    if cap:
        _section(story, W, counter, "この土地に建てられる家")
        for line in cap:
            story.append(Paragraph(f"・{line}", st["body"]))

    money = d.get("money") or {}
    if money.get("costs"):
        _section(story, W, counter, "総額の積み上げ",
                 keep=[_cards([("金額の出ている項目の合計", money.get("total") or "—")],
                              st, W)])
        story.append(Spacer(1, 5))
        data = [[Paragraph("費目", st["cellhead"]), Paragraph("金額", st["cellheadr"]),
                 Paragraph("中身", st["cellhead"])]]
        for name, amount, basis in money["costs"]:
            data.append([Paragraph(name, st["cell"]),
                         Paragraph(amount, st["num"]),
                         Paragraph(basis or "", st["cellsub"])])
        story.append(_header_style(
            Table(data, colWidths=[W * 0.30, W * 0.20, W * 0.50],
                  repeatRows=1), 3))
        if money.get("unknown"):
            story.append(Spacer(1, 4))
            story.append(_callout(
                f"まだ金額の出ていない項目が{money['unknown']}件あります。合計には"
                "入っていません。0円ではなく、いまは誰にも分からないという"
                "意味です（地盤改良は地盤調査まで、解体は見積りまで決まりません）。",
                st, W))

    br = d.get("bridge") or {}
    if br:
        _section(story, W, counter, "つなぎ融資")
        if br.get("interest"):
            story.append(Paragraph(
                f"利息 <b>{br['interest']}</b>（土地の決済から完成まで{br.get('days')}日、"
                f"年{br.get('rate')}％、立て替える額 {br.get('principal')}）",
                st["body"]))
        else:
            story.append(Paragraph("つなぎ融資の利息は計算していません。",
                                   st["note"]))
        draws = br.get("draws") or []
        if draws:
            story.append(Spacer(1, 4))
            data = [[Paragraph(h, st["cellhead"]) for h in
                     ("実行するお金", "いつ", "金額", "完成まで", "利息")]]
            for row in draws:
                data.append([Paragraph(str(x), st["cell"]) for x in row])
            story.append(_header_style(
                Table(data, colWidths=[W * 0.28, W * 0.22, W * 0.18,
                                       W * 0.14, W * 0.18], repeatRows=1), 5))
        for n in (br.get("notes") or []):
            story.append(Paragraph(n, st["note"]))

    events = d.get("events") or []
    if events:
        _section(story, W, counter, "いつ、いくら要るか")
        data = [[Paragraph(h, st["cellhead"]) for h in
                 ("いつ", "何のお金", "金額", "どこから")]]
        for date, name, amount, source in events:
            data.append([Paragraph(date, st["cell"]), Paragraph(name, st["cell"]),
                         Paragraph(amount, st["num"]),
                         Paragraph(source or "", st["cellsub"])])
        story.append(_header_style(
            Table(data, colWidths=[W * 0.22, W * 0.38, W * 0.18, W * 0.22],
                  repeatRows=1), 4))
        story.append(Spacer(1, 3))
        story.append(Paragraph("手付金は融資の実行前に払います。", st["note"]))

    loan = d.get("loan") or {}
    if loan:
        cards = [("借入額", loan.get("principal", "—")),
                 ("月々の返済（金利1.25％の試算）", loan.get("monthly", "—"))]
        if loan.get("burden"):
            cards.append(("返済負担率", f"{loan['burden']}％"))
        _section(story, W, counter, "住宅ローン本体",
                 keep=[_cards(cards, st, W, big=False)])
        story.append(Spacer(1, 4))
        story.append(_kv_table([("総額（土地＋建物予算）", loan.get("total", "—"))],
                               st, W))
        story.append(Paragraph(
            "総額には、外構・地盤改良・付帯工事・諸費用のうち金額の分からない"
            "ものは入っていません。", st["note"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "点数はルール計算であり、土地の価値や安全性を保証するものではありません。"
        "建てられる大きさは、斜線制限・日影規制・条例を入れていない上限です。"
        "最終判断は、現地と書類、専門家の確認を前提としてください。", st["note"]))

    deco = _page_decor(f"{CHECK_TITLE}（土地・PRO）")
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


def build_finance_pdf(ctx: dict) -> bytes:
    """試算結果のコンテキストからPDFを組み立ててバイト列で返す。"""
    _ensure_font()
    st = _styles()
    buf = io.BytesIO()
    doc = _doc(buf, (f"{CHECK_TITLE}｜HOME INDEX PRO" if ctx.get("diag")
                     else "詳細な資金計画｜HOME INDEX PRO"))
    W = doc.width - 12          # フレームの左右の余白（各6pt）を除いた幅
    s = ctx["s"]
    story = []
    counter = _Counter()

    # ---- 診断（結果画面から引き継いだときだけ）----
    # 資金計画だけを渡されても、家族や金融機関には話が半分しか伝わらない。
    # 引き継ぎがあるときは、点数とリスクを先に載せて1枚にする。
    _diagnosis_pages(story, ctx.get("diag"), st, W, counter)

    # ---- 表題 ----
    today = datetime.date.today().strftime("%Y年%m月%d日")
    story.append(_Hero(
        W, "諸費用・金利・控除まで", "詳細な資金計画",
        f"物件価格 {s['price']}（{'新築建売' if s['newbuild'] else '中古'}）\n"
        f"作成日 {today}"))
    story.append(Spacer(1, 8))

    # 見本のPDFは、ページから切り離されて出回る。ここに書いておかないと、
    # 受け取った人が自分の物件の試算だと思って持ち歩くことになる。
    if ctx.get("sample"):
        story.append(_callout(
            "これは見本です。実在の売り出し物件ではありません。"
            "評価額・年税額など前提に置いた数字は、末尾に記載しています。",
            st, W))
        story.append(Spacer(1, 6))

    # ---- 資金の全体像 ----
    cards = [("必要総額", s["total"]), ("借入額", s["principal"]),
             (f"月々の返済（{s['rate']}％・{s['years']}年）", s["monthly"])]
    if s.get("burden"):
        cards.append(("返済負担率", f"{s['burden']}％"))
    rows = [("物件価格", s["price"]), ("諸費用（判明分）", s["costs"]),
            ("必要総額", s["total"]), ("頭金", s["down"]),
            ("借入額", s["principal"]),
            (f"月々の返済（金利{s['rate']}％・{s['years']}年）", s["monthly"])]
    if s.get("burden"):
        rows.append(("返済負担率", f"{s['burden']}％"))
    _section(story, W, counter, "資金の全体像",
             keep=[_cards(cards, st, W, big=len(cards) <= 3), Spacer(1, 6),
                   _kv_table(rows, st, W, strong=("必要総額", "借入額"))])
    story.append(Paragraph(
        "借入額は必要総額から頭金を差し引いた額です（諸費用を借入に含める前提）。"
        "保証料と抵当権設定の登録免許税は借入額に比例するため、"
        "借入額と諸費用が釣り合うまで計算を繰り返しています。", st["note"]))

    # ---- 諸費用の内訳 ----
    _section(story, W, counter, "諸費用の内訳")
    data = [[Paragraph("項目", st["cellhead"]), Paragraph("金額", st["cellheadr"]),
             Paragraph("区分", st["cellhead"])]]
    for c in ctx["costs"]:
        extra = (f"<br/><font size=6.8 color='#8A6512'>※ {c['note']}</font>"
                 if c.get("note") else "")
        name = Paragraph(
            f"{c['name']}<br/><font size=6.8 color='#5E6670'>{c['basis']}</font>"
            + extra, st["cell"])
        data.append([name, Paragraph(c["amount"], st["num"]),
                     _status(c["status_ja"], st)])
    t = Table(data, colWidths=[W * 0.62, W * 0.22, W * 0.16], repeatRows=1)
    story.append(_header_style(t, 3))
    if ctx.get("reg_total"):
        story.append(Spacer(1, 5))
        story.append(_kv_table([("登記費用の小計", ctx["reg_total"])], st, W))
    if ctx.get("unknown"):
        story.append(Spacer(1, 5))
        story.append(_callout(
            f"算出していない項目：{ctx['unknown']}　"
            "情報が足りないため金額を出していません。合計にも含めていません。",
            st, W))

    # ---- 引渡日の精算（諸費用とは別。売主に払う）----
    pr = ctx.get("proration")
    if pr:
        _section(story, W, counter, "引渡日の精算")
        story.append(Paragraph(
            f"{pr['handover']}に引き渡す場合。起算日は{pr['start']}です。"
            "諸費用とは別に、決済当日に売主へ払います。", st["body"]))
        story.append(Spacer(1, 4))
        rows = [[Paragraph("項目", st["cellhead"]), Paragraph("金額", st["cellheadr"])]]
        for i in pr["rows"]:
            rows.append([
                Paragraph(f"{i['name']}<br/><font size=6.8 color='#5E6670'>"
                          f"{i['basis']}</font>", st["cell"]),
                Paragraph(i["amount"], st["num"])])
        story.append(_header_style(
            Table(rows, colWidths=[W * 0.72, W * 0.28], repeatRows=1), 2))
        if pr.get("total"):
            story.append(Spacer(1, 5))
            story.append(_kv_table([("決済当日に必要な現金（精算金）", pr["total"])],
                                   st, W, strong=("決済当日に必要な現金（精算金）",)))
        story.append(Paragraph(
            "起算日、引渡日をどちらの負担にするか、うるう年の数え方は、"
            "いずれも売買契約書の定めが優先します。", st["note"]))

    # ---- 金利シナリオ ----
    data = [[Paragraph(h, st["cellhead"] if j == 0 else st["cellheadr"])
             for j, h in enumerate(("金利", "月々", "現在との差", "総返済額"))]]
    for r in ctx["scenarios"]:
        data.append([Paragraph(f"{r['label']}（{r['rate']}％）", st["cell"]),
                     Paragraph(r["monthly"], st["num"]),
                     Paragraph(r["diff"], st["num"]),
                     Paragraph(r["total"], st["num"])])
    t = _header_style(Table(data, colWidths=[W * 0.30, W * 0.23, W * 0.23, W * 0.24]), 4)
    _section(story, W, counter, "金利が上がったら", keep=[t])
    story.append(Paragraph(
        "将来の予測ではなく、その金利になった場合の返済額です。", st["note"]))

    # ---- 繰上返済 ----
    if ctx.get("prepay"):
        p = ctx["prepay"]
        cards = []
        if p.get("months_saved"):
            cards.append(("返済期間の短縮", p["months_saved"]))
        cards.append(("軽減される利息", p["interest_saved"]))
        cards.append(("繰上返済後の月々", p["new_monthly"]))
        _section(story, W, counter, "繰上返済の効果", keep=[
            Paragraph(f"{p['after']}年後に {p['amount']} を繰上返済した場合"
                      f"（{p['kind']}）", st["sub"]),
            _cards(cards, st, W, big=False)])

    # ---- 住宅ローン控除 ----
    d = ctx["deduction"]
    _section(story, W, counter, "住宅ローン控除")
    story.append(Paragraph(d["basis"], st["sub"]))
    if d["ok"]:
        story.append(_cards([("控除の見込み（最大）", d["total"]),
                             ("借入限度額", d["limit"]),
                             ("控除期間", f"{d['years']}年")], st, W, big=False))
        cells = [Paragraph("年目", st["cellhead"])] + \
                [Paragraph(str(i + 1), st["cellheadr"]) for i in range(len(d["yearly"]))]
        vals = [Paragraph("控除額", st["cell"])] + \
               [Paragraph(y, ParagraphStyle("ny", parent=st["num"], fontSize=7.2))
                for y in d["yearly"]]
        n = len(d["yearly"]) + 1
        t = Table([cells, vals], colWidths=[W * 0.12] + [(W * 0.88) / (n - 1)] * (n - 1))
        story.append(Spacer(1, 5))
        t = _header_style(t, n, zebra=False)
        t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 2),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
        story.append(t)
    for note in d.get("notes", []):
        story.append(Paragraph(f"・{note}", st["note"]))

    # ---- 適正借入額 ----
    if ctx.get("afford"):
        a = ctx["afford"]
        _section(story, W, counter, "年収からみた借入の目安", keep=[
            _cards([("借入可能額", a["principal"]),
                    ("頭金を加えた購入可能額", a["price"])], st, W, big=False),
            Spacer(1, 5),
            _kv_table([("返済負担率の上限", f"{a['limit']}％"),
                       ("毎月の住居費の上限", a["monthly"])]
                      + ([("うち管理費・修繕積立金", "−" + a["extra"]),
                          ("返済に回せる額", a["repayment"])]
                         if a.get("extra") else []), st, W),
            Paragraph(a["note"], st["note"])])

    # 重要事項説明書で読む欄は、契約前チェックの前半（_diagnosis_pages）に
    # 移した。当日に開く順番に並べるため。

    # ---- 見本の前提 ----
    given = ctx.get("sample_given") or []
    if given:
        _section(story, W, counter, "この見本の前提")
        story.append(Paragraph(
            "実在の物件ではありません。上の金額は、すべて次の数字から"
            "計算しています。", st["note"]))
        story.append(Spacer(1, 3))
        story.append(_kv_table([(k, val) for k, val in given], st, W, ratio=0.3))

    # ---- 根拠と免責 ----
    _section(story, W, counter, "この試算の根拠")
    for src in ctx.get("sources", []):
        story.append(Paragraph(f"・{src}", st["note"]))
    story.append(Spacer(1, 8))
    story.append(_callout(
        "税率・料率は取得時点の公的資料にもとづきます。法改正で変わること、また"
        "不動産取得税の税率は標準税率であり都道府県の条例で異なり得ることに"
        "ご注意ください。実際の金額は金融機関・仲介会社・所管の税務署および"
        "都道府県にご確認ください。本試算は物件の価格を評価するものではありません。",
        st, W, color=SUB, bg=ZEBRA))

    deco = _page_decor((CHECK_TITLE if ctx.get("diag") else "詳細な資金計画") + "（PRO）")
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()
