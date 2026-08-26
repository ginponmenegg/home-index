# -*- coding: utf-8 -*-
"""データモデル（Phase C スキーマの実装最小版）。

全ての外部/入力データは出典・信頼度・鮮度を持てるようにする（指示書 第15・39章）。
ここでは MVP に必要な最小限の型付きモデルのみ定義する。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any, List, Dict
import datetime


# ---- 信頼度・状態（第16章） ----
CONFIDENCE = ("S", "A", "B", "C", "D", "E")
FIELD_STATUS = ("confirmed", "estimated", "unknown", "unconfirmed", "not_listed")


@dataclass
class Meta:
    """1つの事実値に付随する出典メタデータ。"""
    source: str = "unknown"
    source_url: Optional[str] = None
    confidence: str = "E"
    status: str = "unknown"
    retrieved_at: Optional[str] = None
    data_updated_at: Optional[str] = None
    raw_value: Optional[str] = None


@dataclass
class SubjectProperty:
    """診断対象の物件（手入力前提・第3章）。面積は㎡が正（Phase C確定）。"""
    property_type: str            # "chuko_kodate" | "shinchiku_kodate"
    price: int                    # 売出価格(円)
    address: str
    land_area_m2: Optional[float] = None
    building_area_m2: Optional[float] = None
    build_year: Optional[int] = None   # 西暦
    layout: Optional[str] = None
    structure: Optional[str] = None
    nearest_station: Optional[str] = None
    station_walk_min: Optional[int] = None   # 駅 or バス停まで徒歩
    bus_min: Optional[int] = None            # バス便：駅までのバス乗車分（None=バス便でない）
    municipality_code: Optional[str] = None
    district_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city_planning: Optional[str] = None
    renovated: bool = False        # リフォーム済み（無料版は有無のみ・内容は見ない）


@dataclass
class MansionSubject:
    """診断対象のマンション（手入力前提）。戸建とは別フローで扱う。

    駅徒歩を `station_walk_min` としているのは、既存の score_location /
    score_asset がこの名前を見るため。名前を合わせると流用が効く。
    """
    address: str
    name: Optional[str] = None                  # マンション名（表示と座標の精度向上に使う）
    price: Optional[int] = None                 # 売出価格(円)
    build_year: Optional[int] = None            # 西暦
    station_walk_min: Optional[int] = None      # 駅まで徒歩(分)
    exclusive_area_m2: Optional[float] = None   # 専有面積(㎡)。㎡単価の土台
    floor: Optional[int] = None                 # 所在階
    total_floors: Optional[int] = None          # 総階数
    direction: Optional[str] = None             # 向き（南/南東/…/不明）
    layout: Optional[str] = None                # 間取り（表示のみ）
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    municipality_code: Optional[str] = None
    district_name: Optional[str] = None
    management_fee: Optional[int] = None        # 管理費(円/月)
    repair_fund: Optional[int] = None           # 修繕積立金(円/月)
    renovated: bool = False                     # リフォーム済み（有無のみ・内容は見ない）
    property_type: str = "chuko_mansion"


@dataclass
class Transaction:
    """XIT001 の1取引レコードを正規化したもの。"""
    trade_price: Optional[int]        # 取引価格(円)
    type: Optional[str]               # 取引種類（宅地(土地と建物) 等）
    municipality_code: Optional[str]
    district_name: Optional[str]
    land_area_m2: Optional[float]     # Area
    building_area_m2: Optional[float] # TotalFloorArea
    build_year: Optional[int]         # BuildingYear → 西暦
    period_year: Optional[int]        # Period の年
    period_quarter: Optional[int]     # Period の四半期
    city_planning: Optional[str]
    structure: Optional[str]
    layout: Optional[str]
    latitude: Optional[float] = None      # 町名ジオコーディング結果（中心）
    longitude: Optional[float] = None
    distance_m: Optional[float] = None    # 対象物件からの距離(m)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Comparable:
    """類似取引（類似度と内訳つき）。"""
    txn: Transaction
    similarity_score: float
    similarity_breakdown: Dict[str, float]
    subject_price_estimate: Optional[int]      # この取引から復元した対象物件の推定価格
    price_basis: str                            # "building" | "land"
    time_adjusted: bool


@dataclass
class PriceAnalysis:
    """価格分析の結果。適正価格は推定であり絶対値ではない（第41章）。"""
    estimate_low: Optional[int]
    estimate_mid: Optional[int]
    estimate_high: Optional[int]
    verdict: str                 # 割安 / 概ね適正 / 割高 / 判定不可
    deviation_pct: Optional[float]
    confidence: str              # low / mid / high
    comparable_count: int
    note: str
    comparables: List[Comparable] = field(default_factory=list)
    # 追加の透明性指標
    used_count: int = 0                 # 実際に価格算出に使った件数
    trimmed_outliers: int = 0           # 外れ値として除外した件数
    dispersion_pct: Optional[float] = None   # レンジ幅/中央値(%)
    unit_building_median: Optional[int] = None  # 建物延床 ㎡単価 中央値(円)
    unit_land_median: Optional[int] = None      # 土地 ㎡単価 中央値(円)


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")
