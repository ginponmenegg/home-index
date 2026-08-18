#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1物件診断CLI（住宅購入AI診断サービス MVP・価格評価まで）。

例（サンプルデータで動作確認・キー不要）:
    python run.py --mock --price 34800000 --land 110 --building 96 --build-year 2006 \
                  --address "神奈川県小田原市南町1-1-1" --city 14206

例（実データ・要キー）:
    set REINFOLIB_KEY=xxxxx   (PowerShellは $env:REINFOLIB_KEY="xxxxx")
    python run.py --price 34800000 --land 110 --building 96 --build-year 2006 \
                  --address "神奈川県小田原市南町1-1-1" --city 14206
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.models import SubjectProperty
from src.pipeline import run_pipeline


def yen(n):
    if n is None:
        return "—"
    return f"{n:,}円（{n/10000:,.0f}万円）"


def main():
    ap = argparse.ArgumentParser(description="住宅購入AI診断 MVP（価格評価）")
    ap.add_argument("--address", required=True, help="物件所在地")
    ap.add_argument("--price", type=int, required=True, help="売出価格(円)")
    ap.add_argument("--type", default="chuko_kodate",
                    choices=["chuko_kodate", "shinchiku_kodate"])
    ap.add_argument("--land", type=float, help="土地面積(㎡)")
    ap.add_argument("--building", type=float, help="建物面積(㎡)")
    ap.add_argument("--build-year", type=int, help="築年(西暦)")
    ap.add_argument("--city", help="市区町村コード(例 14206)")
    ap.add_argument("--district", help="町名(例 南町)")
    ap.add_argument("--city-planning", help="用途地域(任意)")
    ap.add_argument("--structure", help="構造(任意)")
    ap.add_argument("--station-walk", type=int, help="駅/バス停まで徒歩分(任意)")
    ap.add_argument("--bus", type=int, help="駅までバス分(バス便のみ・任意)")
    ap.add_argument("--years", help="取引対象年 カンマ区切り(例 2024,2023,2022)")
    ap.add_argument("--annual-rate", type=float, default=0.0,
                    help="時点補正の年率(例 0.02)。既定0=補正なし")
    ap.add_argument("--k", type=int, default=6,
                    help="価格算出に使う最類似の件数(k近傍)。既定6")
    ap.add_argument("--show", type=int, default=8,
                    help="根拠として表示する類似成約の件数。既定8")
    ap.add_argument("--max-year-gap", type=int, default=25,
                    help="対象と築年がこの年数を超えて離れた事例を除外。既定25")
    # 買い手プロフィール（資金カテゴリ用・無料版の範囲）
    ap.add_argument("--income", type=int, help="世帯年収(円)。返済負担率の評価に使用")
    ap.add_argument("--down", type=int, default=0, help="頭金(円)")
    ap.add_argument("--loan-rate", type=float, default=0.0125, help="金利(小数,例0.0125)")
    ap.add_argument("--loan-years", type=int, default=35, help="返済期間(年)")
    ap.add_argument("--mock", action="store_true", help="サンプルデータで動作確認")
    args = ap.parse_args()

    subject = SubjectProperty(
        property_type=args.type, price=args.price, address=args.address,
        land_area_m2=args.land, building_area_m2=args.building,
        build_year=args.build_year, municipality_code=args.city,
        district_name=args.district, city_planning=args.city_planning,
        structure=args.structure, station_walk_min=args.station_walk,
        bus_min=args.bus)

    trade_years = None
    if args.years:
        trade_years = [int(y) for y in args.years.split(",")]

    res = run_pipeline(
        subject,
        reinfolib_key=os.environ.get("REINFOLIB_KEY"),
        google_key=os.environ.get("GOOGLE_KEY"),
        trade_years=trade_years, annual_rate=args.annual_rate, mock=args.mock,
        k_nearest=args.k, max_year_gap=args.max_year_gap,
        annual_income=args.income, down_payment=args.down,
        loan_rate=args.loan_rate, loan_years=args.loan_years,
        estat_appid=os.environ.get("ESTAT_APPID"),
        estat_table=os.environ.get("ESTAT_TABLE", "0000020201"))

    p = res.price
    print("=" * 56)
    print("  住宅購入AI診断（MVP）")
    print("=" * 56)
    print(f"対象: {subject.address}")
    print(f"種別: {subject.property_type} / 売出: {yen(subject.price)}")
    print(f"土地: {subject.land_area_m2}㎡ / 建物: {subject.building_area_m2}㎡ "
          f"/ 築年: {subject.build_year}")
    if res.geocode:
        print(f"座標: {res.geocode.latitude:.5f}, {res.geocode.longitude:.5f} "
              f"({res.geocode.provider})")
    print(f"取引母集団: {res.transactions_count}件")
    print("-" * 56)
    print(f"推定適正価格レンジ: {yen(p.estimate_low)} 〜 {yen(p.estimate_high)}")
    print(f"　中央値: {yen(p.estimate_mid)}")
    print(f"判定: 【{p.verdict}】" + (f"（中央値比 {p.deviation_pct:+}%）"
                                    if p.deviation_pct is not None else ""))
    print(f"確信度: {p.confidence} / 価格算出に使用: {p.comparable_count}件"
          f"（外れ値除外 {p.trimmed_outliers}件）"
          + (f" / レンジ幅 {p.dispersion_pct}%" if p.dispersion_pct is not None else ""))
    if p.unit_building_median or p.unit_land_median:
        print(f"　㎡単価(中央値): 建物 {p.unit_building_median:,}円/㎡" if p.unit_building_median else "", end="")
        print(f" ・ 土地 {p.unit_land_median:,}円/㎡" if p.unit_land_median else "")
    print(f"注記: {p.note}")
    if res.warnings:
        print("-" * 56)
        for w in res.warnings:
            print(f"⚠ {w}")
    # 根拠（上位N件）
    if p.comparables:
        print("-" * 56)
        print(f"価格算出に使った類似成約（最類似 上位{args.show}件・計{p.comparable_count}件）:")
        for c in p.comparables[:args.show]:
            t = c.txn
            print(f"  ・{t.district_name} {t.build_year}年 "
                  f"土地{t.land_area_m2}㎡/建物{t.building_area_m2}㎡ "
                  f"{yen(t.trade_price)} 類似度{c.similarity_score} "
                  f"→推定{yen(c.subject_price_estimate)}({c.price_basis})")

    # ---- ローン ----
    if res.loan:
        L = res.loan
        print("-" * 56)
        print("住宅ローン（無料版：月々返済額まで）")
        print(f"  借入額 {yen(L.principal)}（頭金 {yen(args.down)}）"
              f" 金利{args.loan_rate*100:.2f}% {args.loan_years}年")
        print(f"  月々返済額: 約 {L.monthly_payment:,}円"
              + (f" / 返済負担率 {L.burden_ratio}%" if L.burden_ratio is not None
                 else "（年収 --income 指定で負担率を評価）"))

    # ---- 100点診断 ----
    d = res.diagnosis
    if d:
        print("=" * 56)
        print(f"  総合診断: {d.total_score}点 / {d.grade}"
              f"（情報充足度 {d.data_sufficiency}%）")
        print("=" * 56)
        for c in d.categories:
            bar = "■" * int(round(c.raw * 10)) + "□" * (10 - int(round(c.raw * 10)))
            print(f"  {c.name:<4}{c.points:>5.1f}/{c.weight:<3} {bar} {c.reason}")
        if d.critical_risks:
            print("-" * 56)
            print("⚠ 重大リスク（要確認）:")
            for r in d.critical_risks:
                print(f"  ・[{r.severity}] {r.type}（{r.status}）: {r.evidence}")
        if d.strengths:
            print("-" * 56)
            print("◎ 強み:")
            for s in d.strengths:
                print(f"  ・{s}")
        if d.weaknesses:
            print("△ 弱み:")
            for s in d.weaknesses:
                print(f"  ・{s}")
        if d.to_confirm:
            print("? 要確認（情報不足）:")
            for s in d.to_confirm:
                print(f"  ・{s}")
        print("-" * 56)
        print(d.comment)
    print("=" * 56)
    print("※ ハザード・人口・周辺施設・建物内部状態は今後の付与対象。"
          "本診断は確認できた範囲のルール計算です。")


if __name__ == "__main__":
    main()
