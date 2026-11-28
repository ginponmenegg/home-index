# -*- coding: utf-8 -*-
"""結果画面に出す「契約前チェックの見本」の画像を作る（2026-11-28）。

    python tools/make_pro_sample.py

/sample/finance.pdf（実在しない見本の物件）の1ページ目と3ページ目を
PNGにして static/ に置く。PDFの見た目を変えたら作り直す。
手元でだけ使う（pypdfium2 が要る。本番には入れない）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SHINDAN_MOCK", "1")


def main():
    import pypdfium2 as pdfium
    import app
    pdf_bytes = app.app.test_client().get("/sample/finance.pdf").data
    pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    for page_no in (1, 3):
        img = pdf[page_no - 1].render(scale=0.9).to_pil().convert("RGB")
        # 上の半分ほどで十分伝わる。縦に長いと結果画面が間延びする。
        w, h = img.size
        img = img.crop((0, 0, w, int(h * 0.62)))
        out = os.path.join(ROOT, "static", f"pro_sample_p{page_no}.png")
        img.save(out, optimize=True)
        print("wrote", out, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main()
