# -*- coding: utf-8 -*-
"""資金計画のPDFレポート生成（PRO）。

日本語は ReportLab の Adobe-Japan1 CID フォント（HeiseiKakuGo-W5）で描画する。
システムライブラリを必要としないため Windows でも Render でも同じ動作になる。

注意：CIDフォントはPDFに埋め込まれない。閲覧側に日本語フォントが無い環境では
代替フォントで表示される。完全な再現が要る場合はTTFの埋め込みが必要（要検討）。
"""
from __future__ import annotations
from typing import Optional
import datetime
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)

FONT = "HeiseiKakuGo-W5"
_registered = False

INK = colors.HexColor("#1f2937")
SUB = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e5e5")
BLACK = colors.HexColor("#111111")
SOFT = colors.HexColor("#fafafa")


def _ensure_font():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
        _registered = True


def _styles():
    base = dict(fontName=FONT, textColor=INK, leading=14)
    return {
        "title": ParagraphStyle("t", fontSize=16, spaceAfter=2, **base),
        "sub": ParagraphStyle("s", fontSize=9, textColor=SUB, fontName=FONT,
                              leading=13, spaceAfter=8),
        "h2": ParagraphStyle("h", fontSize=11, spaceBefore=10, spaceAfter=5,
                             **base),
        "body": ParagraphStyle("b", fontSize=9, **base),
        "cell": ParagraphStyle("c", fontSize=8, fontName=FONT, textColor=INK,
                               leading=11),
        "cellsub": ParagraphStyle("cs", fontSize=7, fontName=FONT,
                                  textColor=SUB, leading=9.5),
        "num": ParagraphStyle("n", fontSize=8.5, fontName=FONT, textColor=INK,
                              leading=11, alignment=TA_RIGHT),
        "note": ParagraphStyle("no", fontSize=7.5, fontName=FONT, textColor=SUB,
                               leading=11),
    }


