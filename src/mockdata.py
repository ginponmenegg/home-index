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
            structure=structure, layout="4LDK")
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
    ] + sample_land_transactions()


def sample_land_transactions():
    """宅地(土地)の成約。土地診断の分布と近隣の傾向を出すのに要る。

    宅地(土地と建物)の成約からは幅員・道路種別・形状がほとんど取れないが、
    宅地(土地)には入っている（実測で96〜100%）。そこを見せるのが土地診断
    なので、モックにも同じ形で入れておく。値はダミー。
    """
    def L(price, land, py, district, width=None, road=None, shape=None):
        return Transaction(
            trade_price=price, type="宅地(土地)",
            municipality_code="14206", district_name=district,
            land_area_m2=land, building_area_m2=None, build_year=None,
            period_year=py, period_quarter=1, city_planning="第一種住居地域",
            structure=None, layout=None,
            road_width_m=width, road_type=road, land_shape=shape,
            coverage_ratio=60, floor_area_ratio=200)
    return [
        L(19800000, 120.0, 2024, "南町", 6.0, "公道", "長方形"),
        L(17500000, 110.5, 2024, "南町", 4.0, "公道", "長方形"),
        L(23000000, 132.0, 2023, "南町", 6.0, "公道", "ほぼ長方形"),
        L(15800000, 105.0, 2023, "南町", 4.0, "私道", "不整形"),
        L(21500000, 125.0, 2022, "南町", 8.0, "公道", "長方形"),
        L(14200000, 98.0, 2022, "南町", 3.5, "私道", "長方形"),
        L(26000000, 145.0, 2024, "南町", 6.0, "公道", "台形"),
        L(16900000, 112.0, 2021, "南町", 4.0, "公道", "長方形"),
        L(12000000, 88.0, 2021, "浜町", 4.0, "公道", "長方形"),
        L(18000000, 120.0, 2020, "浜町", 6.0, "公道", "長方形"),
    ]
