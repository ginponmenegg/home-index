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
    station_walk_min: Optional[int] = None      # 駅 or バス停まで徒歩(分)
    bus_min: Optional[int] = None               # バス便：駅までのバス乗車分
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


@dataclass
class ProDetail:
    """PROで追加入力してもらう詳細（戸建）。

    仕様書§1の原則：ここに入る情報は**物件スコアとリスクにのみ**反映し、
    価格推定には一切渡さない。価額の精度を売らないための線引きなので、
    analyze_price 側にこのオブジェクトを持ち込まないこと。

    値は「わからない」を必ず持つ。埋まっていない項目は評価に反映せず、
    情報充足度だけを下げる（第14章）。PROはこの充足度を上げるサービス。
    """
    # ---- 建物内部の状態（ok / concern / unknown）----
    leak: str = "unknown"            # 雨漏りの跡
    termite: str = "unknown"         # シロアリ・腐朽
    tilt: str = "unknown"            # 床の傾き
    plumbing: str = "unknown"        # 給排水の不具合
    foundation: str = "unknown"      # 基礎のひび

    # ---- 主要設備の更新時期（le5 / le10 / gt10 / unknown）----
    water_heater: str = "unknown"    # 給湯器
    kitchen: str = "unknown"
    bath: str = "unknown"
    electrical: str = "unknown"      # 分電盤・配線

    # ---- 構造・性能 ----
    structure_kind: Optional[str] = None   # 木造在来/2x4/鉄骨/RC など
    insulation: str = "unknown"      # high / standard / low / unknown
    quake_retrofit: str = "unknown"  # done / none / unknown（耐震補強）
    inspection: str = "unknown"      # done / none / unknown（住宅診断の実施）

    # ---- 公的な認定・評価。中古では有無の差が大きい ----
    # 長期優良住宅：yes / no / unknown。中古は認定の承継手続きが要る。
    long_term_excellent: str = "unknown"
    # 住宅性能評価：construction(建設) / design(設計のみ) / existing(既存住宅)
    #               / none / unknown
    performance_cert: str = "unknown"
    # 耐震等級：g3 / g2 / g1 / unknown
    quake_grade: str = "unknown"
    # 既存住宅売買瑕疵保険の付保：yes / no / unknown
    defect_insurance: str = "unknown"

    # ---- リフォームの箇所（無料版は有無のみ。PROは箇所別に受ける）----
    reno_water: bool = False         # 水回り
    reno_exterior: bool = False      # 外壁・屋根
    reno_interior: bool = False      # 内装
    reno_pipes: bool = False         # 給排水管
    reno_year: Optional[int] = None  # 直近のリフォーム年

    # ---- 敷地・法規（リスク精査）----
    road_width: str = "unknown"      # ge4 / lt4 / none / unknown（接道の幅員）
    rebuildable: str = "unknown"     # yes / no / unknown（再建築可否）
    boundary: str = "unknown"        # fixed / unfixed / unknown（境界確定）
    encroachment: str = "unknown"    # none / exists / unknown（越境）

    def known_ratio(self, fields) -> float:
        """指定した項目のうち、答えが埋まっている割合。"""
        vals = [getattr(self, f, "unknown") for f in fields]
        known = sum(1 for v in vals if v not in (None, "", "unknown"))
        return known / len(vals) if vals else 0.0


@dataclass
class BuyerProfile:
    """購入者の家計の輪郭。返済の重さと、出口（何年住むか）を見るために使う。

    個別の借入可否は判断しない（仕様書§8-6）。一般的な試算の材料として扱う。
    """
    age: Optional[int] = None
    household_size: Optional[int] = None
    children: Optional[int] = None
    employment: str = "unknown"      # 正社員/契約/自営/パート など
    tenure_years: Optional[int] = None       # 勤続年数
    other_debt_monthly: Optional[int] = None  # 他の借入の月々返済(円)
    own_funds: Optional[int] = None  # 自己資金の総額(円)
    reserve: Optional[int] = None    # 手元に残す額(円)
    move_in: Optional[str] = None    # 入居予定時期
    hold_years: Optional[int] = None  # 何年住む見込みか


@dataclass
class MansionProDetail:
    """PROで追加入力してもらう詳細（マンション）。

    無料のマンション診断が「取得できない」として未評価のまま残しているのは、
    積立金の残高・大規模修繕の履歴・管理形態・滞納の有無。重要事項説明書と
    総会議事録を見れば分かるので、PROではそこを聞く。

    戸建と同じく、ここに入る情報は管理・資産性・リスクにのみ反映し、価格推定
    には渡さない（仕様書§1）。
    """
    # ---- 管理の健全性。マンションの良し悪しを一番分けるところ ----
    # 積立金の残高は入力に取らない。いくらあれば十分かの公的な目安が無く、
    # 点数にできないうえ、検討段階では重要事項調査報告書が手元に無いことが
    # ほとんどだから。聞く価値はあるので、質問文としては結果画面に出す。
    # 大規模修繕：recent(直近10年以内) / old(10年以上前) / never(未実施) / unknown
    major_repair: str = "unknown"
    # 長期修繕計画：long(30年以上) / short(あるが30年未満・期間不明)
    #               / none / unknown
    long_term_plan: str = "unknown"
    # 管理形態：full(全部委託) / partial(一部委託) / self(自主管理) / unknown
    management_form: str = "unknown"
    # 管理員：live_in(常駐) / daily(日勤) / rounds(巡回) / none / unknown
    manager_style: str = "unknown"
    # 滞納：none / few(少数) / many(多い) / unknown
    arrears: str = "unknown"
    # 値上げ予定：planned(計画的) / steep(急激) / none / unknown
    reserve_increase: str = "unknown"
    # 管理計画認定・管理適正評価：certified / applying / none / unknown
    management_cert: str = "unknown"
    # 共用部の状態：good / normal / concern / unknown
    common_area: str = "unknown"

    # ---- 専有部の状態（ok / concern / unknown）----
    plumbing: str = "unknown"        # 給排水の不具合
    sash: str = "unknown"            # サッシ・建具の不具合
    mold: str = "unknown"            # 結露・カビ
    tilt: str = "unknown"            # 床の傾き

    # ---- 主要設備の更新時期（le5 / le10 / gt10 / unknown）----
    water_heater: str = "unknown"
    kitchen: str = "unknown"
    bath: str = "unknown"

    # ---- リフォームの箇所 ----
    reno_water: bool = False
    reno_interior: bool = False
    reno_pipes: bool = False

    # ---- 権利・耐震 ----
    # 敷地の権利：ownership(所有権) / leasehold(借地権) / unknown
    land_right: str = "unknown"
    # 耐震診断：ok(実施・問題なし) / need(実施・要補強) / done(補強済み)
    #           / never(未実施) / unknown
    quake_diagnosis: str = "unknown"

    # ---- 認定・評価 ----
    performance_cert: str = "unknown"   # 戸建と同じ選択肢
    defect_insurance: str = "unknown"

    def known_ratio(self, fields) -> float:
        vals = [getattr(self, f, "unknown") for f in fields]
        known = sum(1 for v in vals if v not in (None, "", "unknown"))
        return known / len(vals) if vals else 0.0


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")
