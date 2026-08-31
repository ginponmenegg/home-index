# -*- coding: utf-8 -*-
"""対話式の診断ウィザード。質問に答えるだけでフル診断を実行する。

コマンドを覚えなくてよいように、run.py を内部で呼び出す。
金額は「万円」で入力できる（日本人の感覚に合わせ、桁間違いを防ぐ）。
（診断.bat からダブルクリックで起動する想定）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run  # noqa: E402


def ask(label, default=""):
    tip = f"（未入力ならEnter＝{default}）" if default else "（任意・不明ならEnter）"
    v = input(f"{label}{tip}: ").strip()
    return v or default


def ask_money_man(label, default_man=""):
    """金額を『万円』で受け取り、円(文字列)にして返す。空なら空文字。"""
    if default_man:
        tip = f"（万円で入力・未入力ならEnter＝{default_man}万円）"
    else:
        tip = "（万円で入力・任意・不明ならEnter）"
    raw = input(f"{label}{tip}: ").strip().replace(",", "").replace("万", "")
    if not raw:
        raw = default_man
    if not raw:
        return ""
    try:
        yen = int(round(float(raw) * 10000))
        return str(yen)
    except ValueError:
        print(f"  → 数字で入力してください（{label}）。この項目はスキップします。")
        return ""


def main():
    print("=" * 50)
    print("  住宅購入AI診断 — かんたん入力")
    print("=" * 50)
    print("※ 金額は「万円」で入力できます（例 3,500万円 → 3880）\n")

    address = ask("物件の所在地", "〇〇県〇〇市〇〇町1-2-3")
    price = ask_money_man("売出価格", "3880")           # 万円→円
    land = ask("土地面積（㎡）", "120")
    building = ask("建物面積（㎡）", "95")
    byear = ask("築年（西暦）", "2005")
    city = ask("市区町村コード（小田原市=14206）", "14206")
    district = ask("町名（例 城山）", "城山")
    station = ask("最寄駅まで徒歩何分")
    income = ask_money_man("世帯年収")                  # 万円→円
    down = ask_money_man("頭金")                        # 万円→円

    if not price:
        print("売出価格が未入力のため終了します。")
        return

    argv = ["run.py", "--price", price, "--land", land, "--building", building,
            "--build-year", byear, "--address", address, "--city", city,
            "--district", district, "--show", "12"]
    if station:
        argv += ["--station-walk", station]
    if income:
        argv += ["--income", income]
    if down:
        argv += ["--down", down]
    if os.environ.get("SHINDAN_MOCK") == "1":
        argv.append("--mock")

    print("\n診断中です…\n")
    sys.argv = argv
    run.main()


if __name__ == "__main__":
    main()
