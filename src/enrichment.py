# -*- coding: utf-8 -*-
"""Data Enrichment（Phase D）：座標→公的データ付与。

実装：
  - 用途地域（reinfolib XKT002）
  - 区域区分（reinfolib XKT001）市街化区域／市街化調整区域
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
from .osm import fetch_shops_around
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
    # 地盤・区域系（XKT025/020/022/021/016）
    liquefaction: Optional[str] = None      # 「やや液状化しにくい」等の説明文
    liquefaction_level: Optional[int] = None
    embankment: Optional[str] = None        # 大規模盛土造成地の種別（谷埋め型など）
    steep_slope: bool = False               # 急傾斜地崩壊危険区域
    landslide_zone: bool = False            # 地すべり防止地区
    danger_zone: Optional[str] = None       # 災害危険区域の指定事由
    notes: List[str] = field(default_factory=list)

    def any_hit(self) -> bool:
        return bool(self.flood_rank or self.sediment or self.tsunami
                    or self.storm_surge or self.steep_slope
                    or self.landslide_zone or self.danger_zone
                    or self.embankment)


@dataclass
class FacilityResult:
    checked: bool = False
    nearest_station_m: Optional[int] = None
    nearest_station_name: Optional[str] = None
    nearest_hospital_m: Optional[int] = None
    nearest_school_m: Optional[int] = None
    hospital_count_1km: int = 0
    # 生活利便のために足したもの。取れなければ None のままにして評価に入れない。
    nearest_preschool_m: Optional[int] = None
    preschool_count_1km: int = 0
    nearest_library_m: Optional[int] = None
    nearest_hall_m: Optional[int] = None
    welfare_count_1km: int = 0
    notes: List[str] = field(default_factory=list)


@dataclass
class Enrichment:
    use_district: Optional[str] = None
    urbanization: Optional[str] = None
    hazard: HazardResult = field(default_factory=HazardResult)
    facility: FacilityResult = field(default_factory=FacilityResult)
    population: Optional[int] = None
    population_trend: Optional[str] = None   # "増加" / "微増" / "横ばい" / "減少"
    # 学区（XKT004/005）
    elementary_district: Optional[str] = None
    junior_district: Optional[str] = None
    # 250mメッシュの将来推計人口（XKT013）。市区町村単位より地点の実態に近い。
    mesh_pop_now: Optional[float] = None
    mesh_pop_future: Optional[float] = None
    mesh_pop_change_pct: Optional[float] = None
    # 買い物施設（OpenStreetMap）
    shops: Any = None
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


# ---------- 区域区分（市街化区域／市街化調整区域） ----------
# 都市計画決定情報（区域区分）XKT001。実データで確認したこと：
#   - 区分は area_classification_ja に日本語で入る（kubun_id は 21/22/23）
#   - 21「都市計画区域」の面の中に、22「市街化区域」23「市街化調整区域」が
#     重なって入っている。ひとつの地点が 21 と 22/23 の両方に当たる
# 欲しいのは内側の 22/23 なので、21 は読み飛ばす。
URBANIZATION_TOKENS = ["市街化調整区域", "市街化区域"]


def _extract_urbanization(props: dict) -> Optional[str]:
    """区分名だけを見る。他の項目（市区町村名など）に「市街化」の字が
    紛れ込んだものを拾わないよう、値を総なめにはしない。"""
    v = props.get("area_classification_ja")
    if not isinstance(v, str):
        return None
    for tok in URBANIZATION_TOKENS:
        if tok in v:
            return tok
    return None


def fetch_urbanization(lat, lon, key, zoom=15) -> Optional[str]:
    """市街化区域か市街化調整区域か（XKT001）。分からなければ None。

    用途地域と違い、内包判定できなかったときにタイル代表値へ落とさない。
    ひとつのタイルには市街化区域と市街化調整区域が普通に同居していて、
    どちらかを代表に選ぶのは当てずっぽうになる。この項目は採点で最も重い
    減点（リスク15点満点を3点以下に抑える）に直結するので、当てずっぽうで
    付けるくらいなら未取得のままにする。
    """
    x, y = latlon_to_tile(lat, lon, zoom)
    feats = _reinfolib_tile("XKT001", key, zoom, x, y)
    for f in feats:
        if point_in_geometry(lon, lat, f.get("geometry") or {}):
            u = _extract_urbanization(f.get("properties", {}) or {})
            if u:
                return u
    return None


# ---------- ハザード ----------
def _containing_props(lat, lon, feats) -> List[dict]:
    out = []
    for f in feats:
        if point_in_geometry(lon, lat, f.get("geometry") or {}):
            out.append(f.get("properties", {}) or {})
    return out


# ---------- 学区・将来人口・地盤（項目名は実データで確認済み） ----------
def _first_str(props: dict, *keys) -> Optional[str]:
    for k in keys:
        v = props.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def fetch_school_districts(lat, lon, key, zoom=14):
    """小学校区・中学校区の学校名。XKT004/005 はポリゴンで返る。

    項目名は実データで確認：小学校区 A27_004_ja、中学校区 A32_004_ja。
    """
    x, y = latlon_to_tile(lat, lon, zoom)

    def get(spec):
        api, field_name = spec
        try:
            feats = _reinfolib_tile(api, key, zoom, x, y)
        except Exception:
            return api, None
        for props in _containing_props(lat, lon, feats):
            name = _first_str(props, field_name)
            if name:
                return api, name
        return api, None

    got = dict(_parallel(get, [("XKT004", "A27_004_ja"),
                               ("XKT005", "A32_004_ja")], workers=2))
    return got.get("XKT004"), got.get("XKT005")


def _mesh_value(props: dict, year: int) -> Optional[float]:
    """将来推計人口メッシュから、その年の総人口を取り出す。

    総人口の項目名は PT00_2025 のような形だが、O と 0 が紛らわしく実データの
    表示だけでは綴りを確定できない。年で終わるキーを拾い、秘匿フラグ
    （HITOKU****）は除く、という取り方にして綴りに依存しないようにする。
    """
    for k, v in props.items():
        if k.startswith("HITOKU") or not k.endswith(str(year)):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def fetch_future_population(lat, lon, key, zoom=14,
                            base_year=2025, target_year=2050):
    """250mメッシュの将来推計人口（XKT013）。その地点の人口の増減見通し。

    市区町村全体の人口動向より、実際に住む場所の見通しに近い。
    """
    x, y = latlon_to_tile(lat, lon, zoom)
    try:
        feats = _reinfolib_tile("XKT013", key, zoom, x, y)
    except Exception:
        return None, None, None
    for props in _containing_props(lat, lon, feats):
        now = _mesh_value(props, base_year)
        future = _mesh_value(props, target_year)
        if now and future is not None and now > 0:
            return now, future, round((future - now) / now * 100.0, 1)
        if now is not None:
            return now, future, None
    return None, None, None


def fetch_ground_hazards(lat, lon, key, zoom=14) -> dict:
    """液状化・盛土・急傾斜地・地すべり・災害危険区域。

    液状化（XKT025）は note に「やや液状化しにくい」のような説明文が入るので、
    数値レベルの凡例を推測せず、その文言をそのまま持ち回る。
    """
    out = {}

    def get(spec):
        api, z = spec
        xx, yy = latlon_to_tile(lat, lon, z)
        try:
            return api, _containing_props(lat, lon,
                                          _reinfolib_tile(api, key, z, xx, yy))
        except Exception:
            return api, None

    got = dict(_parallel(get, [("XKT025", zoom), ("XKT020", zoom),
                               ("XKT022", 13), ("XKT021", 13),
                               ("XKT016", 13)], workers=5))

    liq = got.get("XKT025") or []
    if liq:
        out["liquefaction"] = _first_str(liq[0], "note")
        try:
            out["liquefaction_level"] = int(liq[0].get("liquefaction_tendency_level"))
        except (TypeError, ValueError):
            pass
    emb = got.get("XKT020") or []
    if emb:
        out["embankment"] = (_first_str(emb[0], "embankment_classification")
                             or "大規模盛土造成地")
    out["steep_slope"] = bool(got.get("XKT022"))
    out["landslide_zone"] = bool(got.get("XKT021"))
    dz = got.get("XKT016") or []
    if dz:
        out["danger_zone"] = (_first_str(dz[0], "A48_007_name_ja", "A48_008_ja")
                              or "災害危険区域")
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
    # ここから生活利便のために足したレイヤ。落ちても診断は続ける。
    try:
        ps = fetch_points_around("XKT007", key, lat, lon)  # 保育園・幼稚園
        n = _nearest(ps, lat, lon)
        if n:
            fr.nearest_preschool_m = int(n[0])
        fr.preschool_count_1km = sum(
            1 for (la, lo, pr) in ps if haversine_m(lat, lon, la, lo) <= 1000)
        ok += 1
    except Exception as e:
        fr.notes.append(f"保育園・幼稚園取得失敗: {e}")
    try:
        lb = fetch_points_around("XKT017", key, lat, lon)  # 図書館
        n = _nearest(lb, lat, lon)
        if n:
            fr.nearest_library_m = int(n[0])
        ok += 1
    except Exception as e:
        fr.notes.append(f"図書館取得失敗: {e}")
    try:
        hl = fetch_points_around("XKT018", key, lat, lon)  # 役場・集会施設
        n = _nearest(hl, lat, lon)
        if n:
            fr.nearest_hall_m = int(n[0])
        ok += 1
    except Exception as e:
        fr.notes.append(f"役場・集会施設取得失敗: {e}")
    try:
        wf = fetch_points_around("XKT011", key, lat, lon)  # 福祉施設
        fr.welfare_count_1km = sum(
            1 for (la, lo, pr) in wf if haversine_m(lat, lon, la, lo) <= 1000)
        ok += 1
    except Exception as e:
        fr.notes.append(f"福祉施設取得失敗: {e}")
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

    tasks = {}  # 用途地域・区域区分・ハザード・周辺施設・人口を並列実行
    with ThreadPoolExecutor(max_workers=5) as ex:
        if reinfolib_key:
            tasks["ud"] = ex.submit(fetch_use_district, lat, lon, reinfolib_key)
            tasks["ur"] = ex.submit(fetch_urbanization, lat, lon, reinfolib_key)
            tasks["hz"] = ex.submit(fetch_hazard, lat, lon, reinfolib_key)
            tasks["fa"] = ex.submit(fetch_facilities, lat, lon, reinfolib_key)
            tasks["sd"] = ex.submit(fetch_school_districts, lat, lon, reinfolib_key)
            tasks["fp"] = ex.submit(fetch_future_population, lat, lon, reinfolib_key)
            tasks["gh"] = ex.submit(fetch_ground_hazards, lat, lon, reinfolib_key)
        # 買い物先は国交省のAPIに無いのでOpenStreetMapから。鍵は要らない。
        tasks["shop"] = ex.submit(fetch_shops_around, lat, lon)
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
            e.urbanization = tasks["ur"].result()
            if not e.urbanization:
                # 非線引き（区域区分が定められていない都市計画区域）なのか、
                # データが無いだけなのかは、この応答からは区別できない。
                # 市街化区域だと決めてしまわず、未取得として出す。
                e.notes.append("区域区分：未取得（市街化区域か市街化調整区域かは要確認）")
        except Exception as ex_:
            e.notes.append(f"区域区分取得失敗: {ex_}")
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
        try:
            e.elementary_district, e.junior_district = tasks["sd"].result()
        except Exception as ex_:
            e.notes.append(f"学区取得失敗: {ex_}")
        try:
            e.mesh_pop_now, e.mesh_pop_future, e.mesh_pop_change_pct = \
                tasks["fp"].result()
        except Exception as ex_:
            e.notes.append(f"将来推計人口取得失敗: {ex_}")
        try:
            for k, v in (tasks["gh"].result() or {}).items():
                setattr(e.hazard, k, v)
        except Exception as ex_:
            e.notes.append(f"地盤情報取得失敗: {ex_}")
    else:
        e.notes.append("REINFOLIB_KEY未設定のため用途地域・区域区分・ハザードをスキップ")

    try:
        e.shops = tasks["shop"].result()
        if e.shops is not None:
            e.notes.extend(e.shops.notes)
    except Exception as ex_:
        e.notes.append(f"買い物施設取得失敗: {ex_}")

    if "pop" in tasks:
        try:
            pop, trend = tasks["pop"].result()
            e.population = pop
            e.population_trend = trend
            if pop is None:
                e.notes.append("人口：統計表から総人口を取得できず（統計表IDの確認要）")
        except Exception as ex_:
            e.notes.append(f"人口取得失敗: {ex_}")
    # e-Stat は使っていない（ESTAT_APPID を設定していない）。人口の動向は
     # 国交省の250mメッシュ将来推計から出しているので、ここで「未取得」と
     # 出す必要はない。内部の環境変数名を利用者に見せる意味もない。
    return e
