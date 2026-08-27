# -*- coding: utf-8 -*-
"""スコアの重み・ルールの設定（config.json で上書き可能）。

プロジェクト直下の config.json があればマージして使う。無ければ既定値。
コードを触らずに重み調整ができるようにするための層（指示書 第11章）。
"""
from __future__ import annotations
import os
import json

DEFAULTS = {
    # 100点スコアのカテゴリ配点（合計100）
    "category_weights": {"物件": 25, "立地": 20, "価格": 20,
                         "リスク": 15, "資金": 10, "資産性": 10},
    # 中古の類似度重み（合計1.0）
    "sim_weights": {"location": 0.30, "land_area": 0.20, "building_area": 0.15,
                    "build_year": 0.15, "recency": 0.10, "city_planning": 0.05,
                    "structure": 0.05},
    # 新築の類似度重み（築年を効かせない）
    "newbuild_sim_weights": {"location": 0.40, "land_area": 0.22,
                             "building_area": 0.18, "build_year": 0.0,
                             "recency": 0.13, "city_planning": 0.04,
                             "structure": 0.03},
    # マンションの配点。②管理（管理費・積立金・修繕履歴）は次段で追加するため、
    # その分を価格・資産性に寄せてある。管理を入れるときは価格35→30・資産性25→20。
    "mansion_category_weights": {"価格": 35, "資産性": 25, "立地": 15,
                                 "リスク": 15, "資金": 10},
    # マンションの類似度重み（合計1.0）。㎡単価で見るので面積の重みは軽い。
    "mansion_sim_weights": {"location": 0.35, "build_year": 0.30,
                            "recency": 0.20, "area": 0.15},
    "neighbor_radius_m": 2000,   # 類似物件の近接半径(m)
    "k_nearest": 6,              # 価格算出に使う最類似件数
    "max_year_gap": 25,          # 築年が離れすぎた事例を除外(年)
    "newbuild_max_age": 1,       # 新築相当＝成約年−築年 がこの値以下
    "loan_rate": 0.0125,         # 既定金利
    "loan_years": 35,            # 既定返済年数
}


def load():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config.json")
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                cfg[k] = v
        except Exception:
            pass
    return cfg


CONFIG = load()
