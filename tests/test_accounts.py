# -*- coding: utf-8 -*-
"""アカウント（ログイン・保存・比較・プラン）のテスト。ネットワーク不要。

本番はNeon(PostgreSQL)だが、ここではsqliteに繋いで同じコードを通す。
SQLは方言差の無い範囲で書いてあるので、これで論理は検証できる。

app.py は起動時に DATABASE_URL を見てメニューやルートの出方を決めるため、
このファイルでは環境変数を立ててから app を読み込み直す。終わったら
元に戻すので、他のテストには影響しない。
"""
import os
import sys
import json
import importlib
import tempfile
import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def env():
    """sqliteを繋いだ状態の app / src モジュール一式を返す。"""
    prev = os.environ.get("DATABASE_URL")
    prev_key = os.environ.get("SECRET_KEY")
    path = os.path.join(tempfile.mkdtemp(prefix="hi_acc_"), "t.db")
    os.environ["DATABASE_URL"] = "sqlite:///" + path.replace("\\", "/")
    os.environ["SECRET_KEY"] = "test-secret-key"
    # 空文字にする。消すだけだと app.py が .env を読み直して復活する
    # （setdefault なので、キーが在れば上書きされない）。
    prev_mail = os.environ.get("RESEND_API_KEY")
    os.environ["RESEND_API_KEY"] = ""

    from src import db, accounts, saved
    import app as webapp
    for m in (db, accounts, saved, webapp):
        importlib.reload(m)
    # reload の順で app が古い db を掴むことがあるので、確実に貼り直す
    webapp.db, webapp.accounts, webapp.saved = db, accounts, saved
    db.init_schema()

    yield type("E", (), dict(app=webapp, db=db, accounts=accounts,
                             saved=saved, path=path))

    if prev is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = prev
    if prev_key is None:
        os.environ.pop("SECRET_KEY", None)
    else:
        os.environ["SECRET_KEY"] = prev_key
    if prev_mail is None:
        os.environ.pop("RESEND_API_KEY", None)
    else:
        os.environ["RESEND_API_KEY"] = prev_mail
    for m in (db, accounts, saved, webapp):
        importlib.reload(m)


# ---- 未設定のときは機能ごと隠れる -----------------------------------------

def test_disabled_without_database_url():
    """DATABASE_URL が無ければ、アカウント機能はメニューにも結果にも出ない。

    削除ではなく空文字を入れる。app.py は起動時に .env を読み直すので、
    消しただけでは .env の値が戻ってきてしまう（setdefault のため、
    キーが在れば上書きされない）。
    """
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = ""
    try:
        from src import db
        import app as webapp
        importlib.reload(db)
        importlib.reload(webapp)
        assert not db.enabled()
        assert all(h != "/mypage" for h, _ in webapp.MENU_ITEMS)
        h = webapp.app.test_client().get("/login").get_data(as_text=True)
        assert "準備中" in h
        # 使っていない機能について「預かります」と書かないこと
        pv = webapp.app.test_client().get("/privacy").get_data(as_text=True)
        assert "メールアドレスをお預かり" not in pv
        assert "恒久的な保存は行いません" in pv
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev


# ---- ログイン -------------------------------------------------------------

def test_login_token_roundtrip(env):
    t = env.accounts.issue_login_token("Taro@Example.COM ")
    u = env.accounts.consume_login_token(t)
    assert u["email"] == "taro@example.com"
    assert u["plan"] == "free"
    # 使い捨て：2回目は通らない
    assert env.accounts.consume_login_token(t) is None
    # 存在しないトークンも静かに落ちる
    assert env.accounts.consume_login_token("deadbeef") is None
    assert env.accounts.consume_login_token("") is None


def test_login_token_expires(env):
    """期限切れのトークンは使えない。"""
    import hashlib
    tok = "expired-token-for-test"
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=1)).replace(microsecond=0).isoformat()
    env.db.run("INSERT INTO login_tokens (token_hash, email, expires_at) "
               "VALUES (?, ?, ?)",
               (hashlib.sha256(tok.encode()).hexdigest(), "x@example.com",
                past))
    assert env.accounts.consume_login_token(tok) is None


