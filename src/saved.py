# -*- coding: utf-8 -*-
"""保存した診断の出し入れと、比較用の整形。

■何を保存するか
診断結果そのもの（点数・カテゴリ別・価格判定・返済）を要約したJSONを持つ。
あとから再計算はしない。公的データも配点も時間とともに変わるので、
「そのとき何と出たか」を残すほうが比較として正しい。

■保存件数の上限
無料プランは3件まで。無料でも複数物件を並べられるようにするため
（比較したいのは2〜3件目が出てきたときなので、1件では意味がない）。

PROは無制限。家探しは何十件も見て絞る作業なので、上限があると
「どれを消すか」を考えさせることになる。それは診断とは関係のない手間で、
しかも消した物件こそ後から見返したくなる。件数の歯止めは、保存ではなく
1日あたりの診断回数（app.py の RATE_LIMIT_PER_DAY）で掛けている。

■メモ
点数に出ない条件（駐車場の広さ、隣家との距離、内見したときの印象）を
書き留めるための欄。採点には一切使わない。ルール計算で出した点数に
主観を混ぜないため、保存するだけで読み返すのは人。
"""
from __future__ import annotations

import json

from . import db
from . import accounts

FREE_LIMIT = 3
PRO_LIMIT = None    # PROは無制限。None は「上限なし」の意味。
NOTE_MAX = 1000     # メモの上限（画面の但し書きと合わせる）

# 再診断のために入力を残すが、家計に関わるものは保存しない。
# プライバシーポリシーで「世帯年収は保存しません」と明言しているので、
# ここを破ると記載と実装が食い違う。定数にして保存の直前で落とす。
NEVER_SAVE = ("income", "down", "reserve", "other_debt")


class LimitReached(Exception):
    """保存件数の上限。呼び出し側でPROへの導線を出す。"""


def limit_for(user: dict | None) -> int | None:
    """保存できる件数。None は無制限。"""
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
        if lim is not None:
            n = exec_(
                "SELECT COUNT(*) AS c FROM saved_diagnoses WHERE user_id = ?",
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
        "SELECT id, kind, title, address, price, total_score, grade, "
        "       created_at, note "
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


def get_one(user_id, sid):
    """1件を中身つきで返す。他人のものは user_id で弾かれる。"""
    row = db.run("SELECT * FROM saved_diagnoses WHERE user_id = ? AND id = ?",
                 (user_id, int(sid)), "one")
    if not row:
        return None
    try:
        row["payload"] = json.loads(row["payload"])
    except Exception:
        row["payload"] = {}
    return row


def set_note(user_id, sid, note: str) -> None:
    """メモを書き換える。空文字なら消す。"""
    note = (note or "").strip()[:NOTE_MAX] or None
    db.run("UPDATE saved_diagnoses SET note = ? WHERE user_id = ? AND id = ?",
           (note, user_id, int(sid)))


def delete(user_id, sid) -> None:
    db.run("DELETE FROM saved_diagnoses WHERE user_id = ? AND id = ?",
           (user_id, int(sid)))


# ---- 比較用の整形 ---------------------------------------------------------

def snapshot(res, subject, sctx, kind: str, enr=None, redo=None) -> dict:
    """診断結果から、保存と読み返しに要る分を抜き出す。

    画面が持っている値をまるごと写すのではなく、あとから見て意味のある
    ものに絞る。全部持つと0.5GBの無料枠を圧迫するし、形も変えにくくなる。
    逆に絞りすぎると詳細を見返せないので、点数の根拠にあたるもの
    （カテゴリ別の理由・災害リスクの根拠・要確認）は残す。

    enr は結果ページが組み立てた立地・防災・人口の表示用の辞書。
    同じ整形をここで書き直すと必ず食い違うので、受け取って使う。

    redo は再診断のときにフォームへ戻す入力。キーは入力フォームの
    name にそろえてある。NEVER_SAVE の項目はここで落とす。
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
                        "weight": c.weight, "pct": int(round(c.raw * 100)),
                        "reason": c.reason}
                       for c in d.categories],
        "risks": [{"sev": r.severity, "type": r.type, "status": r.status,
                   "ev": r.evidence}
                  for r in d.critical_risks],
        "strengths": list(d.strengths or [])[:8],
        "weaknesses": list(d.weaknesses or [])[:8],
        "confirm": list(getattr(d, "to_confirm", None) or [])[:8],
        "spec": dict(sctx or {}),
        "enr": dict(enr) if enr else None,
    }
    if redo:
        # 再診断のときにフォームへ戻す値。家計の入力は落とす。
        out["redo"] = {k: v for k, v in redo.items()
                       if k not in NEVER_SAVE and v not in (None, "")}
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
