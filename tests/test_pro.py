# -*- coding: utf-8 -*-
"""PRO診断のオフライン単体テスト（ネットワーク不要）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime

from src.models import SubjectProperty, ProDetail, BuyerProfile, PriceAnalysis
from src.scoring import build_diagnosis
from src.pro_scoring import apply_pro, score_property_detail, score_risk_detail
from src.loan import compute_loan

YEAR = datetime.date.today().year


def _subject(build_year=2005, **kw):
    return SubjectProperty(property_type="chuko_kodate", price=38_800_000,
                           address="神奈川県小田原市城山4-20-18",
                           land_area_m2=147.0, building_area_m2=90.0,
                           build_year=build_year, station_walk_min=12, **kw)


def _price():
    return PriceAnalysis(35_000_000, 38_000_000, 41_000_000, "概ね適正", 2.1,
                         "high", 8, "note")


def _free(subj=None):
    subj = subj or _subject()
    return build_diagnosis(subj, _price(),
                           compute_loan(38_800_000, 3_000_000, 0.0125, 35,
                                        6_000_000))


def _clean():
    return ProDetail(leak="ok", termite="ok", tilt="ok", plumbing="ok",
                     foundation="ok", water_heater="le5", kitchen="le10",
                     bath="le10", electrical="le5", quake_retrofit="done",
                     insulation="high", inspection="done",
                     road_width="ge4", rebuildable="yes", boundary="fixed",
                     encroachment="none")


def _troubled():
    return ProDetail(leak="concern", termite="concern", tilt="ok",
                     plumbing="unknown", foundation="concern",
                     water_heater="gt10", kitchen="gt10", bath="gt10",
                     electrical="gt10", quake_retrofit="none",
                     insulation="low", road_width="lt4", rebuildable="no",
                     boundary="unfixed", encroachment="exists")


def test_pro_raises_data_sufficiency():
    """PROの本質は情報充足度を上げること（仕様書§2）。"""
    free = _free()
    pro = apply_pro(free, _clean(), _subject())
    assert pro.data_sufficiency > free.data_sufficiency


def test_pro_does_not_touch_the_price():
    """PROで足した情報を価格推定に混ぜない（仕様書§1）。

    「点数は売るが円は売らない」という線引きなので、ここが崩れると
    サービスの前提が変わる。
    """
    free = _free()
    before = (free.categories, )  # 参照が置き換わっていないことも見る
    for detail in (_clean(), _troubled()):
        pro = apply_pro(free, detail, _subject())
        # 価格カテゴリは一切動かない
        fp = [c for c in free.categories if c.name == "価格"][0]
        pp = [c for c in pro.categories if c.name == "価格"][0]
        assert (pp.raw, pp.points, pp.reason) == (fp.raw, fp.points, fp.reason)
    # 元の診断は書き換わっていない（FREEとPROを並べて見せられるように）
    assert free.categories is before[0]


def test_pro_scoring_never_sees_the_price_analysis():
    """apply_pro の入口に PriceAnalysis を渡す口が無いことを確かめる。"""
    import inspect
    params = set(inspect.signature(apply_pro).parameters)
    assert "price" not in params and "price_a" not in params
    src = inspect.getsource(apply_pro)
    assert "analyze_price" not in src


def test_condition_problems_lower_the_property_score():
    free = _free()
    base = [c for c in free.categories if c.name == "物件"][0]
    good = score_property_detail(base, _clean(), YEAR)
    bad = score_property_detail(base, _troubled(), YEAR)
    assert good.raw > base.raw > bad.raw
    assert "シロアリ・腐朽" in bad.reason


def test_unanswered_items_do_not_move_the_score():
    """未回答は評価に入れない。充足度だけが下がる（第14章）。"""
    free = _free()
    base = [c for c in free.categories if c.name == "物件"][0]
    blank = score_property_detail(base, ProDetail(), YEAR)
    assert blank.raw == base.raw
    assert blank.sufficiency < score_property_detail(base, _clean(),
                                                     YEAR).sufficiency


def test_rebuild_restriction_is_a_high_risk():
    free = _free()
    pro = apply_pro(free, _troubled(), _subject())
    kinds = {r.type: r for r in pro.critical_risks}
    assert kinds["再建築不可"].severity == "high"
    assert "境界未確定" in kinds
    risk = [c for c in pro.categories if c.name == "リスク"][0]
    assert risk.raw <= 0.2


def test_quake_boundary_years_are_flagged():
    """1981〜1983年築は建築確認日で新旧が分かれる。断定せず確認を促す。"""
    subj = _subject(build_year=1982)
    pro = apply_pro(_free(subj), _clean(), subj)
    flagged = [r for r in pro.critical_risks
               if r.type == "耐震基準の境界にあたる築年"]
    assert len(flagged) == 1 and flagged[0].status == "unknown"
    # 境界から外れた築年では出さない
    subj2 = _subject(build_year=1995)
    pro2 = apply_pro(_free(subj2), _clean(), subj2)
    assert not [r for r in pro2.critical_risks
                if r.type == "耐震基準の境界にあたる築年"]


def test_other_debt_raises_the_burden_ratio():
    """他の借入は毎月出ていくので返済負担率に含める。"""
    bare = compute_loan(38_800_000, 3_000_000, 0.0125, 35, 6_000_000)
    withdebt = compute_loan(38_800_000, 3_000_000, 0.0125, 35, 6_000_000,
                            monthly_extra=35_000)
    assert withdebt.burden_ratio > bare.burden_ratio


def test_buyer_profile_is_optional():
    """購入者情報が空でも診断は成立する。"""
    pro = apply_pro(_free(), _clean(), _subject(), BuyerProfile())
    assert 0 <= pro.total_score <= 100


def test_comment_says_the_price_is_unchanged():
    """利用者に対しても、価格は無料と同じ計算だと明示する。"""
    pro = apply_pro(_free(), _clean(), _subject())
    assert "推定価格レンジは無料診断と同じ計算" in pro.comment


# ---- 無料診断からPROへの引き継ぎ（画面をまたぐのでFlaskのテストクライアントで）----

def _client():
    os.environ["SHINDAN_MOCK"] = "1"
    import app
    return app.app.test_client()


FREE_INPUT = {"address": "神奈川県小田原市城山4-20-18", "price": "3880",
              "land": "147", "building": "90", "byear": "2005",
              "station": "12", "ptype": "chuko_kodate", "income": "600",
              "down": "300", "loan_years": "35"}


def _hidden_fields(html):
    import re
    return dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)">',
                           html))


def test_free_result_offers_the_pro_diagnosis():
    html = _client().post("/diagnose", data=FREE_INPUT).data.decode("utf-8")
    assert "このまま詳細診断に進む" in html
    carried = _hidden_fields(html)
    # 入力し直さずに済むこと
    for k in ("address", "price", "byear", "land", "building", "station",
              "income", "down"):
        assert carried.get(k) == FREE_INPUT[k] or k == "address"
    assert carried["address"] == FREE_INPUT["address"]


def test_carried_values_land_in_the_pro_form():
    c = _client()
    html = c.post("/diagnose", data=FREE_INPUT).data.decode("utf-8")
    form = c.post("/pro/start", data=_hidden_fields(html)).data.decode("utf-8")
    assert "引き継ぎました" in form
    for k in ("price", "byear", "land", "building", "station", "income"):
        assert f'name="{k}" value="{FREE_INPUT[k]}"' in form


def test_income_is_not_put_in_the_url():
    """年収や検討中の住所をクエリ文字列に載せない。"""
    import inspect
    import app
    src = inspect.getsource(app.pro_start)
    assert "request.args" not in src
    html = _client().post("/diagnose", data=FREE_INPUT).data.decode("utf-8")
    assert 'action="/pro/start"' in html
    assert "/pro/start?" not in html


def test_renovation_is_reasked_by_part():
    """無料版は有無しか聞いていないので、箇所のチェックを勝手に入れない。"""
    c = _client()
    data = dict(FREE_INPUT, reno="1")
    html = c.post("/diagnose", data=data).data.decode("utf-8")
    form = c.post("/pro/start",
                  data=_hidden_fields(html)).data.decode("utf-8")
    assert "選び直してください" in form
    import re
    assert not re.search(r'name="reno_interior"[^>]*checked', form)


def test_pro_result_does_not_offer_itself_again():
    c = _client()
    data = dict(FREE_INPUT, leak="ok", termite="ok")
    html = c.post("/pro/diagnose", data=data).data.decode("utf-8")
    assert "このまま詳細診断に進む" not in html


def test_each_free_result_offers_its_own_pro():
    """戸建の結果は戸建PROへ、マンションの結果はマンションPROへ送る。"""
    c = _client()
    house = c.post("/diagnose", data=FREE_INPUT).data.decode("utf-8")
    assert 'action="/pro/start"' in house
    assert "購入診断(戸建)(PRO)" in house

    flat = c.post("/mansion_diagnose", data={
        "address": "神奈川県藤沢市鵠沼桜が岡3丁目", "price": "7480",
        "area": "96.77", "byear": "2006", "station": "5",
        "mfee": "20100", "rfund": "37550"}).data.decode("utf-8")
    assert 'action="/pro/mansion_start"' in flat
    assert "購入診断(マンション)(PRO)" in flat
    # 未評価として挙げる中身も、物件の種類ごとに違う
    assert "修繕積立金の残高" in flat
    assert "接道や再建築の可否" in house

def test_certifications_lift_the_property_score():
    """長期優良住宅・性能評価・耐震等級・瑕疵保険は中古で差が大きい。"""
    from src.pro_scoring import score_certifications
    free = _free()
    base = [c for c in free.categories if c.name == "物件"][0]
    plain = score_property_detail(base, ProDetail(), YEAR)
    certified = score_property_detail(base, ProDetail(
        long_term_excellent="yes", performance_cert="construction",
        quake_grade="g3", defect_insurance="yes"), YEAR)
    assert certified.raw > plain.raw
    assert "長期優良住宅の認定あり" in certified.reason
    assert "耐震等級3" in certified.reason
    # 積み上がりすぎないよう上限を設けてある
    adj, _bits = score_certifications(ProDetail(
        long_term_excellent="yes", performance_cert="construction",
        quake_grade="g3", defect_insurance="yes"))
    assert adj <= 0.22


def test_certification_grades_are_ordered():
    from src.pro_scoring import score_certifications
    g3 = score_certifications(ProDetail(quake_grade="g3"))[0]
    g2 = score_certifications(ProDetail(quake_grade="g2"))[0]
    g1 = score_certifications(ProDetail(quake_grade="g1"))[0]
    assert g3 > g2 > g1 == 0.0
    built = score_certifications(ProDetail(performance_cert="construction"))[0]
    design = score_certifications(ProDetail(performance_cert="design"))[0]
    assert built > design > 0


def test_answering_none_still_raises_sufficiency():
    """「なし」と答えるのも情報。点は上がらないが充足度は上がる。"""
    free = _free()
    base = [c for c in free.categories if c.name == "物件"][0]
    unknown = score_property_detail(base, ProDetail(), YEAR)
    answered_none = score_property_detail(base, ProDetail(
        long_term_excellent="no", performance_cert="none",
        quake_grade="g1", defect_insurance="no"), YEAR)
    assert answered_none.raw == unknown.raw
    assert answered_none.sufficiency > unknown.sufficiency


def test_long_term_certification_asks_about_succession():
    """中古では認定の承継手続きが要る。持っている前提で話を進めない。"""
    pro = apply_pro(_free(), ProDetail(long_term_excellent="yes"), _subject())
    items = [r for r in pro.critical_risks if r.type == "長期優良住宅の認定の承継"]
    assert len(items) == 1
    assert items[0].status == "unknown"
    assert "承継" in items[0].evidence
    # 認定が無ければ出さない
    assert not [r for r in apply_pro(_free(), ProDetail(), _subject()).critical_risks
                if r.type == "長期優良住宅の認定の承継"]

def test_pro_never_lowers_sufficiency():
    """何も答えずにPROへ来ても、無料診断より低く出てはいけない。

    PROは「充足度を上げるサービス」と説明している以上、下がる表示は
    そもそも売り物と矛盾する。項目を足したときに起きやすい。
    """
    free = _free()
    blank = apply_pro(free, ProDetail(), _subject())
    assert blank.data_sufficiency >= free.data_sufficiency
    for c_free, c_pro in zip(free.categories, blank.categories):
        assert c_pro.sufficiency >= c_free.sufficiency

def test_unanswered_items_become_questions_for_the_agent():
    """戸建でも、答えられなかった項目を「聞くこと」に変える。"""
    from src.pro_scoring import agent_questions
    qs = agent_questions(ProDetail(leak="ok", termite="ok", road_width="ge4"),
                         _subject())
    joined = "".join(qs)
    # 答えた項目は聞かない
    assert "雨漏りの跡はありますか" not in joined
    assert "前面道路の幅員" not in joined
    # 答えていない項目は聞く
    assert "再建築は可能ですか" in joined
    assert "隣地との境界は確定していますか" in joined
    # 入力には無いが、まとめて答えが得られる書類は必ず頼む
    assert "物件状況報告書" in joined


def test_the_most_useful_request_comes_first():
    """個別に20問ぶつけるより、書類を1つ出してもらう方が早い。"""
    from src.pro_scoring import agent_questions
    qs = agent_questions(ProDetail(), _subject())
    assert "物件状況報告書" in qs[0]
    # 影響の大きい法規の確認が、設備の交換時期より前に来る
    joined = qs
    rebuild = next(i for i, q in enumerate(joined) if "再建築は可能" in q)
    heater = next(i for i, q in enumerate(joined) if "給湯器はいつ" in q)
    assert rebuild < heater


def test_confirmation_certificate_asked_for_boundary_years():
    from src.pro_scoring import agent_questions
    qs = "".join(agent_questions(ProDetail(), _subject(build_year=1982)))
    assert "確認済証の日付" in qs
    qs2 = "".join(agent_questions(ProDetail(), _subject(build_year=1995)))
    assert "確認済証の日付" not in qs2

def test_question_card_sits_between_strengths_and_what_is_covered():
    """読む順に置く。強みを見たあとに「では何を聞けばいいか」が続く。"""
    html = _client().post("/pro/diagnose", data=dict(
        FREE_INPUT, leak="ok")).data.decode("utf-8")
    strengths = html.find("◎ 強み")
    questions = html.find("仲介業者に聞くこと")
    covered = html.find("この診断が見ているもの")
    assert -1 < strengths < questions < covered


def test_house_questions_do_not_mention_flat_paperwork():
    """戸建の画面にマンションの管理の話を出さない。"""
    html = _client().post("/pro/diagnose",
                          data=FREE_INPUT).data.decode("utf-8")
    assert "重要事項調査報告書" not in html
    assert "総会議事録" not in html
    assert "物件状況報告書" in html


def test_flat_questions_keep_their_own_wording():
    html = _client().post("/pro/mansion", data={
        "address": "神奈川県藤沢市鵠沼桜が岡3丁目", "price": "7480",
        "area": "96.77", "byear": "2006"}).data.decode("utf-8")
    assert "重要事項調査報告書と、総会議事録" in html
    assert "物件状況報告書" not in html

def test_landing_page_menu_lists_every_page():
    """LPのメニューが他ページと食い違わないようにする。

    以前ここにリンクを直接書いていたため、メニューにページを足しても
    LPだけ古いままになり、PROの2ページが載っていなかった。
    """
    import re
    import app
    html = _client().get("/").data.decode("utf-8")
    nav = re.search(r'<nav class="menu" id="menu" hidden>(.*?)</nav>',
                    html, re.S).group(1)
    hrefs = set(re.findall(r'href="([^"]+)"', nav))
    for path, _label in app.MENU_ITEMS:
        if path == "/":
            continue      # LP自身への導線は要らない
        assert path in hrefs, path

def test_forms_open_empty():
    """フォームを開いた時点で値が入っていないこと。

    戸建の入力欄に小田原の実例が初期値として入っていて、気づかず診断すると
    他人の物件の結果が出ていた。例は placeholder で見せる。
    """
    import re
    c = _client()
    # 入れておいてよい既定値（借入年数・金利など、意味のある初期値）
    allowed = {"35", "1.25", "1", "0", "unknown", "不明"}
    for path in ("/buy", "/mansion", "/pro/diagnose", "/pro/mansion"):
        html = c.get(path).data.decode("utf-8")
        filled = [(m.group(1), m.group(2))
                  for m in re.finditer(r'name="(\w+)" value="([^"]+)"', html)
                  if m.group(2) not in allowed]
        assert not filled, f"{path} に初期値が入っている: {filled}"
        # 貼り付け欄も空
        area = re.search(r'<textarea name="listing"[^>]*>(.*?)</textarea>',
                         html, re.S)
        if area:
            assert area.group(1).strip() == "", path

def test_pro_is_reachable_but_not_advertised():
    """PROはメニューに出さず、フッターから辿れる状態にしておく。

    ログインも課金もまだ噛ませていないので、メニューに常設すると課金前提の
    機能を誰にでも開いたままにすることになる。かといってどこからも辿れないと
    資金計画のページが孤立する。
    """
    import re
    import app
    c = _client()
    html = c.get("/").data.decode("utf-8")
    nav = re.search(r'<nav class="menu" id="menu" hidden>(.*?)</nav>',
                    html, re.S).group(1)
    assert "/pro/" not in nav
    assert not [p for p, _l in app.MENU_ITEMS if p.startswith("/pro/")]
    # フッターからは辿れる（全ページ共通）
    for path in ("/", "/buy", "/mansion", "/terms"):
        page = c.get(path).data.decode("utf-8")
        for pro in ("/pro/diagnose", "/pro/mansion", "/pro/finance"):
            assert pro in page, f"{path} から {pro} に辿れない"


def test_pro_pages_say_they_will_be_charged_for():
    """無料で出しておいて黙って課金を始めない。先に書いておく。"""
    c = _client()
    for path in ("/pro/diagnose", "/pro/mansion", "/pro/finance"):
        html = c.get(path).data.decode("utf-8")
        assert "試験公開中です。" in html, path
        assert "将来は有料" in html, path

def test_copy_guide_is_reachable_from_the_forms():
    """アプリで文字がコピーできない人が、そこで止まらないようにする。"""
    c = _client()
    assert c.get("/copy-guide").status_code == 200
    for path in ("/buy", "/mansion", "/"):
        assert "/copy-guide" in c.get(path).data.decode("utf-8"), path


def test_copy_guide_covers_both_phone_platforms():
    html = _client().get("/copy-guide").data.decode("utf-8")
    for word in ("iPhone", "Android", "スクリーンショット", "レンズ"):
        assert word in html, word
    # ブラウザで開けば済む場合を先に案内する（headの説明文は数えない）
    body = html[html.index("<body>"):]
    assert body.index("ブラウザ") < body.index("スクリーンショット")
    # 読み取れないときの逃げ道も書く
    assert "販売図面のPDF" in html and "手入力" in html

def test_checklist_only_claims_what_the_diagnosis_answers():
    """自分ごと化のチェックリストに、まだ無い機能を書かない。

    複数物件の比較はPROの未実装機能なので、ここに並べると
    できないことを約束することになる。
    """
    import re
    html = _client().get("/").data.decode("utf-8")
    ul = re.search(r'<ul class="checks">(.*?)</ul>', html, re.S).group(1)
    items = re.findall(r"<li>(.*?)</li>", ul, re.S)
    assert len(items) >= 5
    joined = re.sub(r"<[^>]+>", "", "".join(items))
    # 診断が実際に答えを出せることだけ
    for topic in ("価格", "ハザード", "ローン"):
        assert topic in joined, topic
    # 用意していない機能を匂わせない
    assert "比較" not in joined

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"[OK] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
