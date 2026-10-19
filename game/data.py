"""《灰烬信标》数据表。

这一层是纯数据，全部喂给 allin-engine 的数据层的 CampaignLibrary：
    - ITEMS     -> allin_engine.systems.Inventory
    - ENEMIES   -> allin_engine.systems.combat.Enemy / EnemyLibrary
    - DECK      -> allin_engine.systems.combat.Card / CardDeck
    - PUZZLES   -> allin_engine.systems.PuzzleSystem
    - START_*   -> allin_engine.trpg.Character 的开局配点

叙事层（dse-core）的 YAML 只通过「事件名」引用这里的 ID，
两边靠 bridge.py 对齐。
"""

from allin_engine.systems import InventoryItem
from allin_engine.systems.combat import Card, Enemy, Intent

# ---------------------------------------------------------------- 物品

# item_id -> InventoryItem 构造参数
# 注意 max_stack：开局会给 scrap/ration，凡是能反复拿到的必须可堆叠，
# 否则 Inventory.add 会报「背包已满」（曾经把 scrap 锁成 1，导致交易彻底失效）。
ITEMS: dict[str, dict] = {
    "scrap": dict(name="废铁", item_type="material", value=6, max_stack=99,
                  description="扭曲的合金碎片，营地里的硬通货。"),
    "ration": dict(name="压缩口粮", item_type="consumable", quantity=1, max_stack=5,
                   value=10, usable=True, description="嚼起来像湿纸板，但能顶一天。"),
    "medkit": dict(name="急救包", item_type="consumable", quantity=1, max_stack=3,
                   value=35, usable=True, description="针管、凝血胶、半卷绷带。"),
    "flare": dict(name="信号棒", item_type="tool", value=20, usable=True, max_stack=5,
                  description="烧起来是病态的红，狼群怕这个。"),
    "keycard": dict(name="锈迹门禁卡", item_type="key", value=0,
                    description="边角磨圆了，磁条上刻着「B-3 值班室」。"),
    "battery": dict(name="备用电池", item_type="component", value=25, max_stack=10,
                    description="还有电。在这年头这叫奇迹。"),
    "mapbook": dict(name="测绘员手记", item_type="book", value=40,
                    description="每一页角上都画着一个六位数，像是某种编号习惯。"),
    "core": dict(name="反应核心", item_type="quest", value=0,
                 description="掌心大小，温的。它在呼吸。"),
}

I2N = {k: v["name"] for k, v in ITEMS.items()}


def make_item(item_id: str, quantity: int = 1) -> InventoryItem:
    """按 ID 造一个 InventoryItem（缺省数量可被覆盖）。"""
    spec = ITEMS[item_id]
    return InventoryItem(
        item_id=item_id,
        name=spec["name"],
        item_type=spec["item_type"],
        description=spec.get("description", ""),
        quantity=quantity,
        max_stack=spec.get("max_stack", 1),
        value=spec.get("value", 0),
        usable=spec.get("usable", False),
    )


# ---------------------------------------------------------------- 敌人

ENEMIES: dict[str, Enemy] = {
    "cur_dog": Enemy(
        id="cur_dog", name="瘸腿变异犬", hp=22, max_hp=22, armor=0,
        metadata={"category": "beast"},
        intents=[
            Intent(name="扑咬", description="它一瘸一拐地压低身子", damage=4),
            Intent(name="撕扯", description="牙齿擦过你的袖子", damage=6),
            Intent(name="低吼", description="它在等你先动", damage=2, effect_tag="STAGGER"),
        ],
    ),
    # 注：allin 的 combat_state.play_card 直接减 enemy.hp，绕过 EnemyInstance.take_damage，
    #     所以 armor 字段在这条路径上不生效 —— 这里索性设 0，免得界面和实算对不上。
    "raider_scav": Enemy(
        id="raider_scav", name="掠夺者·拾荒客", hp=38, max_hp=38, armor=0,
        metadata={"category": "human"},
        intents=[
            Intent(name="锈刀突刺", description="刀刃上有破伤风的黄", damage=6),
            Intent(name="威胁", description="他晃了晃手里的钢管", damage=4, effect_tag="STAGGER"),
            Intent(name="重砸", description="钢管带着风声落下", damage=10),
        ],
    ),
    "watcher": Enemy(
        id="watcher", name="守望者 RM-07", hp=80, max_hp=80, armor=0, is_boss=True,
        metadata={"category": "boss"},
        intents=[
            Intent(name="伺服压制", description="机械臂横扫过来", damage=8),
            Intent(name="多普勒尖啸", description="音浪撞击你的胸腔", damage=7, effect_tag="STAGGER"),
            Intent(name="棱镜护盾", description="它展开了一圈冷光", damage=4, effect_tag="BLOCK"),
            Intent(name="过载轰击", description="炮口在你眼前亮起", damage=15),
        ],
    ),
}


