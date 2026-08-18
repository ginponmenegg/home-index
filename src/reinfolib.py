# -*- coding: utf-8 -*-
"""不動産情報ライブラリ APIクライアント（Phase B で実仕様確認済み）。

- 認証: ヘッダ Ocp-Apim-Subscription-Key
- XIT001: 取引価格。city(市区町村コード)+year 必須。gzip応答なので requests が自動解凍。
- 外部API直呼びはこのクラスに閉じ込める（第31・47章：抽象化）。
"""
from __future__ import annotations
from typing import List, Optional
import requests
from concurrent.futures import ThreadPoolExecutor

from .models import Transaction

BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
_TXN_CACHE: dict = {}   # (city, year, pc) -> [Transaction] プロセス内キャッシュ


def _to_int(s) -> Optional[int]:
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_float(s) -> Optional[float]:
    try:
        v = str(s).replace(",", "").strip()
        if v == "":
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_build_year(s) -> Optional[int]:
    """'2014年' -> 2014 / '戦前' や空は None。"""
    if not s:
        return None
    s = str(s)
    if "戦前" in s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 4:
        return int(digits[:4])
    return None


def _parse_period(s):
    """'2024年第4四半期' -> (2024, 4)。"""
    if not s:
        return None, None
    s = str(s)
    year = None
    quarter = None
    if "年" in s:
        y = "".join(ch for ch in s.split("年")[0] if ch.isdigit())
        year = int(y) if y else None
    if "第" in s and "四半期" in s:
        q = s.split("第")[1].split("四半期")[0]
        q = "".join(ch for ch in q if ch.isdigit())
        quarter = int(q) if q else None
    return year, quarter


def normalize_txn(rec: dict) -> Transaction:
    """XIT001 の生レコードを Transaction に正規化。"""
    py, pq = _parse_period(rec.get("Period"))
    return Transaction(
        trade_price=_to_int(rec.get("TradePrice")),
        type=rec.get("Type") or None,
        municipality_code=rec.get("MunicipalityCode") or None,
        district_name=rec.get("DistrictName") or None,
        land_area_m2=_to_float(rec.get("Area")),
        building_area_m2=_to_float(rec.get("TotalFloorArea")),
        build_year=_parse_build_year(rec.get("BuildingYear")),
        period_year=py,
        period_quarter=pq,
        city_planning=rec.get("CityPlanning") or None,
        structure=rec.get("Structure") or None,
        layout=rec.get("FloorPlan") or None,
        raw=rec,
    )


class ReinfolibClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("REINFOLIB_KEY が未設定です")
        self.api_key = api_key
        self._session = requests.Session()

    def _get(self, api: str, params: dict) -> dict:
        headers = {"Ocp-Apim-Subscription-Key": self.api_key}
        r = self._session.get(f"{BASE}/{api}", headers=headers, params=params,
                              timeout=40)
        r.raise_for_status()
        return r.json()

    def _year(self, city_code, y, pc):
        ck = (city_code, y, pc)
        cached = _TXN_CACHE.get(ck)
        if cached is not None:
            return cached
        params = {"year": y, "city": city_code}
        if pc:
            params["priceClassification"] = pc
        body = self._get("XIT001", params)
        data = body.get("data", []) if isinstance(body, dict) else []
        txns = [normalize_txn(rec) for rec in data]
        if len(_TXN_CACHE) < 2000:
            _TXN_CACHE[ck] = txns
        return txns

    def get_transactions(self, city_code: str, years: List[int],
                         price_classification: Optional[str] = None
                         ) -> List[Transaction]:
        """複数年の取引を並列取得して結合（時点補正の母集団）。"""
        out: List[Transaction] = []
        with ThreadPoolExecutor(max_workers=min(4, len(years) or 1)) as ex:
            for txns in ex.map(lambda y: self._year(city_code, y, price_classification),
                               years):
                out.extend(txns)
        return out
