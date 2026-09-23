# -*- coding: utf-8 -*-
"""住所 → 市区町村コード。ここが黙って壊れていた。

本番で、全都道府県のコードが引けない状態になっていた。市区町村コードが
決まらないと取引データを1件も取りに行かず、画面には「類似成約が不足し
価格評価できず」と出る。**不足しているのではなく、取りに行っていない。**

原因は二つあった。

1. 取得に失敗したとき、空のリストを「その県には市区町村が無い」として
   記憶し、ディスクにも書いていた。一度そうなると二度と取りに行かない。
2. 政令指定都市は「市」のコードでは XIT001 が1件も返さない
   （01100 札幌市＝0件／01101 札幌市中央区＝1,682件）。ところが
   「札幌市」も「中央区」も3文字で、長さ比較では市が先に当たって勝つ。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
from src import citycode  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- 同梱の表 --------------------------------------------------------------

def test_every_prefecture_is_bundled():
    """APIに頼らない。取りに行って失敗すると、住所が解決できなくなる。"""
    assert len(citycode.BUNDLED_CITIES) == 47
    total = sum(len(v) for v in citycode.BUNDLED_CITIES.values())
    assert 1700 <= total <= 2100, f"{total}件は多すぎるか少なすぎる"


def test_the_bundled_table_is_a_file_in_the_repo():
    path = os.path.join(ROOT, "src", "citycodes.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    assert all(len(k) == 2 for k in t), "都道府県コードは2桁"
    for cities in t.values():
        for c in cities:
            assert c["id"] and c["name"] and c.get("full")
            assert len(c["id"]) == 5


# ---- 政令指定都市 ----------------------------------------------------------

def test_the_twenty_designated_cities_are_known():
    assert len(citycode.DESIGNATED_CITIES) == 20


@pytest.mark.parametrize("addr,code,name", [
    ("北海道札幌市中央区北一条西2丁目", "01101", "札幌市中央区"),
    ("神奈川県横浜市中区本牧元町1", "14104", "横浜市中区"),
    ("大阪府大阪市北区梅田1丁目", "27127", "大阪市北区"),
    # 政令市のコードは末尾100とは限らない（北九州市40100・福岡市40130）
    ("福岡県福岡市早良区西新1丁目", "40137", "福岡市早良区"),
    ("福岡県北九州市門司区西海岸1", "40101", "北九州市門司区"),
    ("静岡県浜松市中央区元城町1", "22138", "浜松市中央区"),
])
def test_a_ward_wins_over_its_city(addr, code, name):
    """市のコードを返したら、成約データが0件になる。"""
    got, got_name, _d = webapp._resolve_city(addr)
    assert got == code, f"{addr} → {got}（{code}のはず）"
    assert got_name == name
    assert not citycode.is_designated_city(got)


def test_a_designated_city_without_a_ward_is_flagged():
    """区が書かれていない。**黙って0件にしない。**"""
    code, _n, _d = webapp._resolve_city("北海道札幌市北一条西2")
    assert code == "01100"
    assert citycode.is_designated_city(code) is True


def test_the_23_wards_of_tokyo_are_not_treated_as_sub_wards():
    """東京23区は特別区。親の市がない。"""
    code, name, dist = webapp._resolve_city("東京都世田谷区経堂1丁目")
    assert (code, name, dist) == ("13112", "世田谷区", "経堂")
    assert not citycode.is_designated_city(code)


# ---- ふつうの市町村 --------------------------------------------------------

@pytest.mark.parametrize("addr,code,district", [
    ("千葉県船橋市前原西2丁目", "12204", "前原西"),
    ("島根県出雲市今市町", "32203", "今市町"),
    ("長野県松本市城西1丁目", "20202", "城西"),
    # 郡は郡ごと。「市川町」を「市」で切らない
    ("兵庫県神崎郡市川町甘地", "28442", "甘地"),
    # 先に「村」で切ると「武蔵村」になる
    ("東京都武蔵村山市大南1-1", "13223", "大南"),
    # 「市」で終わる名前に「市」が続く
    ("三重県四日市市諏訪町", "24202", "諏訪町"),
])
def test_ordinary_municipalities_resolve(addr, code, district):
    got, _n, d = webapp._resolve_city(addr)
    assert got == code, addr
    assert d == district


def test_an_address_without_a_prefecture_is_not_guessed():
    assert webapp._resolve_city("船橋市前原西2丁目")[0] in (None, "12204")


# ---- 失敗を憶えない --------------------------------------------------------

def test_a_failed_fetch_is_not_remembered_as_no_cities():
    """**ここが本番を壊していた。**空を憶えると二度と取りに行かない。"""
    r = citycode.CityCodeResolver(None, cache_file=None)
    # 表に無い都道府県コードを引くと、APIも鍵も無いので空が返る
    r._cities("99")
    assert "99" not in r._pref_cities, "空を憶えてはいけない"


def test_the_bundled_table_is_preferred_over_the_api(monkeypatch):
    """同梱の表があるなら、APIは叩かない。呼び出し回数を使わない。"""
    called = []
    monkeypatch.setattr(citycode.requests, "get",
                        lambda *a, **k: called.append(1))
    r = citycode.CityCodeResolver("ダミーの鍵", cache_file=None)
    assert r._cities("12"), "千葉県が引けない"
    assert not called, "同梱の表があるのにAPIを叩いた"


# ---- 画面に本当のことを書く ------------------------------------------------

def test_the_screen_says_why_the_price_is_missing():
    """「不足」で片づけない。取りに行っていないなら、そう書く。"""
    class S:
        municipality_code = None
    assert "市区町村を特定できなかった" in webapp._price_why(S())

    class D:
        municipality_code = "01100"      # 札幌市（区が分からない）
    assert "区まで入力" in webapp._price_why(D())

    class N:
        municipality_code = "12204"
    assert "見つかりません" in webapp._price_why(N())


def test_the_marker_for_a_thin_result_is_not_a_sentence():
    """文章を印にすると、書き直すたびに判定が壊れる。"""
    assert webapp._PRICE_FAILED == 'data-price="none"'
    assert webapp._PRICE_FAILED in webapp.RESULT


# ---- クラスの形が崩れていないこと ------------------------------------------

def test_the_resolver_still_has_every_method():
    """**関数をクラスの途中に差し込んで、後ろのメソッドを外に出した。**

    import は通り、テストも通り、本番で初めて落ちた。呼ばれるのが
    「取引が1件以上あるとき」だけだったので、コードが引けない間は
    この道を通らなかった。形そのものを見る。
    """
    for name in ("_load_disk", "_save_disk", "_cities",
                 "resolve_from_address", "info"):
        assert callable(getattr(citycode.CityCodeResolver, name, None)), name


def test_info_returns_the_prefecture_and_city():
    r = citycode.CityCodeResolver(None)
    assert r.info("12204") == ("千葉県", "船橋市")
    assert r.info("01101") == ("北海道", "中央区")
    assert r.info("") == (None, None)


def test_the_distance_step_can_call_info():
    """_geocode_districts が info() を呼ぶ。ここが落ちて /sample が500になった。"""
    import inspect

    from src import pipeline
    src = inspect.getsource(pipeline._geocode_districts)
    assert ".info(" in src
    r = citycode.CityCodeResolver(None)
    assert r.info("12204")[1], "呼べる形になっていない"


def test_health_reports_whether_the_table_loaded():
    """外から確かめられるようにする。本番で空になったとき、切り分けに
    時間がかかった。**先頭の ok は死活監視が見ているので外さない。**
    """
    h = webapp.app.test_client().get("/healthz")
    assert h.status_code == 200
    body = h.get_data(as_text=True)
    assert body.startswith("ok"), "UptimeRobot が ok で見ている"
    assert "cities=47" in body


# ---- 町名の表記ゆれ --------------------------------------------------------
# 実データで測って出たもの。
#   住所「北海道札幌市中央区北一条西2丁目」→ 切り出し「北一条西」
#   成約データ側                        →       「北１条西」
# 漢数字と全角アラビア数字で書き分けられていて、一致しなかった。同じ町の
# 成約を「別の町」として扱うため、この住所は comps が0件＝判定不可だった。
# 直すと「概ね適正」が出る。12住所で測って、一致8件→10件。

@pytest.mark.parametrize("a,b", [
    ("北一条西", "北１条西"),          # 漢数字 と 全角アラビア
    ("北十条西", "北１０条西"),
    ("神宮前一丁目", "神宮前"),         # 丁目が町名に残る
    ("前原西", "前原西"),
    ("大字甘地", "甘地"),
    ("四谷三栄町", "四谷三栄町"),       # 変換しても両側同じなら壊れない
])
def test_the_same_town_written_differently_is_the_same_town(a, b):
    assert citycode.same_town(a, b) is True


@pytest.mark.parametrize("a,b", [
    ("経堂", "梅ヶ丘"),
    ("一番町", "十番町"),              # 1番町 と 10番町。混ぜない
    ("北一条西", "北一条東"),
    ("前原西", ""),
    (None, "前原西"),
])
def test_different_towns_stay_different(a, b):
    assert citycode.same_town(a, b) is False


def test_the_trailing_chome_is_dropped_when_it_is_in_kanji():
    """「数字が始まる前まで」で切ると、漢数字の丁目が町名に残る。"""
    assert citycode._leading_district("神宮前一丁目1番") == "神宮前"
    assert citycode._leading_district("経堂1丁目") == "経堂"
    assert citycode._leading_district("北一条西2丁目") == "北一条西"
    # 町名そのものの漢数字は残す（表示に使うので）
    assert citycode._leading_district("四谷三栄町11-4") == "四谷三栄町"
    assert citycode._leading_district("一番町1丁目") == "一番町"


def test_no_module_compares_town_names_raw():
    """**両側に同じ変換をかけないと意味がない。**1か所でも生の == が
    残っていると、そこだけ表記ゆれを拾えない。
    """
    import glob
    bad = []
    for path in glob.glob(os.path.join(ROOT, "src", "*.py")):
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if "district_name ==" in line or "== district_name" in line:
                    bad.append(f"{os.path.basename(path)}:{i}")
    assert not bad, "生の比較が残っている: " + ", ".join(bad)
