# -*- coding: utf-8 -*-
"""画面で使う線画アイコン。

外部のアイコン用フォント（Font Awesome など）は読み込まない。数百個の
字形を1つ使うために読み込むことになり、表示も遅れる。ここに必要な分だけ
SVGのパスを持ち、文字と同じ色（currentColor）で描く。

すべて 24×24 の枠に、線の太さ 1.8 で描いている。形は自前で引いたもの。
"""
from __future__ import annotations

from markupsafe import Markup

_PATHS = {
    # 診断の種類
    "house": "M4 10.5 12 4l8 6.5V20H4z M10 20v-5h4v5",
    "building": "M5 21V4h10v17 M15 9h4v12 M8.5 8h3 M8.5 12h3 M8.5 16h3 M3 21h18",
    "land": "M3 7l6-3 6 3 6-3v13l-6 3-6-3-6 3z M9 4v13 M15 7v13",
    # 採点の区分
    "yen": "M7 4l5 7 5-7 M12 11v9 M8 13h8 M8 16.5h8",
    "trend": "M4 20h16 M5 16l4.5-5 3.5 3.5L19 7 M15 7h4v4",
    "ruler": "M3 17 17 3l4 4L7 21z M7 13l2 2 M10 10l2 2 M13 7l2 2",
    "people": ("M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M3 20c0-3 2.7-5 6-5s6 2 6 5 "
               "M16 5.3a3 3 0 0 1 0 5.4 M18 15.2c1.8.6 3 2.3 3 4.8"),
    "road": "M8 3 5 21 M16 3l3 18 M12 5v2 M12 11v2 M12 17v2",
    "pin": ("M12 21s-7-6.2-7-12a7 7 0 0 1 14 0c0 5.8-7 12-7 12z "
            "M12 11.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"),
    "shield": "M12 3 5 6v5c0 4.4 3 8.3 7 10 4-1.7 7-5.6 7-10V6z M12 8v5 M12 16h.01",
    "wallet": ("M3 7a2 2 0 0 1 2-2h12v4 M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2z "
               "M16 14.5h1.5"),
    # 画面の行き先
    "compare": "M4 5h6v14H4z M14 5h6v14h-6z",
    "bookmark": "M6 3h12v18l-6-4-6 4z",
    "book": "M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z M5 19.5A1.5 1.5 0 0 0 6.5 21H19 M9 7h6",
    "user": "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21c0-4 3.6-6 8-6s8 2 8 6",
    "sparkle": ("M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z "
                "M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"),
    "doc": "M6 3h9l4 4v14H6z M15 3v4h4 M9 12h7 M9 16h7",
    "lock": "M6 11h12v10H6z M8.5 11V8a3.5 3.5 0 0 1 7 0v3",
    "home": "M3 11l9-7 9 7 M5 9.5V20h5v-5h4v5h5V9.5",
    # 結果のまとめ・小札
    "check": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M8 12.5l2.7 2.7L16 9.6",
    "alert": "M12 4 2.8 20h18.4z M12 10v4 M12 17h.01",
    "help": ("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z "
             "M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .9-1 1.6v.4 M12 17h.01"),
    "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3.2 2",
    "tag": "M3 12V4h8l10 10-8 8z M7.5 8h.01",
    "image": "M4 5h16v14H4z M4 15l4.5-4.5 4 4 2.5-2.5L20 17 M15.5 9.5h.01",
    "share": ("M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z "
              "M18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M8.6 13.5l6.8 4 M15.4 6.5l-6.8 4"),
    "arrow": "M5 12h14 M13 6l6 6-6 6",
}

# 採点の区分名 → アイコン。戸建・マンション・土地で使う名前をすべて持つ。
CATEGORY = {
    "物件": "house", "価格": "yen", "資産性": "trend", "建てられる家": "ruler",
    "管理": "people", "接道": "road", "立地": "pin", "リスク": "shield",
    "資金": "wallet",
}

# メニューの行き先 → アイコン
MENU = {
    "/": "home", "/buy": "house", "/mansion": "building", "/land": "land",
    "/plan": "sparkle", "/mypage": "user", "/guide": "book",
    "/terms": "doc", "/privacy": "lock", "/compare": "compare",
    "/pro/diagnose": "sparkle", "/pro/mansion": "sparkle", "/pro/land": "sparkle",
    "/pro/finance": "wallet",
}

CSS = (".ic{width:1.15em;height:1.15em;flex:0 0 auto;vertical-align:-.2em;"
       "fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;"
       "stroke-linejoin:round}")


def icon(name: str, cls: str = "ic") -> Markup:
    """アイコン1つ。知らない名前なら何も出さない（文字だけで読めるようにしておく）。"""
    d = _PATHS.get(name)
    if not d:
        return Markup("")
    return Markup(f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">'
                  f'<path d="{d}"/></svg>')


def for_category(name: str) -> Markup:
    return icon(CATEGORY.get(name, ""))


def for_path(href: str) -> Markup:
    return icon(MENU.get(href, ""))