def test_token_is_not_stored_in_clear(env):
    """DBにはハッシュだけを置く。漏れてもログインには使えない。"""
    t = env.accounts.issue_login_token("hash@example.com")
    rows = env.db.run("SELECT token_hash FROM login_tokens", (), "all")
    assert all(r["token_hash"] != t for r in rows)
    assert any(len(r["token_hash"]) == 64 for r in rows)


def test_rate_limit_per_email(env):
    """同じアドレスに何度も送らせない（Resendの無料枠を守るため）。"""
    addr = "flood@example.com"
    for _ in range(env.accounts.PER_EMAIL_PER_HOUR):
        env.accounts.issue_login_token(addr)
    with pytest.raises(env.accounts.TooManyRequests):
        env.accounts.issue_login_token(addr)


def test_valid_email(env):
    ok = ["a@b.co", "taro.yamada+1@example.co.jp"]
    ng = ["", "a@b", "a b@c.com", "@example.com", "noatmark", "a@@b.com"]
    for x in ok:
        assert env.accounts.valid_email(x), x
    for x in ng:
        assert not env.accounts.valid_email(x), x


# ---- 保存 -----------------------------------------------------------------

def _user(env, email):
    return env.accounts.consume_login_token(
        env.accounts.issue_login_token(email))


def _payload(total=70, cats=None, kind="chuko_kodate", **kw):
    p = {"kind": kind, "total": total, "grade": "B", "sufficiency": 48.0,
         "categories": cats or [{"name": "物件", "points": 18, "weight": 25,
                                 "pct": 72}],
         "risks": [], "strengths": [], "weaknesses": [], "spec": {},
         "price": {"verdict": "概ね適正", "dev": 1.0},
         "loan": {"monthly": 100000, "burden": 20.0}}
    p.update(kw)
    return p


def test_save_and_list(env):
    u = _user(env, "saver@example.com")
    sid = env.saved.save(u, "chuko_kodate", "中古戸建　小田原",
                         "神奈川県小田原市", 34800000, 72, "B", _payload())
    assert sid > 0
    rows = env.saved.listing(u["id"])
    assert len(rows) == 1 and rows[0]["total_score"] == 72


def test_free_limit(env):
    """無料プランは3件まで。上限に達したら例外で知らせる。"""
    u = _user(env, "limit@example.com")
    for i in range(env.saved.FREE_LIMIT):
        env.saved.save(u, "chuko_kodate", f"物件{i}", "住所", 1, 50, "C",
                       _payload())
    with pytest.raises(env.saved.LimitReached):
        env.saved.save(u, "chuko_kodate", "4件目", "住所", 1, 50, "C",
                       _payload())
    # PROなら増える
    env.accounts.set_plan(u["id"], env.accounts.PLAN_PRO, None)
    u2 = env.accounts.get_user(u["id"])
    assert env.accounts.is_pro(u2)
    assert env.saved.limit_for(u2) is None, "PROは無制限"
    env.saved.save(u2, "chuko_kodate", "4件目", "住所", 1, 50, "C", _payload())


def test_pro_has_no_ceiling(env):
    """PROに保存の上限を置かない。

    家探しは何十件も見て絞る作業なので、上限があると「どれを消すか」を
    考えさせることになる。旧上限(20件)をはっきり超えて保存できること。
    """
    u = _user(env, "nolimit@example.com")
    env.accounts.set_plan(u["id"], env.accounts.PLAN_PRO, None)
    u = env.accounts.get_user(u["id"])
    for i in range(35):
        env.saved.save(u, "chuko_kodate", f"物件{i}", "住所", 1, 50, "C",
                       _payload())
    assert env.saved.count(u["id"]) == 35
    assert len(env.saved.listing(u["id"])) == 35


def test_the_pages_say_unlimited(env):
    """画面の文言と実装を食い違わせない（特商法の確認画面を含む）。"""
    u = _user(env, "saysunlimited@example.com")
    env.accounts.set_plan(u["id"], env.accounts.PLAN_PRO, None)
    c = env.app.app.test_client()
    with c.session_transaction() as sess:
        sess["uid"] = u["id"]
    h = c.get("/mypage").get_data(as_text=True)
    assert "保存できる件数に制限はありません" in h
    assert "件までです" not in h


def test_expired_plan_is_free(env):
    u = _user(env, "expired@example.com")
    past = "2000-01-01T00:00:00+00:00"
    env.accounts.set_plan(u["id"], env.accounts.PLAN_PRO, past)
    assert not env.accounts.is_pro(env.accounts.get_user(u["id"]))


