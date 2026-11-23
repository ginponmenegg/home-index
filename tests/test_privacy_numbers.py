# -*- coding: utf-8 -*-
"""プライバシーポリシーの利用目的の番号が、重ならず順に並んでいること。

アカウント機能の有無で目的が1つ増減するので、番号を書き足すたびに
ずれやすい（2026-11-23、(4) が2回出ていた）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_purposes_are_numbered_once_each_in_order():
    import app as webapp
    body = webapp._PRIVACY_BODY
    part = body[body.index("2. 利用目的"):body.index("3. 保存・安全管理")]
    nums = [int(n) for n in re.findall(r"\((\d+)\)", part)]
    assert nums == list(range(1, len(nums) + 1)), nums
