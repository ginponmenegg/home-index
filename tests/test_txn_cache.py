# -*- coding: utf-8 -*-
"""取引データのプロセス内キャッシュ。ネットワーク不要。

■なぜ要るか
XIT001 は1リクエストで「1市区町村・1年分（4四半期すべて）」を返す。
実測（2026-09-10・船橋市12204・2024年）で 2,320件・約1.5MB。
戸建の診断は3年分、マンションは10年分を引くので、市区町村ひとつで
4〜15MBがプロセスに載る。Renderの512MBでは、放っておくと落ちる。

■過去にどうなっていたか
上限が「2000エントリ」だった。1エントリ1.5MBなので最大3GBを許す数字で、
上限として意味をなしていなかった。件数ではなく概算バイト数で持つ。

■生レコードを捨てた話
Transaction は XIT001 の生レコード（raw）を丸ごと抱えていたが、どこからも
読まれていなかった。捨てて 5.1MB → 1.5MB になった。戻すときは raw ではなく
名前付きフィールドで足すこと。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses

from src import reinfolib
from src.models import Transaction

# XIT001 が実際に返す形（フィールド名は実データで確認したもの）
REC = {
    "Type": "宅地(土地と建物)", "Region": "住宅地",
    "MunicipalityCode": "12204", "Prefecture": "千葉県",
    "Municipality": "船橋市", "DistrictName": "前原西",
    "TradePrice": "31800000", "PricePerUnit": "", "FloorPlan": "4LDK",
    "Area": "110", "UnitPrice": "", "LandShape": "長方形",
    "Frontage": "8.0", "TotalFloorArea": "95", "BuildingYear": "1998年",
    "Structure": "木造", "Use": "住宅", "Purpose": "住宅",
    "Direction": "南", "Classification": "市道", "Breadth": "4.0",
    "CityPlanning": "第一種低層住居専用地域", "CoverageRatio": "50",
    "FloorAreaRatio": "100", "Period": "2024年第3四半期",
    "Renovation": "未改装", "Remarks": "",
    "PriceCategory": "不動産取引価格情報", "DistrictCode": "012",
}


def _txns(n):
    return [reinfolib.normalize_txn(REC) for _ in range(n)]


# ---- 生レコードを持たないこと ---------------------------------------------

def test_a_transaction_does_not_carry_the_whole_api_record():
    """raw を戻すと、1市区町村・1年で 1.5MB → 5.1MB に戻る。"""
    names = {f.name for f in dataclasses.fields(Transaction)}
    assert "raw" not in names
    t = reinfolib.normalize_txn(REC)
    assert not hasattr(t, "raw")


def test_the_fields_we_actually_use_are_still_filled():
    """捨てたのは生レコードだけで、使っている値は落としていないこと。"""
    t = reinfolib.normalize_txn(REC)
    assert t.trade_price == 31800000
    assert t.type == "宅地(土地と建物)"
    assert t.municipality_code == "12204"
    assert t.district_name == "前原西"
    assert t.land_area_m2 == 110.0
    assert t.building_area_m2 == 95.0
    assert t.build_year == 1998
    assert (t.period_year, t.period_quarter) == (2024, 3)
    assert t.city_planning == "第一種低層住居専用地域"
    assert t.structure == "木造"
    assert t.layout == "4LDK"


# ---- 枠を超えないこと -----------------------------------------------------

def _with_budget(mb, fn):
    keep = reinfolib._CACHE_BUDGET
    reinfolib._CACHE_BUDGET = int(mb * 1024 * 1024)
    reinfolib.cache_clear()
    try:
        return fn()
    finally:
        reinfolib._CACHE_BUDGET = keep
        reinfolib.cache_clear()


def test_the_cache_stays_inside_its_budget():
    """市区町村を自由に引かれても、メモリが青天井にならないこと。"""
    def run():
        txns = _txns(1000)          # 1エントリ ≒ 0.7MB（概算）
        for i in range(200):
            reinfolib._cache_put(("city%d" % i, 2024, None), txns)
        return reinfolib.cache_stats()
    st = _with_budget(4, run)
    assert st["bytes"] <= st["budget"], st
    assert st["entries"] < 200, "捨てていない"
    assert st["entries"] > 0, "捨てすぎ"


def test_the_oldest_untouched_entry_is_the_one_dropped():
    """LRU。診断中に使っている年が落ちると、同じ年を取り直すことになる。"""
    def run():
        txns = _txns(1000)          # 1エントリ 0.7MB。枠4MBなので5件で満杯
        for i in range(5):
            reinfolib._cache_put(("city%d" % i, 2024, None), txns)
        # 0番を触って新しい側へ回す
        assert reinfolib._cache_get(("city0", 2024, None)) is not None
        for i in range(5, 7):       # 2件追加＝押し出されるのは1番と2番
            reinfolib._cache_put(("city%d" % i, 2024, None), txns)
        return (reinfolib._cache_get(("city0", 2024, None)),
                reinfolib._cache_get(("city1", 2024, None)))
    kept, dropped = _with_budget(4, run)
    assert kept is not None, "触ったものが落ちている"
    assert dropped is None, "触っていない古いものが残っている"


def test_an_entry_too_big_for_the_budget_is_simply_not_kept():
    """1件で枠を超えるものは、はじめから入れない。

    入れてから追い出す作りにすると、枠に収まっていた他のエントリを
    全部巻き添えにしたうえで、結局その1件も残らない。
    """
    def run():
        reinfolib._cache_put(("small", 2024, None), _txns(100))    # 70KB
        reinfolib._cache_put(("huge", 2024, None), _txns(1500))    # 1.05MB
        return reinfolib.cache_stats(), reinfolib._cache_get(("small", 2024, None))
    st, small = _with_budget(0.5, run)          # 枠 512KB
    assert st["entries"] == 1
    assert small is not None, "枠に収まっていたものまで巻き添えにしている"


def test_the_running_total_does_not_drift():
    """同じキーを上書きしたとき、二重に足していないこと。"""
    def run():
        for _ in range(10):
            reinfolib._cache_put(("same", 2024, None), _txns(1000))
        return reinfolib.cache_stats()
    st = _with_budget(8, run)
    assert st["entries"] == 1
    assert st["bytes"] == 1000 * reinfolib._BYTES_PER_TXN, st
