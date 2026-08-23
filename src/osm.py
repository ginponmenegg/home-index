# -*- coding: utf-8 -*-
"""OpenStreetMap から買い物施設を取る（Overpass API）。

不動産情報ライブラリには大規模小売店舗・商業施設のエンドポイントが無い
（API一覧で確認済み）。買い物先の有無は生活利便の中核なので、ここだけ
OSMを使う。スクレイピングではなく公開APIで、ODbLに従い出典を明示する。

品質の断り書き：OSMは有志の編集なので、タグの精度に地域差がある。
実データ（藤沢市）を見た限り、イオン・イトーヨーカドー等の大型店はよく
整備されている一方、衣料品店や飲食店が supermarket として登録されている
例もあった。だから件数を鵜呑みにせず、種別で重みを分けて使う。

Overpassは有志が運営する共有サーバーなので、礼儀として
  - User-Agent を名乗る
  - タイムアウトを短く切る
  - 同じ座標を何度も叩かない（プロセス内キャッシュ）
  - 失敗しても診断は止めない（取れなければ「未取得」）
を守る。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import math
import urllib.parse

import requests

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "HOME-INDEX/1.0 (housing diagnosis; contact via service site)"

# 大型（複合商業施設・百貨店）と、日常の買い物（スーパー）を分けて数える。
BIG_KINDS = ("mall", "department_store")
DAILY_KINDS = ("supermarket",)
ALL_KINDS = BIG_KINDS + DAILY_KINDS

_CACHE: dict = {}


@dataclass
class Shop:
    name: Optional[str]
    kind: str                 # mall / department_store / supermarket
    distance_m: int

    @property
    def is_big(self) -> bool:
        return self.kind in BIG_KINDS


@dataclass
class ShopResult:
    checked: bool = False
    shops: List[Shop] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def nearest_big(self) -> Optional[Shop]:
        big = [s for s in self.shops if s.is_big]
        return min(big, key=lambda s: s.distance_m) if big else None

    @property
    def nearest_daily(self) -> Optional[Shop]:
        daily = [s for s in self.shops if not s.is_big]
        return min(daily, key=lambda s: s.distance_m) if daily else None

    def count_within(self, metres: int, big_only: bool = False) -> int:
        return sum(1 for s in self.shops
                   if s.distance_m <= metres and (s.is_big or not big_only))


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _build_query(lat: float, lon: float, radius_m: int) -> str:
    kinds = "|".join(ALL_KINDS)
    around = f"(around:{radius_m},{lat},{lon})"
    return (f'[out:json][timeout:20];('
            f'node["shop"~"{kinds}"]{around};'
            f'way["shop"~"{kinds}"]{around};'
            f');out center tags;')


def fetch_shops_around(lat: Optional[float], lon: Optional[float],
                       radius_m: int = 1500,
                       timeout: int = 20) -> ShopResult:
    """半径内の買い物施設を近い順に返す。取れなければ checked=False。"""
    res = ShopResult()
    if lat is None or lon is None:
        res.notes.append("座標未取得のため買い物施設は未取得")
        return res

    # 座標を丸めてキャッシュ。近所の物件を続けて診断しても叩き直さない。
    ck = (round(lat, 3), round(lon, 3), radius_m)
    cached = _CACHE.get(ck)
    if cached is not None:
        return cached

    url = OVERPASS + "?data=" + urllib.parse.quote(_build_query(lat, lon, radius_m),
                                                   safe="")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        res.notes.append(f"買い物施設の取得に失敗: {e}")
        return res

    for el in body.get("elements", []) or []:
        tags = el.get("tags") or {}
        kind = tags.get("shop")
        if kind not in ALL_KINDS:
            continue
        # ノードは緯度経度をそのまま、ウェイ（建物の輪郭）は中心点を使う
        la = el.get("lat")
        lo = el.get("lon")
        if la is None or lo is None:
            centre = el.get("center") or {}
            la, lo = centre.get("lat"), centre.get("lon")
        if la is None or lo is None:
            continue
        res.shops.append(Shop(name=tags.get("name"), kind=kind,
                              distance_m=int(_haversine_m(lat, lon, la, lo))))

    res.shops.sort(key=lambda s: s.distance_m)
    res.checked = True
    if len(_CACHE) < 2000:
        _CACHE[ck] = res
    return res
