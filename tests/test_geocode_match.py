# -*- coding: utf-8 -*-
"""住所がどこまで当たったかを見て、座標の使い方を変える。ネットワーク不要。

2026-10-19、平塚市で土地を2件診断したら、2件とも用途地域が違っていた。
国土地理院の住所検索は、見つからない部分を黙って落として返す。

    平塚市岡崎3000-1  → 平塚市岡崎（町の代表点）
    平塚市真土1234    → 平塚市真田（別の町）

それを見ずに、返ってきた座標で用途地域を引いていた。さらに、地点を含む
面が無いときは、同じタイルの別の面の名前を借りていた。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import geocoding as g  # noqa: E402
from src import pipeline as pl  # noqa: E402
from src.enrichment import Enrichment  # noqa: E402
from src.models import LandSubject  # noqa: E402


# ---- どこまで当たったか ---------------------------------------------------

def test_the_hiratsuka_cases_are_told_apart():
    m = g.match_level
    assert m("神奈川県平塚市真土1234", "神奈川県平塚市真田") == g.MISMATCH
    assert m("神奈川県平塚市岡崎3000-1", "神奈川県平塚市岡崎") == g.TOWN
    assert m("神奈川県平塚市中原上宿100", "神奈川県平塚市中原") == g.TOWN
    assert m("神奈川県平塚市入野500", "神奈川県平塚市入野５００番地") == g.ADDRESS
    assert m("神奈川県平塚市岡崎3000の1", "神奈川県平塚市岡崎３０００番地") == g.ADDRESS


def test_a_lived_in_address_still_counts_as_found():
    m = g.match_level
    assert m("神奈川県平塚市田村5丁目12-3",
             "神奈川県平塚市田村五丁目１２番３号") == g.ADDRESS
    assert m("平塚市紅谷町10-1", "神奈川県平塚市紅谷町１０番１号") == g.ADDRESS
    assert m("千葉県船橋市前原西6丁目1-1",
             "千葉県船橋市前原西六丁目１番１号") == g.ADDRESS


def test_only_the_block_or_only_the_city_is_not_the_land():
    m = g.match_level
    assert m("神奈川県平塚市田村5-12-3", "神奈川県平塚市田村五丁目") == g.TOWN
    assert m("神奈川県平塚市真土1234", "神奈川県平塚市") == g.MISMATCH
    assert m("神奈川県足柄上郡開成町吉田島100", "神奈川県足柄上郡開成町") == g.MISMATCH


def test_the_deepest_candidate_wins(monkeypatch):
    """候補が複数返ってきたら、先頭ではなくいちばん深く当たったものを採る。"""
    data = [
        {"geometry": {"coordinates": [139.30, 35.37]},
         "properties": {"title": "神奈川県平塚市真田"}},
        {"geometry": {"coordinates": [139.31, 35.38]},
         "properties": {"title": "神奈川県平塚市真土"}},
    ]

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return data

    monkeypatch.setattr(g.requests, "get", lambda *a, **k: R())
    got = g.GsiGeocoder().geocode("神奈川県平塚市真土1234")
    assert got.address_normalized == "神奈川県平塚市真土"
    assert got.level == g.TOWN
    assert (got.latitude, got.longitude) == (35.38, 139.31)


# ---- 診断の流れでの扱い ---------------------------------------------------

class _Geo:
    def __init__(self, level, title="神奈川県平塚市真田"):
        self.level, self.title = level, title

    def geocode(self, address, expect=None):
        return g.GeocodeResult(35.37, 139.30, self.title, "gsi", level=self.level)


def _run(monkeypatch, level, subject=None, enr=None, title="神奈川県平塚市真田"):
    seen = {}

    def fake_enrich(lat, lon, key, **kw):
        seen["latlon"] = (lat, lon)
        seen["precise"] = kw.get("precise")
        return enr or Enrichment()

    monkeypatch.setattr(pl, "make_geocoder", lambda *a, **k: _Geo(level, title))
    monkeypatch.setattr(pl, "enrich", fake_enrich)
    subj = subject or LandSubject(address="神奈川県平塚市真土1234", price=20_000_000,
                                  land_area_m2=120)
    res = pl.run_land_pipeline(subj, reinfolib_key=None)
    return res, seen


def test_a_different_town_is_not_used_at_all(monkeypatch):
    res, seen = _run(monkeypatch, g.MISMATCH)
    assert seen["latlon"] == (None, None), "別の町の座標で何も引かない"
    assert any("別の場所です" in w for w in res.warnings)
    assert not any("失敗" in w for w in res.warnings if "別の場所" in w), \
        "画面で一行に丸められない言葉で書く"


def test_a_town_level_hit_does_not_look_up_zoning(monkeypatch):
    res, seen = _run(monkeypatch, g.TOWN, title="神奈川県平塚市岡崎")
    assert seen["latlon"] == (35.37, 139.30)
    assert seen["precise"] is False
    assert any("までしか特定できませんでした" in w for w in res.warnings)
    assert any("販売図面に書かれた用途地域" in w for w in res.warnings)


def test_the_zoning_on_the_listing_wins_and_the_gap_is_told(monkeypatch):
    enr = Enrichment(use_district="第一種住居地域", coverage_ratio=60,
                     floor_area_ratio=200)
    subj = LandSubject(address="神奈川県平塚市田村5丁目12-3", price=20_000_000,
                       land_area_m2=120, use_district="第一種低層住居専用地域",
                       coverage_ratio=50, floor_area_ratio=100)
    res, seen = _run(monkeypatch, g.ADDRESS, subject=subj, enr=enr,
                     title="神奈川県平塚市田村五丁目１２番３号")
    assert seen["precise"] is True
    assert res.enrichment.use_district == "第一種低層住居専用地域"
    assert res.capacity.designated_coverage == 50
    assert res.capacity.designated_far == 100
    text = " ".join(res.warnings)
    assert "入力された第一種低層住居専用地域で計算しています" in text
    assert "地図上の位置では第一種住居地域でした" in text
    assert "入力された50%で計算しています" in text


def test_the_land_form_offers_the_zoning_choice():
    import app as webapp
    h = webapp.app.test_client().get("/land").get_data(as_text=True)
    assert '<select name="use_district">' in h
    assert h.count("<option value=\"第一種低層住居専用地域\"") == 1
    assert webapp._valid_use_district("準住居地域") == "準住居地域"
    assert webapp._valid_use_district("でたらめ") is None
