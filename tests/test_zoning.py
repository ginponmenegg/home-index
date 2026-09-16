# -*- coding: utf-8 -*-
"""用途地域タイル（XKT002）から建ぺい率・容積率を読むところ。ネットワーク不要。

ここは「その地点の面」から読まないと意味がない。用途地域の境目では、
隣の面の建ぺい率・容積率を採ると、建てられる延床が何十㎡も変わる。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.enrichment as en


def _square(lon0, lat0, size=0.01):
    """左下を (lon0, lat0) とする正方形のポリゴン。"""
    return {"type": "Polygon", "coordinates": [[
        [lon0, lat0], [lon0 + size, lat0],
        [lon0 + size, lat0 + size], [lon0, lat0 + size], [lon0, lat0]]]}


def _feature(geom, name=None, cov=None, far=None):
    props = {}
    if name is not None:
        props["use_area_ja"] = name
    if cov is not None:
        props["u_building_coverage_ratio_ja"] = cov
    if far is not None:
        props["u_floor_area_ratio_ja"] = far
    return {"type": "Feature", "geometry": geom, "properties": props}


def _with_tile(monkeypatch, feats):
    monkeypatch.setattr(en, "_reinfolib_tile", lambda *a, **k: feats)


def test_the_ratios_come_from_the_polygon_that_contains_the_point(monkeypatch):
    """地点を含む面に名前が無くても、その面の数字を使う。

    実データ（船橋市前原西6丁目）で、地点を含む面は use_area_ja が空のまま
    建ぺい率・容積率だけ入っていた。名前の有無で捨てると、正しい数字を
    捨てて別の面の数字を返すことになる。
    """
    here = _square(140.00, 35.69)          # 地点を含む。名前は空、数字はある
    there = _square(140.50, 35.99)         # 別の場所。名前と別の数字がある
    _with_tile(monkeypatch, [
        _feature(there, "商業地域", 80, 600),
        _feature(here, None, 60, 200),
    ])
    ud, cov, far = en.fetch_zoning(35.695, 140.005, "dummy")
    assert (cov, far) == (60, 200)         # 隣の 80/600 を拾わない
    # 名前だけはタイル代表値で補ってよい（表示と目安にしか使わない）
    assert ud == "商業地域"


def test_an_unlocatable_point_gets_a_name_but_never_borrowed_ratios(monkeypatch):
    """内包判定ができないときに、別の面の建ぺい率・容積率を返さない。

    返してしまうと、違う土地の数字を「この土地に建てられる延床」として
    画面に出すことになる。名前は目安なので返してよいが、面積の計算に
    直結する数字は返さない。
    """
    elsewhere = _square(140.50, 35.99)
    _with_tile(monkeypatch, [_feature(elsewhere, "第一種低層住居専用地域", 50, 100)])
    ud, cov, far = en.fetch_zoning(35.695, 140.005, "dummy")
    assert ud == "第一種低層住居専用地域"
    assert cov is None and far is None


def test_the_containing_polygon_wins_over_the_tile_representative(monkeypatch):
    here = _square(140.00, 35.69)
    _with_tile(monkeypatch, [
        _feature(_square(140.50, 35.99), "商業地域", 80, 600),
        _feature(here, "第一種低層住居専用地域", 50, 100),
    ])
    assert en.fetch_zoning(35.695, 140.005, "dummy") == \
        ("第一種低層住居専用地域", 50, 100)


def test_an_empty_tile_answers_nothing(monkeypatch):
    _with_tile(monkeypatch, [])
    assert en.fetch_zoning(35.695, 140.005, "dummy") == (None, None, None)


def test_the_old_name_only_helper_still_works(monkeypatch):
    here = _square(140.00, 35.69)
    _with_tile(monkeypatch, [_feature(here, "準住居地域", 60, 200)])
    assert en.fetch_use_district(35.695, 140.005, "dummy") == "準住居地域"
