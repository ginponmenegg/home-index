# -*- coding: utf-8 -*-
"""診断された物件の記録。あとで地域ごとの傾向を読むために残す。

■なぜ残すか
無料で診断を配っても、こちらには何も残っていなかった。どの地域の、
どんな物件が、どう検討されているか。これはこのサービスにしか無い
データで、改善の材料にも、他に書けない記事にもなる。

■個人の記録にしないために
残さないものを先に決める。**利用者を結びつける鍵を一切持たない。**
ユーザーID・セッション・IPアドレスを入れない以上、1行は「誰かの行動」
ではなく「物件の観測」になる。同じ人が3件診断しても、別々の3行として
並ぶだけで、繋ぐ手段がこちら側に無い。

住所は**町名まで**。丁目・番地は落とす（split_address）。この粒度は、
こちらが毎日受け取っている国土交通省の取引価格情報と同じ。自分が
使っている公開データより細かくはしない、という線。

**世帯年収・頭金・他の借入は入れない。** 家計の入力は外部にも送って
いないし、ここにも残さない。

■消す
3年で消す。プライバシーポリシーに書いた期間なので、勝手に延ばさない。
"""
from __future__ import annotations

import datetime
import re
from typing import Optional

from . import db

JST = datetime.timezone(datetime.timedelta(hours=9))

# プライバシーポリシーに書いた保存期間。片方だけ変えない。
KEEP_DAYS = 365 * 3

# 都道府県。「京都府」を「京都」+「府」と切らないよう、先に固有名を並べる。
_PREF = re.compile(r"^(東京都|北海道|京都府|大阪府|.{2,3}?県)\s*(.*)$")

# 市区町村は、上から順に当てる。順番に意味がある。
#   1. 郡があるなら郡ごと（神崎郡市川町。「市川町」を「市」で切らない）
#   2. 政令指定都市は区まで（札幌市中央区）
#   3. 市があるなら市まで（武蔵村山市。先に村で切ると「武蔵村」になる）
#   4. 残りは区・町・村
# 市区町村の正しい単位は city_code（不動産情報ライブラリ由来）のほうで、
# ここは人が読むための補助。取り違えても集計の鍵は壊れない。
# 「市」で終わる名前に「市」が続く3つ。規則で解くと四日市＋市諏訪町に
# なるので、名指しで先に当てる。町名が「市」で始まる場合を巻き込まない。
_DOUBLE = ("四日市市", "廿日市市", "野々市市")

_CITY_RULES = (
    re.compile(r"^(.{1,8}?郡.{1,10}?[町村])(.*)$"),
    re.compile(r"^(.{1,10}?市.{1,6}?区)(.*)$"),
    re.compile(r"^(.{1,10}?市)(.*)$"),
    re.compile(r"^(.{1,10}?[区町村])(.*)$"),
)

# 丁目より後ろ。漢数字は町名にも使われる（四谷・三田）ので、
# 「丁目」という語が続くときだけ落とす。
_CHOME = re.compile(r"[一二三四五六七八九十〇零壱弐参]*丁目.*$")


def split_address(address: Optional[str]):
    """住所を (都道府県, 市区町村, 町名) に分ける。丁目・番地は捨てる。

    分けられなければ、その要素は None。**推測で埋めない。**
    """
    s = (address or "").strip().replace("　", " ")
    if not s:
        return None, None, None
    m = _PREF.match(s)
    if not m:
        return None, None, None
    pref, rest = m.group(1), m.group(2)
    city = None
    for name in _DOUBLE:
        if rest.startswith(name):
            return pref, name, _town(rest[len(name):])
    for rule in _CITY_RULES:
        mm = rule.match(rest)
        if mm:
            city, rest = mm.group(1), mm.group(2)
            break
    return pref, city, _town(rest)


def _town(rest: str):
    """残りから町名だけ取る。数字が出たら、そこから後ろは番地。"""
    rest = re.split(r"[0-9０-９]", rest, 1)[0]
    rest = _CHOME.sub("", rest).strip(" -‐－―ー")
    return rest or None


def _today() -> datetime.date:
    return datetime.datetime.now(JST).date()


def _num(v):
    """数として入れてよい値だけ通す。文字列や空は None にする。"""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN を弾く


