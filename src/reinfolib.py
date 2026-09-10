# -*- coding: utf-8 -*-
"""不動産情報ライブラリ APIクライアント（Phase B で実仕様確認済み）。

- 認証: ヘッダ Ocp-Apim-Subscription-Key
- XIT001: 取引価格。city(市区町村コード)+year 必須。gzip応答なので requests が自動解凍。
- 外部API直呼びはこのクラスに閉じ込める（第31・47章：抽象化）。
"""
from __future__ import annotations
from typing import List, Optional
import os
import threading
import requests
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from .models import Transaction

BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"

# ---- 取引データのプロセス内キャッシュ ------------------------------------
# 実測（2026-09-10・船橋市12204・2024年）：1リクエストで2,320件、
# Transactionにして約1.6MB。1市区町村の診断で、戸建は3年分＝約5MB、
# マンションは10年分＝約16MBを積む。
#
# 以前の上限は「2000エントリ」だった。1エントリ1.6MBなので最大3GBを許す数字で、
# Renderの512MBでは上限として意味をなさない。件数ではなく概算バイト数で持ち、
# 超えたら使っていない古いものから捨てる。
_TXN_CACHE: "OrderedDict[tuple, List[Transaction]]" = OrderedDict()
_TXN_LOCK = threading.Lock()
# 1件あたりの概算。実測 1.6MB / 2,320件 ≒ 690バイト。増えても桁は変わらない。
_BYTES_PER_TXN = 700
_CACHE_BUDGET = int(os.environ.get("TXN_CACHE_MB", "96")) * 1024 * 1024
_cache_bytes = 0


def _cache_get(key):
    """取り出したものは新しい側へ回す（LRU）。"""
    with _TXN_LOCK:
        txns = _TXN_CACHE.get(key)
        if txns is not None:
            _TXN_CACHE.move_to_end(key)
        return txns


def _cache_put(key, txns) -> None:
    global _cache_bytes
    size = len(txns) * _BYTES_PER_TXN
    if size > _CACHE_BUDGET:
        return          # 1件で枠を超えるものは持たない（全部捨てる羽目になる）
    with _TXN_LOCK:
        old = _TXN_CACHE.pop(key, None)
        if old is not None:
            _cache_bytes -= len(old) * _BYTES_PER_TXN
        _TXN_CACHE[key] = txns
        _cache_bytes += size
        while _cache_bytes > _CACHE_BUDGET and len(_TXN_CACHE) > 1:
            _k, dropped = _TXN_CACHE.popitem(last=False)
            _cache_bytes -= len(dropped) * _BYTES_PER_TXN


def cache_stats() -> dict:
    """いま何をどれだけ持っているか。テストと運用の確認用。"""
    with _TXN_LOCK:
        return {"entries": len(_TXN_CACHE), "bytes": _cache_bytes,
                "budget": _CACHE_BUDGET}


def cache_clear() -> None:
    global _cache_bytes
    with _TXN_LOCK:
        _TXN_CACHE.clear()
        _cache_bytes = 0


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
        """1市区町村・1年分。year だけで4四半期すべて返る（実測で確認）。

        マニュアルには quarter が必須と書かれているが、指定しなくても
        第1〜第4四半期が揃って返る。四半期ごとに分けて呼ぶとリクエストが
        4倍になるので、このままにしている。
        """
        ck = (city_code, y, pc)
        cached = _cache_get(ck)
        if cached is not None:
            return cached
        params = {"year": y, "city": city_code}
        if pc:
            params["priceClassification"] = pc
        body = self._get("XIT001", params)
        data = body.get("data", []) if isinstance(body, dict) else []
        txns = [normalize_txn(rec) for rec in data]
        _cache_put(ck, txns)
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
