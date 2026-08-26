# -*- coding: utf-8 -*-
"""物件情報テキストの自動抽出（Phase：入力自動化）。

SUUMO等の物件説明を「貼り付け」→ 価格・面積・築年・間取り・住所・駅などを抽出。
指示書 第33章：無断スクレイピングを基盤にしない。本抽出はユーザー自身が
コピーしたテキスト（私的利用）を解析する。URL取得は実験的な補助。

抽出できない項目は None（勝手に埋めない・第14章）。最終はユーザーが確認・修正する
（第36章 Step3「この物件で合っていますか？」）。
"""
from __future__ import annotations
import re
from typing import Optional, Dict

TSUBO = 3.305785  # 1坪=㎡

# 47都道府県（citycode.py と共有）
PREFECTURES = {
    "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県", "05": "秋田県",
    "06": "山形県", "07": "福島県", "08": "茨城県", "09": "栃木県", "10": "群馬県",
    "11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県", "15": "新潟県",
    "16": "富山県", "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
    "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県", "25": "滋賀県",
    "26": "京都府", "27": "大阪府", "28": "兵庫県", "29": "奈良県", "30": "和歌山県",
    "31": "鳥取県", "32": "島根県", "33": "岡山県", "34": "広島県", "35": "山口県",
    "36": "徳島県", "37": "香川県", "38": "愛媛県", "39": "高知県", "40": "福岡県",
    "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県", "45": "宮崎県",
    "46": "鹿児島県", "47": "沖縄県",
}
_PREF_RE = "(?:" + "|".join(PREFECTURES.values()) + ")"

# 神奈川県 主要市区町村コード（西湘・足柄中心＋主要市）
KANAGAWA_CITY_CODES = {
    "横須賀市": "14201", "平塚市": "14203", "鎌倉市": "14204", "藤沢市": "14205",
    "小田原市": "14206", "茅ヶ崎市": "14207", "逗子市": "14208", "三浦市": "14210",
    "秦野市": "14211", "厚木市": "14212", "大和市": "14213", "伊勢原市": "14214",
    "海老名市": "14215", "座間市": "14216", "南足柄市": "14217", "綾瀬市": "14218",
    "葉山町": "14301", "寒川町": "14321", "大磯町": "14341", "二宮町": "14342",
    "中井町": "14361", "大井町": "14362", "松田町": "14363", "山北町": "14364",
    "開成町": "14366", "箱根町": "14382", "真鶴町": "14384", "湯河原町": "14385",
    "愛川町": "14401", "清川村": "14402",
}


def _z2h(s: str) -> str:
    """全角英数記号を半角へ。"""
    if not s:
        return ""
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF10 <= o <= 0xFF19 or 0xFF21 <= o <= 0xFF3A or 0xFF41 <= o <= 0xFF5A:
            out.append(chr(o - 0xFEE0))
        elif ch == "，":
            out.append(",")
        elif ch == "．":
            out.append(".")
        else:
            out.append(ch)
    return "".join(out)


def _parse_price_man(t: str) -> Optional[int]:
    """価格を『万円』単位で返す。億対応。"""
    m = re.search(r"([0-9]+)\s*億\s*([0-9,]+)?\s*万?円", t)
    if m:
        oku = int(m.group(1))
        man = int((m.group(2) or "0").replace(",", "") or 0)
        return oku * 10000 + man
    m = re.search(r"([0-9,]{3,})\s*万円", t)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


_UNIT = r"(㎡|m²|m2|ｍ2|平米|平方メートル|坪)"


def _parse_area(t: str, labels) -> Optional[float]:
    # ラベルと数値が改行・括弧・記号で離れていても拾う（非貪欲・数値以外を最大12字許容）
    for lab in labels:
        m = re.search(re.escape(lab) + r"[^\d]{0,12}?([0-9]{1,4}(?:\.[0-9]+)?)\s*"
                      + _UNIT, t)
        if m:
            val = float(m.group(1))
            if m.group(2) == "坪":
                val = round(val * TSUBO, 2)
            return val
    return None


# リフォームは「済み」と「可・要」で意味が逆になる。取り違えると価格の
# 上乗せまで狂うので、済みを表す言い方を先に見て、無ければ否定形を見る。
_RENO_DONE = ("リフォーム済", "リノベーション済", "リノベ済", "フルリフォーム",
              "フルリノベ", "改装済", "内装リフォーム")
_RENO_NOT = ("リフォーム可", "要リフォーム", "リフォーム前", "リフォーム相談",
             "リフォーム不可", "リノベーション可")


def _parse_renovated(t: str) -> Optional[bool]:
    """リフォーム済みか。記載が無ければ None（不明のまま返す）。"""
    for w in _RENO_DONE:
        if w in t:
            return True
    for w in _RENO_NOT:
        if w in t:
            return False
    return None


