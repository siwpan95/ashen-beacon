"""allin-bridge：把 allin-engine 接到 dse-core 上。

分工
----
dse-core    叙事层：场景图、条件文本、选项、once/tunnel、结局。全同步阻塞主循环。
allin-engine 规则层：TRPG 角色、卡牌战斗、背包、谜题、存档、ECS 世界、事件总线。

本插件是唯一的双向通道：

    dse -> allin  内容里 emit 事件（allin/check、allin/gain:<id> ...）
                  玩家输入（engine/command 拦截）
    allin -> dse  pull() 把派生值写进 ctx.state["allin"]。
                  因为 dse 的条件表达式命名空间 == state 顶层键，
                  所以 YAML 里能直接写 if: 'allin.item.scrap >= 3'、
                  文本里能写 {allin.hp} / {allin} —— 这是唯一的取值方式。

pull() 会把所有「可能出现的数据键」都补上缺省值（False / 0），
避免条件表达式引用未定义名字时刷屏告警。
"""

from __future__ import annotations

import os
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from dse_core.runtime import Service

from allin_engine import GameEngine
from allin_engine.core import Component, System, World
from allin_engine.systems import SaveLoadManager
from allin_engine.systems.combat import CardDeck, CombatEngine, EnemyInstance
from allin_engine.trpg import Character, preset_trpg

from data import (
    CARD_AWARDS, ENEMIES, ITEMS, LEARN_TECHNIQUES, PUZZLES, RECIPES,
    START_ATTRIBUTES, START_CP, START_SKILLS, START_TECHNIQUES, STARTING_DECK,
    make_item,
)

try:  # 出图依赖 numpy/Pillow；没装就静默关闭，游戏本体照常跑
    from viz import AVAILABLE as VIZ_AVAILABLE
except Exception:  # noqa: BLE001 - 任何导入问题都不该影响游戏
    VIZ_AVAILABLE = False

# 出图是同步阻塞的（战报一次 6~8 秒），必须给玩家一个关掉的开关。
# DSE_NO_VIZ=1 或 play.py --no-viz 都走这里。
if os.environ.get("DSE_NO_VIZ", "").strip().lower() in ("1", "true", "yes", "on"):
    VIZ_AVAILABLE = False

TECHNIQUES = list(LEARN_TECHNIQUES)

# 战斗中不拦截的内置命令（放行给引擎默认处理器）
_PASSTHROUGH = {"help", "?", "quit", "exit", "rollback", "rb", "back"}

_BOX_W = 46   # 战斗面板内宽（显示列数）
_SCRAP_RATE = 6   # 1 废铁折算多少经营资金（集市用）


