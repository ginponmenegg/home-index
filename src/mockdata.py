# -*- coding: utf-8 -*-
"""オフライン検証用サンプルデータ（※実データではありません）。

--mock 実行時に使用。ネットワーク/キーが無くてもパイプラインの動作を確認できる。
小田原市(14206)の宅地(土地と建物)を模した現実的なダミー。
"""
from .models import Transaction


def sample_transactions():
    def T(price, land, bldg, year, py, pq, district, cp="第一種住居地域",
          structure="木造"):
        return Transaction(
            trade_price=price, type="宅地(土地と建物)",
            municipality_code="14206", district_name=district,
            land_area_m2=land, building_area_m2=bldg, build_year=year,
            period_year=py, period_quarter=pq, city_planning=cp,
            structure=structure, layout="4LDK",
            raw={"mock": True})
    return [
        T(34000000, 115, 98, 2006, 2024, 3, "南町"),
        T(33500000, 108, 95, 2004, 2024, 1, "南町"),
        T(36000000, 120, 102, 2008, 2023, 4, "南町"),
        T(31000000, 100, 90, 2002, 2023, 2, "本町", cp="商業地域"),
        T(35500000, 118, 100, 2007, 2024, 2, "浜町"),
        T(29000000, 95, 85, 1999, 2022, 3, "浜町"),
        T(38000000, 130, 110, 2012, 2024, 4, "南町"),
        T(32500000, 105, 92, 2005, 2023, 1, "栄町"),
        T(40000000, 140, 120, 2015, 2024, 2, "南町"),
        T(28000000, 90, 80, 1996, 2022, 1, "扇町", structure="軽量鉄骨"),
        T(34800000, 112, 96, 2006, 2024, 3, "南町"),
        T(33000000, 60, None, 2014, 2024, 4, "本町", cp="商業地域",
          structure="ＲＣ"),  # マンション類似の異形（建物面積欠損）
    ]