def _parse_layout(t: str) -> Optional[str]:
    m = re.search(r"([1-9][0-9]?)\s*(S?LDK|S?DK|S?K|LDK|DK)", t)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


def _parse_build_year(t: str) -> Optional[int]:
    import datetime
    cy = datetime.date.today().year
    for pat in (r"(?:築年月|建築年月|完成年月|完成時期|竣工年月|建築年|入居)"
                r"[^\d]{0,10}?((?:19|20)\d{2})\s*年",
                r"((?:19|20)\d{2})\s*年\s*\d{0,2}\s*月?\s*(?:築|新築|建築|完成|竣工|引渡)",
                r"(?:築|新築|建築)\s*((?:19|20)\d{2})\s*年",
                r"((?:19|20)\d{2})\s*年\s*築"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    m = re.search(r"築\s*([0-9]{1,2})\s*年", t)  # 築N年
    if m:
        return cy - int(m.group(1))
    for era, base in (("令和", 2018), ("平成", 1988), ("昭和", 1925)):
        m = re.search(era + r"\s*([0-9]{1,2})\s*年", t)
        if m:
            return base + int(m.group(1))
    return None


def _parse_station_walk(t: str):
    walk = None
    name = None
    m = re.search(r"(?:徒歩|歩)\s*(?:約)?\s*([0-9]{1,3})\s*分", t)
    if m:
        walk = int(m.group(1))
    m2 = re.search(r"([^\s　\n/／｜|、,。]{1,14}駅)", t)
    if m2:
        name = m2.group(1)
    if walk is None:  # 距離(m)表記からの推定
        m3 = re.search(r"駅[^0-9]{0,10}?([0-9]{3,4})\s*m", t)
        if m3:
            walk = max(1, round(int(m3.group(1)) / 80.0))
    return walk, name


def _parse_bus(t: str) -> Optional[int]:
    """バス便：駅までのバス乗車分。バス表記が無ければ None。"""
    m = re.search(r"バス\s*(?:約)?\s*([0-9]{1,2})\s*分", t)
    if m:
        return int(m.group(1))
    return None


def _parse_ptype(t: str, byear: Optional[int]) -> Optional[str]:
    """種別判定。中古/新築の明示があればそれに従い、無ければ築年で判断。
    築年が記載されていない戸建は『新築』と判断する（実務ルール）。"""
    is_kodate = ("戸建" in t or "一戸建" in t)
    if "中古" in t:
        return "chuko_kodate"
    if "新築" in t:
        return "shinchiku_kodate"
    if is_kodate:
        # 築年無記名の戸建は新築とみなす
        return "chuko_kodate" if byear else "shinchiku_kodate"
    # 種別語が無くても築年が無ければ新築寄り
    return None if byear else "shinchiku_kodate"


def _parse_address(t: str):
    addr = None
    m = re.search(r"(" + _PREF_RE + r"[^\s　\n,、／/｜|]+)", t)
    if m:
        addr = m.group(1)
    city_code = None
    district = None
    for name, code in KANAGAWA_CITY_CODES.items():
        if name in t:
            city_code = code
            mm = re.search(re.escape(name) + r"([一-龥ぁ-んァ-ヶー]+)", t)
            if mm:
                district = mm.group(1)[:8]
            break
    return addr, city_code, district


def parse_listing_text(text: str) -> Dict[str, object]:
    """貼り付けテキストから物件項目を抽出。値が無いものは None。"""
    import datetime
    t = _z2h(text or "")
    walk, station_name = _parse_station_walk(t)
    addr, city, district = _parse_address(t)
    byear = _parse_build_year(t)
    ptype = _parse_ptype(t, byear)
    # 新築で築年が無ければ、築年＝現在年（築0年相当）を補完
    if ptype == "shinchiku_kodate" and byear is None:
        byear = datetime.date.today().year
    return {
        "price_man": _parse_price_man(t),
        "land": _parse_area(t, ["土地面積", "敷地面積", "土地"]),
        "building": _parse_area(t, ["建物面積", "延床面積", "延べ床面積", "建物"]),
        "layout": _parse_layout(t),
        "byear": byear,
        "station": walk,
        "station_name": station_name,
        "bus": _parse_bus(t),
        "ptype": ptype,
        "renovated": _parse_renovated(t),
        "address": addr,
        "city": city,
        "district": district,
    }


# ---- マンション向けの読み取り ----------------------------------------
# 戸建と共通の部品（価格・住所・築年・駅徒歩・間取り・面積）はそのまま使い、
# マンション固有の項目だけここに足す。

DIRECTIONS_8 = ("南東", "南西", "北東", "北西", "南", "北", "東", "西")


def _parse_monthly_yen(t: str, labels, exclude=()) -> Optional[int]:
    """「管理費 12,000円」のような月額を円で拾う。

    価格は万円だがこれらは円で書かれるので、万円のパーサとは分けてある。

    判定は行単位で行う。前後の行まで覗くと、「修繕積立金」の隣にある
    「修繕積立基金」や、別項目の「駐車場の管理費」を巻き込んで、正しい
    月額まで捨ててしまう。ただしPDFではラベルと金額が改行で切れることが
    あるので、値は次の行までを見る。

    exclude は、その行にあれば別物とみなす語（駐車場の管理費、購入時の
    一括金である修繕積立基金など）。
    """
    lines = t.splitlines()
    for lab in labels:
        for i, line in enumerate(lines):
            if lab not in line or any(x in line for x in exclude):
                continue
            segment = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            m = re.search(re.escape(lab) + r"[^\d]{0,24}?([0-9][0-9,]{2,8})\s*円",
                          segment)
            if m:
                try:
                    return int(m.group(1).replace(",", ""))
                except ValueError:
                    continue
    return None

def _parse_floors(t: str):
    """所在階と総階数。「3階/5階建」のような並記を最優先で読む。

    「5階建」だけなら総階数、「所在階3階」なら所在階。どちらか分からない
    「3階」単独は、取り違えると評価が変わるので拾わない。
    """
    floor = total = None
    m = re.search(r"([0-9]{1,2})\s*階\s*[/／]\s*(?:地上)?([0-9]{1,2})\s*階建", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"所在階[^\d]{0,8}?([0-9]{1,2})\s*階", t)
    if m:
        floor = int(m.group(1))
    m = re.search(r"(?:地上)?([0-9]{1,2})\s*階建", t)
    if m:
        total = int(m.group(1))
    if floor is None:
        m = re.search(r"([0-9]{1,2})\s*階\s*部分", t)
        if m:
            floor = int(m.group(1))
    return floor, total


def _parse_direction(t: str) -> Optional[str]:
    """向き。2文字の方位（南東など）を先に見る。"""
    for d in DIRECTIONS_8:
        if re.search(re.escape(d) + r"\s*向き", t):
            return d
    m = re.search(r"(?:バルコニー|採光|開口部?)[^。\n]{0,12}?"
                  r"(南東|南西|北東|北西|南|北|東|西)", t)
    if m:
        return m.group(1)
    return None


def looks_like_mansion(t: str) -> Optional[bool]:
    """貼り付けたのがマンションの物件かどうか。判断できなければ None。

    戸建のページを貼ってしまったときに気づけるようにするためのもの。
    """
    t = _z2h(t or "")
    if re.search(r"マンション|区分所有|専有面積", t):
        return True
    if re.search(r"一戸建|戸建|土地面積|建ぺい率", t):
        return False
    return None


def parse_mansion_text(text: str) -> Dict[str, object]:
    """貼り付けテキストからマンションの項目を抽出。値が無いものは None。"""
    t = _z2h(text or "")
    walk, station_name = _parse_station_walk(t)
    addr, city, district = _parse_address(t)
    floor, total_floors = _parse_floors(t)
    return {
        "price_man": _parse_price_man(t),
        # バルコニーを拾わないよう、専有と明記されたものだけを見る
        "area": _parse_area(t, ["専有面積", "専有部分の面積", "専有"]),
        "layout": _parse_layout(t),
        "byear": _parse_build_year(t),
        "station": walk,
        "station_name": station_name,
        "floor": floor,
        "total_floors": total_floors,
        "direction": _parse_direction(t),
        # 「修繕積立基金」は購入時の一括金なので月額と混ぜない
        "renovated": _parse_renovated(t),
        "mfee": _parse_monthly_yen(t, ["管理費"],
                                   exclude=("駐車", "駐輪", "バイク", "トランク")),
        "rfund": _parse_monthly_yen(t, ["修繕積立金", "修繕費"],
                                    exclude=("基金", "一時金", "駐車")),
        "address": addr,
        "city": city,
        "district": district,
        "is_mansion": looks_like_mansion(text),
    }


def extract_from_url(url: str) -> Dict[str, object]:
    """URLのページを取得しテキスト抽出→parse（実験的・best-effort）。
    公開メタ情報とページテキストのみを解析する。"""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JutakuShindan/0.1)"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    html = r.text
    # JSON-LD / og:title / description を軽く拾ってテキストに合流
    extra = " ".join(re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html,
                                re.S))
    metas = " ".join(re.findall(r'<meta[^>]+content="([^"]+)"', html))
    # タグ除去してテキスト化
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return parse_listing_text(" ".join([extra, metas, text]))

def extract_from_pdf(file_or_path) -> str:
    """販売図面などのPDFからテキストを抽出して返す（parse前の生テキスト）。
    テキストで作られたPDFのみ対応（スキャン画像PDFは空文字を返す）。
    file_or_path: ファイルパス(str) か 開いたファイルオブジェクト。"""
    import pdfplumber
    parts = []
    with pdfplumber.open(file_or_path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if txt:
                parts.append(txt)
    return "\n".join(parts)
