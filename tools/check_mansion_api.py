# -*- coding: utf-8 -*-
"""XIT001 のマンション成約が、こちらの想定どおりの形で返るかを実データで確認する。

    python tools/check_mansion_api.py            # 既定は小田原市(14206)
    python tools/check_mansion_api.py 13113      # 市区町村コードを指定

REINFOLIB_KEY は環境変数か、プロジェクト直下の .env から読む。
出力にキーは含まれない。

確認したいのは次の2点。
  1. マンションの取引種別（Type）が実際どういう文字列で入っているか
  2. 専有面積がどのフィールド（Area / TotalFloorArea）に入っているか
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


def main():
    load_env()
    key = os.environ.get("REINFOLIB_KEY", "")
    if not key or "ここに" in key:
        print("REINFOLIB_KEY が設定されていません（.env を確認してください）")
        return 1

    city = sys.argv[1] if len(sys.argv) > 1 else "14206"
    from src.reinfolib import ReinfolibClient
    from src.mansion_price import is_mansion_txn, txn_area_m2

    txns = ReinfolibClient(key).get_transactions(city, [2025, 2024])
    print(f"市区町村 {city} / 取得件数 {len(txns)}")

    print("\n[1] Type の内訳")
    for t, n in collections.Counter(x.type for x in txns).most_common():
        mark = " ← マンション判定" if (t and "マンション" in t) else ""
        print(f"    {t!r}: {n}{mark}")

    mans = [t for t in txns if is_mansion_txn(t)]
    print(f"\n[2] マンションとして拾えた件数: {len(mans)}")
    if not mans:
        print("    0件。Type の文字列が想定と違う可能性があります（上の内訳を確認）")
        return 1

    in_area = sum(1 for t in mans if t.land_area_m2)
    in_total = sum(1 for t in mans if t.building_area_m2)
    got = sum(1 for t in mans if txn_area_m2(t))
    print(f"    Area(→land_area_m2) に面積あり : {in_area}")
    print(f"    TotalFloorArea(→building_area_m2) に面積あり: {in_total}")
    print(f"    面積を取得できた件数: {got} / {len(mans)}")

    print("\n[3] 先頭3件（㎡単価が現実的な範囲か）")
    for t in mans[:3]:
        area = txn_area_m2(t)
        unit = int(t.trade_price / area) if (area and t.trade_price) else None
        print(f"    {t.district_name} / {t.build_year}年築 / {area}㎡ / "
              f"{t.trade_price:,}円 / {unit:,}円/㎡" if unit else
              f"    {t.district_name} / 面積か価格が欠損")

    print("\n[4] 生レコードのキー一覧（1件目）")
    print("   ", sorted(mans[0].raw.keys()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
