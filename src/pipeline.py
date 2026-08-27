# -*- coding: utf-8 -*-
"""1物件パイプライン（指示書 第36章の最小一気通貫）。

住所→座標→取引取得→類似抽出→価格分析 までを通す。
スコアリング/最終診断は下流（Phase F）で、ここは価格評価まで。
"""
from __future__ import annotations
from typing import List, Optional
import datetime

from concurrent.futures import ThreadPoolExecutor
from .models import (SubjectProperty, MansionSubject, Transaction,
                     PriceAnalysis, now_iso)
from .geocoding import make_geocoder, GsiGeocoder
from .reinfolib import ReinfolibClient
from .comparable import (extract_comparables, DEFAULT_WEIGHTS, NEWBUILD_WEIGHTS,
                         NEIGHBOR_RADIUS_M, DETACHED_TYPES)
from .price_analysis import analyze_price
from .enrichment import enrich, haversine_m
from .citycode import CityCodeResolver
from .config import CONFIG
from .loan import compute_loan, LoanResult
from .scoring import build_diagnosis, Diagnosis
from .mansion_price import (analyze_mansion_price, extract_mansion_comparables,
                            is_mansion_txn, same_building_candidates)
from .mansion_scoring import build_mansion_diagnosis


_DISTRICT_GEO: dict = {}   # (prefix, 町名) -> (lat, lon) プロセス内キャッシュ


def _p(fn, items, workers=8):
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


def _geocode_districts(subject: SubjectProperty, txns, reinfolib_key=None,
                       max_unique: int = 120):
    """取引の町名を並列ジオコーディングし距離を付与（結果はプロセス内キャッシュ）。全国対応。"""
    if subject.latitude is None or subject.longitude is None:
        return
    pref_name, city = CityCodeResolver(reinfolib_key).info(subject.municipality_code)
    if not pref_name or not city:
        return  # 市名不明 → 距離付与はスキップ（町名一致で評価）
    prefix = f"{pref_name}{city}"
    geocoder = GsiGeocoder()

    targets = []
    for t in txns:
        if t.type in DETACHED_TYPES and t.district_name \
                and t.district_name not in targets:
            targets.append(t.district_name)
    targets = targets[:max_unique]
    to_fetch = [n for n in targets if (prefix, n) not in _DISTRICT_GEO]

    def gc(name):
        try:
            g = geocoder.geocode(f"{prefix}{name}")
            return name, (g.latitude, g.longitude)
        except Exception:
            return name, (None, None)

    for name, latlon in _p(gc, to_fetch, 8):
        _DISTRICT_GEO[(prefix, name)] = latlon

    for t in txns:
        if not t.district_name:
            continue
        latlon = _DISTRICT_GEO.get((prefix, t.district_name))
        if latlon and latlon[0] is not None:
            t.latitude, t.longitude = latlon
            t.distance_m = haversine_m(subject.latitude, subject.longitude,
                                       latlon[0], latlon[1])


class DiagnosisResult:
    def __init__(self):
        self.subject: Optional[SubjectProperty] = None
        self.geocode = None
        self.transactions_count = 0
        self.price: Optional[PriceAnalysis] = None
        self.enrichment = None
        self.loan: Optional[LoanResult] = None
        self.diagnosis: Optional[Diagnosis] = None
        self.warnings: List[str] = []
        # 同じ建物の別住戸かもしれない事例（マンションのみ・断定はしない）
        self.same_building: List = []
        self.generated_at = now_iso()


def _geocode_mansion_districts(subject, txns, reinfolib_key=None,
                               max_unique: int = 120):
    """マンション成約の町名に座標を付ける。戸建版と同じキャッシュを使う。"""
    if subject.latitude is None or subject.longitude is None:
        return
    pref_name, city = CityCodeResolver(reinfolib_key).info(subject.municipality_code)
    if not pref_name or not city:
        return
    prefix = f"{pref_name}{city}"
    geocoder = GsiGeocoder()

    targets = []
    for t in txns:
        if is_mansion_txn(t) and t.district_name and t.district_name not in targets:
            targets.append(t.district_name)
    targets = targets[:max_unique]
    to_fetch = [n for n in targets if (prefix, n) not in _DISTRICT_GEO]

    def gc(name):
        try:
            g = geocoder.geocode(f"{prefix}{name}")
            return name, (g.latitude, g.longitude)
        except Exception:
            return name, (None, None)

    for name, latlon in _p(gc, to_fetch, 8):
        _DISTRICT_GEO[(prefix, name)] = latlon

    for t in txns:
        if not t.district_name:
            continue
        latlon = _DISTRICT_GEO.get((prefix, t.district_name))
        if latlon and latlon[0] is not None:
            t.latitude, t.longitude = latlon
            t.distance_m = haversine_m(subject.latitude, subject.longitude,
                                       latlon[0], latlon[1])


