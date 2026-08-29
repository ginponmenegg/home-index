# -*- coding: utf-8 -*-
"""独自ドメインを正のURLに寄せる転送。ネットワーク不要。"""
import os
import sys
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _app_with(host):
    """CANONICAL_HOST を設定した状態の app を読み直して返す。"""
    prev = os.environ.get("CANONICAL_HOST")
    os.environ["CANONICAL_HOST"] = host
    import app as webapp
    importlib.reload(webapp)
    return webapp, prev


def _restore(webapp, prev):
    if prev is None:
        os.environ["CANONICAL_HOST"] = ""
    else:
        os.environ["CANONICAL_HOST"] = prev
    importlib.reload(webapp)


def test_redirects_other_hosts_to_the_canonical_one():
    webapp, prev = _app_with("home-index.example")
    try:
        c = webapp.app.test_client()
        r = c.get("/buy", headers={"Host": "home-index-h2hf.onrender.com"})
        assert r.status_code == 301
        assert r.headers["Location"] == "https://home-index.example/buy"
        # クエリは落とさない
        r = c.get("/compare?id=1&id=2",
                  headers={"Host": "home-index-h2hf.onrender.com"})
        assert r.headers["Location"] == \
            "https://home-index.example/compare?id=1&id=2"
        # 正のホストならそのまま通す
        assert c.get("/buy", headers={"Host": "home-index.example"}
                     ).status_code == 200
    finally:
        _restore(webapp, prev)


def test_healthz_is_not_redirected():
    """UptimeRobot は onrender.com を叩いてサービスを起こしている。

    ここを転送すると、監視がサービス本体を見なくなる。
    """
    webapp, prev = _app_with("home-index.example")
    try:
        r = webapp.app.test_client().get(
            "/healthz", headers={"Host": "home-index-h2hf.onrender.com"})
        assert r.status_code == 200
    finally:
        _restore(webapp, prev)


def test_post_is_not_redirected():
    """POSTを301で転送するとGETに変わり、入力内容が消える実装がある。"""
    webapp, prev = _app_with("home-index.example")
    try:
        r = webapp.app.test_client().post(
            "/parse", data={"listing": "価格3000万円"},
            headers={"Host": "home-index-h2hf.onrender.com"})
        assert r.status_code == 200
    finally:
        _restore(webapp, prev)


def test_no_redirect_when_unset():
    """未設定なら何もしない（いまの本番とローカルの挙動を変えない）。"""
    prev = os.environ.get("CANONICAL_HOST")
    os.environ["CANONICAL_HOST"] = ""
    import app as webapp
    importlib.reload(webapp)
    try:
        assert webapp.app.test_client().get(
            "/buy", headers={"Host": "anything.example"}).status_code == 200
    finally:
        if prev is not None:
            os.environ["CANONICAL_HOST"] = prev
        importlib.reload(webapp)
