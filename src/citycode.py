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


# 全国の市区町村コード（1,920件）。出典は XIT002 と同じで、中身も同じ。
# **変わらないものを毎回取りに行かない。**取得に失敗すると住所が解決できず、
# 成約データを1件も取れなくなる。年に数件しか変わらないので同梱する。
# 作り直しは scratchpad/build_citycodes.py（47リクエスト）。
def _load_bundled() -> Dict[str, List[dict]]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "citycodes.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:          # pragma: no cover
        return {}


BUNDLED_CITIES: Dict[str, List[dict]] = _load_bundled()

# 政令指定都市の「市」のコード。**このコードでは XIT001 が1件も返さない。**
# 01100（札幌市）は0件、01101（札幌市中央区）は1,682件。区まで分からないと
# 価格評価ができないので、呼び出し側が画面に書けるように集めておく。
# 末尾が100とは限らない（北九州市40100・福岡市40130）ので、表から数える。
DESIGNATED_CITIES = {
    c["id"] for cities in BUNDLED_CITIES.values() for c in cities
    if any(o.get("full", "").startswith(c["name"]) and o["id"] != c["id"]
           and o["name"].endswith("区") for o in cities)}


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
        """まず同梱の表を見る。APIは、表に無い県のときだけ。

        **失敗を憶えてはいけない。**以前は取得に失敗したとき空のリストを
        「その県には市区町村が無い」として記憶し、ディスクにも書いていた。
        一度そうなると、その県の住所は二度と市区町村コードが引けず、
        取引を1件も取りに行かないまま「類似成約が不足」と表示していた。
        本番で全都道府県がこの状態になっていた。
        """
        bundled = BUNDLED_CITIES.get(pref_code)
        if bundled:
            return bundled
        cached = self._pref_cities.get(pref_code)
        if cached:
            return cached
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
        if cities:                    # 空は憶えない
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
        # 住所に含まれる市区町村名のうち最長一致を採用する。
        #
        # **政令指定都市は、市のコードでは取引が1件も返らない。**
        # 01100（札幌市）は0件、01101（札幌市中央区）は1,682件。区まで
        # 引かないと価格評価ができない。ところが「札幌市」も「中央区」も
        # 3文字なので、長さ比較では市のほうが先に当たって勝っていた。
        # 同梱の表は区に「札幌市中央区」というフルネームを持たせてあるので、
        # そちらで照合すれば6文字対3文字で区が勝つ。
        best, best_len, matched = None, -1, ""
        for c in cities:
            for label in (c.get("full") or c["name"], c["name"]):
                if label and label in address and len(label) > best_len:
                    best, best_len, matched = c, len(label), label
        if not best:
            return None, None, None
        district = None
        after = address.split(matched, 1)[1] if matched in address else ""
        m = _leading_district(after)
        if m:
            district = m
        return best["id"], (best.get("full") or best["name"]), district

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


def is_designated_city(code: Optional[str]) -> bool:
    """政令指定都市の「市」のコードか。区まで要る、という意味。"""
    return bool(code) and code in DESIGNATED_CITIES


def _leading_district(after: str) -> Optional[str]:
    import re
    # 市区町村名の後ろの、数字が始まる前までの漢字/かなを町名とみなす
    m = re.match(r"[\s　]*([一-龥ぁ-んァ-ヶーヶ々]+)", after or "")
    if m:
        return m.group(1)[:10]
    return None
