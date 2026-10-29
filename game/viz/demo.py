"""生成示例图，用来验证整条纹理管线（不需要跑游戏）。

    cd game && python -m viz.demo

输出到 game/out/ 下。
"""

from __future__ import annotations

from pathlib import Path

from . import report

OUT = Path(__file__).resolve().parent.parent / "out"

BATTLE = {
    "enemy": "守望者 RM-07",
    "result": "win",
    "scene": "地下大厅",
    "turns": 6,
    "player_max_hp": 30,
    "enemy_max_hp": 80,
    "hp_series": [30, 30, 23, 23, 16, 16, 9, 12, 5, 8],
    "enemy_series": [80, 62, 62, 44, 44, 26, 26, 8, 8, 0],
    "cards": {"挥砍": 9, "破甲重砍": 6, "架住": 4, "战地急救": 3, "突刺": 2, "排险": 1},
    "damage_dealt": 80,
    "damage_taken": 26,
    "blocked": 21,
    "healed": 16,
    "time": "2026-10-19 17:02",
}

SHEET = {
    "name": "柯岚",
    "hp": 23,
    "max_hp": 30,
    "attributes": {"力量": 13, "敏捷": 14, "体质": 14, "智力": 12, "感知": 13, "魅力": 10},
    "skills": {"近战攻击": 6, "活力": 5, "远程攻击": 4, "侦察": 4, "手上功夫": 4,
               "恐吓": 3, "调查": 3, "历史": 2, "奥术施法": 2},
    "techniques": ["猛力攻击", "火焰箭"],
    "items": [("压缩口粮", 2), ("废铁", 5), ("急救包", 1), ("测绘员手记", 1), ("锈迹门禁卡", 1)],
    "cards": 9,
    "locales": 7,
}


def main() -> int:
    # 没装 numpy / Pillow 时 report 是 None，直接调会抛
    # AttributeError: 'NoneType' object has no attribute 'battle_report'。
    if report is None:
        print("  出图需要 numpy 和 Pillow：")
        print("    pip install numpy pillow")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    a = report.battle_report(BATTLE, OUT / "demo_battle.png")
    b = report.character_sheet(SHEET, OUT / "demo_sheet.png")
    print("已生成：")
    print(" ", a)
    print(" ", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