def run_mansion_pipeline(subject: MansionSubject,
                         reinfolib_key: Optional[str] = None,
                         google_key: Optional[str] = None,
                         trade_years: Optional[List[int]] = None,
                         annual_rate: float = 0.0,
                         mock: bool = False,
                         k_nearest: int = 6,
                         max_year_gap: int = 25,
                         annual_income: Optional[int] = None,
                         down_payment: int = 0,
                         loan_rate: float = 0.0125,
                         loan_years: int = 35,
                         estat_appid: Optional[str] = None,
                         estat_table: str = "0000020201") -> DiagnosisResult:
    """マンション1件を診断する。戸建の run_pipeline とは独立した経路。

    取引の取得・ジオコーディング・ハザードは戸建と同じ部品を使い、
    価格分析（㎡単価）とスコアの組み立てだけマンション用に差し替えている。
    """
    result = DiagnosisResult()
    result.subject = subject
    current_year = datetime.date.today().year
    if trade_years is None:
        # マンションは1つの市区町村で拾える成約が戸建より少ないので、
        # 戸建の3年より長く遡る。古さは類似度側で割り引く。
        span = CONFIG.get("mansion_trade_years", 10)
        trade_years = [current_year - i for i in range(1, span + 1)]

    # 1) 住所 → 座標。マンション名があれば添えて引く。建物まで当たれば座標が
    #    正確になり、近隣事例の距離判定がそのぶん正しくなる。当たらなければ
    #    住所だけで引き直す（名前で外すくらいなら住所の方が確実）。
    if not mock:
        geocoder = make_geocoder(google_key)
        queries = []
        if subject.name:
            queries.append((f"{subject.address} {subject.name}", True))
        queries.append((subject.address, False))
        last_error = None
        for query, with_name in queries:
            try:
                gc = geocoder.geocode(query)
            except Exception as e:
                last_error = e
                continue
            result.geocode = gc
            subject.latitude, subject.longitude = gc.latitude, gc.longitude
            if with_name:
                result.warnings.append(
                    "マンション名を含めて座標を特定しました（近隣事例の距離がより正確になります）")
            break
        else:
            result.warnings.append(f"ジオコーディング失敗: {last_error}")

    # 2) 成約事例の取得
    txns: List[Transaction] = []
    if mock:
        from .mockdata import sample_transactions
        txns = sample_transactions()
        result.warnings.append("MOCKモード: サンプルデータを使用（実データではありません）")
    elif not subject.municipality_code:
        result.warnings.append("市区町村コードが解決できず取引取得をスキップ")
    elif not reinfolib_key:
        result.warnings.append("REINFOLIB_KEY 未設定のため取引取得をスキップ")
    else:
        try:
            client = ReinfolibClient(reinfolib_key)
            txns = client.get_transactions(subject.municipality_code, trade_years)
        except Exception as e:
            result.warnings.append(f"取引取得失敗: {e}")
    result.transactions_count = len(txns)

    # 3) 類似事例の抽出。近隣で足りなければ市内全域へ広げる
    if not mock:
        _geocode_mansion_districts(subject, txns, reinfolib_key)

    comps = extract_mansion_comparables(subject, txns, current_year,
                                        radius_m=NEIGHBOR_RADIUS_M,
                                        max_year_gap=max_year_gap)
    if len(comps) < 5:
        wider = extract_mansion_comparables(subject, txns, current_year,
                                            radius_m=5000,
                                            max_year_gap=max_year_gap)
        if len(wider) > len(comps):
            comps = wider
            result.warnings.append(
                "同町・近接のマンション成約が少ないため、近接範囲を約5kmに拡大して算出")
    if len(comps) < 3:
        comps = extract_mansion_comparables(subject, txns, current_year,
                                            radius_m=None,
                                            max_year_gap=max_year_gap)
        result.warnings.append(
            "近接の類似成約が少ないため、市内全域のマンション事例で参考価格を算出")

    # 4) ㎡単価による価格分析
    result.price = analyze_mansion_price(subject, comps, current_year,
                                         annual_rate, k_nearest=k_nearest)

    # 同じ建物の可能性がある事例。類似度で上位に来るので価格には既に効いて
    # いるが、根拠として別枠で見せられるよう取り出しておく。
    result.same_building = same_building_candidates(subject, comps)
    if result.same_building:
        result.warnings.append(
            f"同じ町名・同じ築年の成約が{len(result.same_building)}件あります"
            "（同一マンションの可能性が高い事例）")

    # 5) 用途地域・ハザード・周辺施設（座標ベースなので戸建と共通）
    if not mock:
        result.enrichment = enrich(subject.latitude, subject.longitude,
                                   reinfolib_key, estat_appid=estat_appid,
                                   city_code=subject.municipality_code,
                                   estat_table=estat_table)
        if result.enrichment:
            result.warnings.extend(result.enrichment.notes)

    # 6) ローン。管理費と修繕積立金は住み続ける限り毎月出ていくので、
    #    返済負担率にも含めて見る（戸建は monthly_extra=0 のまま）。
    monthly_extra = (subject.management_fee or 0) + (subject.repair_fund or 0)
    result.loan = compute_loan(subject.price or 0, down_payment, loan_rate,
                               loan_years, annual_income,
                               monthly_extra=monthly_extra)

    # 7) 採点
    e = result.enrichment
    result.diagnosis = build_mansion_diagnosis(
        subject, result.price, result.loan,
        use_district=(e.use_district if e else None),
        urbanization=(e.urbanization if e else None),
        hazard=(e.hazard if e else None),
        facility=(e.facility if e else None),
        shops=(e.shops if e else None),
        pop_change_pct=(e.mesh_pop_change_pct if e else None),
        current_year=current_year)
    return result


