# 灰烬信标 · Ashen Beacon

一款终端文字冒险游戏。叙事层用 **dse-core**，规则层用 **allin-engine**，
两者通过一个桥接插件连通。

大崩塌之后的废土。有人说地平线尽头那座塔还在响。
你带着一张残缺的地图和两卷绷带往北走。

## 跑起来

```bash
pip install dse-core allin-engine
pip install numpy pillow        # 可选：装了才有战报/角色卡出图
cd game
python play.py                  # 开始玩
python play.py --check           # 只做内容体检（悬空跳转 / 结局可达）
python play.py --script "1,1"    # 非交互跑一串输入
python play.py --entry library-hill    # 从任意场景开始（调试用）
python -m viz.demo               # 不跑游戏，直接出两张示例图
```

## 玩法

游戏里的每一种操作都来自两个引擎中某一个：

| 输入 | 来源 |
|---|---|
| `数字` 选选项 | dse-core 的场景图 |
| `stats` 角色卡 | allin-engine 的 TRPG 内核 |
| `inv` / `use medkit` | allin-engine 的 Inventory |
| `check 侦察 11` 掷骰 | allin-engine 的 d20 判定 |
| `answer 4173` 解谜 | allin-engine 的 PuzzleSystem |
| 卡牌战斗（出牌 / `e` / `f`） | allin-engine 的 CombatEngine |
| `save` / `load` / `slots` | allin-engine 的 SaveLoadManager 存盘 + dse 的 state 快照 |
| `rollback [n]` 回退 | dse-core 的历史快照 |

## 双引擎怎么分工

```
        ┌──────────────────────── dse-core ────────────────────────┐
        │  场景图(YAML) · 条件文本 · 选项/once/隧道 · 结局 · 成就  │
        └───────────────┬──────────────────────┬───────────────────┘
                        │ scene/mode 接管       │ emit / command 拦截
                   ┌────▼──────────────────────▼─────┐
                   │      bridge.py  (AllinBridge)    │  ← 唯一的双向通道
                   └────┬──────────────────────┬─────┘
        ┌───────────────▼──────────────────────▼───────────────────┐
        │              allin-engine                                │
        │  TRPG Character · CardCombat · Inventory · PuzzleSystem  │
        │  SaveLoadManager · ECS World · EventBus                  │
        └──────────────────────────────────────────────────────────┘
```

- **dse-core** 负责"故事在哪一幕"：YAML 里的场景、分支、`@restart`、`{call:}` 隧道。
- **allin-engine** 负责"这一手能不能成"：血量、技能等级、战斗、背包、谜题。
- **bridge.py** 是唯一知道两边存在的代码。

### 数据怎么从 allin 流到 dse

dse 的条件表达式命名空间**只有 `state` 的顶层键**，没有任何注册外部函数的钩子。
所以桥的做法是：`pull()` 把 allin 的派生值整体刷进 `state["allin"]`——

```yaml
# YAML 里可以直接写：
text: '你还剩 {allin.hp} 点力气。'
choices:
  - text: 买绷带（3 废铁）
    if: 'allin.item.scrap >= 3'      # ← 读的就是 pull() 写进去的值
```

`pull()` 会把**所有可能出现的数据键都补上缺省值**（物品 0、技术 False、谜题 False），
否则条件表达式引用未定义名字时每次求值都会告警刷屏。

### dse → allin 走事件

内容是纯数据，不能直接调 Python。所以统一用 `emit` + 命名约定：

```yaml
onEnter: { emit: 'allin/check:侦察:11' }        # 技能判定
do:      { emit: 'allin/gain:medkit' }          # 拿东西
do:      { emit: 'allin/pay:scrap:3' }          # 花东西（不能全扣光）
do:      { emit: 'allin/learn:火焰箭' }         # 学技术
do:      { emit: 'allin/train:活力' }           # 技能升级
```

## 踩过的坑（都修在代码里了，注释上有标注）

1. **`scene/mode` 在渲染之前触发。** 战斗不能在这里同步跑完，否则玩家先看完整场战斗才看到场景文本。
   做法是这里只返回 `True` 标记接管，真正的 `begin_battle()` 挂在 `scene/rendered` 上。

2. **dse 的 effect 语义是「数值 + 原值 = 累加」。** 一度把 DC 写成 `'allin.dc': 13`，
   结果第一次 13、第二次叠成 23、第三次 35。现在把技能和 DC 直接编码进事件名（`allin/check:手上功夫:12`）。

3. **条件表达式不认连字符。** `visits.road-north` 会被解析成 `road - north` 减法。
   要计数就自己写计数器（本作用 `roadTrips`）。

