# -*- coding: utf-8 -*-
"""1物件パイプライン（指示書 第36章の最小一気通貫）。

住所→座標→取引取得→類似抽出→価格分析 までを通す。
スコアリング/最終診断は下流（Phase F）で、ここは価格評価まで。
"""
from __future__ import annotations
from typing import List, Optional
import datetime

from concurrent.futures import ThreadPoolExecutor
from .models import SubjectProperty, Transaction, PriceAnalysis, now_iso
from .geocoding import make_geocoder, GsiGeocoder
from .reinfolib import ReinfolibClient
from .comparable import (extract_comparables, DEFAULT_WEIGHTS, NEWBUILD_WEIGHTS,
                         NEIGHBOR_RADIUS_M, DETACHED_TYPES)
from .price_analysis import analyze_price
from .enrichment import enrich, haversine_m
from .citycode import CityCodeResolver
from .loan import compute_loan, LoanResult
from .scoring import build_diagnosis, Diagnosis


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
        self.generated_at = now_iso()


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
        current_year=current_year)
    return result
