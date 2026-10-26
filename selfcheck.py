# -*- coding: utf-8 -*-
"""《灰烬信标》无头自检 —— 不开窗口、不进入叙事层。

    python selfcheck.py

只碰两层，都是「不开窗口也能跑」的纯逻辑：

* ``game/data.py``    —— 物品 / 敌人 / 卡组 / 配方 / 谜题 / 开局配点（喂给 allin-engine 的数据层）
* ``allin_engine``    —— 角色配点、背包堆叠与存读、一场完整卡牌战斗

叙事层 ``dse_core`` 未安装，``game/bridge.py`` 需要它，所以这里不 import
（也不需要）：战斗数值与战斗循环本身就是本仓 ``game/tools/sim.py`` 用的那条路径。
输出保持 ASCII，避免 Windows 控制台代码页差异把中文打成乱码。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAME = ROOT / "game"
sys.path.insert(0, str(GAME))

import data  # noqa: E402  —— 项目自身的数据层

from allin_engine import GameEngine  # noqa: E402
from allin_engine.systems import Inventory  # noqa: E402
from allin_engine.systems.combat import CardDeck, EnemyInstance  # noqa: E402
from allin_engine.trpg import Character, preset_trpg  # noqa: E402


# --------------------------------------------------------------------- 检查项
def check_item_tables() -> str:
    """物品表可构造，配方/谜题/卡牌引用不悬空，背包合并 + 存读往返。"""
    # 1) 每个物品都能按 make_item 造出来，且与 I2N 名称表一致
    for item_id in data.ITEMS:
        item = data.make_item(item_id)
        assert item.item_id == item_id, item_id
        assert item.name == data.I2N[item_id], item_id
        assert item.max_stack >= 1, item_id

    # 2) 配方 / 卡牌奖励 / 谜题奖励引用的 id 必须都在物品表里
    for recipe_id, recipe in data.RECIPES.items():
        assert recipe["needs"], f"{recipe_id}: no ingredients"
        for item_id, qty in recipe["needs"]:
            assert item_id in data.ITEMS, f"{recipe_id}: unknown material {item_id}"
            assert qty > 0, f"{recipe_id}: bad quantity"
        yield_id, yield_qty = recipe["yields"]
        assert yield_id in data.ITEMS, f"{recipe_id}: unknown product {yield_id}"
        assert yield_qty > 0, f"{recipe_id}: bad yield quantity"
    for award_id, card in data.CARD_AWARDS.items():
        assert card.id == award_id, f"card award {award_id} mismatched"
    for puzzle in data.PUZZLES:
        reward = puzzle.get("rewards", {}).get("item")
        if reward:
            assert reward in data.ITEMS, f"puzzle {puzzle['puzzle_id']}: unknown reward {reward}"

    # 3) 背包：同类材料合并、扣减、to_dict/from_dict 往返
    inv = Inventory()
    ok, _ = inv.add(data.make_item("scrap", 3))
    assert ok, "cannot add to inventory"
    ok, _ = inv.add(data.make_item("scrap", 2))
    assert ok, "cannot add to inventory"
    assert inv.total_quantity == 5, f"same material did not stack: {inv.total_quantity}"
    assert inv.item_count == 1, f"same material took {inv.item_count} slots"
    assert inv.remove("scrap", 2) is True, "remove failed"
    assert inv.total_quantity == 3, f"wrong quantity after remove: {inv.total_quantity}"
    twin = Inventory.from_dict(inv.to_dict())
    assert twin.total_quantity == 3
    assert [i.item_id for i in twin.list_all()] == [i.item_id for i in inv.list_all()]

    return (f"{len(data.ITEMS)} items + {len(data.RECIPES)} recipes resolve, "
            f"inventory stack/save-load round-trip")


def build_character() -> tuple[GameEngine, Character]:
    """按 data.py 的开局配点造角色（与 bridge.py 的开局路径一致）。"""
    engine = GameEngine()
    engine.add_core().add_world().add_systems().add_combat()
    preset_trpg(engine.ctx)
    char = Character(starting_cp=data.START_CP, ctx=engine.ctx)
    for name, value in data.START_ATTRIBUTES.items():
        char.set_attribute(name, value)
    # 技能名写错这里就会抛「未知技能」——正好钉住 data.py 与引擎技能库的对齐
    for name, rank in data.START_SKILLS.items():
        char.purchase_skill(name, rank)
    return engine, char


def check_character_build() -> str:
    """开局配点能造出可战斗角色：属性落位、技能全命中技能库、技术可购。"""
    _, char = build_character()
    for name, value in data.START_ATTRIBUTES.items():
        assert char.attributes.get(name) == value, f"attribute {name} not applied"
    assert len(char.skills) == len(data.START_SKILLS), "some skills failed (name not in library?)"
    assert int(char.max_hp) > 0, "max_hp not computed"
    assert char.cp >= 0, "CP overspent"

    tech = data.START_TECHNIQUES[0]
    ok, msg = char.purchase_technique(tech)
    assert ok, f"starting technique rejected: {msg}"
    assert char.has_technique(tech)

    return (f"start build -> {int(char.max_hp)} HP, {len(char.skills)} skills, "
            f"technique ok, {char.cp} CP left")


def _run_combat(seed: int):
    """用贪心出牌把「初始卡组 vs 瘸腿变异犬」打到底，返回战斗状态。"""
    engine_root, char = build_character()
    engine = engine_root.get("combat")
    library = engine_root.get("enemy_library")
    library.register_batch(list(data.ENEMIES.values()))

    random.seed(seed)
    deck = CardDeck()
    deck.add_batch(list(data.STARTING_DECK))
    deck.shuffle()
    hp = int(char.max_hp)
    enemy = EnemyInstance(library.get_enemy("cur_dog"))
    state = engine.start_combat(hp, hp, 40, 40, enemy, list(deck.cards))

    guard = 0
    while not state.combat_over and guard < 200:
        guard += 1
        inner = 0
        while not state.combat_over and inner < 20:
            inner += 1
            playable = [i for i, card in enumerate(state.hand) if card.ap_cost <= state.ap]
            if not playable:
                break
            if not engine.play_card(playable[0]):
                break
        if not state.combat_over:
            engine.end_player_turn()
    return state


def check_combat_flow() -> str:
    """战斗：伤害真的结算、状态推进到胜负、同种子结果可复现。"""
    first = _run_combat(20261026)
    second = _run_combat(20261026)

    assert first.enemy.hp < data.ENEMIES["cur_dog"].hp, "enemy hp never dropped"
    assert 0 <= first.player_hp <= first.player_max_hp, "player hp out of range"
    assert first.turn_number >= 1, "turn counter never advanced"
    signature = lambda s: (s.player_won, round(s.player_hp, 6), s.enemy.hp,  # noqa: E731
                           s.turn_number, s.combat_over)
    assert signature(first) == signature(second), "same seed gave different combat result"
    assert first.player_won or first.combat_over, "combat never reached an outcome"

    verdict = "win" if first.player_won else "loss/unresolved"
    return (f"card combat vs cur_dog: {first.turn_number} turns, {verdict}, "
            f"{first.player_hp:.0f}/{first.player_max_hp:.0f} HP, reproducible")


# --------------------------------------------------------------------- 入口
def main() -> int:
    checks = [
        ("item/recipe/inventory", check_item_tables),
        ("starting build", check_character_build),
        ("combat flow", check_combat_flow),
    ]
    passed: list[str] = []
    for name, fn in checks:
        try:
            passed.append(fn())
        except Exception as exc:  # noqa: BLE001 —— 自检要把失败原因打给人看
            print(f"selfcheck FAIL: {name} -> {type(exc).__name__}: {exc}")
            return 1
    print("selfcheck ok: " + "; ".join(passed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
