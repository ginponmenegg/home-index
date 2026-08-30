# -*- coding: utf-8 -*-
"""全国の市区町村コード解決（Phase：全国対応）。

住所テキスト → 都道府県 → XIT002（都道府県内市区町村一覧）で市区町村コードを解決。
結果はメモリ＋ディスク(JSON)にキャッシュし、都道府県ごとに1回だけAPIを呼ぶ。
APIキーが無い場合は同梱の神奈川辞書でオフライン解決（既存エリアの後方互換）。
"""
from __future__ import annotations
from typing import Optional, Dict, List, Tuple
import os
import json
import requests

from .extract import KANAGAWA_CITY_CODES, PREFECTURES  # 共有・オフライン後方互換

REINFOLIB_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"

_NAME2PREF = {v: k for k, v in PREFECTURES.items()}


def detect_prefecture(text: str) -> Optional[str]:
    """住所テキストから都道府県コード(2桁)を返す。"""
    if not text:
        return None
    for name, code in _NAME2PREF.items():
        if name in text:
            return code
    return None


class CityCodeResolver:
    def __init__(self, reinfolib_key: Optional[str] = None,
                 cache_file: Optional[str] = None):
        self.key = reinfolib_key
        self.cache_file = cache_file
        self._pref_cities: Dict[str, List[dict]] = {}  # pref_code -> [{id,name}]
        self._load_disk()

    # ---- キャッシュ ----
    def _load_disk(self):
        if self.cache_file and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    self._pref_cities = json.load(f)
            except Exception:
                self._pref_cities = {}

    def _save_disk(self):
        if not self.cache_file:
            return
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._pref_cities, f, ensure_ascii=False)
        except Exception:
            pass

    # ---- 市区町村一覧の取得 ----
    def _cities(self, pref_code: str) -> List[dict]:
        if pref_code in self._pref_cities:
            return self._pref_cities[pref_code]
        cities: List[dict] = []
        if self.key:
            try:
                r = requests.get(f"{REINFOLIB_BASE}/XIT002",
                                 headers={"Ocp-Apim-Subscription-Key": self.key},
                                 params={"area": pref_code}, timeout=30)
                r.raise_for_status()
                body = r.json()
                data = body.get("data", body) if isinstance(body, dict) else body
                for it in data:
                    code = it.get("id") or it.get("code") or it.get("city_code")
                    name = it.get("name") or it.get("city")
                    if code and name:
                        cities.append({"id": str(code), "name": str(name)})
            except Exception:
                cities = []
        if not cities and pref_code == "14":  # 神奈川はオフライン辞書で補完
            cities = [{"id": c, "name": n} for n, c in KANAGAWA_CITY_CODES.items()]
        self._pref_cities[pref_code] = cities
        self._save_disk()
        return cities

    # ---- 住所から解決 ----
    def resolve_from_address(self, address: str, pref_hint: Optional[str] = None
                             ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """住所 → (市区町村コード, 市区町村名, 町名)。

        pref_hint は都道府県コード(2桁)。住所に都道府県が書かれていないとき、
        呼び出し側が別の手段で調べた値を渡す。市区町村名と町名は住所から
        取るので、ここで補うのは都道府県だけでよい。
        """
        pref = detect_prefecture(address or "") or pref_hint
        if not pref:
            return None, None, None
        cities = self._cities(pref)
        # 住所に含まれる市区町村名のうち最長一致を採用（政令市の区も拾える）
        best = None
        for c in cities:
            if c["name"] and c["name"] in address:
                if best is None or len(c["name"]) > len(best["name"]):
                    best = c
        if not best:
            return None, None, None
        district = None
        after = address.split(best["name"], 1)[1] if best["name"] in address else ""
        m = _leading_district(after)
        if m:
            district = m
        return best["id"], best["name"], district

    def info(self, code: str) -> Tuple[Optional[str], Optional[str]]:
        """市区町村コード → (都道府県名, 市区町村名)。"""
        if not code or len(code) < 2:
            return None, None
        pref_code = code[:2]
        pref_name = PREFECTURES.get(pref_code)
        for c in self._cities(pref_code):
            if c["id"] == code:
                return pref_name, c["name"]
        return pref_name, None


def _leading_district(after: str) -> Optional[str]:
    import re
    # 市区町村名の後ろの、数字が始まる前までの漢字/かなを町名とみなす
    m = re.match(r"[\s　]*([一-龥ぁ-んァ-ヶーヶ々]+)", after or "")
    if m:
        return m.group(1)[:10]
    return None
