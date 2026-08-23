# -*- coding: utf-8 -*-
"""「類似成約が不足し価格評価できず」がどこで起きているかを実データで確かめる。

    python tools/diagnose_mansion.py "神奈川県小田原市栄町1-1-1" 70 2005

引数は 住所 / 専有面積(㎡) / 築年(西暦)。省略すると小田原市の例で動く。
REINFOLIB_KEY は環境変数か、プロジェクト直下の .env から読む。出力にキーは出ない。

見るのは3つ。
  1. 取引がそもそも何件取れているか（年別・種別・町名別）
  2. 類似事例が各段階（近接2km→5km→市内全域）で何件残り、何で落ちているか
  3. 座標のまわりで、どの施設レイヤ（XKT***）が実際にデータを返すか
"""
import collections
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_env():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in io.open(path, encoding="utf-8-sig"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()

import datetime  # noqa: E402
from src.citycode import CityCodeResolver  # noqa: E402
from src.geocoding import make_geocoder  # noqa: E402
from src.models import MansionSubject  # noqa: E402
from src.reinfolib import ReinfolibClient  # noqa: E402
from src.mansion_price import (analyze_mansion_price, is_mansion_txn,  # noqa: E402
                               txn_area_m2, extract_mansion_comparables,
                               _location_similarity)
from src.pipeline import _geocode_mansion_districts  # noqa: E402

# 施設レイヤの棚卸しに試す候補。当たりだけ拾えればよいので広めに舐める。
# XKT001〜030では大型商業施設が見つからなかったので、範囲を広げて舐める。
# なお fetch_points_around はポイントだけを拾うので、面で来るレイヤ
# （用途地域XKT002・ハザードXKT026-029など）はここに出てこない。
CANDIDATE_LAYERS = [f"XKT{n:03d}" for n in range(1, 61)]
KNOWN = {"XKT002": "用途地域（面・既に利用）", "XKT006": "学校（既に利用）",
         "XKT010": "医療機関（既に利用）", "XKT015": "駅（既に利用）",
         "XKT007": "保育園・幼稚園", "XKT011": "福祉施設", "XKT017": "図書館", "XKT018": "役場・公的集会施設",
         "XKT026": "洪水（既に利用）", "XKT027": "高潮（既に利用）",
         "XKT028": "津波（既に利用）", "XKT029": "土砂（既に利用）"}


def main():
    address = sys.argv[1] if len(sys.argv) > 1 else "神奈川県小田原市栄町1-1-1"
    area = float(sys.argv[2]) if len(sys.argv) > 2 else 70.0
    build_year = int(sys.argv[3]) if len(sys.argv) > 3 else 2005

    key = os.environ.get("REINFOLIB_KEY")
    if not key:
        print("REINFOLIB_KEY が設定されていません（.env か環境変数）")
        return
    year = datetime.date.today().year
    years = [year - 1, year - 2, year - 3]

    print(f"■ 対象: {address} / {area}㎡ / {build_year}年築")

    resolver = CityCodeResolver(key)
    code, cityname, district = resolver.resolve_from_address(address)
    print(f"  市区町村コード: {code}（{cityname}） 町名: {district}")
    if not code:
        print("  → コードが解決できていません。ここが原因です。")
        return

    subj = MansionSubject(address=address, price=None, build_year=build_year,
                          exclusive_area_m2=area, municipality_code=code,
                          district_name=district)
    try:
        gc = make_geocoder(os.environ.get("GOOGLE_KEY")).geocode(address)
        subj.latitude, subj.longitude = gc.latitude, gc.longitude
        print(f"  座標: {gc.latitude:.5f}, {gc.longitude:.5f}（{gc.provider}）")
    except Exception as e:
        print(f"  座標の取得に失敗: {e}")

    # ---- 1. 取引の総量 ----
    client = ReinfolibClient(key)
    txns = client.get_transactions(code, years)
    mans = [t for t in txns if is_mansion_txn(t)]
    print(f"\n[1] 取引 {len(txns)}件 / うちマンション {len(mans)}件"
          f"（対象年 {years}）")
    by_year = collections.Counter(t.period_year for t in mans)
    print("    年別:", dict(sorted(by_year.items(), reverse=True)))
    by_dist = collections.Counter(t.district_name for t in mans)
    print(f"    町名は{len(by_dist)}種類。多い順:",
          by_dist.most_common(8))
    if district:
        print(f"    対象と同じ町名「{district}」: {by_dist.get(district, 0)}件")

    # ---- 2. どこで落ちているか ----
    print("\n[2] 類似事例の絞り込み")
    no_price = sum(1 for t in mans if not t.trade_price)
    no_area = sum(1 for t in mans if t.trade_price and not txn_area_m2(t))
    year_gap = sum(1 for t in mans
                   if t.trade_price and txn_area_m2(t) and t.build_year
                   and abs(build_year - t.build_year) > 25)
    print(f"    価格なしで除外: {no_price}件")
    print(f"    面積なしで除外: {no_area}件")
    print(f"    築年が25年超離れて除外: {year_gap}件")

    _geocode_mansion_districts(subj, txns, key)
    with_dist = sum(1 for t in mans if t.distance_m is not None)
    print(f"    町名から距離を付けられた: {with_dist}/{len(mans)}件")

    for label, radius in (("近接2km", 2000), ("近接5km", 5000),
                          ("市内全域", None)):
        comps = extract_mansion_comparables(subj, txns, year, radius_m=radius)
        zero_loc = sum(1 for t in mans
                       if _location_similarity(subj, t, radius) <= 0.0)
        print(f"    {label:8} → 残り {len(comps):3}件"
              f"（距離・町名で落ちたもの {zero_loc}件）")

    comps = extract_mansion_comparables(subj, txns, year, radius_m=None)
    pa = analyze_mansion_price(subj, comps, year)
    print(f"\n[3] 価格分析: {pa.verdict} / 事例{pa.comparable_count}件 "
          f"/ 確信度{pa.confidence}")
    print(f"    {pa.note}")
    if pa.estimate_mid:
        print(f"    推定 {pa.estimate_low:,}〜{pa.estimate_high:,}円"
              f"（中央 {pa.estimate_mid:,}円）")

    # ---- 3. 施設レイヤの棚卸し ----
    if subj.latitude is None:
        print("\n[4] 座標が無いため施設レイヤの確認をスキップ")
        return
    print("\n[4] 座標まわりで取得できた施設レイヤ（生活利便の材料さがし）")
    from src.enrichment import fetch_points_around
    for api in CANDIDATE_LAYERS:
        try:
            pts = fetch_points_around(api, key, subj.latitude, subj.longitude)
        except Exception:
            continue
        if not pts:
            continue
        keys = sorted((pts[0][2] or {}).keys())[:8]
        name = KNOWN.get(api, "")
        print(f"    {api} {len(pts):4}件 {name}")
        print(f"        項目: {keys}")


if __name__ == "__main__":
    main()