# ---------------------------------------------------------------- 卡组

def _vent(state) -> None:
    """「排险」：稳住手，回复 6 点神经压力。"""
    if hasattr(state, "player_nerve") and hasattr(state, "player_max_nerve"):
        state.player_nerve = min(state.player_max_nerve, state.player_nerve + 6)
        state.log("你深吸一口气，手不抖了。（神经 +6）")


STARTING_DECK: list[Card] = [
    Card(id="slash", name="挥砍", description="", ap_cost=1, damage=6),
    Card(id="slash", name="挥砍", description="", ap_cost=1, damage=6),
    Card(id="slash", name="挥砍", description="", ap_cost=1, damage=6),
    Card(id="thrust", name="突刺", description="", ap_cost=1, damage=5),
    Card(id="brace", name="架住", description="", ap_cost=1, block=7),
    Card(id="fieldkit", name="战地急救", description="", ap_cost=1, heal=8),
    Card(id="sunder", name="破甲重砍", description="", ap_cost=2, damage=12),
    Card(id="vent", name="排险", description="稳住手（神经 +6）", ap_cost=0, effect=_vent),
]

# 站点 / 战斗掉落的卡牌奖励
# award_id -> Card
CARD_AWARDS: dict[str, Card] = {
    "flash": Card(id="flash", name="信号棒照射", description="令敌人迟疑（下一次攻击 -3）",
                  ap_cost=1, damage=3),
    "overload": Card(id="overload", name="过载充能", description="", ap_cost=2, damage=9, block=4),
    "stim": Card(id="stim", name="兴奋剂", description="", ap_cost=0, heal=5),
}


# ---------------------------------------------------------------- 谜题

PUZZLES: list[dict] = [
    {
        "puzzle_id": "bunker_terminal",
        "title": "B-3 值班终端",
        "puzzle_type": "text_decrypt",
        "description": "屏幕上滚动着褪色的登录提示，光标一闪一闪。",
        "question": "请输入 4 位值班口令：",
        "answer": ["4173", "四一七三"],
        "hints": [
            "数字写在别处 —— 你身上某件东西的边角。",
            "测绘员的习惯：每页右下角都标勘查序号。",
            "手记封面那行小字：「第 4 区 · 173 号」→ 4 与 173。",
        ],
        "rewards": {"item": "keycard"},
        "attempts_allowed": 3,
    },
]


# ---------------------------------------------------------------- 开局配点

START_CP = 420

START_ATTRIBUTES: dict[str, int] = {
    "体质": 14,
    "敏捷": 14,
    "力量": 13,
    "感知": 13,
    "智力": 12,
    "魅力": 10,
}

# 技能名必须命中 allin trpg 的 SKILL_LIBRARY（30 项），写错会「未知技能」。
# 剩余 CP 留作沿途训练（training）用 —— 这是唯一的养成途径。
START_SKILLS: dict[str, int] = {
    "活力": 5,          # 决定 HP（约 29 点）
    "近战攻击": 6,
    "远程攻击": 4,
    "侦察": 4,
    "手上功夫": 4,      # 撬锁、拆零件（≈通用「巧手」）
    "恐吓": 3,          # ≈「威吓」
    "调查": 3,          # ≈「修理」：拆开东西看它怎么坏的
    "历史": 2,          # ≈「学识」
    "奥术施法": 2,      # 老家伙教过你几个词
    "医疗": 2,
}

# 开局就掌握的技术
START_TECHNIQUES: list[str] = ["猛力攻击"]

# 沿途可能习得的技术（名字同样要命中 TECHNIQUES 库）
LEARN_TECHNIQUES: list[str] = [
    "猛力攻击",
    "火焰箭",       # 奥术施法 ≥1
    "魔法飞弹",     # 奥术施法 ≥3
    "治疗言灵",     # 奥术施法 ≥3
    "连环打击",     # 近战攻击 ≥8
    "火球术",       # 奥术施法 ≥5
]