def _kv_table(rows, st, width):
    data = [[Paragraph(k, st["body"]), Paragraph(v, st["num"])] for k, v in rows]
    t = Table(data, colWidths=[width * 0.55, width * 0.45])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _header_style(t, ncols):
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build_finance_pdf(ctx: dict) -> bytes:
    """試算結果のコンテキストからPDFを組み立ててバイト列で返す。"""
    _ensure_font()
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="詳細な資金計画｜HOME INDEX PRO", author="HOME INDEX")
    W = doc.width
    s = ctx["s"]
    story = []

    # ---- 表題 ----
    story.append(Paragraph("HOME INDEX　詳細な資金計画", st["title"]))
    today = datetime.date.today().strftime("%Y年%m月%d日")
    story.append(Paragraph(
        f"作成日 {today}　／　物件価格 {s['price']}　"
        f"（{'新築建売' if s['newbuild'] else '中古'}）", st["sub"]))

    # ---- 資金の全体像 ----
    story.append(Paragraph("資金の全体像", st["h2"]))
    rows = [("物件価格", s["price"]), ("諸費用（判明分）", s["costs"]),
            ("必要総額", s["total"]), ("頭金", s["down"]),
            ("借入額", s["principal"]),
            (f"月々の返済（金利{s['rate']}％・{s['years']}年）", s["monthly"])]
    if s.get("burden"):
        rows.append(("返済負担率", f"{s['burden']}％"))
    story.append(_kv_table(rows, st, W))
    story.append(Paragraph(
        "借入額は必要総額から頭金を差し引いた額です（諸費用を借入に含める前提）。"
        "保証料と抵当権設定の登録免許税は借入額に比例するため、"
        "借入額と諸費用が釣り合うまで計算を繰り返しています。", st["note"]))

    # ---- 諸費用の内訳 ----
    story.append(Paragraph("諸費用の内訳", st["h2"]))
    data = [[Paragraph("項目", st["cellsub"]), Paragraph("金額", st["num"]),
             Paragraph("区分", st["cellsub"])]]
    for c in ctx["costs"]:
        name = Paragraph(
            f"{c['name']}<br/><font size=7 color='#6b7280'>{c['basis']}</font>",
            st["cell"])
        data.append([name, Paragraph(c["amount"], st["num"]),
                     Paragraph(c["status_ja"], st["cellsub"])])
    t = Table(data, colWidths=[W * 0.60, W * 0.25, W * 0.15], repeatRows=1)
    story.append(_header_style(t, 3))
    if ctx.get("reg_total"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"登記費用の小計：{ctx['reg_total']}", st["body"]))
    if ctx.get("unknown"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"算出していない項目：{ctx['unknown']}　"
            "情報が足りないため金額を出していません。合計にも含めていません。",
            st["note"]))

    # ---- 金利シナリオ ----
    story.append(Paragraph("金利が上がったら", st["h2"]))
    data = [[Paragraph(h, st["cellsub"]) for h in
             ("金利", "月々", "現在との差", "総返済額")]]
    for r in ctx["scenarios"]:
        data.append([Paragraph(f"{r['label']}（{r['rate']}％）", st["cell"]),
                     Paragraph(r["monthly"], st["num"]),
                     Paragraph(r["diff"], st["num"]),
                     Paragraph(r["total"], st["num"])])
    t = Table(data, colWidths=[W * 0.30, W * 0.23, W * 0.23, W * 0.24])
    story.append(_header_style(t, 4))
    story.append(Paragraph(
        "将来の予測ではなく、その金利になった場合の返済額です。", st["note"]))

    # ---- 繰上返済 ----
    if ctx.get("prepay"):
        p = ctx["prepay"]
        rows = []
        if p.get("months_saved"):
            rows.append(("返済期間の短縮", p["months_saved"]))
        rows.append(("軽減される利息", p["interest_saved"]))
        rows.append(("繰上返済後の月々", p["new_monthly"]))
        story.append(KeepTogether([
            Paragraph("繰上返済の効果", st["h2"]),
            Paragraph(f"{p['after']}年後に {p['amount']} を繰上返済した場合"
                      f"（{p['kind']}）", st["sub"]),
            _kv_table(rows, st, W)]))

    # ---- 住宅ローン控除 ----
    d = ctx["deduction"]
    story.append(Paragraph("住宅ローン控除", st["h2"]))
    story.append(Paragraph(d["basis"], st["sub"]))
    if d["ok"]:
        story.append(_kv_table(
            [("控除の見込み（最大）", d["total"]),
             ("借入限度額", d["limit"]),
             ("控除期間", f"{d['years']}年")], st, W))
        cells = [Paragraph("年目", st["cellsub"])] + \
                [Paragraph(str(i + 1), st["num"]) for i in range(len(d["yearly"]))]
        vals = [Paragraph("控除額", st["cell"])] + \
               [Paragraph(y, st["num"]) for y in d["yearly"]]
        n = len(d["yearly"]) + 1
        t = Table([cells, vals], colWidths=[W * 0.16] + [(W * 0.84) / (n - 1)] * (n - 1))
        story.append(Spacer(1, 4))
        story.append(_header_style(t, n))
    for note in d.get("notes", []):
        story.append(Paragraph(f"・{note}", st["note"]))

    # ---- 適正借入額 ----
    if ctx.get("afford"):
        a = ctx["afford"]
        story.append(KeepTogether([
            Paragraph("年収からみた借入の目安", st["h2"]),
            _kv_table([("返済負担率の上限", f"{a['limit']}％"),
                       ("月々返済の上限", a["monthly"]),
                       ("借入可能額", a["principal"]),
                       ("頭金を加えた購入可能額", a["price"])], st, W),
            Paragraph(a["note"], st["note"])]))

    # ---- 根拠と免責 ----
    story.append(Paragraph("この試算の根拠", st["h2"]))
    for src in ctx.get("sources", []):
        story.append(Paragraph(f"・{src}", st["note"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "税率・料率は取得時点の公的資料にもとづきます。法改正で変わること、また"
        "不動産取得税の税率は標準税率であり都道府県の条例で異なり得ることに"
        "ご注意ください。実際の金額は金融機関・仲介会社・所管の税務署および"
        "都道府県にご確認ください。本試算は物件の価格を評価するものではありません。",
        st["note"]))

    def _footer(canv, _doc):
        canv.saveState()
        canv.setFont(FONT, 7)
        canv.setFillColor(SUB)
        canv.drawString(18 * mm, 10 * mm,
                        "HOME INDEX　詳細な資金計画（PRO）　参考情報であり、"
                        "最終判断は専門家の確認を前提としてください。")
        canv.drawRightString(A4[0] - 18 * mm, 10 * mm, f"- {canv.getPageNumber()} -")
        canv.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
