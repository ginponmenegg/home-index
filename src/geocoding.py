# -*- coding: utf-8 -*-
"""ジオコーディング抽象化レイヤ（指示書 第31章：Google依存を避ける）。

GeocodingService インターフェースに対し、GSI(国土地理院・キー不要) と
Google の2実装を差し替え可能にする。呼び出し側は具象を知らない。

■どこまで当たったかを必ず見る（2026-10-19）
国土地理院の住所検索は、見つからなかった部分を黙って落として返す。
平塚市で土地を2件診断したら、2件とも用途地域が違っていた。調べると、

    平塚市岡崎3000-1  → 平塚市岡崎（町の代表点。番地は当たっていない）
    平塚市真土1234    → 平塚市真田（**別の町**）

だった。土地は住居表示ではなく地番で書かれることが多く、地番の地域では
番地まで当たらない。町の代表点は実際の土地から数百m離れることがあり、
似た名前の別の町なら位置ごと違う。それを見ずに用途地域を引いていた。

返ってきた住所を、入力した住所と突き合わせて3段で判定する。

    address  番地（または番）まで一致した
    town     町までしか当たっていない。位置は町の代表点
    mismatch 入力と違う町に当たった。この座標は使えない
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Protocol
import re
import unicodedata

import requests


ADDRESS = "address"
TOWN = "town"
MISMATCH = "mismatch"
_RANK = {ADDRESS: 0, TOWN: 1, MISMATCH: 2, "unknown": 1}


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    address_normalized: str
    provider: str
    level: str = "unknown"
    raw: Optional[dict] = None

    @property
    def precise(self) -> bool:
        """番地まで当たったか。用途地域のように土地ごとに違うものは、
        これが真のときだけ座標から引く。"""
        return self.level == ADDRESS


class GeocodingService(Protocol):
    def geocode(self, address: str) -> GeocodeResult: ...


# ---- 住所の突き合わせ -----------------------------------------------------

_PREF = re.compile(r"^(北海道|東京都|京都府|大阪府|[^都道府県]{2,3}県)")
# 先頭の市区町村名（郡を含む）。最短で取るので「平塚市紅谷町」は「平塚市」で止まる
_MUNI = re.compile(r"^(?:.+?郡)?.+?[市区町村]")
_KANJI_DIGIT = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9}


def _kanji_number(run: str) -> str:
    """「十二」「二十三」のような漢数字を算用数字にする（丁目に使う範囲）。"""
    if "十" not in run:
        return "".join(str(_KANJI_DIGIT.get(c, c)) for c in run)
    head, _, tail = run.partition("十")
    tens = _KANJI_DIGIT.get(head, 1) if head else 1
    ones = _KANJI_DIGIT.get(tail, 0) if tail else 0
    return str(tens * 10 + ones)


def normalize_address(s: str) -> str:
    """突き合わせ用の形にする。表示には使わない。

    全角を半角に、都道府県を外し、丁目・番地・番・号をハイフンにそろえる。
    「の」と長音記号は、数字にはさまれたときだけハイフンとみなす
    （町名の中の「の」「ー」は残す）。
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace(" ", "").replace("　", "")
    s = _PREF.sub("", s)
    s = re.sub(r"[〇一二三四五六七八九十]+(?=丁目)",
               lambda m: _kanji_number(m.group(0)), s)
    s = re.sub(r"(丁目|番地|番|号)", "-", s)
    s = re.sub(r"(?<=\d)[の之ノーｰ‐−－―](?=\d)", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _split_town(s: str):
    """（数字が出てくる前まで, 残り）に分ける。"""
    m = re.match(r"^(\D*)(.*)$", s)
    return m.group(1), m.group(2)


def match_level(query: str, title: str) -> str:
    """入力した住所に対して、返ってきた住所がどこまで当たっているか。"""
    q, t = normalize_address(query), normalize_address(title)
    if not t:
        return MISMATCH
    q_town, q_num = _split_town(q)
    t_town, t_num = _split_town(t)
    # 市区町村までしか当たっていない（市役所あたりの代表点）。土地の位置には使えない
    m = _MUNI.match(t_town)
    if m and not t_town[m.end():]:
        return MISMATCH
    if t_town != q_town:
        # 「中原上宿」に対して「中原」のように、入力より広い範囲で当たったなら
        # 町の代表点。そうでなければ違う町に当たっている（真土→真田）。
        if q_town.startswith(t_town) and len(t_town) >= 3:
            return TOWN
        return MISMATCH
    if not q_num or not t_num:
        return TOWN
    # 丁目までしか当たっていなければ、位置は丁目の代表点
    if (title or "").rstrip().endswith("丁目"):
        return TOWN
    if q_num == t_num or q_num.startswith(t_num + "-"):
        return ADDRESS
    return TOWN


def _best(candidates, query):
    """候補の中から、いちばん深く当たったものを選ぶ。同じなら先頭を優先。"""
    best = None
    for c in candidates:
        lv = match_level(query, c[0])
        if best is None or _RANK[lv] < _RANK[best[1]]:
            best = (c, lv)
    return best


class GsiGeocoder:
    """国土地理院 住所検索API（キー不要・無料）。保存制約が緩い前提。"""
    URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
    provider = "gsi"

    def geocode(self, address: str, expect: Optional[str] = None) -> GeocodeResult:
        """expect は突き合わせに使う住所。名前を添えて引くときに、名前を
        除いた住所で当たり具合を判定するために渡す。"""
        r = requests.get(self.URL, params={"q": address}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            raise ValueError(f"GSI: 住所が見つかりません: {address}")
        cands = []
        for top in data[:5]:
            lon, lat = top["geometry"]["coordinates"]  # GSIは[経度, 緯度]
            title = top.get("properties", {}).get("title", address)
            cands.append((title, float(lat), float(lon), top))
        (title, lat, lon, top), level = _best(cands, expect or address)
        return GeocodeResult(lat, lon, title, self.provider, level=level, raw=top)


class GoogleGeocoder:
    """Google Geocoding API（要APIキー＋課金）。座標保存は規約に注意。"""
    URL = "https://maps.googleapis.com/maps/api/geocode/json"
    provider = "google"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def geocode(self, address: str, expect: Optional[str] = None) -> GeocodeResult:
        r = requests.get(self.URL, params={
            "address": address, "key": self.api_key,
            "language": "ja", "region": "jp"}, timeout=30)
        data = r.json()
        if data.get("status") != "OK":
            raise ValueError(f"Google: status={data.get('status')}")
        res = data["results"][0]
        loc = res["geometry"]["location"]
        # 番地まで当たったときだけ ROOFTOP か補間になる。それ以外は代表点。
        lt = res["geometry"].get("location_type", "")
        level = ADDRESS if lt in ("ROOFTOP", "RANGE_INTERPOLATED") else TOWN
        return GeocodeResult(float(loc["lat"]), float(loc["lng"]),
                             res.get("formatted_address", address),
                             self.provider, level=level, raw=res)


def make_geocoder(google_key: Optional[str] = None) -> GeocodingService:
    """設定に応じてジオコーダを返す。既定はGSI（キー不要）。"""
    if google_key:
        return GoogleGeocoder(google_key)
    return GsiGeocoder()
