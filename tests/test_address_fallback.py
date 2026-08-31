# -*- coding: utf-8 -*-
"""都道府県を省いた住所でも市区町村を特定できること。ネットワーク不要。

「小田原市城山1-2-3」のように都道府県を書かずに入力されると、
市区町村コードが決まらず取引データを1件も取れなかった。
結果として価格評価が丸ごと落ち、「類似成約が不足」と出ていた。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.citycode import CityCodeResolver, detect_prefecture


def _resolver():
    cache = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "citycode_cache.json")
    return CityCodeResolver(os.environ.get("REINFOLIB_KEY"), cache)


def test_prefecture_is_required_without_a_hint():
    """都道府県が無いと解決できない、という前提そのものを固定する。"""
    assert detect_prefecture("小田原市城山1-2-3") is None
    assert detect_prefecture("神奈川県小田原市城山1-2-3") == "14"


def test_hint_recovers_the_city_and_the_district():
    """都道府県だけ補えば、市区町村名も町名も元の住所から取れること。

    正規化した住所をそのまま使わないのは、町名が「城山四丁目」のように
    丁目付きになり、成約データ側の「城山」と一致しなくなるため。
    """
    r = _resolver()
    full = r.resolve_from_address("神奈川県小田原市城山1-2-3")
    if full[0] is None:
        import pytest
        pytest.skip("市区町村一覧のキャッシュもキーも無い環境")
    hinted = r.resolve_from_address("小田原市城山1-2-3", pref_hint="14")
    assert hinted == full, (hinted, full)
    assert hinted[2] == "城山", "町名に丁目が付いてはいけない"


def test_bad_hint_does_not_invent_a_city():
    """見当違いのヒントで、無関係な市区町村に当ててしまわないこと。"""
    r = _resolver()
    if r.resolve_from_address("神奈川県小田原市城山1-2-3")[0] is None:
        import pytest
        pytest.skip("市区町村一覧のキャッシュもキーも無い環境")
    # 北海道(01)には小田原市が無いので、解決できないのが正しい
    assert r.resolve_from_address("小田原市城山1-2-3", pref_hint="01")[0] is None


def test_the_message_tells_the_user_what_to_do():
    """内部の変数名ではなく、利用者が取れる行動を書く。"""
    import io
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "pipeline.py"),
        encoding="utf-8").read()
    assert "--city で指定" not in src, "CLIの引数名を画面に出さない"
    assert "都道府県から入力すると改善します" in src


def test_no_estat_variable_name_is_shown():
    """使っていない e-Stat の環境変数名を利用者に見せない。"""
    import io
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "enrichment.py"),
        encoding="utf-8").read()
    assert 'e.notes.append("人口：ESTAT_APPID未設定のため未取得")' not in src


def test_overpass_has_more_than_one_endpoint():
    """公開サーバ1つに依存しない。落ちていたら次を試す。"""
    from src import osm
    assert len(osm.OVERPASS_ENDPOINTS) >= 2
    assert osm.OVERPASS == osm.OVERPASS_ENDPOINTS[0]


def test_population_falls_back_to_the_mesh_estimate():
    """e-Statを使っていないので、人口はメッシュ推計を出すこと。

    en.population は常に空になる（ESTAT_APPID を設定していないため）。
    そのままだと結果画面の「人口」が出っぱなしで「—」になる。
    """
    import io
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
    assert "mesh_pop_now" in src, "メッシュの人数を表示に使っていない"
    i = src.index("pop_label = ")
    block = src[i:i + 400]
    assert "250mメッシュ" in block, "出典の粒度を明示する"