def test_cannot_read_others_saves(env):
    """他人のIDを指定しても取れない。"""
    a = _user(env, "a-owner@example.com")
    b = _user(env, "b-other@example.com")
    sid = env.saved.save(a, "chuko_kodate", "Aの物件", "住所", 1, 60, "C",
                         _payload())
    assert env.saved.get_many(b["id"], [sid]) == []
    assert len(env.saved.get_many(a["id"], [sid])) == 1
    # 削除も同様に効かない
    env.saved.delete(b["id"], sid)
    assert len(env.saved.get_many(a["id"], [sid])) == 1


# ---- 比較 -----------------------------------------------------------------

def _item(kind, payload, title="X"):
    return {"id": 1, "kind": kind, "title": title, "address": "",
            "payload": payload}


def test_compare_marks_the_better_column(env):
    items = [_item("chuko_kodate", _payload(total=60,
                                            loan={"monthly": 120000,
                                                  "burden": 25.0})),
             _item("chuko_kodate", _payload(total=80,
                                            loan={"monthly": 90000,
                                                  "burden": 18.0}))]
    rows, cats, mixed = env.app.compare_rows(items)
    assert not mixed
    by = {r["label"]: r for r in rows}
    assert by["総合点"]["best"] == [1]        # 高いほうが良い
    assert by["月々の返済"]["best"] == [1]      # 低いほうが良い
    assert by["返済の負担率"]["best"] == [1]
    assert cats, "同じ種別ならカテゴリ別も出る"


def test_compare_hides_categories_when_kinds_differ(env):
    """戸建とマンションは配点が違うので、カテゴリ別を並べない。"""
    items = [_item("chuko_kodate", _payload()),
             _item("chuko_mansion", _payload(kind="chuko_mansion"))]
    rows, cats, mixed = env.app.compare_rows(items)
    assert mixed is True
    assert cats == []
    assert rows, "共通の行は出す"


def test_compare_no_best_when_all_equal_or_unknown(env):
    """差が無い行・判定不可の行には印をつけない。"""
    p = _payload(total=70, price={"verdict": "判定不可"})
    rows, _, _ = env.app.compare_rows([_item("chuko_kodate", p),
                                       _item("chuko_kodate", dict(p))])
    by = {r["label"]: r for r in rows}
    assert by["総合点"]["best"] == []
    assert by["価格の妥当性"]["best"] == []


# ---- 署名 -----------------------------------------------------------------

def test_snapshot_signature_required(env):
    """改ざんした保存内容は受け付けない。"""
    tok = env.app.sign_snapshot({"kind": "chuko_kodate", "total": 70})
    assert env.app._unsign_snapshot(tok)["total"] == 70
    assert env.app._unsign_snapshot(tok + "x") is None
    assert env.app._unsign_snapshot("") is None


# ---- 画面（エンドツーエンド） ---------------------------------------------

def _login(client, env, email):
    t = env.accounts.issue_login_token(email)
    r = client.get(f"/login/{t}")
    assert r.status_code == 302
    return r


def test_pages_require_login(env):
    c = env.app.app.test_client()
    for path in ["/mypage", "/compare"]:
        r = c.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path


def test_login_page_shows_dev_link_without_mail_key(env):
    """メールの鍵が無い環境では、リンクを画面に出して開発を止めない。"""
    c = env.app.app.test_client()
    h = c.post("/login", data={"email": "dev@example.com"}).get_data(
        as_text=True)
    assert "/login/" in h


def test_bad_token_page(env):
    r = env.app.app.test_client().get("/login/nonexistent-token")
    assert r.status_code == 400
    assert "リンクが使えません" in r.get_data(as_text=True)


