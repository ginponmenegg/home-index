# -*- coding: utf-8 -*-
"""ジオコーディング抽象化レイヤ（指示書 第31章：Google依存を避ける）。

GeocodingService インターフェースに対し、GSI(国土地理院・キー不要) と
Google の2実装を差し替え可能にする。呼び出し側は具象を知らない。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Protocol
import requests


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    address_normalized: str
    provider: str
    level: str = "unknown"
    raw: Optional[dict] = None


class GeocodingService(Protocol):
    def geocode(self, address: str) -> GeocodeResult: ...


class GsiGeocoder:
    """国土地理院 住所検索API（キー不要・無料）。保存制約が緩い前提。"""
    URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
    provider = "gsi"

    def geocode(self, address: str) -> GeocodeResult:
        r = requests.get(self.URL, params={"q": address}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            raise ValueError(f"GSI: 住所が見つかりません: {address}")
        top = data[0]
        lon, lat = top["geometry"]["coordinates"]  # GSIは[経度, 緯度]
        title = top.get("properties", {}).get("title", address)
        return GeocodeResult(float(lat), float(lon), title, self.provider,
                             level="street", raw=top)


class GoogleGeocoder:
    """Google Geocoding API（要APIキー＋課金）。座標保存は規約に注意。"""
    URL = "https://maps.googleapis.com/maps/api/geocode/json"
    provider = "google"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def geocode(self, address: str) -> GeocodeResult:
        r = requests.get(self.URL, params={
            "address": address, "key": self.api_key,
            "language": "ja", "region": "jp"}, timeout=30)
        data = r.json()
        if data.get("status") != "OK":
            raise ValueError(f"Google: status={data.get('status')}")
        res = data["results"][0]
        loc = res["geometry"]["location"]
        return GeocodeResult(float(loc["lat"]), float(loc["lng"]),
                             res.get("formatted_address", address),
                             self.provider, level=res["geometry"].get("location_type", "unknown"),
                             raw=res)


def make_geocoder(google_key: Optional[str] = None) -> GeocodingService:
    """設定に応じてジオコーダを返す。既定はGSI（キー不要）。"""
    if google_key:
        return GoogleGeocoder(google_key)
    return GsiGeocoder()
