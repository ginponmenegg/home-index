# -*- coding: utf-8 -*-
"""マンションのバス便。ネットワーク不要。

戸建には「駅までバス（分）」の入力欄があり、立地と資産性の両方でバス便を
別扱いしていた。マンションの入力欄にはこれが無く、駅徒歩しか聞いていな
かったため、バス便のマンションが駅徒歩の物件と同じ扱いで採点されていた。

マンションは駅からの距離がそのまま出口（売れるか）に響くので、戸建以上に
効く項目になる。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import MansionSubject, SubjectProperty
from src.scoring import score_asset
from src.mansion_scoring import score_mansion_asset
from src.extract import parse_mansion_text


def _mansion(**kw):
    base = dict(address="神奈川県小田原市城山1-2-3", price=34_800_000,
                exclusive_area_m2=70.0)
    base.update(kw)
    return MansionSubject(**base)


def test_bus_route_scores_below_any_walking_distance():
    """バス便は、徒歩が何分でも駅徒歩圏の物件とは別の水準で見る。"""
    walk_far = score_mansion_asset(_mansion(station_walk_min=25,
                                            build_year=2010), 2026)
    by_bus = score_mansion_asset(_mansion(station_walk_min=5, bus_min=12,
                                          build_year=2010), 2026)
    assert by_bus.raw < walk_far.raw


def test_bus_route_is_reported_as_a_weakness():
    c = score_mansion_asset(_mansion(station_walk_min=5, bus_min=12,
                                     build_year=2010), 2026)
    assert any("バス" in x for x in c.minus)
    assert not any("駅徒歩" in x for x in c.plus), \
        "バス便の物件で「駅徒歩5分」を強みにしてはいけない（バス停までの徒歩）"


def test_kodate_and_mansion_use_the_same_field_name():
    """立地の採点は戸建・マンション共通の関数で、bus_min を getattr で読む。"""
    assert MansionSubject(address="x").bus_min is None
    assert SubjectProperty(property_type="chuko_kodate", price=0,
                           address="x").bus_min is None
    # 戸建側の資産性でも減点される（既存のふるまいが壊れていないこと）
    c = score_asset(SubjectProperty(property_type="chuko_kodate", price=0,
                                    address="x", bus_min=10), None, None)
    assert any("バス" in x for x in c.minus)


def test_pasted_mansion_text_picks_up_the_bus_ride():
    p = parse_mansion_text("中古マンション 価格3,480万円 専有面積70.00m2 "
                           "築年月 2010年3月 〇〇駅 バス12分 停歩5分")
    assert p["bus"] == 12
    assert parse_mansion_text("〇〇駅 徒歩8分")["bus"] is None


def test_every_form_offers_the_bus_field():
    """無料もPROも、戸建もマンションも、バス便を入力できる。

    以前はマンションに欄が無く、PROへ引き継ぐ経路でも落ちていた。
    """
    import app as appmod
    c = appmod.app.test_client()
    for url in ("/buy", "/mansion", "/pro/diagnose", "/pro/mansion"):
        html = c.get(url).get_data(as_text=True)
        assert 'name="bus"' in html, f"{url} にバス便の入力欄が無い"