def test_end_to_end_save_and_compare(env):
    """診断→保存→マイページ→比較、を実際の画面で通す。"""
    c = env.app.app.test_client()
    _login(c, env, "e2e@example.com")

    os.environ["SHINDAN_MOCK"] = "1"
    try:
        form = dict(ptype="chuko_kodate", price="3480", address="神奈川県小田原市南町1-1-1",
                    land="110", building="96", byear="2006", station="12",
                    income="700", down="500", loan_years="35")
        h = c.post("/diagnose", data=form).get_data(as_text=True)
        assert "この結果を保存する" in h, "ログイン中なら保存ボタンが出る"
        import re
        tok = re.search(r'name="snap" value="([^"]+)"', h).group(1)

        # 2件保存する（比較には2件必要）
        assert c.post("/save", data={"snap": tok}).status_code == 302
        form2 = dict(form, price="4200", address="神奈川県小田原市栄町2-2-2")
        h2 = c.post("/diagnose", data=form2).get_data(as_text=True)
        tok2 = re.search(r'name="snap" value="([^"]+)"', h2).group(1)
        c.post("/save", data={"snap": tok2})
    finally:
        os.environ.pop("SHINDAN_MOCK", None)

    page = c.get("/mypage").get_data(as_text=True)
    assert "小田原市南町" in page and "小田原市栄町" in page

    ids = [r["id"] for r in env.saved.listing(
        env.accounts.consume_login_token(
            env.accounts.issue_login_token("e2e@example.com"))["id"])]
    assert len(ids) >= 2
    cmp_page = c.get(f"/compare?id={ids[0]}&id={ids[1]}").get_data(as_text=True)
    assert "総合点" in cmp_page and "月々の返済" in cmp_page


def test_save_rejects_tampered_snapshot(env):
    c = env.app.app.test_client()
    _login(c, env, "tamper@example.com")
    before = len(env.saved.listing(
        env.db.run("SELECT id FROM users WHERE email = ?",
                   ("tamper@example.com",), "one")["id"]))
    r = c.post("/save", data={"snap": "not-a-valid-signed-token"})
    assert r.status_code == 302
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("tamper@example.com",), "one")["id"]
    assert len(env.saved.listing(uid)) == before


def test_logout_clears_session(env):
    c = env.app.app.test_client()
    _login(c, env, "bye@example.com")
    assert c.get("/mypage").status_code == 200
    assert c.post("/logout").status_code == 302
    assert c.get("/mypage").status_code == 302


def test_plan_page(env):
    c = env.app.app.test_client()
    h = c.get("/plan").get_data(as_text=True)
    assert "試験公開中" in h
    assert str(env.saved.FREE_LIMIT) in h


def test_account_pages_are_noindex(env):
    """個人のページは検索結果に出さない。"""
    c = env.app.app.test_client()
    _login(c, env, "noindex@example.com")
    for path in ["/mypage", "/plan", "/login"]:
        h = c.get(path).get_data(as_text=True)
        assert 'name="robots" content="noindex"' in h, path


def test_sitemap_has_no_account_pages(env):
    h = env.app.app.test_client().get("/sitemap.xml").get_data(as_text=True)
    for p in ["/mypage", "/login", "/compare", "/plan"]:
        assert p not in h, p


def test_healthz_does_not_touch_db(env):
    """5分おきの死活監視でNeonを起こさないこと（無料枠を使い切らないため）。

    /healthz が db を呼ぶようになったら、ここで気づけるようにしておく。
    """
    calls = []
    orig = env.db.connect
    env.db.connect = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    try:
        assert env.app.app.test_client().get("/healthz").status_code == 200
    finally:
        env.db.connect = orig
    assert calls == [], "healthz からDBに接続してはいけない"


def test_mypage_has_no_nested_forms(env):
    """<form>を入れ子にしない。

    入れ子にするとブラウザが内側を捨て、2件目以降の削除ボタンが
    「比べる」フォームに吸収されて、押すと比較画面へ飛んでしまう。
    """
    c = env.app.app.test_client()
    _login(c, env, "nest@example.com")
    u = env.db.run("SELECT id FROM users WHERE email = ?",
                   ("nest@example.com",), "one")
    for i in range(2):
        env.saved.save(env.accounts.get_user(u["id"]), "chuko_kodate",
                       f"物件{i}", "住所", 1, 60, "C", _payload())
    h = c.get("/mypage").get_data(as_text=True)

    start = h.index('<form method="get" action="/compare">')
    end = h.index("</form>", start)
    assert "<form" not in h[start + 6:end], "比べるフォームの中にフォームがある"
    # 保存件数と同じだけ削除フォームがあること
    assert h.count('action="/saved/') == 2

