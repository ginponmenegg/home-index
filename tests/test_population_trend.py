import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.enrichment import _population_trend, _classify_trend


def test_odawara_recent_decline():
    # 小田原型：長期は増だが直近は減 → 直近2点で減少と判定されること（バグ再発防止）
    series = [("1980", 173000), ("2000", 200000), ("2015", 194000), ("2020", 188000)]
    latest, trend = _population_trend(series)
    assert latest == 188000
    assert trend in ("減少", "微減"), f"got {trend}"


def test_growing_town_positive_control():
    series = [("2010", 5000), ("2015", 5200), ("2020", 5500)]
    _, trend = _population_trend(series)
    assert trend in ("増加", "微増"), f"got {trend}"


def test_ignores_old_baseline():
    # 旧ロジックなら1980基準で"増加"だが、直近では減少になること
    series = [("1980", 100000), ("2015", 130000), ("2020", 126000)]
    _, trend = _population_trend(series)
    assert trend in ("減少", "微減"), f"got {trend}"


def test_thresholds():
    assert _classify_trend(1000, 1040) == "増加"
    assert _classify_trend(1000, 1010) == "微増"
    assert _classify_trend(1000, 990) == "微減"
    assert _classify_trend(1000, 950) == "減少"
    assert _classify_trend(1000, 1002) == "横ばい"