4. **`Inventory` 的 `max_stack` 默认 1。** 货币类物品（废铁）锁成 1 的话，
   `inv.add()` 直接报「背包已满」，整个交易系统失效。

5. **`@restart` 只重置 dse 的 state。** allin 的角色不会跟着回血，
   不挂 `allin/rebirth` 钩子的话，重开时会带着 0 HP 必死。

6. **allin 卡牌战斗里敌人的 `armor` 不生效**——`CombatState.play_card` 直接减 `hp`，
   绕过 `EnemyInstance.take_damage()`。所以配表里 armor 全部设 0，免得界面和实算对不上。

7. **中文宽字符会把 ASCII 画的边框挤歪。** 用 `unicodedata.east_asian_width` 按显示列数补齐。

8. **技能名必须命中 allin 的 `SKILL_LIBRARY`。** 写"巧手/威吓/修理"会返回「未知技能」，
   真名是「手上功夫/恐吓/调查」。

## 难度实测

用贪心策略模拟 200~300 场（玩家 30 HP / 8 张初始卡组）：

| 对手 | 玩家满血胜率 | 平均回合 | 备注 |
|---|---|---|---|
| 瘸腿变异犬 | 100% | 2.0 | 新手战 |
| 掠夺者·拾荒客 | 100% | 3.0 | 掉约 9 HP |
| 守望者 RM-07 | **63%** | 5.5 | 残血进场几乎必输——先休整 |

Boss 是真的会输的，别带着 15 点血往上撞。

## 纹理贴图出图（数据可视化）

装上 `numpy pillow` 后，游戏会自动把对局数据烘成图片（没装就静默关闭，游戏照常跑）：

- **每场战斗结束** → `game/out/battle_<时间戳>.png`：血量曲线、四项结算数字牌、出牌分布、胜败印章；
- **`stats` 命令** → `game/out/character_sheet.png`：属性雷达、技能条、技术标签、背包。

![战报示例](docs/demo_battle.png)
![角色卡示例](docs/demo_sheet.png)

整条管线是手工实现的纹理贴图（`game/viz/`，无 matplotlib、无第三方绘图库）：

```
noise     Perlin / fBm / 域扭曲 / Worley   ← 程序化材质的来源
datatex   数据烘焙成单通道纹理 → 1D 色带 LUT 上色 → 梯度法线凹凸光照 → 图层混合
canvas    numpy 像素合成 + Pillow 文字；paste_quad() 单应变换 UV 贴图
charts    折线 / 横向条形 / 雷达 / 进度条（直接画在 Canvas 上）
report    战报(1180×820) 与角色卡(1000×720) 的排版
```

其中 `paste_quad()` 是真正的纹理映射：求 dst→src 的 8 自由度单应矩阵，
用 `PIL.Image.transform(PERSPECTIVE)` 逐像素反向采样——所以战报右侧的数字牌是
「斜放在沙地上的铁牌」，而不是一张贴上去的矩形图。

### 游戏数据怎么变成图

`bridge.py` 在战斗中做遥测（`_telem_mark` / `_telem_take`，动作前后快照差分）：
出牌次数、双方血量序列、造成/承受伤害、获得格挡、治疗。战斗结束把整包数据交给
`report.battle_report()`。`stats` 则直接从 TRPG 角色 + 背包 + 卡组现读。

## 文件

```
game/
├── play.py              启动器（--check / --script / --entry）
├── dse.yml              插件清单：哪些 dse 插件 + 挂哪个桥
├── bridge.py            ★ 双引擎桥接（dse Service 插件）
├── data.py              物品 / 敌人 / 卡组 / 谜题 / 开局配点
├── content/beacon.yml   全部场景文本（改剧情只动这里）
├── viz/                 ★ 纹理贴图出图（noise/datatex/canvas/charts/report）
├── out/                 战报与角色卡（运行时生成）
└── saves/               存档（运行时生成）
docs/                   示例图
```

## 改自己的游戏

- **改剧情**：只动 `content/beacon.yml`，纯文本，无需改 Python。
- **改数值 / 加敌人 / 加物品 / 加卡牌**：只动 `data.py`。
- **加新事件**：在 `bridge.py` 的 `_route_content_events()` 里加一个前缀分支
  （它会扫描全部场景的 `emit` 名，自动注册 handler）。

新增鉴定时注意：`emit` 不携带参数，参数只能"写进 state 再 emit"或者直接编码在事件名里——
推荐后者，没有顺序依赖。

## 许可证

MIT © 2026 siwpan。见 [LICENSE](LICENSE)。

依赖的两个引擎同为 MIT：[dse-core](https://pypi.org/project/dse-core/) · [allin-engine](https://pypi.org/project/allin-engine/)。