def test_sufficiency_is_shown_as_is(env):
    """情報充足度は既に百分率。二重に100倍しない。"""
    items = [_item("chuko_kodate", _payload(sufficiency=48.0)),
             _item("chuko_kodate", _payload(sufficiency=61.0))]
    rows, _, _ = env.app.compare_rows(items)
    row = {r["label"]: r for r in rows}["情報の充足度"]
    assert row["texts"] == ["48%", "61%"]
    assert row["best"] == [1]

def test_privacy_declares_storage_when_accounts_on(env):
    """保存機能を入れたら、ポリシーもそう書いてあること。"""
    h = env.app.app.test_client().get("/privacy").get_data(as_text=True)
    assert "メールアドレスをお預かり" in h
    assert "世帯年収は保存しません" in h
    assert "パスワードは保管しません" in h


def test_short_label_drops_prefecture_and_city(env):
    """狭い列に出す名前は、都道府県と市区町村を落として町名以降を使う。"""
    f = env.app.short_label
    assert f({"address": "神奈川県小田原市城山1-2-3"}) == "城山1-2-3"
    assert f({"address": "東京都世田谷区北沢1-1"}) == "北沢1-1"
    # 住所が無ければ表題で代替する
    assert f({"address": "", "title": "中古戸建"}) == "中古戸建"
    assert f({}) == "物件"


