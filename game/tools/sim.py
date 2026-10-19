"""战斗平衡模拟：用贪心 AI 把每个敌人打 N 场，量化胜率与回合数。

用法：
    python tools/sim.py                 # 默认每敌 300 场，满血进场
    python tools/sim.py --hp 18         # 残血进场（看「没休整就上」有多亏）
    python tools/sim.py --enemy watcher

不改游戏状态，纯离线模拟。调 data.py 的敌人数值后跑一遍就知道是松了还是紧了。
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from allin_engine import GameEngine  # noqa: E402
from allin_engine.systems.combat import CardDeck, EnemyInstance  # noqa: E402
from allin_engine.trpg import Character, preset_trpg  # noqa: E402

from data import (  # noqa: E402
    ENEMIES, START_ATTRIBUTES, START_CP, START_SKILLS, START_TECHNIQUES,
    STARTING_DECK,
)


def build_char() -> Character:
    ae = GameEngine()
    ae.add_core().add_world().add_systems().add_combat()
    preset_trpg(ae.ctx)
    c = Character(starting_cp=START_CP, ctx=ae.ctx)
    for k, v in START_ATTRIBUTES.items():
        c.set_attribute(k, v)
    for s, r in START_SKILLS.items():
        c.purchase_skill(s, r)
    for t in START_TECHNIQUES:
        c.purchase_technique(t)
    return c


def greedy_turn(combat, engine, rng) -> None:
    """打完一个玩家回合：贪心出牌，出不动了就结束回合。"""
    guard = 0
    while not combat.combat_over and guard < 40:
        guard += 1
        hand = combat.hand
        if not hand:
            break
        hp_frac = combat.player_hp / max(1, combat.player_max_hp)
        incoming = _incoming(combat)

        def score(card):
            s = 0.0
            if card.damage:
                s += card.damage * 1.0
            if card.block:
                # 快死了 / 这一下很重 → 格挡价值上升
                s += card.block * (1.6 if hp_frac < 0.5 else 0.8)
                if incoming >= combat.player_hp:
                    s += card.block * 2.0
            if card.heal:
                s += card.heal * (1.8 if hp_frac < 0.6 else 0.4)
            if card.effect:
                s += 2.0
            return s / max(1, card.ap_cost)

        playable = [c for c in hand if c.ap_cost <= combat.ap]
        if not playable:
            break
        best = max(playable, key=score)
        idx = hand.index(best)
        if not engine.play_card(idx):
            break
    if not combat.combat_over:
        engine.end_player_turn()


def _incoming(combat) -> int:
    try:
        return int(combat.enemy.peek_intent().damage or 0)
    except Exception:  # noqa: BLE001
        return 0


def simulate(enemy_id: str, runs: int, start_hp: int | None, extra_cards: list) -> dict:
    ae = GameEngine()
    ae.add_core().add_world().add_systems().add_combat()
    engine = ae.get("combat")
    lib = ae.get("enemy_library")
    try:
        lib.register_batch(list(ENEMIES.values()))
    except Exception:  # noqa: BLE001
        pass

    tpl = lib.get_enemy(enemy_id)
    char = build_char()
    max_hp = int(char.max_hp)

    wins = 0
    turns = []
    hp_left = []
    for _ in range(runs):
        deck = CardDeck()
        deck.add_batch(list(STARTING_DECK) + list(extra_cards))
        deck.shuffle()
        hp = start_hp if start_hp is not None else max_hp
        st = engine.start_combat(hp, max_hp, 40, 40, EnemyInstance(tpl), list(deck.cards))
        guard = 0
        while not st.combat_over and guard < 200:
            guard += 1
            greedy_turn(st, engine, None)
        if st.check_victory():
            st.combat_over = True
            st.player_won = True
        if st.player_won:
            wins += 1
            hp_left.append(max(0, st.player_hp))
        turns.append(st.turn_number)

    return {
        "enemy": tpl.name,
        "hp": tpl.max_hp,
        "win": wins / max(1, runs),
        "turns": statistics.fmean(turns) if turns else 0,
        "hp_left": statistics.fmean(hp_left) if hp_left else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="《灰烬信标》战斗平衡模拟")
    ap.add_argument("--runs", type=int, default=300)
    ap.add_argument("--hp", type=int, default=None, help="进场血量（缺省满血）")
    ap.add_argument("--enemy", default=None, help="只模拟某一个敌人 id")
    args = ap.parse_args()

    ids = [args.enemy] if args.enemy else list(ENEMIES)
    print(f"每敌 {args.runs} 场 · 进场 {'满血' if args.hp is None else str(args.hp) + ' HP'} "
          f"· 卡组 8 张初始牌\n")
    print(f"{'敌人':<18}{'HP':>5}{'胜率':>8}{'平均回合':>10}{'残血':>8}")
    print("-" * 50)
    for eid in ids:
        r = simulate(eid, args.runs, args.hp, [])
        print(f"{r['enemy']:<18}{r['hp']:>5}{r['win'] * 100:>7.0f}%"
              f"{r['turns']:>10.1f}{r['hp_left']:>8.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
