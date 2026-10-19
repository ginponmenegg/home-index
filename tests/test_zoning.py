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
    # 名前も隣から借りない（2026-10-19）。分からなければ未取得のまま
    assert ud is None


def test_an_unlocatable_point_borrows_nothing(monkeypatch):
    """内包判定ができないときに、別の面の名前も数字も返さない。

    以前は名前だけ同じタイルの別の面から借りていた。タイルは1km四方ほどあり、
    用途地域がいくつも入っているので、借りた名前はほとんど当てずっぽう
    （平塚市の土地2件で用途地域を外した一因。2026-10-19）。
    """
    elsewhere = _square(140.50, 35.99)
    _with_tile(monkeypatch, [_feature(elsewhere, "第一種低層住居専用地域", 50, 100)])
    assert en.fetch_zoning(35.695, 140.005, "dummy") == (None, None, None)


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


# ---- 全角の「第１種」と、丁目までしか分からないとき（2026-10-19）---------

def test_the_full_width_class_number_is_read():
    """データは「第１種中高層住居専用地域」と全角数字で書いている。

    読めずに、同じタイルの別の面の名前を借りていた（平塚市幸町13-2で、
    第一種中高層住居専用地域が近隣商業地域と出た）。
    """
    f = en._extract_use_district
    assert f({"use_area_ja": "第１種中高層住居専用地域"}) == "第一種中高層住居専用地域"
    assert f({"use_area_ja": "第２種低層住居専用地域"}) == "第二種低層住居専用地域"
    assert f({"use_area_ja": "第１種住居地域"}) == "第一種住居地域"
    assert f({"use_area_ja": "近隣商業地域"}) == "近隣商業地域"
    assert f({"use_area_ja": "準工業地域"}) == "準工業地域"
    assert f({"use_area_ja": "工業専用地域"}) == "工業専用地域"


def test_the_polygon_with_a_full_width_name_is_named_by_itself(monkeypatch):
    here = _square(140.00, 35.69)
    _with_tile(monkeypatch, [
        _feature(_square(140.50, 35.99), "近隣商業地域", 80, 200),
        _feature(here, "第１種中高層住居専用地域", "60%", "200%"),
    ])
    assert en.fetch_zoning(35.695, 140.005, "dummy") ==         ("第一種中高層住居専用地域", 60, 200)


def test_around_a_town_centre_the_zone_must_be_the_same_everywhere(monkeypatch):
    """丁目までの住所では、代表点の周りがすべて同じときだけ値を使う。"""
    big = _square(139.90, 35.60, size=0.2)
    _with_tile(monkeypatch, [_feature(big, "第１種低層住居専用地域", "50%", "100%")])
    got, seen = en.fetch_zoning_around(35.695, 140.005, "dummy")
    assert got == ("第一種低層住居専用地域", 50, 100)
    assert seen == ["第一種低層住居専用地域"]


def test_a_mixed_neighbourhood_gives_no_zone(monkeypatch):
    """周りに別の用途地域があれば決めない。代表点の値を出すと外すことがある。"""
    west = {"type": "Polygon", "coordinates": [[
        [139.90, 35.60], [140.005, 35.60], [140.005, 35.80],
        [139.90, 35.80], [139.90, 35.60]]]}
    east = {"type": "Polygon", "coordinates": [[
        [140.005, 35.60], [140.10, 35.60], [140.10, 35.80],
        [140.005, 35.80], [140.005, 35.60]]]}
    _with_tile(monkeypatch, [_feature(west, "第１種中高層住居専用地域", "60%", "200%"),
                             _feature(east, "近隣商業地域", "80%", "200%")])
    got, seen = en.fetch_zoning_around(35.695, 140.0049, "dummy")
    assert got == (None, None, None)
    assert seen == ["第一種中高層住居専用地域", "近隣商業地域"]

