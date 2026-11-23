# -*- coding: utf-8 -*-
"""提携の不動産会社への相談窓口。ネットワーク不要。

画面に書いた約束を、作りとテストで固定する。
- 採点のコードは窓口を知らない（提携で点数が変わる経路が無い）
- 提携先が設定されていなければ、窓口は一切出ず、約束は「紹介しない」のまま
- 対応地域の外には出さない
- 同意が無ければ送らない。ほかの会社で申込み中なら送らない
- 提携先に渡すのは、フォームに書いた項目だけ（点数・年収は渡さない）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARTNER = {
    "key": "partner_a", "name": "見本不動産株式会社",
    "license": "神奈川県知事（1）第00000号", "areas": ["東京都", "神奈川県"],
    "handles": "中古戸建・マンション・土地",
    "contact_note": "2営業日以内にご連絡します", "notify_env": "TEST_PARTNER_A_EMAIL",
    "relation": "", "referral_fee": False,
}


@pytest.fixture
def on(tmp_path, monkeypatch):
    """提携先を1社設定した状態。"""
    from src import referral
    conf = tmp_path / "partners.json"
    conf.write_text(json.dumps({"partners": [PARTNER]}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setattr(referral, "_CONF", str(conf))
    monkeypatch.setenv("TEST_PARTNER_A_EMAIL", "partner@example.jp")
    sent = []

    def fake_send(to, subject, html, text=""):
        sent.append({"to": to, "subject": subject, "html": html, "text": text})
        return True, ""
    import app as webapp
    monkeypatch.setattr(webapp.mailer, "send", fake_send)
    webapp._SOUDAN_RATE.clear()
    return webapp, sent, conf


def _client():
    import app as webapp
    return webapp.app.test_client()


# ---- 採点と切り離されていること --------------------------------------------

SCORING = ("scoring.py", "pro_scoring.py", "land_scoring.py", "mansion_scoring.py",
           "land_pro_scoring.py", "mansion_pro_scoring.py", "pipeline.py",
           "price_analysis.py", "land_price.py", "mansion_price.py", "comparable.py",
           "finance.py", "land_finance.py", "loan.py", "household.py")


def test_the_scoring_code_does_not_know_the_partners():
    """提携先が誰かで点数が変わる経路を、作りとして持たない。"""
    for name in SCORING:
        path = os.path.join(ROOT, "src", name)
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        assert "referral" not in src, name


# ---- 提携先が無いとき --------------------------------------------------------

def _repo_partners():
    with open(os.path.join(ROOT, "referral_partners.json"), encoding="utf-8") as f:
        return json.load(f)["partners"]


@pytest.fixture
def off(monkeypatch):
    """リポジトリの設定のまま、通知先の環境変数だけが無い状態。"""
    for p in _repo_partners():
        monkeypatch.delenv(p["notify_env"], raising=False)


def test_the_repository_partner_waits_for_its_notify_address(off):
    """会社名と免許番号は設定済み。通知先を本番の環境変数に入れるまで出ない。"""
    from src import referral
    ps = _repo_partners()
    assert ps and all(p["name"] and p["license"] and p["notify_env"] for p in ps)
    assert not referral.enabled()


def test_the_partner_is_the_operators_company_and_about_says_so():
    """提携先が運営者の所属する会社であることを、設定に書いてある。

    窓口の枠と /soudan の会社情報には関係を出さない（relation は空）。
    関係は /about の「相談の窓口について」で書く。"""
    p = [x for x in _repo_partners() if x["key"] == "citycreation"][0]
    assert p["name"] == "株式会社シティクリエイション"
    assert p["license"] == "神奈川県知事（2）第029987号"
    assert p["operator_employer"] is True and p["relation"] == ""


def test_without_a_partner_the_promise_stays_as_it_was(off):
    c = _client()
    lp = c.get("/").get_data(as_text=True)
    assert "物件を売らない・紹介しない" in lp
    assert "/soudan" not in lp
    assert "PLACEHOLDER" not in lp
    assert c.get("/soudan").status_code == 404
    assert c.post("/soudan/send", data={}).status_code == 404


def test_a_partner_without_a_notify_address_does_not_count(tmp_path, monkeypatch):
    """届け先が無い提携先を出すと、申込みが宙に浮く。"""
    from src import referral
    conf = tmp_path / "p.json"
    conf.write_text(json.dumps({"partners": [PARTNER]}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setattr(referral, "_CONF", str(conf))
    monkeypatch.delenv("TEST_PARTNER_A_EMAIL", raising=False)
    assert not referral.enabled()


# ---- 提携先があるとき --------------------------------------------------------

def test_with_a_partner_the_top_page_says_so(on):
    lp = _client().get("/").get_data(as_text=True)
    assert "診断は中立。物件は売らない" in lp
    assert "物件を売らない・紹介しない" not in lp
    assert 'href="/soudan?from=lp"' in lp
    assert "紹介料を受け取っていません" in lp
    assert "PLACEHOLDER" not in lp


def test_the_window_names_the_partner_and_its_license(on):
    h = _client().get("/soudan").get_data(as_text=True)
    assert "見本不動産株式会社" in h
    assert "神奈川県知事（1）第00000号" in h
    assert "紹介料を受け取っていません" in h
    assert 'name="agree"' in h


def test_a_fee_is_written_where_the_window_is(on):
    """紹介料を受け取るなら、窓口を出すその場所に書く（ステマ規制）。"""
    webapp, sent, conf = on
    p = dict(PARTNER, referral_fee=True)
    conf.write_text(json.dumps({"partners": [p]}, ensure_ascii=False), encoding="utf-8")
    from src import referral
    block = referral.result_block("神奈川県横浜市港北区日吉本町1丁目")
    assert "紹介料を受け取ることがあります" in block
    assert "紹介料を受け取っていません" not in block


def test_a_relation_to_the_operator_is_shown(on):
    webapp, sent, conf = on
    p = dict(PARTNER, relation="運営者の勤務先です")
    conf.write_text(json.dumps({"partners": [p]}, ensure_ascii=False), encoding="utf-8")
    from src import referral
    assert "運営者の勤務先です" in referral.result_block("東京都世田谷区")
    assert "運営者の勤務先です" in _client().get("/soudan").get_data(as_text=True)


def test_the_result_block_only_appears_inside_the_area(on):
    from src import referral
    assert "提携の不動産会社" in referral.result_block("神奈川県横浜市港北区日吉本町1丁目")
    assert "提携の不動産会社" in referral.result_block("横浜市港北区日吉本町1丁目")
    assert "提携の不動産会社" in referral.result_block("東京都世田谷区")
    assert referral.result_block("千葉県船橋市前原西2丁目") == ""
    assert referral.result_block("") == ""


def test_the_result_block_sits_below_the_score_explanation(on):
    """結果の上部やリスクの説明の途中に出さない。"""
    import app as webapp
    t = webapp.RESULT
    assert t.index("この診断が見ているもの") < t.index("referral_block(")
    assert t.index("referral_block(") < t.index("<b>免責</b>")


def test_the_address_is_not_put_in_the_url(on):
    """物件の住所は POST で渡す。URLに載せない。"""
    from src import referral
    block = referral.result_block("東京都世田谷区")
    assert 'method="post" action="/soudan"' in block
    assert "?address=" not in block


# ---- 申込み -----------------------------------------------------------------

def _form(**kw):
    d = {"address": "神奈川県横浜市港北区日吉本町1丁目", "kind": "buy",
         "stage": "found", "other": "none", "when": "3か月以内",
         "name": "見本 花子", "email": "sample@example.jp", "tel": "",
         "note": "擁壁が気になる", "agree": "on", "from": "result", "website": ""}
    d.update(kw)
    return d


def test_a_valid_application_goes_to_the_partner(on):
    webapp, sent, conf = on
    r = _client().post("/soudan/send", data=_form())
    h = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "お申込みを受け付けました" in h
    assert sent and sent[0]["to"] == "partner@example.jp"
    assert "見本 花子" in sent[0]["text"]
    assert "日吉本町1丁目" in sent[0]["text"]


def test_what_goes_to_the_partner_has_no_score_and_no_income(on):
    webapp, sent, conf = on
    _client().post("/soudan/send", data=_form(income="700", down="500", score="80"))
    text = sent[0]["text"]
    for word in ("700", "点数：", "年収：", "頭金："):
        assert word not in text, word


def test_no_consent_means_nothing_is_sent(on):
    webapp, sent, conf = on
    h = _client().post("/soudan/send", data=_form(agree="")).get_data(as_text=True)
    assert "同意していただく必要があります" in h
    assert sent == []


def test_someone_already_applying_elsewhere_is_not_sent(on):
    """ほかの会社で申込み中の人を送ると、業者どうしの取り合いになる。"""
    webapp, sent, conf = on
    h = _client().post("/soudan/send", data=_form(other="applied")).get_data(as_text=True)
    assert "ご案内できません" in h
    assert sent == []


def test_outside_the_area_is_not_sent(on):
    webapp, sent, conf = on
    h = _client().post("/soudan/send",
                       data=_form(address="千葉県船橋市前原西2丁目")).get_data(as_text=True)
    assert "対応地域の外" in h
    assert sent == []


def test_searching_needs_a_served_prefecture(on):
    webapp, sent, conf = on
    c = _client()
    h = c.post("/soudan/send", data=_form(address="", stage="search",
                                          pref="")).get_data(as_text=True)
    assert "都県を選んでください" in h
    assert sent == []
    c.post("/soudan/send", data=_form(address="", stage="search",
                                      pref="東京都", area="世田谷区"))
    to_partner = [m for m in sent if m["to"] == "partner@example.jp"]
    assert len(to_partner) == 1 and "東京都 世田谷区" in to_partner[0]["text"]


def test_the_hidden_field_stops_bots(on):
    webapp, sent, conf = on
    _client().post("/soudan/send", data=_form(website="http://spam.example"))
    assert sent == []


def test_a_failed_mail_is_not_reported_as_received(on, monkeypatch):
    webapp, sent, conf = on
    monkeypatch.setattr(webapp.mailer, "send", lambda *a, **k: (False, "down"))
    h = _client().post("/soudan/send", data=_form()).get_data(as_text=True)
    assert "お申込みを受け付けました" not in h
    assert "送れませんでした" in h


def test_the_form_keeps_what_was_typed_after_an_error(on):
    webapp, sent, conf = on
    h = _client().post("/soudan/send", data=_form(agree="", note="駅まで徒歩10分以内")
                       ).get_data(as_text=True)
    assert "駅まで徒歩10分以内" in h
    assert "見本 花子" in h


def test_two_partners_in_one_area_are_chosen_by_the_person(on):
    """こちらで割り振らない。選ばずに送ったら止める。"""
    webapp, sent, conf = on
    b = dict(PARTNER, key="partner_b", name="見本住宅株式会社",
             notify_env="TEST_PARTNER_A_EMAIL")
    conf.write_text(json.dumps({"partners": [PARTNER, b]}, ensure_ascii=False),
                    encoding="utf-8")
    c = _client()
    assert 'name="partner"' in c.get("/soudan").get_data(as_text=True)
    h = c.post("/soudan/send", data=_form()).get_data(as_text=True)
    assert "相談する会社を選んでください" in h and sent == []
    c.post("/soudan/send", data=_form(partner="partner_b"))
    assert "見本住宅株式会社" in sent[0]["text"]


def test_the_operator_gets_a_copy_when_a_contact_is_set(on, monkeypatch):
    """届いたかを運営者が確かめられるように、控えを送る。"""
    webapp, sent, conf = on
    monkeypatch.setattr(webapp, "CONTACT", "operator@example.jp")
    _client().post("/soudan/send", data=_form())
    assert [m["to"] for m in sent] == ["partner@example.jp", "operator@example.jp"]


def test_the_application_is_kept_for_a_year_at_most():
    from src import referral
    assert referral.RETENTION_DAYS == 365
    import app as webapp
    assert "1年間保存し、その後削除します" in webapp._PRIVACY_BODY


def test_about_says_the_partner_is_the_employer(on, monkeypatch):
    """勤務先を提携先にするなら、/about の「勤務先とは関係のない」を書き換える。"""
    webapp, sent, conf = on
    p = dict(PARTNER, operator_employer=True, relation="運営者が在籍する会社です")
    conf.write_text(json.dumps({"partners": [p]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(webapp, "operator_named", lambda: True)
    h = _client().get("/about").get_data(as_text=True)
    assert "相談の窓口について" in h
    assert "見本不動産株式会社に所属しています" in h
    assert "窓口の提携先は所属する会社です" in h
    assert "勤務先とは関係のない" not in h
    assert "PLACEHOLDER" not in h


def test_about_without_an_employer_partner_keeps_the_old_line(on, monkeypatch):
    webapp, sent, conf = on
    monkeypatch.setattr(webapp, "operator_named", lambda: True)
    h = _client().get("/about").get_data(as_text=True)
    assert "相談の窓口について" not in h
    assert "勤務先とは関係のない" in h


def test_the_relation_is_not_in_the_consent_box(on):
    """関係は会社情報と結果の枠に出す。同意の欄には入れない。"""
    webapp, sent, conf = on
    p = dict(PARTNER, operator_employer=True, relation="運営者が在籍する会社です")
    conf.write_text(json.dumps({"partners": [p]}, ensure_ascii=False), encoding="utf-8")
    h = _client().get("/soudan").get_data(as_text=True)
    agree = h[h.index('class="agree"'):h.index('id="send"')]
    assert "運営者が在籍する会社です" not in agree
    assert "運営者が在籍する会社です" in h


# ---- 相談の種類（2026-11-23）------------------------------------------------

def test_the_form_offers_four_kinds_of_consultation(on):
    h = _client().get("/soudan").get_data(as_text=True)
    for word in ("購入（物件探し・この物件の購入）", "住み替え（今の家を売って、買い替える）",
                 "売却（空き家など）", "相続した家・土地をどうするか"):
        assert word in h, word


def test_a_sale_of_an_empty_house_goes_without_a_buying_stage(on):
    """売却・相続には「探している／決まっている」が無い。聞かずに送る。"""
    webapp, sent, conf = on
    _client().post("/soudan/send", data=_form(
        address="", topic="sell", stage="", pref="神奈川県", area="平塚市"))
    to_partner = [m for m in sent if m["to"] == "partner@example.jp"]
    assert len(to_partner) == 1
    text = to_partner[0]["text"]
    assert "売却（空き家など）" in text
    assert "家・土地のある地域：神奈川県 平塚市" in text
    assert "いまの状況" not in text
    assert to_partner[0]["subject"].startswith("【HOME INDEX】売却の相談")


def test_a_move_names_itself_in_the_subject(on):
    webapp, sent, conf = on
    _client().post("/soudan/send", data=_form(address="", topic="kaikae", stage="",
                                              pref="東京都", area="世田谷区"))
    assert sent[0]["subject"].startswith("【HOME INDEX】住み替えの相談")


def test_an_unknown_kind_is_refused(on):
    webapp, sent, conf = on
    h = _client().post("/soudan/send", data=_form(topic="loan")).get_data(as_text=True)
    assert "相談の種類を選んでください" in h
    assert sent == []


def test_a_sale_outside_the_area_is_not_sent(on):
    webapp, sent, conf = on
    _client().post("/soudan/send", data=_form(address="", topic="inherit", stage="",
                                              pref="千葉県", area="船橋市"))
    assert sent == []
