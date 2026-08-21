# -*- coding: utf-8 -*-
"""OGP画像（static/ogp.png）とアイコン（static/icon-180.png）を焼く。

LPのヒーローと同じ絵柄（濃紺＋等高線＋区画＋物件ピン）をPillowで描く。
実行時に生成しないのは、本番のコンテナに日本語フォントが無いため。
Windows上でこのスクリプトを走らせ、出来たPNGをコミットする。

    python tools/make_images.py

SNS各社はOG画像をキャッシュするので、差し替えたら og:image のクエリ
（?v=2 など）を上げるか、各社のデバッガでキャッシュを破棄すること。
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "static", "ogp.png")

BG = (12, 27, 42)          # --hero-bg
BG_LIGHT = (17, 37, 55)    # --hero-bg-2
PAPER = (240, 238, 233)    # --paper
PIN = (224, 168, 63)       # --pin

FONTS = r"C:\Windows\Fonts"
F_NOTO = os.path.join(FONTS, "NotoSansJP-VF.ttf")   # 見出し・本文（OFL）
F_MINCHO = os.path.join(FONTS, "yumindb.ttf")       # タグライン（明朝）
F_GEO = os.path.join(FONTS, "GOTHICB.TTF")          # 欧文ワードマーク（Century Gothic）


def noto(size, weight="Regular"):
    f = ImageFont.truetype(F_NOTO, size)
    f.set_variation_by_name(weight)
    return f


def seeded(s):
    """毎回同じ絵にするための線形合同法。LPのcanvasと同じ考え方。"""
    def nxt():
        nonlocal s
        s = (s * 1664525 + 1013904223) % 4294967296
        return s / 4294967296
    return nxt


def draw_tracked(draw, xy, text, font, fill, tracking=0):
    """字間を空けて描く。Pillowにletter-spacingが無いので1文字ずつ進める。"""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def bezier(p0, p1, p2, p3, n=60):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return pts


def build():
    img = Image.new("RGB", (W, H), BG)

    # 右上のほのかな明るみ（LPの radial-gradient 相当）
    glow = Image.radial_gradient("L").resize((int(W * 1.5), int(H * 2.2)))
    glow = Image.eval(glow, lambda v: max(0, 235 - v))
    layer = Image.new("RGB", (W, H), BG_LIGHT)
    mask = Image.new("L", (W, H), 0)
    mask.paste(glow, (int(W * 0.78 - W * 0.75), int(H * 0.18 - H * 1.1)))
    img = Image.composite(layer, img, mask)

    art = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(art)

    # 等高線
    for i in range(-6, 26):
        y0 = H * 0.12 + i * (H * 0.052)
        pts = []
        for x in range(-20, W + 21, 6):
            t = x / W
            y = (y0
                 + math.sin(t * 3.1 + i * 0.22) * (H * 0.075)
                 + math.sin(t * 7.4 + i * 0.11) * (H * 0.026)
                 + math.sin(t * 1.7 - i * 0.30) * (H * 0.045))
            pts.append((x, y))
        col = (168, 203, 233, 77) if i % 5 == 0 else (150, 187, 220, 38)
        d.line(pts, fill=col, width=1)

    # 区画（宅地の割付）。原点を移して少し傾ける
    rnd = seeded(20260820)
    ox, oy, ang = W * 0.60, H * 0.42, -0.19
    cos_a, sin_a = math.cos(ang), math.sin(ang)

    def place(px, py):
        return (ox + px * cos_a - py * sin_a, oy + px * sin_a + py * cos_a)

    for r in range(5):
        for c in range(7):
            if rnd() < 0.24:
                continue
            pw = W * 0.052 * (0.7 + rnd() * 0.6)
            ph = H * 0.10 * (0.7 + rnd() * 0.5)
            px, py = (c - 3) * (W * 0.058), (r - 2) * (H * 0.115)
            quad = [place(px, py), place(px + pw, py),
                    place(px + pw, py + ph), place(px, py + ph)]
            fill = (198, 222, 244, 15) if rnd() < 0.22 else None
            d.polygon(quad, outline=(198, 222, 244, 51), fill=fill)

    # 道路
    road = (226, 238, 250, 33)
    d.line(bezier((-10, H * 0.74), (W * 0.3, H * 0.62), (W * 0.55, H * 0.58), (W + 10, H * 0.30)),
           fill=road, width=3)
    d.line(bezier((W * 0.36, -10), (W * 0.42, H * 0.35), (W * 0.66, H * 0.52), (W + 10, H * 0.80)),
           fill=road, width=3)

    img = Image.alpha_composite(img.convert("RGBA"), art)

    # 文字を読ませるための覆い（左からと下から）
    veil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    for x in range(W):
        a = int(203 * max(0.0, 1 - (x / (W * 0.66)) ** 1.4))
        vd.line([(x, 0), (x, H)], fill=BG + (a,))
    for y in range(H):
        a = int(140 * max(0.0, (y - H * 0.55) / (H * 0.45)) ** 1.2)
        vd.line([(0, y), (W, y)], fill=BG + (a,))
    img = Image.alpha_composite(img, veil).convert("RGB")

    d = ImageDraw.Draw(img)

    # 物件ピン。見出しに重ならないよう右上の余白へ置き、ラベルは左側に流す
    px, py = 1086, 104
    for rr, a in ((30, 60), (19, 110)):
        d.ellipse([px - rr, py - rr, px + rr, py + rr], outline=PIN + (a,), width=2)
    d.ellipse([px - 7, py - 7, px + 7, py + 7], fill=PIN)
    f_pin = noto(20, "Medium")
    label = "検討中の物件"
    d.text((px - 42 - d.textlength(label, font=f_pin), py - 13), label, font=f_pin, fill=PIN)

    # 文字組み
    L = 78
    draw_tracked(d, (L, 118), "住宅購入 セカンドオピニオン", noto(21, "Medium"),
                 (240, 238, 233, 190), tracking=3)

    f_h1 = noto(92, "Black")
    d.text((L - 4, 172), "この家、かっていい？", font=f_h1, fill=PAPER)

    f_tag = ImageFont.truetype(F_MINCHO, 40)
    d.text((L, 316), "買う前に、", font=f_tag, fill=PAPER)
    x_em = L + d.textlength("買う前に、", font=f_tag)
    em = "データで答え合わせ。"
    d.text((x_em, 316), em, font=f_tag, fill=PAPER)
    w_em = d.textlength(em, font=f_tag)
    d.line([(x_em, 368), (x_em + w_em, 368)], fill=PIN, width=2)

    d.text((L, 400), "気になる物件の価格・災害リスク・返済を、公的データで100点に採点します。",
           font=noto(24), fill=(197, 205, 214))

    # 下段：ワードマークと出典
    d.line([(L, 492), (W - L, 492)], fill=(60, 82, 105), width=1)
    x = draw_tracked(d, (L, 520), "HOME INDEX", ImageFont.truetype(F_GEO, 30),
                     PAPER, tracking=4)
    d.text((x + 26, 528), "国土交通省 不動産情報ライブラリ ／ 総務省 e-Stat ／ 国土地理院",
           font=noto(19), fill=(150, 164, 180))

    img.save(OUT, "PNG", optimize=True)
    print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")


def build_icon():
    """iOSのホーム画面用アイコン（180x180）。SVGのfaviconを読まない環境の受け皿でもある。

    絵柄は static/favicon.svg と同じで、ブランドシンボル（家×棒グラフ）。
    """
    size, pad = 180, 24
    s = (size - pad * 2) / 120.0            # シンボルは120単位で描かれている
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.2), fill=BG)

    def p(x, y):
        return (pad + x * s, pad + (y - 4) * s)

    house = [p(60, 5), p(108, 48), p(108, 112), p(12, 112), p(12, 48), p(60, 5)]
    d.line(house, fill=PAPER, width=max(2, round(9 * s)), joint="curve")
    for x, y, w, h in ((12, 100, 96, 12), (30, 84, 12, 16), (48, 72, 12, 28),
                       (66, 62, 12, 38), (84, 78, 12, 22)):
        x0, y0 = p(x, y)
        x1, y1 = p(x + w, y + h)
        d.rectangle([x0, y0, x1, y1], fill=PAPER)

    out = os.path.join(ROOT, "static", "icon-180.png")
    img.save(out, "PNG", optimize=True)
    print("wrote", out, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    build()
    build_icon()
