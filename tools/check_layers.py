# -*- coding: utf-8 -*-
"""これから使うレイヤが、どの形・どの項目名で返ってくるかを実データで確かめる。

    python tools/check_layers.py                       # 藤沢の座標で確認
    python tools/check_layers.py 35.327976 139.475037  # 座標を指定

面（ポリゴン）やメッシュのレイヤは、項目名を推測で決めるわけにいかない。
ズームも仕様上レイヤごとに違うので、13〜15を順に試して当たったものを出す。
出力にAPIキーは含まれない。
"""
import io
import json
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

from src.enrichment import _reinfolib_tile, latlon_to_tile  # noqa: E402

# 使う予定のレイヤ。名前は不動産情報ライブラリのAPI一覧より。
LAYERS = [
    ("XKT004", "小学校区"),
    ("XKT005", "中学校区"),
    ("XKT007", "保育園・幼稚園等"),
    ("XKT011", "福祉施設"),
    ("XKT013", "250mメッシュ別将来推計人口"),
    ("XKT016", "災害危険区域"),
    ("XKT017", "図書館"),
    ("XKT018", "市区町村役場・集会施設"),
    ("XKT020", "大規模盛土造成地"),
    ("XKT021", "地すべり防止地区"),
    ("XKT022", "急傾斜地崩壊危険区域"),
    ("XKT025", "液状化の発生傾向図"),
    ("XKT031", "人口集中地区"),
]


def shorten(v, n=40):
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def main():
    lat = float(sys.argv[1]) if len(sys.argv) > 2 else 35.327976
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else 139.475037
    key = os.environ.get("REINFOLIB_KEY")
    if not key:
        print("REINFOLIB_KEY が設定されていません（.env か環境変数）")
        return

    print(f"■ 座標 {lat}, {lon}\n")
    for api, label in LAYERS:
        hit = None
        for z in (14, 13, 15, 12, 11):
            x, y = latlon_to_tile(lat, lon, z)
            try:
                feats = _reinfolib_tile(api, key, z, x, y)
            except Exception as e:
                hit = hit or ("error", z, str(e)[:60])
                continue
            if feats:
                hit = ("ok", z, feats)
                break
        if not hit:
            print(f"{api} {label}: どのズームでも0件（この地点に該当なし）")
            continue
        if hit[0] == "error":
            print(f"{api} {label}: 取得失敗 z={hit[1]} {hit[2]}")
            continue

        _, z, feats = hit
        types = {}
        for f in feats:
            t = (f.get("geometry") or {}).get("type")
            types[t] = types.get(t, 0) + 1
        print(f"{api} {label}: z={z} {len(feats)}件 形状={types}")
        props = feats[0].get("properties") or {}
        for k in sorted(props)[:14]:
            print(f"    {k} = {shorten(props[k])}")
        print()


if __name__ == "__main__":
    main()
