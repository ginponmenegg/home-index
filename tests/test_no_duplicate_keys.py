# -*- coding: utf-8 -*-
"""辞書のキーが二度書かれていないか。

`pro_scoring.AGENT_QUESTIONS` に "termite" が2回入っていた。Pythonは
後勝ちで黙って通すので、動作は壊れない。壊れないまま、**書いたはずの
質問が1つ消えている**という状態になる。気づく手立てが無いので、
構文木で見る。
"""
import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dup_keys(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        for k in sorted({k for k in keys if keys.count(k) > 1}):
            out.append(f"{os.path.basename(path)}:{node.lineno} 「{k}」")
    return out


def test_no_dict_literal_repeats_a_key():
    bad = []
    for path in (sorted(glob.glob(os.path.join(ROOT, "src", "*.py")))
                 + [os.path.join(ROOT, "app.py")]):
        bad += _dup_keys(path)
    assert not bad, "同じキーが二度書かれている: " + " / ".join(bad)


def test_the_check_would_have_caught_it():
    """見張りが空振りしていないこと。"""
    import tempfile
    src = 'X = {"a": 1, "b": 2, "a": 3}\n'
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        assert _dup_keys(path), "重複を見つけられていない"
    finally:
        os.unlink(path)
