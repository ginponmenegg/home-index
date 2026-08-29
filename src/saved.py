# -*- coding: utf-8 -*-
"""保存した診断の出し入れと、比較用の整形。

■何を保存するか
診断結果そのもの（点数・カテゴリ別・価格判定・返済）を要約したJSONを持つ。
あとから再計算はしない。公的データも配点も時間とともに変わるので、
「そのとき何と出たか」を残すほうが比較として正しい。

■保存件数の上限
無料プランは3件まで、PROは20件まで。無料でも複数物件を並べられるように
（比較したいのは2〜3件目が出てきたときなので、1件では意味がない）。
"""
from __future__ import annotations

import json

from . import db
from . import accounts

FREE_LIMIT = 3
PRO_LIMIT = 20


class LimitReached(Exception):
    """保存件数の上限。呼び出し側でPROへの導線を出す。"""


def limit_for(user: dict | None) -> int:
    return PRO_LIMIT if accounts.is_pro(user) else FREE_LIMIT


def count(user_id) -> int:
    row = db.run("SELECT COUNT(*) AS c FROM saved_diagnoses WHERE user_id = ?",
                 (user_id,), "one")
    return int(row["c"]) if row else 0


def save(user: dict, kind: str, title: str, address: str, price,
         total_score: int, grade: str, payload: dict) -> int:
    """1件保存してIDを返す。上限に達していれば LimitReached。"""
    lim = limit_for(user)
    now = db.now()

    def _go(exec_):
        n = exec_("SELECT COUNT(*) AS c FROM saved_diagnoses WHERE user_id = ?",
                  (user["id"],)).fetchone()
        if int(dict(n)["c"]) >= lim:
            raise LimitReached(str(lim))
        row = exec_(
            "INSERT INTO saved_diagnoses "
            "(user_id, kind, title, address, price, total_score, grade, "
            " payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING id",
            (user["id"], kind, title[:120], (address or "")[:200], price,
             total_score, grade,
             json.dumps(payload, ensure_ascii=False), now)).fetchone()
        return int(dict(row)["id"])

    return db.run_many(_go)


def listing(user_id) -> list[dict]:
    """一覧（payloadは読まない・軽い列だけ）。新しい順。"""
    return db.run(
        "SELECT id, kind, title, address, price, total_score, grade, created_at "
        "FROM saved_diagnoses WHERE user_id = ? ORDER BY id DESC",
        (user_id,), "all") or []


def get_many(user_id, ids: list[int]) -> list[dict]:
    """比較のために複数件まとめて読む。他人のIDは user_id で弾かれる。"""
    ids = [int(i) for i in ids][:6]
    if not ids:
        return []
    marks = ",".join(["?"] * len(ids))
    rows = db.run(
        f"SELECT * FROM saved_diagnoses WHERE user_id = ? AND id IN ({marks})",
        tuple([user_id] + ids), "all") or []
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload"])
        except Exception:
            r["payload"] = {}
    order = {v: i for i, v in enumerate(ids)}
    rows.sort(key=lambda r: order.get(int(r["id"]), 99))
    return rows


def delete(user_id, sid) -> None:
    db.run("DELETE FROM saved_diagnoses WHERE user_id = ? AND id = ?",
           (user_id, int(sid)))


# ---- 比較用の整形 ---------------------------------------------------------

def snapshot(res, subject, sctx, kind: str) -> dict:
    """診断結果から、保存・比較に必要な分だけ抜き出す。

    画面の描画に使っている値をそのまま持つのではなく、比較表に出す項目に
    絞る。全部持つと0.5GBの無料枠を圧迫するし、後で形を変えにくくなる。
    """
    d = res.diagnosis
    p = getattr(res, "price", None)
    L = getattr(res, "loan", None)
    out = {
        "kind": kind,
        "total": d.total_score,
        "grade": d.grade,
        "sufficiency": d.data_sufficiency,
        "comment": d.comment,
        "categories": [{"name": c.name, "points": c.points,
                        "weight": c.weight, "pct": int(round(c.raw * 100))}
                       for c in d.categories],
        "risks": [{"sev": r.severity, "type": r.type, "status": r.status}
                  for r in d.critical_risks],
        "strengths": list(d.strengths or [])[:4],
        "weaknesses": list(d.weaknesses or [])[:4],
        "spec": dict(sctx or {}),
    }
    if p and p.verdict != "判定不可":
        out["price"] = {"verdict": p.verdict, "dev": p.deviation_pct,
                        "mid": p.estimate_mid, "count": p.comparable_count,
                        "conf": p.confidence}
    else:
        out["price"] = {"verdict": "判定不可"}
    if L is not None:
        extra = getattr(L, "monthly_extra", 0) or 0
        out["loan"] = {"monthly": L.monthly_payment + extra,
                       "burden": L.burden_ratio,
                       "principal": L.principal}
    return out
