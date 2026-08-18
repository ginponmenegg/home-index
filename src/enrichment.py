# -*- coding: utf-8 -*-
"""Data Enrichment（Phase D）：座標→公的データ付与。

実装：
  - 用途地域（reinfolib XKT002）
  - ハザード（reinfolib XKT026洪水 / XKT027高潮 / XKT028津波 / XKT029土砂）
      いずれも座標→タイル(z=15)→GeoJSON→ポリゴン内判定。公式仕様で確認済み。
  - 人口・人口動向（e-Stat 社会・人口統計体系 市区町村データ statsCode=00200502）
      getStatsData を市区町村コードで取得し、メタから「総人口」を特定して最新値と増減傾向を出す。
方針：取得できないものは unknown を返し、勝手に埋めない（第14章）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import math
import requests
from concurrent.futures import ThreadPoolExecutor
import logging
logger = logging.getLogger(__name__)

# 取得済みタイルのプロセス内キャッシュ（同一エリアの再診断を高速化）
_TILE_CACHE: Dict[tuple, list] = {}
_HTTP = requests.Session()  # コネクション再利用で高速化


def _parallel(fn, items, workers=8):
    """items を並列処理して結果リストを返す（順序保持）。"""
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))

REINFOLIB_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
ESTAT_BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"

USE_DISTRICT_TOKENS = [
    "第一種低層住居専用地域", "第二種低層住居専用地域", "田園住居地域",
    "第一種中高層住居専用地域", "第二種中高層住居専用地域",
    "第一種住居地域", "第二種住居地域", "準住居地域",
    "近隣商業地域", "商業地域", "準工業地域", "工業地域", "工業専用地域",
]

# 洪水浸水想定区域（想定最大規模）の浸水深ランク A31a_205
FLOOD_RANK_LABEL = {
    1: "0〜0.5m未満", 2: "0.5〜3.0m未満", 3: "3.0〜5.0m未満",
    4: "5.0〜10.0m未満", 5: "10.0〜20.0m未満", 6: "20.0m以上",
}


@dataclass
class HazardResult:
    checked: bool = False               # ハザードAPIを取得できたか
    flood_rank: Optional[int] = None    # 洪水 浸水深ランク(最大)
    flood_label: Optional[str] = None
    flood_river: Optional[str] = None
    sediment: Optional[str] = None      # "特別警戒区域" / "警戒区域" / None
    tsunami: bool = False               # 津波浸水想定域内
    storm_surge: bool = False           # 高潮浸水想定域内
    notes: List[str] = field(default_factory=list)

    def any_hit(self) -> bool:
        return bool(self.flood_rank or self.sediment or self.tsunami
                    or self.storm_surge)


@dataclass
class FacilityResult:
    checked: bool = False
    nearest_station_m: Optional[int] = None
    nearest_station_name: Optional[str] = None
    nearest_hospital_m: Optional[int] = None
    nearest_school_m: Optional[int] = None
    hospital_count_1km: int = 0
    notes: List[str] = field(default_factory=list)


@dataclass
class Enrichment:
    use_district: Optional[str] = None
    urbanization: Optional[str] = None
    hazard: HazardResult = field(default_factory=HazardResult)
    facility: FacilityResult = field(default_factory=FacilityResult)
    population: Optional[int] = None
    population_trend: Optional[str] = None   # "増加" / "微増" / "横ばい" / "減少"
    notes: List[str] = field(default_factory=list)

    @property
    def hazard_known(self) -> bool:
        return self.hazard.checked


# ---------- タイル・幾何 ----------
def latlon_to_tile(lat, lon, z):
    lat_rad = math.radians(lat)
    n = 2.0 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _point_in_ring(lon, lat, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def point_in_geometry(lon, lat, geom) -> bool:
    t = geom.get("type")
    coords = geom.get("coordinates", [])
    if t == "Polygon":
        if not coords or not _point_in_ring(lon, lat, coords[0]):
            return False
        return not any(_point_in_ring(lon, lat, h) for h in coords[1:])
    if t == "MultiPolygon":
        for poly in coords:
            if poly and _point_in_ring(lon, lat, poly[0]) \
               and not any(_point_in_ring(lon, lat, h) for h in poly[1:]):
                return True
        return False
    return False


def _reinfolib_tile(api, key, z, x, y):
    ck = (api, z, x, y)
    cached = _TILE_CACHE.get(ck)
    if cached is not None:
        return cached
    headers = {"Ocp-Apim-Subscription-Key": key}
    r = _HTTP.get(f"{REINFOLIB_BASE}/{api}", headers=headers,
                  params={"response_format": "geojson", "z": z, "x": x, "y": y},
                  timeout=40)
    r.raise_for_status()
    body = r.json()
    feats = body.get("features", []) if isinstance(body, dict) else []
    if len(_TILE_CACHE) < 5000:
        _TILE_CACHE[ck] = feats
    return feats


# ---------- 用途地域 ----------
def _extract_use_district(props: dict) -> Optional[str]:
    for v in props.values():
        if isinstance(v, str):
            for tok in USE_DISTRICT_TOKENS:
                if tok in v:
                    return tok
    return None


def fetch_use_district(lat, lon, key, zoom=15) -> Optional[str]:
    x, y = latlon_to_tile(lat, lon, zoom)
    feats = _reinfolib_tile("XKT002", key, zoom, x, y)
    for f in feats:
        if point_in_geometry(lon, lat, f.get("geometry") or {}):
            ud = _extract_use_district(f.get("properties", {}) or {})
            if ud:
                return ud
    for f in feats:  # 内包判定不能時はタイル代表値
        ud = _extract_use_district(f.get("properties", {}) or {})
        if ud:
            return ud
    return None


# ---------- ハザード ----------
def _containing_props(lat, lon, feats) -> List[dict]:
    out = []
    for f in feats:
        if point_in_geometry(lon, lat, f.get("geometry") or {}):
            out.append(f.get("properties", {}) or {})
    return out


def fetch_hazard(lat, lon, key, zoom=15) -> HazardResult:
    h = HazardResult()
    x, y = latlon_to_tile(lat, lon, zoom)

    def get(api):
        try:
            return api, _containing_props(lat, lon, _reinfolib_tile(api, key, zoom, x, y))
        except Exception as e:
            return api, e

    results = dict(_parallel(get, ["XKT026", "XKT029", "XKT028", "XKT027"], workers=4))
    ok = 0
    # 洪水 XKT026
    r = results.get("XKT026")
    if isinstance(r, list):
        ranks = [int(p["A31a_205"]) for p in r if str(p.get("A31a_205", "")).strip().isdigit()]
        if ranks:
            h.flood_rank = max(ranks)
            h.flood_label = FLOOD_RANK_LABEL.get(h.flood_rank, "浸水想定あり")
            for p in r:
                if p.get("A31a_202"):
                    h.flood_river = p.get("A31a_202")
                    break
        ok += 1
    # 土砂 XKT029
    r = results.get("XKT029")
    if isinstance(r, list):
        codes = [str(p.get("A33_002", "")).strip() for p in r]
        if "2" in codes:
            h.sediment = "特別警戒区域"
        elif "1" in codes:
            h.sediment = "警戒区域"
        ok += 1
    # 津波 XKT028
    r = results.get("XKT028")
    if isinstance(r, list):
        h.tsunami = len(r) > 0
        ok += 1
    # 高潮 XKT027
    r = results.get("XKT027")
    if isinstance(r, list):
        h.storm_surge = len(r) > 0
        ok += 1

    h.checked = ok >= 1
    return h


# ---------- 周辺施設（駅/医療/学校のポイント） ----------
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def _facility_name(props: dict) -> Optional[str]:
    for v in props.values():
        if isinstance(v, str) and v.strip() and not v.strip().replace(".", "").isdigit():
            return v.strip()
    return None


def fetch_points_around(api, key, lat, lon, z=14, ring=1):
    """周辺タイル(2*ring+1)^2 のポイントを並列で集める。個別タイル失敗は無視。"""
    cx, cy = latlon_to_tile(lat, lon, z)
    tiles = [(cx + dx, cy + dy)
             for dx in range(-ring, ring + 1) for dy in range(-ring, ring + 1)]

    def get(t):
        try:
            return _reinfolib_tile(api, key, z, t[0], t[1])
        except Exception:
            return []

    pts = []
    for feats in _parallel(get, tiles, workers=8):
        for f in feats:
            g = f.get("geometry") or {}
            if g.get("type") == "Point" and g.get("coordinates"):
                lo, la = g["coordinates"][0], g["coordinates"][1]
                pts.append((la, lo, f.get("properties", {}) or {}))
    return pts


def _nearest(pts, lat, lon):
    best = None
    for (la, lo, pr) in pts:
        d = haversine_m(lat, lon, la, lo)
        if best is None or d < best[0]:
            best = (d, pr)
    return best


def fetch_facilities(lat, lon, key) -> FacilityResult:
    fr = FacilityResult()
    ok = 0
    try:
        st = fetch_points_around("XKT015", key, lat, lon)  # 駅
        n = _nearest(st, lat, lon)
        if n:
            fr.nearest_station_m = int(n[0])
            fr.nearest_station_name = _facility_name(n[1])
        ok += 1
    except Exception as e:
        fr.notes.append(f"駅取得失敗: {e}")
    try:
        ho = fetch_points_around("XKT010", key, lat, lon)  # 医療機関
        n = _nearest(ho, lat, lon)
        if n:
            fr.nearest_hospital_m = int(n[0])
        fr.hospital_count_1km = sum(
            1 for (la, lo, pr) in ho if haversine_m(lat, lon, la, lo) <= 1000)
        ok += 1
    except Exception as e:
        fr.notes.append(f"医療取得失敗: {e}")
    try:
        sc = fetch_points_around("XKT006", key, lat, lon)  # 学校
        n = _nearest(sc, lat, lon)
        if n:
            fr.nearest_school_m = int(n[0])
        ok += 1
    except Exception as e:
        fr.notes.append(f"学校取得失敗: {e}")
    fr.checked = ok >= 1
    return fr


# ---------- 人口（e-Stat 社会・人口統計体系 市区町村データ） ----------
def _parse_population_series(body: dict, city_code: str):
    """getStatsData応答から、その市区町村の『総人口』の時系列を返す。
    返り値: [(time_str, value_int), ...] 昇順。取れなければ []。"""
    root = body.get("GET_STATS_DATA", {}).get("STATISTICAL_DATA", {})
    class_inf = root.get("CLASS_INF", {}).get("CLASS_OBJ", [])
    if isinstance(class_inf, dict):
        class_inf = [class_inf]
    # 「総人口」を指す分類コードとその次元IDを特定
    target_dim = None
    target_code = None
    for obj in class_inf:
        dim_id = obj.get("@id")
        cls = obj.get("CLASS", [])
        if isinstance(cls, dict):
            cls = [cls]
        for c in cls:
            name = str(c.get("@name", ""))
            if "総人口" in name or name.strip() in ("人口", "総数"):
                target_dim, target_code = dim_id, c.get("@code")
                break
        if target_code:
            break
    values = root.get("DATA_INF", {}).get("VALUE", [])
    if isinstance(values, dict):
        values = [values]
    series = []
    for v in values:
        if v.get("@area") != city_code:
            continue
        if target_dim and v.get("@" + target_dim) != target_code:
            continue
        raw = v.get("$")
        try:
            val = int(float(str(raw).replace(",", "")))
        except (ValueError, TypeError):
            continue
        series.append((str(v.get("@time", "")), val))
    series.sort(key=lambda x: x[0])
    return series


def _classify_trend(prev: int, latest: int):
    """直近2時点の増減率からラベルを返す（1調査期=約5年想定・しきい値は調整可）。"""
    if prev <= 0:
        return None
    chg = (latest - prev) / prev
    if chg > 0.03:
        return "増加"
    elif chg > 0.005:
        return "微増"
    elif chg < -0.03:
        return "減少"
    elif chg < -0.005:
        return "微減"
    else:
        return "横ばい"


def _population_trend(series):
    """series: 時刻昇順の [(time, value)] → (最新値, 傾向ラベル)。
    傾向は『最新年 と その1つ前』で判定する。古い年を基準にすると
    長期の増加が出て実態(直近の減少)と食い違うため（このバグの修正点）。"""
    if not series:
        return None, None
    latest_time, latest = series[-1]
    if len(series) < 2:
        logger.info("population: 1時点のみ %s(%s) → trend=None", latest_time, latest)
        return latest, None
    prev_time, prev = series[-2]
    trend = _classify_trend(prev, latest)
    chg = (latest - prev) / prev if prev > 0 else float("nan")
    logger.info("population trend %s(%s)->%s(%s) chg=%.2f%% => %s",
                prev_time, prev, latest_time, latest, chg * 100, trend)
    return latest, trend


def fetch_population(city_code: str, appid: str,
                     stats_data_id: str = "0000020201"):
    """社会・人口統計体系 市区町村データから総人口と『直近の』傾向を取得（best-effort）。"""
    params = {"appId": appid, "statsDataId": stats_data_id,
              "cdArea": city_code, "limit": 500}
    r = requests.get(f"{ESTAT_BASE}/getStatsData", params=params, timeout=40)
    r.raise_for_status()
    body = r.json()
    status = body.get("GET_STATS_DATA", {}).get("RESULT", {}).get("STATUS")
    if status != 0:
        return None, None
    series = _parse_population_series(body, city_code)
    return _population_trend(series)

# ---------- 束ねる ----------
def enrich(lat: Optional[float], lon: Optional[float],
           reinfolib_key: Optional[str],
           estat_appid: Optional[str] = None,
           city_code: Optional[str] = None,
           estat_table: str = "0000020201") -> Enrichment:
    e = Enrichment()
    if lat is None or lon is None:
        e.notes.append("座標未取得のためエンリッチメントをスキップ")
        return e

    tasks = {}  # 用途地域・ハザード・周辺施設・人口を並列実行
    with ThreadPoolExecutor(max_workers=4) as ex:
        if reinfolib_key:
            tasks["ud"] = ex.submit(fetch_use_district, lat, lon, reinfolib_key)
            tasks["hz"] = ex.submit(fetch_hazard, lat, lon, reinfolib_key)
            tasks["fa"] = ex.submit(fetch_facilities, lat, lon, reinfolib_key)
        if estat_appid and city_code:
            tasks["pop"] = ex.submit(fetch_population, city_code, estat_appid, estat_table)

    if reinfolib_key:
        try:
            e.use_district = tasks["ud"].result()
            if not e.use_district:
                e.notes.append("用途地域：該当ポリゴンを特定できず（要確認）")
        except Exception as ex_:
            e.notes.append(f"用途地域取得失敗: {ex_}")
        try:
            e.hazard = tasks["hz"].result()
            if e.hazard.checked and not e.hazard.any_hit():
                e.notes.append("ハザード：洪水/土砂/津波/高潮の指定区域に該当なし")
        except Exception as ex_:
            e.notes.append(f"ハザード取得失敗: {ex_}")
        try:
            e.facility = tasks["fa"].result()
        except Exception as ex_:
            e.notes.append(f"周辺施設取得失敗: {ex_}")
    else:
        e.notes.append("REINFOLIB_KEY未設定のため用途地域・ハザードをスキップ")

    if "pop" in tasks:
        try:
            pop, trend = tasks["pop"].result()
            e.population = pop
            e.population_trend = trend
            if pop is None:
                e.notes.append("人口：統計表から総人口を取得できず（統計表IDの確認要）")
        except Exception as ex_:
            e.notes.append(f"人口取得失敗: {ex_}")
    else:
        e.notes.append("人口：ESTAT_APPID未設定のため未取得")
    return e