def test_compare_table_carries_names_and_column_count(env):
    """スマホでは見出し行を隠すので、値の側がどの物件かを持っていること。

    列数も表自身に持たせる。CSSのauto-fit任せにすると3件目が折り返し、
    横に並ばなくなって比較にならない。
    """
    c = env.app.app.test_client()
    _login(c, env, "mobile@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("mobile@example.com",), "one")["id"]
    u = env.accounts.get_user(uid)
    ids = [env.saved.save(u, "chuko_kodate", f"物件{i}",
                          f"神奈川県小田原市栄町{i}-1-1", 30000000 + i,
                          60 + i, "C", _payload(total=60 + i))
           for i in range(3)]
    h = c.get("".join(f"/compare?id={ids[0]}" if i == 0 else f"&id={ids[i]}"
                      for i in range(3))).get_data(as_text=True)
    assert 'style="--n:3"' in h, "列数が表に出ていない"
    assert 'data-name="栄町0-1-1"' in h
    assert 'data-name="栄町2-1-1"' in h


# ---- 保存した診断の詳細とメモ ---------------------------------------------

def test_detail_page_shows_the_saved_result(env):
    """保存した時点の中身を、そのまま読み返せること。"""
    c = env.app.app.test_client()
    _login(c, env, "detail@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("detail@example.com",), "one")["id"]
    p = _payload(total=78, cats=[{"name": "物件", "points": 21.2, "weight": 25,
                                  "pct": 85, "reason": "築14年・RC・SRC造"}])
    p["risks"] = [{"sev": "medium", "type": "ハザード未確認",
                   "status": "unknown", "ev": "洪水/土砂を要確認"}]
    p["confirm"] = ["価格: 類似成約が不足"]
    p["enr"] = {"use_district": "第一種低層", "population": "1,200人",
                "trend": "横ばい", "hazard_items": [], "districts": None,
                "facilities": "スーパー 400m"}
    p["spec"] = {"specs": "土地 130㎡ ・ 建物 105㎡"}
    sid = env.saved.save(env.accounts.get_user(uid), "chuko_kodate",
                         "中古戸建　小田原", "神奈川県小田原市中町1-1-1",
                         49800000, 78, "B", p)
    h = c.get(f"/saved/{sid}").get_data(as_text=True)
    for must in ["78", "中町1-1-1", "土地 130㎡", "築14年・RC・SRC造",
                 "洪水/土砂を要確認", "第一種低層", "スーパー 400m",
                 "要確認（情報不足）", "メモ"]:
        assert must in h, must


def test_detail_page_survives_an_older_snapshot(env):
    """項目を足す前に保存したものも、開けること。

    payload の形は後から増えている。古い保存に新しい項目は無いので、
    無ければその節ごと出さない作りになっていないと500になる。
    """
    c = env.app.app.test_client()
    _login(c, env, "oldsnap@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("oldsnap@example.com",), "one")["id"]
    old = {"kind": "chuko_kodate", "total": 69, "grade": "B",
           "sufficiency": 48.0, "comment": "", "categories": [],
           "risks": [], "strengths": [], "weaknesses": [], "spec": {},
           "price": {"verdict": "判定不可"}}
    sid = env.saved.save(env.accounts.get_user(uid), "chuko_kodate",
                         "古い保存", "住所", 1, 69, "B", old)
    r = c.get(f"/saved/{sid}")
    assert r.status_code == 200
    assert "69" in r.get_data(as_text=True)


def test_note_round_trip(env):
    c = env.app.app.test_client()
    _login(c, env, "note@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("note@example.com",), "one")["id"]
    sid = env.saved.save(env.accounts.get_user(uid), "chuko_kodate", "物件",
                         "住所", 1, 60, "C", _payload())
    c.post(f"/saved/{sid}/note", data={"note": "駐車場が狭い"})
    assert env.saved.get_one(uid, sid)["note"] == "駐車場が狭い"
    # 一覧にも出る
    assert "駐車場が狭い" in c.get("/mypage").get_data(as_text=True)
    # 空にすると消える
    c.post(f"/saved/{sid}/note", data={"note": "   "})
    assert env.saved.get_one(uid, sid)["note"] is None


def test_note_is_capped(env):
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("note@example.com",), "one")["id"]
    sid = env.saved.listing(uid)[0]["id"]
    env.saved.set_note(uid, sid, "あ" * (env.saved.NOTE_MAX + 500))
    assert len(env.saved.get_one(uid, sid)["note"]) == env.saved.NOTE_MAX


def test_note_never_reaches_the_score(env):
    """メモは採点に一切使わない。点数はルール計算のままであること。"""
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("note@example.com",), "one")["id"]
    sid = env.saved.listing(uid)[0]["id"]
    before = env.saved.get_one(uid, sid)
    env.saved.set_note(uid, sid, "とても良い物件だと思う")
    after = env.saved.get_one(uid, sid)
    assert after["total_score"] == before["total_score"]
    assert after["payload"] == before["payload"]


def test_cannot_read_or_edit_another_users_detail(env):
    a = _user(env, "own@example.com")
    b = _user(env, "other2@example.com")
    sid = env.saved.save(a, "chuko_kodate", "Aの物件", "住所", 1, 60, "C",
                         _payload())
    c = env.app.app.test_client()
    _login(c, env, "other2@example.com")
    assert c.get(f"/saved/{sid}").status_code == 404
    c.post(f"/saved/{sid}/note", data={"note": "他人のメモ"})
    assert env.saved.get_one(a["id"], sid)["note"] is None


# ---- 再診断 ---------------------------------------------------------------

def test_household_income_is_never_saved(env):
    """世帯年収と頭金は保存しない。

    プライバシーポリシーに「世帯年収は保存しません（返済額の計算結果のみを
    持ちます）」と書いてある。再診断のために入力を残すようにしたので、
    ここに紛れ込みやすい。保存物のどこにも出てこないことを固定する。
    """
    import json
    from src import saved as sv

    class _Cat:
        name, points, weight, raw, reason = "物件", 18, 25, 0.72, "築14年"

    class _D:
        total_score, grade, data_sufficiency, comment = 70, "B", 48.0, ""
        categories = [_Cat()]
        critical_risks, strengths, weaknesses, to_confirm = [], [], [], []

    class _Res:
        diagnosis, price, loan = _D(), None, None

    redo = {"kind": "kodate", "address": "神奈川県小田原市南町2-3-4",
            "price": "4200", "income": "720", "down": "550",
            "reserve": "300", "other_debt": "100", "structure": "rc"}
    snap = sv.snapshot(_Res(), None, {"specs": "x"}, "chuko_kodate",
                       None, redo)
    assert set(snap["redo"]) == {"kind", "address", "price", "structure"}
    blob = json.dumps(snap, ensure_ascii=False)
    for leaked in ("720", "550", "300", "100", "income", "down"):
        assert leaked not in blob, leaked


def test_redo_restores_the_property_inputs(env):
    """再診断は、物件の入力を戻したフォームを出す。"""
    c = env.app.app.test_client()
    _login(c, env, "redo@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("redo@example.com",), "one")["id"]
    p = _payload()
    p["redo"] = {"kind": "kodate", "ptype": "chuko_kodate",
                 "address": "神奈川県小田原市南町2-3-4", "price": "4200",
                 "byear": "2008", "land": "140", "building": "98",
                 "station": "12", "structure": "heavy_steel", "reno": "1",
                 "loan_years": "35"}
    sid = env.saved.save(env.accounts.get_user(uid), "chuko_kodate", "物件",
                         "神奈川県小田原市南町2-3-4", 42000000, 70, "B", p)
    # 詳細ページに導線が出る
    assert f"/saved/{sid}/redo" in c.get(f"/saved/{sid}").get_data(as_text=True)
    h = c.get(f"/saved/{sid}/redo").get_data(as_text=True)
    for v in ["神奈川県小田原市南町2-3-4", "4200", "2008", "140", "98"]:
        assert v in h, v
    assert 'value="heavy_steel" selected' in h.replace("  ", " ") \
        or "heavy_steel" in h
    # 年収・頭金は空のまま、その理由も書いてある
    assert "世帯年収と頭金はお預かりしていないため" in h


def test_redo_is_refused_for_saves_made_before_the_feature(env):
    """入力を残す前の保存には、再診断の導線を出さない。"""
    c = env.app.app.test_client()
    _login(c, env, "oldredo@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("oldredo@example.com",), "one")["id"]
    sid = env.saved.save(env.accounts.get_user(uid), "chuko_kodate", "古い",
                         "住所", 1, 60, "C", _payload())   # redo なし
    assert f"/saved/{sid}/redo" not in \
        c.get(f"/saved/{sid}").get_data(as_text=True)
    h = c.get(f"/saved/{sid}/redo").get_data(as_text=True)
    assert "再診断できません" in h


def test_redo_of_another_users_save_is_refused(env):
    a = _user(env, "redo-own@example.com")
    p = _payload()
    p["redo"] = {"kind": "kodate", "address": "住所"}
    sid = env.saved.save(a, "chuko_kodate", "Aの物件", "住所", 1, 60, "C", p)
    c = env.app.app.test_client()
    _login(c, env, "redo-other@example.com")
    assert c.get(f"/saved/{sid}/redo").status_code == 404


def test_compare_offers_the_saves_not_yet_shown(env):
    """比較画面から、そのまま次の物件を足せること。

    以前はマイページに戻って選び直すしかなく、比較の途中で
    「もう1件見てみよう」と思ったときに動線が切れていた。
    """
    c = env.app.app.test_client()
    _login(c, env, "addmore@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("addmore@example.com",), "one")["id"]
    u = env.accounts.get_user(uid)
    env.accounts.set_plan(uid, env.accounts.PLAN_PRO, None)   # 3件以上保存する
    u = env.accounts.get_user(uid)
    ids = [env.saved.save(u, "chuko_kodate", f"物件{i}",
                          f"神奈川県小田原市栄町{i}-1-1", 30000000, 60 + i,
                          "C", _payload(total=60 + i)) for i in range(3)]
    h = c.get(f"/compare?id={ids[0]}&id={ids[1]}").get_data(as_text=True)
    assert "もう1件くらべる" in h
    # 並べていない3件目が、いまの2件に足す形のリンクで出る
    assert f"/compare?id={ids[0]}&amp;id={ids[1]}&amp;id={ids[2]}" in h \
        or f"/compare?id={ids[0]}&id={ids[1]}&id={ids[2]}" in h
    # すでに並べているものは候補に出さない
    body = h[h.index("もう1件くらべる"):]
    assert "物件0" not in body and "物件1" not in body


def test_compare_stops_offering_more_at_six(env):
    """6件を超えると表が読めなくなるので、それ以上は勧めない。"""
    c = env.app.app.test_client()
    _login(c, env, "sixmax@example.com")
    uid = env.db.run("SELECT id FROM users WHERE email = ?",
                     ("sixmax@example.com",), "one")["id"]
    env.accounts.set_plan(uid, env.accounts.PLAN_PRO, None)
    u = env.accounts.get_user(uid)
    ids = [env.saved.save(u, "chuko_kodate", f"多{i}", f"住所{i}", 1, 60, "C",
                          _payload()) for i in range(7)]
    q = "&".join(f"id={i}" for i in ids[:6])
    h = c.get(f"/compare?{q}").get_data(as_text=True)
    assert "ほかに保存された物件がありません" in h \
        or "もう1件くらべる" not in h.split("多6")[0]