def _dw(text: str) -> int:
    """显示宽度：中日韩全角字占 2 列，否则 1 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _box(content: str) -> str:
    """画一行面板内容；按「显示列数」补齐，避免中文把右边框挤歪。"""
    return "  ║" + content + " " * max(0, _BOX_W - _dw(content)) + "║"


def _bar(cur: int, mx: int, width: int = 20) -> str:
    filled = 0 if mx <= 0 else max(0, min(width, int(cur / mx * width)))
    return "█" * filled + "░" * (width - filled)


# ------------------------------------------------------------------ ECS

@dataclass
class Locale(Component):
    """场景实体组件：一处被走过的地点。"""
    scene_id: str = ""
    name: str = ""


@dataclass
class Footprints(Component):
    """访问计数。"""
    visits: int = 0


class CodexSystem(System):
    """遍历带 Locale 组件的实体，把走访统计写进 world data。"""

    priority = 50

    def update(self, world: World, dt: float) -> None:
        total = 0
        for e in world.each(Locale, Footprints):
            total += e.get(Footprints).visits
        world.set_data("total_footprints", total)
        world.set_data("known_locales", len(world.get_entities_with_components(Locale)))


# ------------------------------------------------------------------ 插件

class AllinBridge(Service):
    """dse-core 插件：桥接 allin-engine。"""

    name = "allin"
    inject = ("state", "engine", "io", "scenes")

    # ------------------------------------------------------------ 生命周期

    def setup(self) -> None:
        self.combat: object | None = None          # allin CombatState
        self.combat_cfg: dict = {}
        self.battle_pending: dict | None = None
        self.puzzle_active: str | None = None
        # allin 的 PuzzleSystem 不校验 attempts_allowed（data.py 里写了 3 但没生效），
        # 只能在 bridge 侧自己数。按 puzzle_id 累计 —— 若按「进入次数」清零，
        # 玩家出去再进来就能白拿三次新机会。
        self.puzzle_attempts: dict[str, int] = {}
        self.telemetry: dict | None = None   # 当前战斗的遥测数据（喂给战报出图）
        self._telem_pre: tuple | None = None

        self._boot_allin()
        self._wire()
        self._route_content_events()
        self.pull()

    def _boot_allin(self) -> None:
        """装配 allin-engine：ECS + 事件总线 + 世界 + 系统 + 战斗 + TRPG 角色。"""
        root = self.ctx.root

        self.ae = GameEngine()
        self.ae.add_core().add_world().add_systems().add_combat()
        self.bus = self.ae.bus
        self.world = self.ae.world
        self.world.add_system(CodexSystem())

        # TRPG 规则内核挂进同一个 Context，和 ECS 世界共享事件总线
        preset_trpg(self.ae.ctx)
        self.char = Character(starting_cp=START_CP, ctx=self.ae.ctx)

        self.bus.subscribe("character/changed", self._on_char_changed)
        self.bus.subscribe("PUZZLE_SOLVED", self._on_puzzle_solved)

        # 数据层：卡库 / 敌库 / 背包 / 谜题 / 存档
        self.card_library = self.ae.get("card_library")
        self.enemy_library = self.ae.get("enemy_library")
        self.combat_engine: CombatEngine = self.ae.get("combat")
        try:
            self.card_library.register_batch(STARTING_DECK)
        except Exception:  # noqa: BLE001 - 重复注册不影响开局
            pass
        try:
            self.enemy_library.register_batch(list(ENEMIES.values()))
        except Exception:  # noqa: BLE001
            pass

        self.deck = CardDeck()
        self.deck.add_batch(list(STARTING_DECK))
        self.deck.shuffle()

        self.inv = self.ae.get("inventory")
        self.puzzles = self.ae.get("puzzles")
        self.puzzles.load_from_json(PUZZLES)

        self.saves = SaveLoadManager(save_dir=str(root / "saves"), version=2)

        # 开局配点（CP 不足时逐项降级，不崩）
        for attr, val in START_ATTRIBUTES.items():
            self.char.set_attribute(attr, val)
        for skill, rank in START_SKILLS.items():
            self.char.purchase_skill(skill, rank)
        for tech in START_TECHNIQUES:
            self.char.purchase_technique(tech)
        self.char.current_hp = int(self.char.max_hp)

        # 开局随身（静默 —— 别在标题画面之前报流水账）
        self.gain("ration", 2, quiet=True)
        self.gain("scrap", 1, quiet=True)

    def _wire(self) -> None:
        """订阅 dse 侧事件。"""
        self.on("scene/mode", self._on_scene_mode)
        self.on("scene/rendered", self._on_scene_rendered)
        self.on("scene/enter", self._on_scene_enter)
        self.on("choice/made", lambda *a: self.pull())
        self.on("engine/command", self._on_command)

    # ------------------------------------------------------------ dse -> allin

    def _route_content_events(self) -> None:
        """扫描全部场景里的 emit 名，按前缀注册处理器。

        dse 的 emit 不携带参数 —— 所以「 gain 什么」写在事件名里，
        「检定什么」先写进 state.allin.checkSkill / checkDc 再 emit。
        """
        seen: set[str] = set()

        def walk(effects) -> None:
            if not isinstance(effects, dict):
                return
            ev = effects.get("emit")
            if isinstance(ev, str):
                seen.add(ev)
            elif isinstance(ev, list):
                seen.update(x for x in ev if isinstance(x, str))

        for sc in self.ctx.scenes.dict.values():
            walk(sc.get("onEnter"))
            walk(sc.get("onExit"))
            for c in sc.get("choices") or []:
                walk(c.get("do"))

        for name in sorted(seen):
            if name == "allin/check":
                self.on(name, self._ev_check)
            elif name.startswith("allin/check:"):
                # allin/check:<技能>:<DC> —— 推荐写法。
                # 不要把 skill/dc 塞进 state 再 emit：dse 的 effect 语义是
                #「数值 + 原值 = 累加」，写 'allin.dc': 13 会叠加上一次的值。
                parts = name.split(":")
                skill, dc = parts[1], (int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 10)
                self.on(name, lambda _s=skill, _d=dc: self.check(_s, _d))
            elif name == "allin/rest":
                self.on(name, self._ev_rest)
            elif name == "allin/rebirth":
                # dse 的 @restart 只重置 dse 自己的 state，allin 的角色不会跟着回血 ——
                # 不挂这个钩子的话，重开时必须带着 0 HP 再走一遍。
                self.on(name, self._ev_rebirth)
            elif name.startswith("allin/gain:"):
                # allin/gain:<物品>[:<数量>] —— 数量缺省 1
                parts = name.split(":")
                n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
                self.on(name, lambda _i=parts[1], _n=n: self.gain(_i, _n))
            elif name.startswith("allin/craft:"):
                self.on(name, lambda _n=name: self.craft(_n.split(":", 1)[1]))
            elif name == "allin/market:open":
                # 进集市：把背囊里的废铁折成经营用的钱（1 废铁 = 6）
                self.on(name, self._ev_market_open)
            elif name == "allin/market:close":
                # 收市：钱换回废铁，余数留着（下次进集市接着用）
                self.on(name, self._ev_market_close)
            elif name.startswith("allin/lose:"):
                # allin/lose:<物品>[:<数量>] —— 数量缺省 1
                # 必须用和 allin/pay: 一样的三段解析：split(":", 1) 会把 "wire:2"
                # 整个当成物品名，物品表里查不到就直接 return —— 静默 no-op，
                # 结果是「消耗 2 铜线」的分支根本不扣东西，玩家白拿奖励。
                parts = name.split(":")
                n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
                self.on(name, lambda _i=parts[1], _n=n: self.lose(_i, _n))
            elif name.startswith("allin/pay:"):
                # allin/pay:<物品>:<数量> —— 交易用；数量缺省 1（避免整堆扣光）
                parts = name.split(":")
                n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
                self.on(name, lambda _i=parts[1], _n=n: self.lose(_i, _n))
            elif name.startswith("allin/learn:"):
                self.on(name, lambda _n=name: self.learn(_n.split(":", 1)[1]))
            elif name.startswith("allin/award:"):
                self.on(name, lambda _n=name: self.award(_n.split(":", 1)[1]))
            elif name.startswith("allin/train:"):
                self.on(name, lambda _n=name: self.train(_n.split(":", 1)[1]))
            elif name.startswith("allin/puzzle:"):
                self.on(name, lambda _n=name: self._open_puzzle(_n.split(":", 1)[1]))

    # ------------------------------------------------------------ allin -> dse

    def pull(self) -> None:
        """把 allin 侧派生值刷进 dse state 的顶层 allin 键。"""
        st = self.ctx.state
        hp = int(self.char.current_hp or 0)
        mx = max(1, int(self.char.max_hp))
        st.set("allin.hp", hp)
        st.set("allin.maxHp", mx)
        # 只写内容真正会读的键。pct / hurt / alive / floor / bagSize / cards /
        # lastRoll 全仓零引用，每步白算一次还随存档落盘，已清掉。

        # 物品：全部 ID 都写，缺省 0（避免条件表达式引用未定义键时刷告警）
        for item_id in ITEMS:
            st.set(f"allin.item.{item_id}", self.inv.count(item_id))

        # 技术
        for tech in TECHNIQUES:
            st.set(f"allin.tech.{tech}", self.char.has_technique(tech))

        # 谜题
        for spec in PUZZLES:
            st.set(f"allin.solved.{spec['puzzle_id']}", self.puzzles.is_solved(spec["puzzle_id"]))

        st.set("allin.locales", self.world.get_data("known_locales", 0) or 0)

        # 累积变量：信号强度 = 你身上所有跟「收/发」有关的东西凑出来的值。
        # 终章按这个值决定广播能传多远 —— 一件件小事攒出来的结局差异。
        # radio 权重取 4：组装一台整机要 wire×2 + lens + battery（4 点零件），
        # 成品只值 2 点的话「攒齐一套设备」反而让 signal 净降 2、成就更难拿。
        # 4 点正好让合成前后持平（自然收集上限 13→15，结局分档与阈值 6 都仍可达）。
        st.set("allin.signal",
               self.inv.count("radio") * 4 + self.inv.count("battery")
               + self.inv.count("lens") + self.inv.count("wire"))

    # ------------------------------------------------------------ 物品 / 角色

    def gain(self, item_id: str, qty: int = 1, quiet: bool = False) -> None:
        if item_id not in ITEMS:
            self.ctx.io.print(f"  [未知物品 {item_id}]")
            return
        ok, msg = self.inv.add(make_item(item_id, qty))
        if not quiet:
            self.ctx.io.print(f"  ＋ {ITEMS[item_id]['name']} ×{qty}")
            if not ok:
                self.ctx.io.print(f"    （{msg}）")
        self.pull()

    def lose(self, item_id: str, qty: int = 1) -> None:
        """移除物品。qty=0 在 Inventory 里是"全部移除"，这里默认锁成 1，避免整堆扣光。"""
        if item_id not in ITEMS:
            return
        have = self.inv.count(item_id)
        n = have if qty <= 0 else min(qty, have)
        if n:
            self.inv.remove(item_id, n)
            self.ctx.io.print(f"  － {ITEMS[item_id]['name']}" + (f" ×{n}" if n > 1 else ""))
        self.pull()

    def craft(self, recipe_id: str) -> None:
        """按配方合成：材料齐了才扣，扣完才给产物（不半途扣一半）。"""
        spec = RECIPES.get(recipe_id)
        if not spec:
            self.ctx.io.print(f"  [未知配方 {recipe_id}]")
            return
        missing = [f"{ITEMS[i]['name']}×{n}" for i, n in spec["needs"]
                   if self.inv.count(i) < n]
        if missing:
            self.ctx.io.print(f"  材料不够：缺 {'、'.join(missing)}")
            return
        for i, n in spec["needs"]:
            self.inv.remove(i, n)
        out_id, out_n = spec["yields"]
        self.inv.add(make_item(out_id, out_n))
        self.ctx.io.print(f"  ✦ 合成：{spec['name']} → {ITEMS[out_id]['name']} ×{out_n}")
        self.pull()

    def _ev_market_open(self) -> None:
        """背囊里的废铁折成经营资金。

        第一次进市营地赊你 30 底钱（否则两手空空的人根本开不了张）；
        只赊一次，用 flags.marketCredited 记账 —— 不记账的话反复进出就能刷钱。
        """
        st = self.ctx.state
        have = self.inv.count("scrap")
        if have:
            self.inv.remove("scrap", have)
        credited = bool(st.get("flags.marketCredited"))
        st.set("flags.marketCredited", True)
        floor = 0 if credited else 30
        st.set("gold", int(st.get("gold") or 0) + have * _SCRAP_RATE + floor)
        if have:
            self.ctx.io.print(f"  ✦ 折了 {have} 块废铁，手头 {st.get('gold')} 块。")
        elif floor:
            self.ctx.io.print(f"  ✦ 老陈赊你 {floor} 块：『挣了还我，赔了算我的。』")
        self.pull()

    def _ev_market_close(self) -> None:
        """收市结算：钱按 1 废铁 = 6 换回废铁，零头留着。"""
        st = self.ctx.state
        gold = int(st.get("gold") or 0)
        back = gold // _SCRAP_RATE
        st.set("gold", gold - back * _SCRAP_RATE)
        if back:
            self.inv.add(make_item("scrap", back))
            self.ctx.io.print(f"  ✦ 收市，换回 {back} 块废铁。")
        self.pull()

    def _grant_rewards(self, rewards: dict | None) -> None:
        """谜题奖励：item 进背包、card 进卡组、flag 回写 dse 的 state.flags。"""
        for k, v in (rewards or {}).items():
            if k == "item":
                self.gain(v)
            elif k == "card":
                self.award(v)
            elif k == "flag":
                self.ctx.state.set(f"flags.{v}", True)

    def _open_puzzle(self, pid: str) -> None:
        """进入谜题场景：把题干打出来；道德困境额外列出可选项。"""
        self.puzzle_active = pid
        self.puzzle_attempts.setdefault(pid, 0)
        spec = self.puzzles.get_puzzle(pid)
        if spec is None:
            return
        io = self.ctx.io
        if spec.puzzle_type == "moral_dilemma" and getattr(spec, "options", None):
            io.blank()
            io.print(f"  {spec.description}")
            io.blank()
            for i, opt in enumerate(spec.options, 1):
                io.print(f"    [{i}] {opt['text']}")
            io.print("  输入 answer <编号>，例如 answer 2")
            io.blank()

    def train(self, skill: str) -> None:
        """技能提升一级（花 CP）。"""
        cur = int(self.char.skills.get(skill, 0))
        ok, msg = self.char.purchase_skill(skill, cur + 1)
        self.ctx.io.print(f"  ✦ {msg}")
        if ok:
            self.bus.emit("character/changed", "skills")
        self.pull()

    def learn(self, tech: str) -> None:
        ok, msg = self.char.purchase_technique(tech)
        self.ctx.io.print(f"  ✦ {msg}")
        if ok:
            self.bus.emit("character/changed", "techniques")
        self.pull()

    def award(self, card_id: str) -> None:
        card = CARD_AWARDS.get(card_id)
        if not card:
            return
        self.deck.add(card)
        self.ctx.io.print(f"  ✦ 卡组加入新牌：{card.name}")
        self.pull()

    def use(self, item_id: str) -> None:
        # 防御纵深：入口（restore_allin）已经过滤过未知 id，这里再兜一道 ——
        # 否则走到 ITEMS[item_id]['name'] 会 KeyError，直接把游戏打退出（exit 1）。
        if item_id not in ITEMS:
            self.ctx.io.print(f"  [认不出的东西：{item_id}]")
            return
        if self.inv.count(item_id) <= 0:
            self.ctx.io.print("  你没有这个东西。")
            return
        if item_id == "medkit":
            if self.char.current_hp >= self.char.max_hp:
                self.ctx.io.print("  身上没有要缝的口子，留着吧。")
                return
            self.inv.remove(item_id, 1)
            self.ctx.io.print(f"  你咬着袖子把凝血胶按上去。{self.char.heal(12)}")
            self.pull()
        elif item_id == "ration":
            if self.char.current_hp >= self.char.max_hp:
                self.ctx.io.print("  还不到饿的份上，先留着。")
                return
            self.inv.remove(item_id, 1)
            self.ctx.io.print(f"  口粮下肚，胃里踏实了点。{self.char.heal(4)}")
            self.pull()
        elif item_id == "mapbook":
            self.ctx.io.print("  封面内侧写着：「第 4 区 · 173 号」，边上还有一行：")
            self.ctx.io.print("  「给还听得见电台的人。」")
        else:
            self.ctx.io.print(f"  {ITEMS[item_id]['name']}在这儿用不上。")

    def _ev_rest(self) -> None:
        self.char.long_rest()
        self.ctx.io.print("  你在火堆边眯了一觉。伤口不再渗血，脑子也清了。")
        self.pull()

    def _ev_rebirth(self) -> None:
        """重开：把角色拉回开局状态。"""
        self.char.current_hp = max(1, int(self.char.max_hp))
        self.deck = CardDeck()
        self.deck.add_batch(list(STARTING_DECK))
        self.deck.shuffle()
        self.inv.clear()
        self.gain("ration", 2, quiet=True)
        self.gain("scrap", 1, quiet=True)
        self.ctx.io.print("  你又醒了。伤收了口，风还是那个风。")
        self.pull()

    def check(self, skill: str, dc: int) -> None:
        """做一次 d20 技能判定，结果写进 state 供状态文本与条件选项使用。"""
        st = self.ctx.state
        line = str(self.char.d20_check(skill, dc)).strip()
        st.set("allin.ok", "成功" in line)
        st.set("allin.rollLine", line)
        self.ctx.io.print(f"  🎲 {line}")
        self.pull()

    def _ev_check(self) -> None:
        """动态检定：先把 allin.skill / allin.dc 写进 state，再 emit allin/check。"""
        st = self.ctx.state
        self.check(st.get("allin.skill") or "侦察", int(st.get("allin.dc") or 10))

    # ------------------------------------------------------------ 事件回调

    def _on_char_changed(self, data) -> None:
        # 订阅位：角色属性变化时引擎会调到这里。
        # 原来只往一个从不被读的 log_lines 里 append，纯白占内存，已去掉。
        pass

    def _on_puzzle_solved(self, data) -> None:
        self.ctx.io.print(f"  ✦ 谜题解开：{data.get('title')}")
        self.pull()

    def _on_scene_enter(self, scene_id: str, scene: dict) -> None:
        """每进一个场景，在 ECS 世界留下足迹。"""
        entity = self.world.get_entity(scene_id)
        if entity is None:
            entity = self.world.create_entity(scene_id)
            entity.add(Locale(scene_id=scene_id, name=scene.get("title") or scene_id))
            entity.add(Footprints(visits=0))
        fp = entity.get(Footprints)
        fp.visits += 1
        self.world.update(0.0)

        # 兜底：任何路径回到开局场景（@restart）且人是空的，就当重开处理
        engine = self.ctx.engine
        if engine.current == engine.entry and int(self.char.current_hp or 0) <= 0:
            self._ev_rebirth()
        self.pull()

    def _on_scene_mode(self, scene_id: str, scene: dict, mode: str):
        """接管 battle 模式（只标记，真正开战在 scene/rendered 之后）。"""
        if mode != "battle":
            return None
        cfg = scene.get("battle") or {}
        enemy_id = cfg.get("enemy")
        if not enemy_id or not self.enemy_library.has_enemy(enemy_id):
            self.ctx.io.print(f"  [战斗配置缺失：未登记的敌人 {enemy_id} —— 直接放行]")
            target = cfg.get("onWin")
            return {"redirect": target} if target else True
        # 只标记接管；真正开战等 scene/rendered（先让读者看到场景文本）
        self.battle_pending = cfg
        return True

    def _on_scene_rendered(self, scene_id: str, scene: dict, mode: str) -> None:
        if self.battle_pending is not None:
            cfg, self.battle_pending = self.battle_pending, None
            self.begin_battle(cfg)
            return
        self._print_hud()

    def _print_hud(self) -> None:
        st = self.ctx.state
        hp, mx = st.get("allin.hp"), st.get("allin.maxHp")
        scrap = st.get("allin.item.scrap")
        io = self.ctx.io
        io.print(f"  [ HP {hp}/{mx} · 废铁 {scrap} · 卡组 {self.deck.count} ]")
        io.blank()

    # ------------------------------------------------------------ 战斗

    def begin_battle(self, cfg: dict) -> None:
        enemy_id = cfg.get("enemy")

        tpl = self.enemy_library.get_enemy(enemy_id)
        inst = EnemyInstance(tpl)

        hp = int(self.char.current_hp or 1)
        mx = max(1, int(self.char.max_hp))
        self.combat_log = []
        self.telemetry = {
            "enemy": inst.name,
            "scene": self._scene_label(),
            "hp_series": [hp],
            "enemy_series": [int(inst.hp)],
            "cards": {},
            "damage_dealt": 0,
            "damage_taken": 0,
            "blocked": 0,
            "healed": 0,
        }

        self.combat = self.combat_engine.start_combat(
            hp, mx, 40, 40, inst, list(self.deck.cards),
            log_cb=lambda m: self.combat_log.append(m),
        )
        self.combat_cfg = cfg
        self._render_combat(clear=True)

    def _scene_label(self) -> str:
        """当前场景的中文名（拿不到就退回场景 id，再退回空串）。"""
        try:
            sid = self.ctx.engine.current
            scene = self.ctx.scenes.get(sid) or {}
            return str(scene.get("title") or scene.get("name") or sid or "")
        except Exception:  # noqa: BLE001 - 标签拿不到不该影响战斗
            return ""

    def _telem_mark(self) -> None:
        """动作前记一次快照，之后 _telem_take 用差值结算遥测。"""
        st = self.combat
        self._telem_pre = (st.player_hp, st.enemy.hp) if st is not None else None

    def _telem_take(self, card=None) -> None:
        st, t = self.combat, self.telemetry
        if st is None or t is None or self._telem_pre is None:
            return
        php_pre, ehp_pre = self._telem_pre
        self._telem_pre = None
        t["damage_dealt"] += max(0, int(ehp_pre - st.enemy.hp))
        d = int(st.player_hp - php_pre)
        if d < 0:
            t["damage_taken"] += -d
        else:
            t["healed"] += d
        if card is not None:
            t["cards"][card.name] = t["cards"].get(card.name, 0) + 1
            t["blocked"] += int(card.block or 0)
        t["hp_series"].append(max(0, int(st.player_hp)))
        t["enemy_series"].append(max(0, int(st.enemy.hp)))

    def _render_combat(self, clear: bool = False) -> None:
        io, st = self.ctx.io, self.combat
        io.blank()
        io.print("  ╔" + "═" * _BOX_W + "╗")
        io.print(_box(f" 对手：{st.enemy.name}"))
        io.print(_box(f" 它下一手：{st.enemy.peek_intent().name}"))
        io.print("  ╠" + "═" * _BOX_W + "╣")
        io.print(_box(f" 你   {_bar(st.player_hp, st.player_max_hp)} "
                      f"{max(0, st.player_hp):>3}/{st.player_max_hp:<3}"))
        io.print(_box(f" 敌方 {_bar(st.enemy.hp, st.enemy.max_hp)} "
                      f"{max(0, st.enemy.hp):>3}/{st.enemy.max_hp:<3}"))
        io.print(_box(f" 行动点 {st.ap}   格挡 {st.player_block}   回合 {st.turn_number}"))
        io.print("  ╚" + "═" * _BOX_W + "╝")
        io.blank()
        io.print("  手牌：")
        for i, c in enumerate(st.hand, 1):
            bits = []
            if c.damage:
                bits.append(f"伤{c.damage}")
            if c.block:
                bits.append(f"挡{c.block}")
            if c.heal:
                bits.append(f"治{c.heal}")
            if c.effect:
                bits.append("特效")
            io.print(f"    [{i}] {c.name}（{c.ap_cost}点） {' '.join(bits)}")
        io.blank()
        for entry in self.combat_log[-6:]:
            io.print(f"    {entry}")
        self.combat_log = []
        io.blank()
        io.print("  输入编号出牌 · e 结束回合 · f 逃跑 · help 帮助")
        io.blank()

    def combat_input(self, line: str) -> dict:
        io, st = self.ctx.io, self.combat
        low = line.strip().lower()

        if low in ("e", "end", "结束"):
            self._telem_mark()
            self.combat_engine.end_player_turn()
            self._resolve_checks()
            self._telem_take()
            if not st.combat_over:
                self._render_combat()
            return self._maybe_finish() or {"type": "noop"}

        if low in ("f", "flee", "逃跑"):
            # 逃跑要保留「战斗当时的 HP」，不能传 0：
            # 内容层没有任何死亡判定，0 HP 的玩家照样能在世界上走，
            # 下一场战斗 begin_battle 的 int(current_hp or 1) 兜底成 1 HP，等于必死。
            self._sync_hp_back(st.player_hp)
            self.combat = None
            io.print("  你退开半步，然后开始跑。它追了两步就放弃了——值不当。")
            target = self.combat_cfg.get("onFlee")
            if not target:
                io.print("  [战斗配置缺少 onFlee，视为脱身后返回]")
                return {"type": "noop"}
            return {"type": "scene", "id": target}

        try:
            idx = int(line) - 1
        except ValueError:
            return {"type": "noop"}

        if 0 <= idx < len(st.hand):
            card = st.hand[idx]
            self._telem_mark()
            if self.combat_engine.play_card(idx):
                self._telem_take(card)
                self._resolve_checks()
                self._render_combat()
            else:
                io.print("  行动点不够，或者这张牌打不出。")
        else:
            io.print("  行动点不够，或者这张牌打不出。")
        result = self._maybe_finish()
        return result or {"type": "noop"}

    def _resolve_checks(self) -> None:
        st = self.combat
        if st.check_victory() or st.check_defeat():
            st.combat_over = True
            st.player_won = bool(st.enemy.hp <= 0)

    def _maybe_finish(self):
        st = self.combat
        if st is None or not st.combat_over:
            return None
        won = bool(st.player_won)
        io = self.ctx.io
        self._sync_hp_back(st.player_hp)

        if won:
            io.print(f"  ✦ 你放倒了 {st.enemy.name}。")
            gold = st.gold_reward
            self.gain("scrap", max(1, gold // 8))
            io.print(f"  ✦ 经验 {st.exp_reward}")
        else:
            io.print("  你的膝盖先着了地。视野在收紧……")

        self._emit_battle_report(won, int(st.turn_number))
        self.telemetry = None
        self.combat = None
        target = self.combat_cfg.get("onWin" if won else "onLose")
        if not target:
            io.print("  [战斗配置缺少跳转目标，游戏在此止步]")
            return {"type": "quit"}
        return {"type": "scene", "id": target}

    def _sync_hp_back(self, battle_hp: int) -> None:
        """战斗结束把 HP 写回 TRPG 角色（角色才是 HP 的唯一真源，避免重复结算）。"""
        mx = max(1, int(self.char.max_hp))
        self.char.current_hp = max(0, min(mx, int(battle_hp)))
        self.pull()

    # ------------------------------------------------------------ 出图

    def _out_dir(self):
        return self.ctx.root / "out"

    def _emit_battle_report(self, won: bool, turns: int) -> None:
        """战斗结束把遥测烘成一张战报图。失败静默——出图永远不值得打断游戏。"""
        if not VIZ_AVAILABLE or self.telemetry is None:
            return
        try:
            from viz.report import battle_report

            t = dict(self.telemetry)
            t.update({
                "result": "win" if won else "lose",
                "turns": turns,
                "player_max_hp": int(self.char.max_hp),
                "enemy_max_hp": int(t.get("enemy_max_hp") or max(t["enemy_series"] + [1])),
            })
            # 时间戳只到秒：同一秒内连打两场会互相覆盖（脚本跑图很容易触发）。
            # 补三位毫秒，既保持可读也让文件名唯一。
            stamp = time.strftime("%Y%m%d_%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
            path = battle_report(t, self._out_dir() / f"battle_{stamp}.png")
            self.ctx.io.print(f"  ✦ 战报已生成：{path.name}")
        except Exception as exc:  # noqa: BLE001 - 出图问题不影响游戏
            if os.environ.get("DSE_DEBUG"):
                print(f"[viz] 战报生成失败：{exc!r}")

    def _emit_character_sheet(self) -> None:
        """stats 命令顺手出一张角色卡图。"""
        if not VIZ_AVAILABLE:
            return
        try:
            from viz.report import character_sheet

            c = self.char
            items = [(it.name, it.quantity) for it in self.inv.list_all()]
            data = {
                "name": "柯岚",
                "hp": int(c.current_hp or 0),
                "max_hp": int(c.max_hp),
                "attributes": dict(c.attributes),
                "skills": dict(c.skills),
                "techniques": [t for t in TECHNIQUES if c.has_technique(t)],
                "items": items,
                "cards": len(self.deck.cards),
                "locales": self.world.get_data("known_locales", 0) or 0,
            }
            path = character_sheet(data, self._out_dir() / "character_sheet.png")
            self.ctx.io.print(f"  ✦ 角色卡已生成：{path.name}")
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("DSE_DEBUG"):
                print(f"[viz] 角色卡生成失败：{exc!r}")

    # ------------------------------------------------------------ 命令

    def _refresh(self) -> dict:
        """改变世界状态的命令收尾：重绘场景，让条件文本/条件选项立刻跟上。

        战斗中不重绘（那会走一遍 engine.render，把战斗面板挤乱）。
        """
        busy = self.combat is not None and not self.combat.combat_over
        return {"type": "noop"} if busy else {"type": "rerender"}

    def _on_command(self, line: str, choices: list):
        raw = line.strip()
        low = raw.lower()

        # 战斗中把战斗态的 HP 实时写回角色。
        # char.current_hp 在战斗全程停在战前值（只在战斗结束/逃跑时才同步），
        # 不同步的话战斗里 stats / save 读到的都是上一场结束时的血 —— 看着像凭空回血。
        if self.combat is not None and not self.combat.combat_over:
            self._sync_hp_back(self.combat.player_hp)

        if low in ("help", "?") and not (self.combat and not self.combat.combat_over):
            self.do_help()
            return {"type": "noop"}

        if low.startswith("check "):
            parts = raw.split()
            if len(parts) >= 3:
                skill, dc = parts[1], parts[2]
                self.ctx.state.set("allin.skill", skill)
                self.ctx.state.set("allin.dc", int(dc) if dc.isdigit() else 10)
                self._ev_check()
            else:
                # 少写 DC 时原来是静默 no-op：玩家敲完什么都没发生，很容易以为
                # 判断没生效。这里明确给用法。
                self.ctx.io.print("  用法：check <技能> <DC>，例如 check 侦察 11")
            return self._refresh()

        if low.startswith("use "):
            self.use(raw.split(None, 1)[1].strip())
            return self._refresh()

        if low.startswith("answer "):
            guess = raw.split(None, 1)[1].strip()
            if self.puzzle_active:
                pid = self.puzzle_active
                limit = next(
                    (p.get("attempts_allowed") for p in PUZZLES
                     if p.get("puzzle_id") == pid), None)
                tried = self.puzzle_attempts.get(pid, 0)
                # data.py 里三个谜题都写了 attempts_allowed: 3，但 allin 的
                # PuzzleSystem 根本不看这个字段 —— 不自己拦就能无限试。
                if limit and tried >= int(limit):
                    self.ctx.io.print("  终端锁了 —— 你已经试够了次数。")
                    return self._refresh()
                res = self.puzzles.verify_answer(pid, guess)
                self.ctx.io.print(f"  {res['message']}")
                if res["success"]:
                    self._grant_rewards(res.get("rewards"))
                    self.puzzle_active = None
                else:
                    if res.get("hint"):
                        self.ctx.io.print(f"  提示：{res['hint']}")
                    self.puzzle_attempts[pid] = tried + 1
                    if limit and self.puzzle_attempts[pid] >= int(limit):
                        self.ctx.io.print("  你试得太多次了，这东西不打算再给你机会。")
                return self._refresh()
            return None

        if low in ("recipes", "配方"):
            self.do_recipes()
            return {"type": "noop"}

        if low.startswith("craft "):
            self.craft(raw.split(None, 1)[1].strip())
            return self._refresh()

        if low in ("inv", "bag", "背包"):
            self.do_inv()
            return {"type": "noop"}

        if low in ("stats", "status", "char", "角色"):
            self.do_stats()
            return {"type": "noop"}

        if low == "save" or low.startswith("save "):
            return self.do_save(raw)
        # save 不带槽号默认存槽 1，load 却必须带 —— 不对称会让玩家很困惑。
        if low == "load" or low.startswith("load "):
            return self.do_load(raw)
        if low in ("slots", "存档"):
            for slot in self.saves.list_saves():
                self.ctx.io.print(f"    槽{slot.slot}  {slot.readable_time}")
            return {"type": "noop"}

        if self.combat is not None and not self.combat.combat_over:
            if low in ("rollback", "rb", "back"):
                # 回滚前必须清掉战斗态，否则会留下「幽灵战斗」：
                # dse 回到上一场景、照常列出选项，旧战斗却还活着，
                # 把玩家的场景选项输入劫持成出牌，两个选项都选不了。
                # 注意 engine.rollback() 全程不 emit，没有事件钩子可挂，只能在这里拦。
                # 三者一起清，和 do_load 对齐（只清 combat 会在 battle_pending
                # 场景留下同类的幽灵状态）。
                self.combat = None
                self.battle_pending = None
                self.puzzle_active = None
                self.combat_log = []
            if low in _PASSTHROUGH:
                return None
            return self.combat_input(raw)

        return None

    def do_inv(self) -> None:
        io = self.ctx.io
        items = self.inv.list_all()
        io.blank()
        if not items:
            io.print("  背包是空的，除了口袋两角的沙。")
        else:
            io.print("  背包：")
            for it in items:
                tag = f"  ×{it.quantity}" if it.quantity > 1 else ""
                io.print(f"    · {it.name}{tag}  ({it.item_id})")
        io.print(f"  已用格位 {self.inv.item_count}/{self.inv.capacity}")
        io.print("  输入 use <id> 使用，例如 use medkit")
        io.blank()

    def do_recipes(self) -> None:
        """列出配方：能做的标 ○，缺材料的标出缺什么。"""
        io = self.ctx.io
        io.blank()
        io.print("  ─── 配方 ───")
        for rid, spec in RECIPES.items():
            missing = [f"{ITEMS[i]['name']}×{n - self.inv.count(i)}"
                       for i, n in spec["needs"] if self.inv.count(i) < n]
            out_id, out_n = spec["yields"]
            mark = "○" if not missing else "×"
            io.print(f"  {mark} {rid}：{spec['name']} → {ITEMS[out_id]['name']} ×{out_n}")
            if missing:
                io.print(f"      缺 {'、'.join(missing)}")
        io.print("  输入 craft <配方名> 合成，例如 craft makeshift_kit")
        io.blank()

    def do_stats(self) -> None:
        io = self.ctx.io
        c = self.char
        io.blank()
        io.print("  ─── 角色 ───")
        io.print(f"  HP {int(c.current_hp or 0)}/{int(c.max_hp)}   AC {c.ac}   强韧 +{c.save_bonus('fort')}")
        attrs = "  ".join(f"{k} {c.attributes.get(k, 10)}" for k in Character.ATTRIBUTES)
        io.print(f"  属性：{attrs}")
        sk = sorted(c.skills.items(), key=lambda kv: -kv[1])[:6]
        io.print("  技能：" + "  ".join(f"{k} {v}" for k, v in sk) if sk else "  技能：无")
        io.print("  技术：" + ("、".join(t for t in TECHNIQUES if c.has_technique(t)) or "无"))
        io.print(f"  近战 +{c.attack_bonus()} · 远程 +{c.attack_bonus(False)}")
        self._emit_character_sheet()
        io.blank()

    def do_help(self) -> None:
        io = self.ctx.io
        io.blank()
        io.print("  ─── 命令 ───")
        io.print("  编号            选择当前列出的选项")
        io.print("  stats / inv     看角色卡 / 看背包")
        io.print("  check <技能> <DC>   掷一次判定，例：check 侦察 11")
        io.print("  answer <答案>   回答谜题，例：answer 4173")
        io.print("  use <id>        用东西，例：use medkit")
        io.print("  recipes / craft <配方>   看配方 / 合成，例：craft makeshift_kit")
        io.print("  save / load <槽> / slots     存档 / 读档 / 列出")
        io.print("  rollback [n]    回退 n 个选择点")
        io.print("  quit            退出")
        io.print("  ─── 战斗中 ───")
        io.print("  编号 出牌 · e 结束回合 · f 逃跑")
        io.blank()

    # ------------------------------------------------------------ 存档

    def do_save(self, raw: str) -> dict:
        parts = raw.split()
        slot = 1
        if len(parts) > 1 and parts[1].isdigit():
            slot = int(parts[1])
        # 战斗中存档：HP 已在 _on_command 开头同步过，但战斗态本身不进存档，
        # 读档会回到本场战斗第 1 回合 —— 先讲清楚，别让玩家以为存下了半场战斗。
        if self.combat is not None and not self.combat.combat_over:
            self.ctx.io.print("  [战斗中存档：读档后本场战斗会从头再来]")
        self.pull()
        payload = {
            "scene": self.ctx.engine.current,
            "dse": self.ctx.state.snapshot(),
            "allin": self.allin_snapshot(),
        }
        ok = self.saves.save(payload, slot)
        self.ctx.io.print("  已存。" if ok else "  存档失败。")
        return {"type": "noop"}

    def do_load(self, raw: str) -> dict:
        parts = raw.split()
        slot = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        data = self.saves.load(slot)
        if not data:
            self.ctx.io.print("  这个槽位是空的。")
            return {"type": "noop"}
        # 存档是明文 JSON：手改坏、被别的版本写过、写到一半断电，都会走到这里。
        # 原来直接 data["dse"] 会 KeyError 把整个游戏打退出（exit 1）。
        if not isinstance(data, dict) or not isinstance(data.get("dse"), dict):
            self.ctx.io.print("  存档损坏：读不出内容，换个槽位试试。")
            return {"type": "noop"}
        self.ctx.state.restore(data["dse"])
        self.restore_allin(data.get("allin") or {})
        target = data.get("scene")
        if not target:
            self.ctx.io.print("  存档损坏：没有场景。")
            return {"type": "noop"}
        self.combat = None
        self.puzzle_active = None
        self.ctx.io.print(f"  已读档 → {target}")
        return {"type": "scene", "id": target}

    def allin_snapshot(self) -> dict:
        try:
            inv_state = self.inv.to_dict()
        except Exception:  # noqa: BLE001
            inv_state = {}
        return {
            "hp": int(self.char.current_hp or 0),
            "attributes": dict(self.char.attributes),
            "skills": dict(self.char.skills),
            "techniques": [t for t in TECHNIQUES if self.char.has_technique(t)],
            "deck": [c.id for c in self.deck.cards],
            "inventory": inv_state,
            "solved": list(self.puzzles.all_solved_ids()),
            "codex": self.world.get_data("known_locales", 0) or 0,
        }

    def restore_allin(self, snap: dict) -> None:
        # attributes / skills 是「同一对象的原地 update」——本来就是对的，不要动。
        self.char.attributes.update(snap.get("attributes") or {})
        self.char.skills.update(snap.get("skills") or {})

        # 技术：权威存储是 char.techniques（== char.ctx.state.techniques，透明代理）。
        # 直接写 list，不用 purchase_technique() —— 它会重扣 CP，且查前置技能不满足时
        # 「静默失败不报错」，读档时若技术与技能的恢复顺序有任何错位就会静默丢技术。
        for t in snap.get("techniques") or []:
            if t not in TECHNIQUES:
                self.ctx.io.print(f"  [存档里有认不出的技术：{t}，已跳过]")
                continue
            if t not in self.char.techniques:
                self.char.techniques.append(t)

        # HP 钳到 [0, max_hp]：存档是明文 JSON，玩家能把 hp 写成任意整数（含负数）。
        # 钳制是「损坏检测」不是「防篡改」——手改存档是单机玩家的正常玩法，
        # 我们要做的只是别让坏数据把游戏搞崩。
        mx = max(1, int(self.char.max_hp))
        self.char.current_hp = max(0, min(mx, int(snap.get("hp") or 0)))

        # 卡组：从 card_library 取原型重建（get_card 返回副本，安全）
        self.deck = CardDeck()
        for cid in snap.get("deck") or []:
            card = self.card_library.get_card(cid) or CARD_AWARDS.get(cid)
            if card:
                self.deck.add(card)
        self.deck.shuffle()

        # 背包（Inventory.from_dict 是 classmethod，实测 roundtrip 通过）
        data = snap.get("inventory")
        try:
            restored = type(self.inv).from_dict(data) if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 - 结构异常退回空包
            restored = None
        self.inv.clear()
        if restored is not None:
            # 入口过滤：Inventory.from_dict() 对未知 id 零过滤，脏 id 会一路带到
            # use() 的 ITEMS[item_id]['name'] 上炸 KeyError。这里是唯一入口，堵在这。
            # 跳过 + 告警而不是拒绝整档 —— 内容改版删物品时，老存档会带
            # 「当时合法、现在未知」的 id，整档拒绝会直接报废合法老档。
            for item in restored.list_all():
                if item.item_id not in ITEMS:
                    self.ctx.io.print(f"  [存档里有认不出的东西：{item.item_id}，已跳过]")
                    continue
                self.inv.add(item)

        # PuzzleSystem 不随档走，把通关过的谜题回放一遍重建 solved 标记
        for pid in snap.get("solved") or []:
            answers = next((p["answer"] for p in PUZZLES if p["puzzle_id"] == pid), None)
            if answers:
                self.puzzles.reset_puzzle(pid)
                self.puzzles.verify_answer(pid, answers[0])
        self.pull()
        self.world.set_data("known_locales", snap.get("codex", 0))
        self.pull()

    def dispose(self) -> None:
        try:
            Path(self.ctx.root / "saves").mkdir(exist_ok=True)
        except OSError:
            pass
