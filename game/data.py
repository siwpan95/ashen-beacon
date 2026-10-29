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

    # —— 第二批：这些多半是「别人留下的」，描述里自带一段能还原出来的故事 ——
    "water": dict(name="净水壶", item_type="consumable", quantity=1, max_stack=5,
                  value=12, usable=True,
                  description="壶身上用刀刻了七道杠，第八道刻到一半停了。"),
    "wire": dict(name="铜线", item_type="material", value=8, max_stack=99,
                 description="剥皮剥得很仔细，是懂行的人留下的。"),
    "fuse": dict(name="保险丝", item_type="component", value=18, max_stack=10,
                 description="玻璃管里的细丝还完整。这种东西断一根少一根。"),
    "radio": dict(name="半导体收音机", item_type="tool", value=60, usable=True,
                  description="调谐旋钮停在 87.6，那个位置被人用指甲抠出了一道白痕。"),
    "lens": dict(name="光学镜片", item_type="component", value=30, max_stack=5,
                 description="从一副望远镜上拆下来的。镜筒上刻着「勿忘」。"),
    "tape": dict(name="录音带", item_type="book", value=25,
                 description="标签写着「给听见的任何人」。A 面录过，B 面是空的。"),
    "dogtag": dict(name="狗牌", item_type="quest", value=0,
                   description="两片，一片是别人的。绳子是被扯断的，不是解开的。"),
    "mask": dict(name="防尘面具", item_type="tool", value=45,
                 description="滤罐换过三次，第三次换的人手很抖，螺纹拧歪了。"),
    "fuel": dict(name="燃料", item_type="material", value=20, max_stack=10,
                 description="半瓶。瓶身上的手写字被油泡糊了，只剩「别」字。"),
}

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
        id="watcher", name="守望者 RM-07", hp=96, max_hp=96, armor=0, is_boss=True,
        metadata={"category": "boss"},
        intents=[
            Intent(name="伺服压制", description="机械臂横扫过来", damage=8),
            Intent(name="多普勒尖啸", description="音浪撞击你的胸腔", damage=7, effect_tag="STAGGER"),
            Intent(name="棱镜护盾", description="它展开了一圈冷光", damage=4, effect_tag="BLOCK"),
            Intent(name="过载轰击", description="炮口在你眼前亮起", damage=15),
        ],
    ),
    # —— 第二批：三个常规遭遇 + 一个中期小 Boss ——
    # 数值是 tools/sim.py 跑出来的，不是拍的：满血进场胜率 / 平均回合 / 剩血都量过。
    "swarm": Enemy(
        id="swarm", name="蚀骨鼠群", hp=22, max_hp=22, armor=0,
        metadata={"category": "beast"},
        intents=[
            Intent(name="围上来", description="它们从四面同时动", damage=4),
            Intent(name="啃咬", description="有东西钻进了裤脚", damage=6),
            Intent(name="尖啸", description="声音尖得让人站不住", damage=3, effect_tag="STAGGER"),
        ],
    ),
    "husk": Enemy(
        id="husk", name="尘埃行尸", hp=48, max_hp=48, armor=0,
        metadata={"category": "undead"},
        intents=[
            Intent(name="扑", description="它走得很慢，但从不减速", damage=7),
            Intent(name="抓握", description="手指上的防化服破布蹭过你", damage=9),
            Intent(name="咳", description="它咳出一口灰色的东西", damage=5, effect_tag="STAGGER"),
        ],
    ),
    "drone": Enemy(
        id="drone", name="巡检无人机", hp=40, max_hp=40, armor=0,
        metadata={"category": "machine"},
        intents=[
            Intent(name="点射", description="红点在胸口停了一瞬", damage=8),
            Intent(name="绕飞", description="它升高，重新校正角度", damage=4, effect_tag="BLOCK"),
            Intent(name="俯冲", description="螺旋桨的声音压到头顶", damage=10),
        ],
    ),
    "raider_vet": Enemy(
        id="raider_vet", name="掠夺者·老兵", hp=72, max_hp=72, armor=0, is_boss=True,
        metadata={"category": "human"},
        intents=[
            Intent(name="老练的刺", description="他不砍要害，他砍你站的位置", damage=10),
            Intent(name="压制射击", description="枪声在水泥里滚了两圈", damage=13),
            Intent(name="换弹", description="他退到掩体后，手没停", damage=6, effect_tag="BLOCK"),
            Intent(name="抵近", description="他把刀换到了反手", damage=11, effect_tag="STAGGER"),
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

    # —— 第二批：沿途拾取/战斗奖励 ——
    "shiv": Card(id="shiv", name="磨尖的家伙", description="", ap_cost=1, damage=7),
    "barricade": Card(id="barricade", name="垒起来", description="", ap_cost=1, block=10),
    "rally": Card(id="rally", name="振作", description="", ap_cost=1, heal=6, block=4),
    "lance": Card(id="lance", name="熔丝刺", description="把保险丝捅进去", ap_cost=2, damage=13),
    "laststand": Card(id="laststand", name="死撑", description="", ap_cost=0, block=6),
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
    {
        # 配电站：三根保险丝的通电顺序。答案是「先断再接」，所以是倒序。
        "puzzle_id": "relay_fuse",
        "title": "中继站配电盘",
        "puzzle_type": "text_decrypt",
        "description": "三根保险丝，三个槽位，槽位下面有人用铅笔写了号。",
        "question": "按什么顺序换？（三位数，例：213）",
        "answer": ["321", "3-2-1", "三二一"],
        "hints": [
            "带上电的那一根不能先拔 —— 会烧掉整个盘。",
            "先拔没电的那根（3 号），再拔 2 号，最后才是 1 号。",
            "倒着来：3 → 2 → 1。",
        ],
        "rewards": {"item": "fuse"},
        "attempts_allowed": 3,
    },
    {
        # 道德困境（allin 的 moral_dilemma）：三个选项都不干净，选了就承担。
        # bridge._open_puzzle 会把 options 打出来，玩家 answer <编号>，
        # 奖励里的 flag 会回写 dse 的 state.flags —— 终章文本据此变化（延迟后果）。
        "puzzle_id": "camp_dilemma",
        "title": "营地丢了一批药",
        "puzzle_type": "moral_dilemma",
        "description": "头子把你按在火堆边坐下，说昨晚上丢了两支抗生素，\n"
                       "「就那丫头，十三四岁，瘦得像根柴。你要是帮我拎回来，我分你一份；\n"
                       "你要有别的想法，也行 —— 但药得回来。」",
        "question": "你怎么处理？",
        "options": [
            {"text": "把孩子找出来交给头子。药是你拿的报酬，孩子的事归他们。",
             "rewards": {"flag": "handedKid", "item": "medkit"}},
            {"text": "告诉她往北走，别回头。药你自己赔。",
             "rewards": {"flag": "freedKid", "item": "ration"}},
            {"text": "谁也不帮。你明天一早就走，这事跟你没关系。",
             "rewards": {"flag": "walkedAway"}},
        ],
        "hints": [],
        "attempts_allowed": 3,
    },
]


# ---------------------------------------------------------------- 配方

# 合成走 bridge 的 allin/craft:<id>：材料从背包扣，产物进背包。
# 设计原则同辐射的「分解—合成」：拆出来的东西一定还能看出原来是什么。
RECIPES: dict[str, dict] = {
    "makeshift_kit": dict(name="凑合的急救包", needs=[("wire", 1), ("ration", 1)],
                          yields=("medkit", 1),
                          description="绷带没有，用布和一点点运气。"),
    # 已删除 flare_up（需 fuel）与 filter（需 mask）：
    # 这两种材料的产出点在 69 个场景里是 0，配方永远做不出来，却还列在
    # `recipes` 里让玩家反复找材料。等以后真给 fuel / mask 加了掉落再恢复。
    "antenna": dict(name="天线", needs=[("wire", 2), ("lens", 1), ("battery", 1)],
                    yields=("radio", 1),
                    description="铜线绕成圈，镜片当反射面 —— 老办法，但管用。"),
}


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
