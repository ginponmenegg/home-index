# -*- coding: utf-8 -*-
"""PDFにBIZ UDフォントが埋め込まれていること（2026-11-23）。

fonts/ のファイルが消えると、標準のCIDフォントに黙って戻る。
そうなったら気づけるように、作ったPDFの字体名を見る。
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_font_files_and_licenses_are_in_the_repository():
    for f in ("BIZUDPGothic-Regular.ttf", "BIZUDPGothic-Bold.ttf",
              "BIZUDPMincho-Regular.ttf", "OFL-BIZUDPGothic.txt",
              "OFL-BIZUDPMincho.txt"):
        assert os.path.exists(os.path.join(ROOT, "fonts", f)), f


def test_the_sample_pdf_embeds_biz_ud():
    pdfplumber = pytest.importorskip("pdfplumber")
    os.environ["SHINDAN_MOCK"] = "1"
    import app
    r = app.app.test_client().get("/sample/finance.pdf")
    with pdfplumber.open(io.BytesIO(r.data)) as pdf:
        names = {c["fontname"] for p in pdf.pages for c in p.chars}
    assert any("BIZUDPGothic" in n for n in names), names
    assert any("BIZUDPMincho" in n for n in names), names
    assert not any("HeiseiKakuGo" in n for n in names), names