def record(kind: str, subject, diagnosis, enrichment=None,
           city_code: Optional[str] = None) -> None:
    """1件ぶんを残す。失敗しても診断を巻き添えにしない。"""
    if not db.enabled():
        return
    try:
        pref, city, district = split_address(getattr(subject, "address", None))
        risks = ";".join(
            sorted({r.type for r in (getattr(diagnosis, "critical_risks", None)
                                     or [])}))
        cats = ";".join(
            f"{c.name}:{c.points}" for c in (getattr(diagnosis, "categories",
                                                     None) or []))
        db.run(
            "INSERT INTO observations (day, kind, ptype, pref, city,"
            " city_code, district, price_yen, land_m2, building_m2,"
            " build_year, station_min, use_district, total_score, grade,"
            " sufficiency, risks, categories)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_today().isoformat(), kind,
             getattr(subject, "property_type", None),
             pref, city, city_code, district,
             _num(getattr(subject, "price", None)),
             _num(getattr(subject, "land_area_m2", None)),
             _num(getattr(subject, "building_area_m2", None)
                  or getattr(subject, "exclusive_area_m2", None)),
             _num(getattr(subject, "build_year", None)),
             _num(getattr(subject, "station_walk_min", None)),
             (getattr(enrichment, "use_district", None) if enrichment else None),
             _num(getattr(diagnosis, "total_score", None)),
             getattr(diagnosis, "grade", None),
             _num(getattr(diagnosis, "data_sufficiency", None)),
             risks or None, cats or None))
    except Exception as e:                                   # pragma: no cover
        print(f"[observations] 残せませんでした: {e}")
    _purge_once_a_day()


_purged_on: Optional[str] = None


def _purge_once_a_day() -> None:
    """期限切れを消す。process ごとに1日1回で足りる。"""
    global _purged_on
    today = _today().isoformat()
    if _purged_on == today:
        return
    _purged_on = today
    purge()


def purge() -> int:
    """保存期間を過ぎた行を消す。消した件数を返す。"""
    if not db.enabled():
        return 0
    limit = (_today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    try:
        db.run("DELETE FROM observations WHERE day < ?", (limit,))
    except Exception as e:                                   # pragma: no cover
        print(f"[observations] 古い行を消せませんでした: {e}")
        return 0
    return 1


COLUMNS = ("day", "kind", "ptype", "pref", "city", "city_code", "district",
           "price_yen", "land_m2", "building_m2", "build_year",
           "station_min", "use_district", "total_score", "grade",
           "sufficiency", "risks", "categories")


def rows(limit: int = 50000):
    """新しい順に返す。書き出し用。"""
    if not db.enabled():
        return []
    return db.run(
        f"SELECT {', '.join(COLUMNS)} FROM observations"
        " ORDER BY day DESC, rowid DESC LIMIT ?", (int(limit),), "all") or []


def count() -> int:
    if not db.enabled():
        return 0
    r = db.run("SELECT COUNT(*) AS n FROM observations", (), "one")
    return int(r["n"]) if r else 0


# ---- 集計 ------------------------------------------------------------------
# CSVを開かないと何も分からない、では見ない。よく見るものは画面に出す。
# どれも日付で絞れるようにしてあるのは、記事に使うときに「直近1年」の
# ように期間を言えるようにするため。

def _since(days: Optional[int]) -> str:
    if not days:
        return "0000-01-01"
    return (_today() - datetime.timedelta(days=int(days))).isoformat()


def by_kind(days: Optional[int] = None):
    """種別ごとの件数と平均点。"""
    return db.run(
        "SELECT kind, COUNT(*) AS n, AVG(total_score) AS avg_score,"
        " AVG(sufficiency) AS avg_suff FROM observations"
        " WHERE day >= ? GROUP BY kind ORDER BY n DESC",
        (_since(days),), "all") or []


def by_city(days: Optional[int] = None, limit: int = 12):
    """市区町村ごとの件数と平均点。多い順。

    件数が1件の市区町村でも隠さない。**平均を名乗れる数ではない**ので、
    件数を必ず並べて出すこと。
    """
    return db.run(
        "SELECT pref, city, COUNT(*) AS n, AVG(total_score) AS avg_score,"
        " AVG(price_yen) AS avg_price FROM observations"
        " WHERE day >= ? AND city IS NOT NULL"
        " GROUP BY pref, city ORDER BY n DESC, city LIMIT ?",
        (_since(days), int(limit)), "all") or []


def common_risks(days: Optional[int] = None, limit: int = 10):
    """よく出た注意項目。1行に「;」で複数入っているので、ここで開く。"""
    rows = db.run("SELECT risks FROM observations"
                  " WHERE day >= ? AND risks IS NOT NULL AND risks <> ''",
                  (_since(days),), "all") or []
    count = {}
    for r in rows:
        for name in str(r["risks"]).split(";"):
            name = name.strip()
            if name:
                count[name] = count.get(name, 0) + 1
    return sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
