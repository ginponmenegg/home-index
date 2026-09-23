# -*- coding: utf-8 -*-
"""本番は PostgreSQL、テストは sqlite。この差で一度落ちた。

`/metrics/data.csv` が本番で 500 になった。原因は

    ORDER BY day DESC, rowid DESC

の `rowid`。sqlite の疑似列なので、postgres には無い。手元のテストは
923件すべて通っていた。**走らせて気づくことができない種類の間違い**で、
気づけるのは本番で踏んだときだけだった。

だから、走らせるのではなく読む。SQL文の中に sqlite でしか通らない
書き方が入っていないかを、文字として見張る。
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = [os.path.join(ROOT, "app.py")] + [
    os.path.join(ROOT, "src", f)
    for f in sorted(os.listdir(os.path.join(ROOT, "src")))
    if f.endswith(".py")
]

# sqlite でしか通らない書き方。見つけたら、両方で通る書き方に直す。
BANNED = {
    "rowid": "sqlite の疑似列。postgres には無い",
    "AUTOINCREMENT": "postgres は BIGSERIAL。db._PK で書き分ける",
    "INSERT OR REPLACE": "postgres は ON CONFLICT ... DO UPDATE",
    "INSERT OR IGNORE": "postgres は ON CONFLICT ... DO NOTHING",
    "IFNULL": "COALESCE なら両方で通る",
    "GROUP_CONCAT": "postgres は STRING_AGG",
    "PRAGMA ": "sqlite 専用",
}

# db.py の方言表だけは、書き分けるために両方の綴りを持つ。
ALLOWED = {("db.py", "AUTOINCREMENT")}


def _sql_text(path):
    """SQLらしい文字列だけを拾う。

    Python の strftime とSQLの strftime を取り違えないように、
    SELECT / INSERT / UPDATE / DELETE / CREATE / ALTER / ORDER BY の
    いずれかを含む行だけを見る。
    """
    out = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|"
                         r"ORDER BY|GROUP BY|PRAGMA)\b", line):
                out.append((i, line))
    return out


@pytest.mark.parametrize("path", SRC, ids=[os.path.basename(p) for p in SRC])
def test_no_sqlite_only_sql(path):
    name = os.path.basename(path)
    bad = []
    for lineno, line in _sql_text(path):
        for token, why in BANNED.items():
            if token.lower() in line.lower() and (name, token) not in ALLOWED:
                bad.append(f"{name}:{lineno} {token} — {why}")
    assert not bad, "本番(postgres)で落ちる書き方:\n" + "\n".join(bad)


def test_the_export_does_not_order_by_rowid():
    """踏んだ場所そのものを名指しで見張る。

    上の走査は行単位なので、SQLを複数行に割ると抜けることがある。
    実際に落ちた一箇所だけは、明示的に見る。

    説明の文章には rowid と書いてある（なぜ使えないかを残すため）。
    見るのはSQLのほうだけ。
    """
    import inspect

    from src import observations
    body = inspect.getsource(observations.rows)
    sql = body.split('"""', 2)[-1]          # docstring を落とす
    assert "rowid" not in sql
    assert observations.COLUMNS


def test_the_banned_list_would_have_caught_it():
    """この見張りが、実際に落ちた書き方を捕まえられること。

    見張りそのものが効いているかを確かめないと、空振りしていても
    緑のままになる。
    """
    line = "        \" ORDER BY day DESC, rowid DESC LIMIT ?\", (int(limit),)"
    hit = [t for t in BANNED if t.lower() in line.lower()]
    assert hit == ["rowid"]


def test_the_export_orders_by_something_that_exists():
    """並び順に使う列が、実際にその表にあること。

    主キーが無い表なので、並べ替えに使えるのは COLUMNS の中身だけ。
    """
    from src import observations
    q = observations.rows.__doc__
    assert q, "なぜこの並びなのかを残すこと"
    import inspect
    body = inspect.getsource(observations.rows)
    order = body.split("ORDER BY", 1)[1]
    used = set(re.findall(r"[a-z_]+", order.split("LIMIT")[0]))
    used -= {"day", "desc", "coalesce", "int", "limit", "all", "or"}
    for col in used:
        assert col in observations.COLUMNS, f"{col} は observations に無い"
