# -*- coding: utf-8 -*-
"""PROの土地診断（つなぎ融資・総額・時系列）。ネットワーク不要。"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402

PRO = dict(address="神奈川県小田原市南町1-1", price="1800", area="120",
           budget="2500", household="4", road_width="6", road_type="公道",
           frontage="8", station="12", coverage="60", far="200",
           city="14206", district="南町", income="800", down="500",
           loan_years="35", condition="none",
           contract_date="2026-03-01", deposit="100",
           settlement="2026-04-10", completion="2027-02-28",
           bridge_rate="2.8", own_funds="300",
           extra_work="300", exterior="150", other_costs="250")

GOOD_SITE = dict(water="done20", sewer="connected", gas="city_done",
                 ground="ok", retaining="none", level="flat",
                 oldhouse="none", leftovers="none")
BAD_SITE = dict(water="none", sewer="septic", gas="lpg", ground="needed",
                retaining="uncertified", level="lower", oldhouse="buyer",
                leftovers="exists")


@pytest.fixture(autouse=True)
def mock_mode():
    keep = os.environ.get("SHINDAN_MOCK")
    os.environ["SHINDAN_MOCK"] = "1"
    yield
    if keep is None:
        os.environ.pop("SHINDAN_MOCK", None)
    else:
        os.environ["SHINDAN_MOCK"] = keep


@pytest.fixture
def client():
    return webapp.app.test_client()


def _post(client, **kw):
    return client.post("/pro/land",
                       data=dict(PRO, **kw)).get_data(as_text=True)


# ---- 画面が出る ----
def test_the_pro_form_opens(client):
    h = client.get("/pro/land").get_data(as_text=True)
    assert "/pro/land" in h
    assert "つなぎ融資の金利" in h
    assert "土地の決済予定日" in h
    assert "建物の完成（引渡し）予定日" in h


def test_the_pro_form_explains_why_a_bridge_loan_exists(client):
    h = client.get("/pro/land").get_data(as_text=True)
    assert "建物が" in h and "完成しないと実行されない" in h


def test_the_result_has_all_three_money_sections(client):
    h = _post(client)
    assert "<h2>つなぎ融資</h2>" in h
    assert "<h2>いつ、いくら要るか</h2>" in h
    assert "<h2>総額の積み上げ</h2>" in h
    # 無料と同じ「資金」の見出しと混ざらないよう、PROでは名前を変える
    assert "<h2>住宅ローン本体</h2>" in h


def test_the_bridge_interest_is_shown_and_separate_from_the_mortgage(client):
    h = _post(client)
    assert "55.98万円" in h            # 実測値（手計算と一致）
    assert "住宅ローンとは別に" in h


def test_the_timeline_puts_the_deposit_first_and_calls_it_cash(client):
    h = _post(client)
    i = h.index("<h2>いつ、いくら要るか</h2>")
    j = h.index("</table>", i)
    rows = h[i:j]
    assert rows.index("土地の手付金") < rows.index("着工金")
    assert "融資の実行前に払うので、現金が要ります" in rows


def test_the_total_adds_up(client):
    h = _post(client)
    # 1800 + 2500 + 300 + 150 + 250 + 55.98 = 5055.98万円
    assert "5,055.98万円" in h


# ---- 金額を出さない項目 ----
def test_costs_with_no_public_source_show_a_dash_not_a_number(client):
    h = _post(client)
    assert "地盤改良" in h
    assert "公的な費用統計が無いため、目安の金額も出しません" in h
    assert "いまは誰にも分からない" in h
    # 相場としてよく出回る数字を、こちらから書かないこと
    for figure in ("30〜80万", "50〜150万", "100〜200万"):
        assert figure not in h, figure


def test_an_item_answered_as_unnecessary_disappears(client):
    h = _post(client, **GOOD_SITE)         # 古家なし → 解体は出さない
    assert "古家の解体" not in h


def test_an_item_answered_as_needed_loses_the_uncertain_label(client):
    h = _post(client, **BAD_SITE)          # 未引込 → 引き込みが要る
    assert "上下水道・ガスの引き込み（要否も未確認）" not in h
    assert "上下水道・ガスの引き込み" in h


# ---- 現地と書類（A群）----
def test_the_site_questions_are_on_the_form(client):
    h = client.get("/pro/land").get_data(as_text=True)
    for label in ("上水道の引き込み", "下水道", "ガス", "地盤調査", "擁壁",
                  "道路との高低差", "古家", "残置物・工作物"):
        assert label in h, label
    assert "引き込み済み（口径13mm）" in h
    assert "擁壁あり・検査済証がない" in h


def test_the_site_answers_move_the_risk_score(client):
    def risk(h):
        m = re.search(r"リスク</span>\s*<span class=\"muted\">([\d.]+) / 25", h)
        return float(m.group(1))
    assert risk(_post(client, **BAD_SITE)) < risk(_post(client, **GOOD_SITE))


def test_leaving_the_site_questions_blank_does_not_dock_the_score(client):
    """調べていないことを、悪い土地として採点しない。"""
    def risk(h):
        m = re.search(r"リスク</span>\s*<span class=\"muted\">([\d.]+) / 25", h)
        return float(m.group(1))
    blank = risk(_post(client))
    free = client.post("/land_diagnose", data=PRO).get_data(as_text=True)
    assert risk(free) == blank


def test_the_unanswered_questions_come_back_as_things_to_check(client):
    h = _post(client)
    assert "地盤調査をするまで" in h
    assert "検査済証" in h


def test_a_bad_site_is_spelled_out_in_words(client):
    h = _post(client, **BAD_SITE)
    assert "検査済証がありません" in h
    assert "浄化槽" in h


def test_a_thirteen_millimetre_supply_explains_the_catch(client):
    h = _post(client, water="done13")
    assert "増径" in h


# ---- 前提を隠さない ----
def test_the_assumed_payment_split_is_declared(client):
    h = _post(client)                      # start_pct / framing_pct は未入力
    assert "請負契約書でご確認ください" in h
    assert "着工日が未入力" in h


def test_a_given_split_removes_the_assumption_notice(client):
    h = _post(client, start_pct="10", framing_pct="40",
              start_date="2026-07-01", framing_date="2026-11-01")
    assert "請負契約書でご確認ください" not in h


def test_a_missing_rate_is_not_filled_in_for_the_user(client):
    h = _post(client, bridge_rate="")
    assert "金利が未入力" in h
    # 勝手な金利を置いたら、その数字が独り歩きする
    assert "利息 0円" in h or "計算していません" in h


def test_missing_dates_ask_rather_than_guess(client):
    h = _post(client, settlement="", completion="")
    assert "決済予定日" in h


# ---- 無料からの導線 ----
def test_the_free_result_offers_the_pro_money_view(client):
    free = dict(PRO)
    for k in ("contract_date", "deposit", "settlement", "completion",
              "bridge_rate", "own_funds"):
        free.pop(k, None)
    h = client.post("/land_diagnose", data=free).get_data(as_text=True)
    assert "つなぎ融資の利息は、この診断に入っていません" in h
    assert 'action="/pro/land/start"' in h


def test_the_handover_keeps_what_was_typed(client):
    h = client.post("/pro/land/start", data=PRO).get_data(as_text=True)
    assert 'value="1800"' in h
    assert 'value="2026-04-10"' in h       # 決済日も引き継ぐ
    assert 'value="2.8"' in h


def test_the_free_result_has_no_money_sections(client):
    h = client.post("/land_diagnose", data=PRO).get_data(as_text=True)
    assert "<h2>つなぎ融資</h2>" not in h
    assert "<h2>総額の積み上げ</h2>" not in h


# ---- PROの導線 ----
def test_pro_land_is_listed_with_the_other_pro_tools():
    assert "/pro/land" in webapp._PRO_MENU_HTML
    assert "/pro/land" in webapp._PRO_LINKS_MEMBER


def test_the_score_is_the_same_when_the_site_is_unanswered(client):
    """現地の答えが無いあいだは、無料とPROで同じ点になること。

    資金の見せ方を足しただけでは点は動かない。動くのは、現地と書類に
    答えてもらったときだけ。
    """
    def score(h):
        return int(re.search(r'<b style="color:[^"]+">(\d+)</b><small>点',
                             h).group(1))
    free = client.post("/land_diagnose", data=PRO).get_data(as_text=True)
    pro = _post(client)
    assert score(free) == score(pro)
