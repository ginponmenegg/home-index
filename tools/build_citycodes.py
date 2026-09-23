# -*- coding: utf-8 -*-
"""全国の市区町村コード表を作って src/citycodes.json に置く。

出典は不動産情報ライブラリ XIT002（都道府県内市区町村一覧）。いま実行時に
毎回叩いているのと同じもの。**中身が変わらないものを毎回取りに行く理由が
ない**ので、一度取って同梱する。

政令指定都市は、市（コード末尾100）と区（101以降）の両方が返る。区は
名前が「中央区」のように短く、他県の区と重なる。親の市名を前に付けた
フルネームを一緒に持たせる。
"""
import io
import json
import os
import subprocess
import sys
import urllib.parse

sys.path.insert(0, r"C:\Users\ginga\Documents\GitHub\home-index")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import app  # noqa: E402,F401  （.env を読ませる）
from src.citycode import PREFECTURES, REINFOLIB_BASE  # noqa: E402

KEY = os.environ["REINFOLIB_KEY"]
OUT = r"C:\Users\ginga\Documents\GitHub\home-index\src\citycodes.json"


def fetch(pref):
    url = f"{REINFOLIB_BASE}/XIT002?" + urllib.parse.urlencode({"area": pref})
    out = subprocess.run(
        ["curl", "-s", "--compressed", "-H",
         f"Ocp-Apim-Subscription-Key: {KEY}", url], capture_output=True)
    d = json.loads(out.stdout.decode("utf-8") or "{}")
    return [{"id": str(i["id"]), "name": str(i["name"])}
            for i in d.get("data", [])]


table = {}
total = 0
for code in sorted(PREFECTURES):
    cities = fetch(code)
    if not cities:
        print(f"  !! {code} {PREFECTURES[code]} が取れなかった")
        continue
    # 政令市の区に、親の市名を付けたフルネームを足す
    parents = {c["id"]: c["name"] for c in cities if c["id"].endswith("100")}
    for c in cities:
        full = c["name"]
        if not c["id"].endswith("100"):
            head = c["id"][:3] + "100"
            if head in parents and c["name"].endswith("区"):
                full = parents[head] + c["name"]
        c["full"] = full
    table[code] = cities
    total += len(cities)
    print(f"  {code} {PREFECTURES[code]:<6} {len(cities):>3}件")

assert len(table) == 47, f"都道府県が{len(table)}件しか取れていない"
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(table, f, ensure_ascii=False, separators=(",", ":"))
print(f"\n{total}件を {OUT} に書き出しました "
      f"（{os.path.getsize(OUT) // 1024}KB）")

# 政令市の区が、フルネームで引けるか
for code in ("01", "14", "27"):
    ward = [c for c in table[code] if c["full"] != c["name"]][:2]
    print("  例:", [(c["id"], c["full"]) for c in ward])
