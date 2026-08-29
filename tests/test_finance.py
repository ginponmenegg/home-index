# -*- coding: utf-8 -*-
"""資金計画（PRO）のオフライン単体テスト。ネットワーク不要。

既存の tests/test_logic.py と同じ形式（pytest 無しでも自走する）。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.finance import (brokerage_fee, stamp_duty, registration_tax,
                         acquisition_tax, purchase_costs, rate_scenarios,
                         prepayment, remaining_balance, affordable_loan,
                         loan_deduction,
                         UNKNOWN, ESTIMATED, COMPUTED)
from src.loan import monthly_payment


def test_price_split_config():
    """価格の按分設定：中古は割合、新築建売は建物を定額とする。"""
    from src.finance import FCONFIG
    ps = FCONFIG.get("price_split", {})
    assert ps.get("land_ratio") == 0.60
    assert ps.get("new_build_building_price") == 20_000_000
    assert ps.get("old_building_hint_years") == 30
    assert ps.get("old_building_hint_ratio") == 0.20


def test_man_yen_notation():
    """金額表記：1万円以上は万円、端数は小数で残し、1万円未満は円。"""
    from src.finance import man_yen
    assert man_yen(20_000_000) == "2,000万円"
    assert man_yen(1_214_400) == "121.44万円"
    assert man_yen(8_880_000) == "888万円"
    assert man_yen(10_000) == "1万円"
    assert man_yen(200) == "200円"
    assert man_yen(0) == "0円"
    assert man_yen(None) == "—"


def test_basis_uses_man_yen():
    """根拠欄も万円表記に揃っていること（円のべた書きが残っていない）。"""
    it = brokerage_fee(34_800_000)
    assert "3,480万円" in it.basis and "34,800,000円" not in it.basis
    reg = registration_tax(land_price=20_000_000, loan_amount=30_000_000)
    assert "1,400万円" in reg[0].basis
    d = loan_deduction(30_000_000, 0.0125, 35, category="その他",
                       annual_income=8_000_000, floor_area_m2=90.0)
    assert "2,000万円" in d.basis


def test_brokerage_fee():
    """価格帯ごとの速算式（×5% / ×4%＋2万円 / ×3%＋6万円）に消費税を加える。"""
    # 400万円超：3,480万×3%＋6万＝110.4万（税抜）→ 121.44万円
    it = brokerage_fee(34_800_000)
    assert it.status == COMPUTED
    assert it.amount == 1_214_400, it.amount
    assert "× 3% ＋ 6万円" in it.basis
    # 200万円以下：150万×5%＝7.5万 → 8.25万円
    low = brokerage_fee(1_500_000)
    assert low.amount == 82_500 and "× 5%" in low.basis
    # 200万円超400万円以下：300万×4%＋2万＝14万 → 15.4万円
    mid = brokerage_fee(3_000_000)
    assert mid.amount == 154_000 and "× 4% ＋ 2万円" in mid.basis
    # 境界値：200万円ちょうどは5%区分、400万円ちょうどは4%区分
    assert brokerage_fee(2_000_000).amount == 110_000
    assert brokerage_fee(4_000_000).amount == 198_000
    # 800万円ちょうどは空家特例の上限33万円と一致する
    assert brokerage_fee(8_000_000).amount == 330_000
    # 価格未入力
    assert brokerage_fee(0).amount is None


def test_brokerage_vacant_house_note():
    """800万円以下は空家特例に触れる可能性を注記する（自動適用ではない）。"""
    low = brokerage_fee(5_000_000)
    assert "33万円" in (low.note or "") and "特例" in (low.note or "")
    high = brokerage_fee(34_800_000)
    assert "特例により" not in (high.note or "")


def test_loan_guarantee_and_flat_items():
    from src.finance import loan_guarantee_fee
    g = loan_guarantee_fee(30_000_000)
    assert g.amount == 660_000        # 3,000万円 × 2.2%
    assert loan_guarantee_fee(None).amount is None


def test_new_build_only_registration_items():
    """表題登記・保存登記は新築のときだけ計上する。"""
    kw = dict(land_price=20_000_000, building_price=14_800_000,
              loan_amount=30_000_000, floor_area_m2=90.47, build_year=2005,
              quake_conforming=True)
    used = purchase_costs(34_800_000, **kw)
    names = [i.name for i in used.items]
    assert "表題登記費用" not in names and "所有権保存登記費用" not in names
    new = purchase_costs(34_800_000, new_build=True, **kw)
    names2 = [i.name for i in new.items]
    assert "表題登記費用" in names2 and "所有権保存登記費用" in names2
    # 登記費用の小計に表題・保存も含まれる
    from src.finance import registration_cost_total
    assert registration_cost_total(new) - registration_cost_total(used) == 200_000


def test_option_cost_is_opt_in():
    """オプション費用は任意。選択したときだけ計上する。"""
    kw = dict(land_price=20_000_000, building_price=14_800_000,
              loan_amount=30_000_000, floor_area_m2=90.47, build_year=2005,
              quake_conforming=True)
    off = purchase_costs(34_800_000, **kw)
    on = purchase_costs(34_800_000, option_cost=True, **kw)
    assert "オプション費用" not in [i.name for i in off.items]
    assert on.total - off.total == 500_000


def test_earthquake_insurance_changes_total():
    kw = dict(land_price=20_000_000, building_price=14_800_000,
              loan_amount=30_000_000, floor_area_m2=90.47, build_year=2005,
              quake_conforming=True)
    no_eq = purchase_costs(34_800_000, **kw)
    eq = purchase_costs(34_800_000, earthquake_insurance=True, **kw)
    assert eq.total - no_eq.total == 275_000 - 100_000


def test_stamp_duty_brackets():
    # 1,000万円超5,000万円以下 → 1万円
    assert stamp_duty(34_800_000).amount == 10_000
    # 500万円超1,000万円以下 → 5,000円
    assert stamp_duty(9_000_000).amount == 5_000
    # 5,000万円超1億円以下 → 3万円
    assert stamp_duty(60_000_000).amount == 30_000
    # 境界値：ちょうど1,000万円は「1,000万円以下」の区分
    assert stamp_duty(10_000_000).amount == 5_000
    assert stamp_duty(10_000_001).amount == 10_000


def test_registration_tax_matches_official_examples():
    """法務局資料『登録免許税はどのように計算するのですか？』の計算例と一致すること。

    課税標準は1,000円未満切捨、税額は100円未満切捨（最低1,000円）。
    """
    # 計算例1：土地 5,125,300円 × 15/1000 → 76,800円
    land = registration_tax(land_assessed=5_125_300)[0]
    assert land.amount == 76_800, land.amount
    # 計算例2：建物 3,246,600円 × 20/1000（本則）→ 64,900円
    bld = registration_tax(building_assessed=3_246_600, residential=False)[1]
    assert bld.amount == 64_900, bld.amount
    # 計算例（抵当権）：債権金額 15,000,000円 × 4/1000（本則）→ 60,000円
    mtg = registration_tax(loan_amount=15_000_000, residential=False)[2]
    assert mtg.amount == 60_000, mtg.amount


def test_registration_tax_rounding_floors():
    """端数処理：税額の100円未満切捨と、最低1,000円の下限。"""
    # 評価額1,000円未満でも課税標準は1,000円、税額は下限1,000円
    tiny = registration_tax(land_assessed=500)[0]
    assert tiny.amount == 1_000
    # 課税標準の1,000円未満は切り捨てられる（5,125,300 → 5,125,000）
    a = registration_tax(land_assessed=5_125_300)[0].amount
    b = registration_tax(land_assessed=5_125_000)[0].amount
    assert a == b


def test_registration_tax_estimates_and_flags():
    items = registration_tax(land_price=20_000_000, building_price=14_800_000,
                             loan_amount=30_000_000)
    by = {i.name: i for i in items}
    land = by["登録免許税（土地の所有権移転）"]
    # 評価額を推定した場合は estimated になり、根拠に明記される
    assert land.status == ESTIMATED
    assert "推定" in land.basis
    # 2,000万円×70% = 1,400万円 × 1.5% = 21万円
    assert land.amount == 210_000, land.amount
    # 抵当権は借入額ベース：3,000万円 × 0.1% = 3万円
    assert by["登録免許税（抵当権の設定）"].amount == 30_000
    # 実額の評価額を渡したら computed になる
    items2 = registration_tax(land_assessed=10_000_000, building_assessed=5_000_000)
    assert items2[0].status == COMPUTED
    assert items2[0].amount == 150_000


def test_unknown_items_are_not_invented():
    """情報が足りない項目は金額を作らない（第14章）。"""
    # 評価額も新築時期も無ければ建物の不動産取得税は出さない
    items = acquisition_tax()
    assert all(i.amount is None and i.status == UNKNOWN for i in items)
    # 築年を渡さない場合、建物分は未確認のまま
    pc = purchase_costs(34_800_000, land_price=20_000_000,
                        building_price=14_800_000, loan_amount=30_000_000)
    assert "不動産取得税（建物）" in pc.unknown_items
    assert pc.is_complete is False
    # 合計は判明分のみ。未確認をゼロとして足し込んでいないこと
    assert pc.total == sum(i.amount for i in pc.items if i.amount)
    assert pc.total > 0


def test_acquisition_tax_building_deduction():
    """中古住宅の控除額が新築時期の区分どおりに引かれること。"""
    # 2005年築（H9.4.1以降）→ 控除1,200万円。評価額2,000万円 → (2000-1200)万×3%
    items = acquisition_tax(building_assessed=20_000_000, build_year=2005,
                            floor_area_m2=90.0, quake_conforming=True)
    b = [i for i in items if i.name == "不動産取得税（建物）"][0]
    assert b.amount == 240_000, b.amount
    # 控除が評価額を上回ればゼロ（中古では珍しくない）
    items2 = acquisition_tax(building_assessed=8_000_000, build_year=2005,
                             floor_area_m2=90.0, quake_conforming=True)
    assert [i for i in items2 if i.name == "不動産取得税（建物）"][0].amount == 0
    # 床面積が要件外なら軽減なしで課税される
    items3 = acquisition_tax(building_assessed=20_000_000, build_year=2005,
                             floor_area_m2=30.0, quake_conforming=True)
    b3 = [i for i in items3 if i.name == "不動産取得税（建物）"][0]
    assert b3.amount == 600_000 and "軽減なし" in b3.basis


def test_acquisition_tax_month_unknown_uses_worse_side():
    """新築の月日が不明なら不利側（控除額の小さい方）を採る（運営方針）。"""
    # 1985年は S60.6.30まで=420万 / S60.7.1から=450万 → 小さい420万を採る
    items = acquisition_tax(building_assessed=20_000_000, build_year=1985,
                            floor_area_m2=90.0, quake_conforming=True)
    b = [i for i in items if i.name == "不動産取得税（建物）"][0]
    assert "不利側" in b.basis
    assert b.amount == int(round((20_000_000 - 4_200_000) * 0.03))
    # 月日が判明していれば不利側の注記は出ない
    items2 = acquisition_tax(building_assessed=20_000_000, build_year=1985,
                             build_month=8, build_day=1, floor_area_m2=90.0,
                             quake_conforming=True)
    b2 = [i for i in items2 if i.name == "不動産取得税（建物）"][0]
    assert "不利側" not in b2.basis
    assert b2.amount == int(round((20_000_000 - 4_500_000) * 0.03))


def test_acquisition_tax_nonconforming_reduces_from_tax():
    """耐震基準不適合は控除ではなく税額から減額される。"""
    items = acquisition_tax(building_assessed=10_000_000, build_year=1979,
                            floor_area_m2=90.0, quake_conforming=False)
    b = [i for i in items if i.name == "不動産取得税（建物）"][0]
    # 1,000万×3%=30万 − 軽減10万5,000円
    assert b.amount == 300_000 - 105_000


def test_acquisition_tax_land_reduction():
    """土地は1/2特例のうえ、45,000円と床面積基準の大きい方を減額する。"""
    items = acquisition_tax(building_assessed=8_000_000, land_assessed=14_000_000,
                            land_area_m2=147.07, floor_area_m2=90.47,
                            build_year=2005, quake_conforming=True)
    land = [i for i in items if i.name == "不動産取得税（土地）"][0]
    # 軽減が本税を上回るためゼロ
    assert land.amount == 0
    assert "1/2（宅地）" in land.basis
    # 土地が広く建物が小さいと軽減しきれず残る
    items2 = acquisition_tax(building_assessed=8_000_000, land_assessed=60_000_000,
                             land_area_m2=500.0, floor_area_m2=45.0,
                             build_year=2005, quake_conforming=True)
    land2 = [i for i in items2 if i.name == "不動産取得税（土地）"][0]
    assert land2.amount > 0


def test_loan_deduction_categories():
    """区分ごとの借入限度額と控除期間が正しく効くこと。"""
    kw = dict(annual_income=8_000_000, floor_area_m2=90.0, build_year=2005)
    other = loan_deduction(30_000_000, 0.0125, 35, category="その他", **kw)
    assert other.years == 10 and other.limit == 20_000_000
    # 残高が限度額を上回るので毎年 2,000万×0.7%
    assert other.total == 140_000 * 10
    eco = loan_deduction(30_000_000, 0.0125, 35, category="省エネ基準適合", **kw)
    assert eco.years == 13 and eco.total == 140_000 * 13
    # 買取再販の長期優良は限度額が既存より大きい
    ex = loan_deduction(50_000_000, 0.0125, 35, category="長期優良・低炭素", **kw)
    re = loan_deduction(50_000_000, 0.0125, 35, category="長期優良・低炭素",
                        is_resale=True, **kw)
    assert re.limit > ex.limit
    # 所得要件を超えたら対象外
    ng = loan_deduction(30_000_000, 0.0125, 35, category="その他",
                        annual_income=25_000_000, floor_area_m2=90.0)
    assert ng.status == UNKNOWN and ng.total == 0


def test_loan_deduction_kosodate_and_area():
    kw = dict(annual_income=6_000_000, floor_area_m2=90.0, build_year=2005)
    base = loan_deduction(40_000_000, 0.0125, 35, category="省エネ基準適合", **kw)
    kos = loan_deduction(40_000_000, 0.0125, 35, category="省エネ基準適合",
                         is_kosodate=True, **kw)
    assert kos.limit == 30_000_000 and base.limit == 20_000_000
    assert kos.total > base.total
    # その他住宅には上乗せが無い
    other = loan_deduction(40_000_000, 0.0125, 35, category="その他",
                           is_kosodate=True, **kw)
    assert other.limit == 20_000_000
    # 床面積が足りなければ対象外
    small = loan_deduction(30_000_000, 0.0125, 35, category="その他",
                           annual_income=6_000_000, floor_area_m2=35.0)
    assert small.status == UNKNOWN


def test_fire_insurance_and_scrivener():
    from src.finance import fire_insurance, judicial_scrivener
    no_eq = fire_insurance(False)
    eq = fire_insurance(True)
    assert no_eq.amount == 100_000 and eq.amount == 275_000
    assert "地震保険あり" in eq.name
    assert judicial_scrivener().amount == 50_000


def test_registration_cost_subtotal():
    from src.finance import registration_cost_total
    pc = purchase_costs(34_800_000, land_price=20_000_000,
                        building_price=14_800_000, loan_amount=30_000_000,
                        build_year=2005, floor_area_m2=90.47,
                        quake_conforming=True)
    sub = registration_cost_total(pc)
    # 登録免許税3件（100円未満切捨後）＋司法書士報酬
    assert sub == 210_000 + 26_600 + 30_000 + 50_000


def test_rate_scenarios_monotonic():
    sc = rate_scenarios(30_000_000, 35, 0.0125)
    assert sc[0].label == "現在の金利" and sc[0].diff_monthly == 0
    # 現在／+0.5%／+1.0%／+1.5% の4段階
    assert [s.label for s in sc] == ["現在の金利", "+0.5%", "+1.0%", "+1.5%"]
    # 金利が上がるほど月額も上がる
    monthlies = [s.monthly for s in sc]
    assert monthlies == sorted(monthlies)
    assert sc[-1].diff_monthly > 0


def test_remaining_balance_decreases():
    p, r, y = 30_000_000, 0.0125, 35
    b0 = remaining_balance(p, r, y, 0)
    b12 = remaining_balance(p, r, y, 12)
    bend = remaining_balance(p, r, y, y * 12)
    assert b0 == p
    assert b12 < p
    assert bend <= 1000  # 完済時はほぼ0


def test_prepayment_shortens_term_and_saves_interest():
    res = prepayment(30_000_000, 0.0125, 35, 3_000_000,
                     after_months=12, kind="期間短縮型")
    assert res.months_saved > 0
    assert res.interest_saved > 0
    # 期間短縮型は月額が変わらない
    assert res.new_monthly == monthly_payment(30_000_000, 0.0125, 35)
    # 返済額軽減型は月額が下がる
    res2 = prepayment(30_000_000, 0.0125, 35, 3_000_000,
                      after_months=12, kind="返済額軽減型")
    assert res2.new_monthly < res.new_monthly
    assert res2.months_saved == 0
    # 繰上返済額0なら効果も0
    res3 = prepayment(30_000_000, 0.0125, 35, 0, after_months=12)
    assert res3.months_saved == 0


def test_affordable_loan_matches_score_finance_thresholds():
    # 年収400万円以上は35%基準（score_finance と同じ）
    a = affordable_loan(8_000_000, 0.0125, 35)
    assert a.burden_limit == 35.0
    # 年収400万円未満は30%基準
    b = affordable_loan(3_000_000, 0.0125, 35)
    assert b.burden_limit == 30.0
    # 逆算の整合性：算出された借入額の月額が上限月額とほぼ一致する
    m = monthly_payment(a.max_principal, 0.0125, 35)
    assert abs(m - a.max_monthly) <= 2, (m, a.max_monthly)
    # 頭金は購入可能額に加算される
    c = affordable_loan(8_000_000, 0.0125, 35, down_payment=5_000_000)
    assert c.max_price == c.max_principal + 5_000_000


def test_free_tier_loan_untouched():
    """既存の無料版ロジックを壊していないこと。"""
    from src.loan import compute_loan
    L = compute_loan(38_800_000, down_payment=8_800_000, annual_rate=0.0125,
                     years=35, annual_income=8_000_000)
    assert L.principal == 30_000_000
    assert L.burden_ratio is not None


def test_pdf_report_renders_japanese():
    """PDFレポートが生成でき、日本語と主要な数値が入っていること。"""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import app as webapp
    c = webapp.app.test_client()
    data = dict(price="3480", newbuild="0", land_price="2088",
                building_price="1392", land_assessed="", building_assessed="",
                land_ratio="60", land_area="147.07", floor_area="90.47",
                byear="2005", bmonth="", bday="", quake="yes", down="500",
                income="800", loan_years="35", rate="1.25", quake_ins="0",
                option_cost="0", deduction_cat="その他", resale="0",
                kosodate="0", prepay="300", prepay_after="10",
                prepay_kind="期間短縮型")
    r = c.post("/pro/finance.pdf", data=data)
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data[:5] == b"%PDF-"
    assert len(r.data) > 3000
    # ファイル名がUTF-8で指定されている
    assert "filename*=UTF-8''" in r.headers.get("Content-Disposition", "")
    # 中身に日本語と金額が入っている
    try:
        import pdfplumber, io as _io
        with pdfplumber.open(_io.BytesIO(r.data)) as pdf:
            text = "".join((pg.extract_text() or "") for pg in pdf.pages)
        assert "詳細な資金計画" in text
        assert "諸費用の内訳" in text
        assert "3,480万円" in text
        assert "住宅ローン控除" in text
    except ImportError:
        pass  # pdfplumber が無い環境では中身の検証はスキップ


def test_menu_on_every_page():
    """三本線メニューが全ページの固定バーに出ること。

    LPだけは専用のバー（id="burger"）を持ち、他のページは共通の
    brand_bar（id="hiBurger"）を使う。どちらでも三本線は出ている必要がある。
    またLPは自分自身への「トップ」リンクを載せない（今そこにいるため）ので、
    そこだけ除外して他のページへの導線が揃っていることを見る。
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import app as webapp
    c = webapp.app.test_client()
    for path in ["/", "/terms", "/privacy", "/pro/finance"]:
        h = c.get(path).get_data(as_text=True)
        assert ('id="hiBurger"' in h) or ('id="burger"' in h), path
        for href, label in webapp.MENU_ITEMS:
            if path == "/" and href == "/":
                continue
            assert label in h, (path, label)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"[OK] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