def run_pipeline(subject: SubjectProperty,
                 reinfolib_key: Optional[str] = None,
                 google_key: Optional[str] = None,
                 trade_years: Optional[List[int]] = None,
                 annual_rate: float = 0.0,
                 mock: bool = False,
                 weights=None,
                 k_nearest: int = 6,
                 max_year_gap: int = 25,
                 annual_income: Optional[int] = None,
                 down_payment: int = 0,
                 loan_rate: float = 0.0125,
                 loan_years: int = 35,
                 estat_appid: Optional[str] = None,
                 estat_table: str = "0000020201") -> DiagnosisResult:
    result = DiagnosisResult()
    result.subject = subject
    current_year = datetime.date.today().year
    if trade_years is None:
        trade_years = [current_year - 1, current_year - 2, current_year - 3]

    # 1) ジオコーディング（座標）。mock時はスキップ可
    if not mock:
        try:
            geocoder = make_geocoder(google_key)
            gc = geocoder.geocode(subject.address)
            result.geocode = gc
            subject.latitude, subject.longitude = gc.latitude, gc.longitude
        except Exception as e:
            result.warnings.append(f"ジオコーディング失敗: {e}")

    # 2) 取引取得
    txns: List[Transaction] = []
    if mock:
        from .mockdata import sample_transactions
        txns = sample_transactions()
        result.warnings.append("MOCKモード: サンプルデータを使用（実データではありません）")
    else:
        if not subject.municipality_code:
            result.warnings.append(
                "municipality_code 未指定のため取引取得をスキップ（--city で指定）")
        elif not reinfolib_key:
            result.warnings.append("REINFOLIB_KEY 未設定のため取引取得をスキップ")
        else:
            try:
                client = ReinfolibClient(reinfolib_key)
                txns = client.get_transactions(subject.municipality_code, trade_years)
            except Exception as e:
                result.warnings.append(f"取引取得失敗: {e}")
    result.transactions_count = len(txns)

    # 3) 類似抽出
    # 町名を距離付与（同町＋近接に絞るため）。mock時・対象外市はスキップ。
    if not mock:
        _geocode_districts(subject, txns, reinfolib_key)

    newbuild_only = (subject.property_type == "shinchiku_kodate")
    w = weights or (NEWBUILD_WEIGHTS if newbuild_only else DEFAULT_WEIGHTS)

    def _ext(radius, nb, wts):
        return extract_comparables(subject, txns, current_year, weights=wts,
                                   max_year_gap=max_year_gap, newbuild_only=nb,
                                   radius_m=radius)

    # 同町＋2km以内で抽出。少なければ近接範囲を拡大→最後は全市。
    comps = _ext(NEIGHBOR_RADIUS_M, newbuild_only, w)
    if len(comps) < 5:
        wider = _ext(5000, newbuild_only, w)
        if len(wider) > len(comps):
            comps = wider
            result.warnings.append("同町・近接の事例が少ないため、近接範囲を約5kmに拡大して算出")
    if newbuild_only and len(comps) < 3:
        comps = _ext(None, False, DEFAULT_WEIGHTS)
        result.warnings.append(
            "新築の近接成約が少ないため、中古を含む事例で参考価格を算出（新築プレミアムは別途考慮）")
    elif len(comps) < 3:
        comps = _ext(None, newbuild_only, w)
        result.warnings.append("近接の類似成約が少ないため、市内全域の事例で参考価格を算出")

    # 4) 価格分析
    result.price = analyze_price(subject, comps, current_year, annual_rate,
                                 k_nearest=k_nearest)

    # 5) エンリッチメント（用途地域・ハザード・人口。mock時はスキップ）
    if mock:
        result.enrichment = None
    else:
        result.enrichment = enrich(subject.latitude, subject.longitude,
                                   reinfolib_key, estat_appid=estat_appid,
                                   city_code=subject.municipality_code,
                                   estat_table=estat_table)
        if result.enrichment:
            result.warnings.extend(result.enrichment.notes)

    # 6) ローン計算（無料版：月々返済額まで）
    result.loan = compute_loan(subject.price, down_payment, loan_rate,
                               loan_years, annual_income)

    # 7) 100点スコア＋Critical Risk＋最終診断
    e = result.enrichment
    result.diagnosis = build_diagnosis(
        subject, result.price, result.loan,
        use_district=(e.use_district if e else None),
        urbanization=(e.urbanization if e else None),
        hazard=(e.hazard if e else None),
        facility=(e.facility if e else None),
        population_trend=(e.population_trend if e else None),
        shops=(e.shops if e else None),
        pop_change_pct=(e.mesh_pop_change_pct if e else None),
        current_year=current_year)
    return result
