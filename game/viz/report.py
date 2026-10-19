"""出图：战斗战报 / 角色卡。

两张图都走同一条管线：
    程序化材质（noise）→ 数据纹理（bake_*）→ 色带 LUT → 凹凸光照 → 混合
        → 透视贴图（paste_quad）→ 图层与文字
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from . import charts, datatex as dt, material
from .canvas import Canvas
from .noise import normalize

__all__ = ["battle_report", "character_sheet"]

INK = (0.93, 0.89, 0.81)
DIM = (0.60, 0.56, 0.50)
ACCENT = (0.90, 0.55, 0.22)
BLOOD = (0.82, 0.26, 0.22)
MINT = (0.33, 0.74, 0.66)


def _backdrop(w: int, h: int, seed: int = 0) -> np.ndarray:
    """底色：沙地材质 + 凹凸光照 + 暗角。"""
    mat = material.sand(w, h, seed=seed)
    lit = dt.bump_light(mat["height"], azimuth=125, elevation=38, strength=2.2)
    rgb = mat["albedo"] * lit[..., None]
    vig = dt.vignette(w, h, strength=0.72, power=2.0)[..., None]
    rgb = rgb * vig
    rgb *= dt.grain(w, h, 0.035, seed)[..., None]
    return np.clip(rgb, 0, 1).astype(np.float32)


def _panel(c: Canvas, x: int, y: int, w: int, h: int, seed: int = 3, tilt: float = 1.0) -> None:
    """把金属铭牌纹理透视贴到一块面板位置。

    tilt 控制左右两侧的纵向错位，做出一点「斜放铁牌」的透视感。
    """
    mat = material.plate(w + 40, h + 40, seed=seed)
    lit = dt.bump_light(mat["height"], azimuth=115, elevation=50, strength=1.6)
    tex = np.clip(mat["albedo"] * (0.55 + 0.65 * lit[..., None]), 0, 1)
    tex = tex * np.array([0.72, 0.70, 0.66], np.float32)

    d = tilt * 4.0
    quad = [
        (x + d, y),
        (x + w - d * 0.6, y),
        (x + w - d, y + h),
        (x + d * 0.6, y + h),
    ]
    c.paste_quad(tex, quad, mode="overlay", alpha=0.92)
    c.rect(x, y, w, h, color=(0.75, 0.55, 0.30), alpha=0.22, width=1, outline=True)


def battle_report(data: dict, path: str | Path, width: int = 1180, height: int = 820) -> Path:
    """生成一张战斗战报。

    data 需要包含（缺项会退化，不抛异常）：
        enemy, result, scene, turns, player_max_hp, enemy_max_hp,
        hp_series, enemy_series, cards {名字: 次数},
        damage_dealt, damage_taken, blocked, healed
    """
    W, H = width, height
    hp = [float(v) for v in data.get("hp_series") or [data.get("player_max_hp", 1)]]
    ehp = [float(v) for v in data.get("enemy_series") or [data.get("enemy_max_hp", 1)]]
    cards: dict = data.get("cards") or {}
    pmax = max(1.0, float(data.get("player_max_hp") or max(hp)))
    emax = max(1.0, float(data.get("enemy_max_hp") or max(ehp)))
    won = str(data.get("result", "")).lower() in ("win", "victory", "true", "1")

    c = Canvas(W, H)
    c.buf = _backdrop(W, H, seed=int(data.get("seed", 7)))

    # ── 数据纹理：把玩家血量曲线烘成热力场，铺在右侧当「余温」
    field = dt.bake_series([v / pmax for v in hp], int(W * 0.55), int(H * 0.62), sigma=5.0)
    rgb = dt.apply_lut(field, dt.lut("ember"))
    c.paste_rect(rgb, int(W * 0.44), int(H * 0.32), mode="screen", alpha=0.30)

    # ── 标题
    c.text(52, 46, "战 报", size=40, color=INK)
    c.text(56, 100, str(data.get("enemy") or "未知对手"), size=26, color=ACCENT)
    sub = f"{data.get('scene') or ''}　·　{data.get('turns', len(hp) - 1)} 回合　·　" \
          f"{'胜' if won else '败'}"
    c.text(56, 140, sub, size=15, color=DIM)
    c.line([(52, 172), (W - 52, 172)], color=(0.5, 0.42, 0.32), width=1, alpha=0.6)

    # ── 左侧：血量曲线
    c.text(56, 196, "生命曲线", size=17, color=INK)
    chart_box = (108, 250, 470, 250)
    charts.line_chart(
        c, chart_box,
        [("你", hp, BLOOD), ("敌方", ehp, MINT)],
        vmin=0.0, vmax=max(pmax, emax), label_size=13,
    )
    c.text(chart_box[0], chart_box[1] + chart_box[3] + 18, "回合 →", size=12, color=DIM)

    # ── 右侧：四个数字牌（透视贴图 + 数值）
    stats = [
        ("造成伤害", int(data.get("damage_dealt") or 0), ACCENT),
        ("承受伤害", int(data.get("damage_taken") or 0), BLOOD),
        ("获得格挡", int(data.get("blocked") or 0), MINT),
        ("治疗回复", int(data.get("healed") or 0), (0.55, 0.80, 0.42)),
    ]
    bx, by, bw, bh, gap = 640, 214, 232, 96, 16
    for i, (label, val, col) in enumerate(stats):
        px = bx + (i % 2) * (bw + gap)
        py = by + (i // 2) * (bh + gap)
        _panel(c, px, py, bw, bh, seed=i * 13 + 3)
        c.text(px + 18, py + 16, label, size=13, color=DIM)
        c.text(px + 18, py + 40, f"{val}", size=34, color=col)

    # ── 底部：卡牌使用分布
    c.text(56, 552, "出牌分布", size=17, color=INK)
    if cards:
        items = sorted(cards.items(), key=lambda kv: -kv[1])[:6]
        charts.bar_chart(c, (150, 590, 520, 150), items, color=ACCENT, label_size=13)
    else:
        c.text(150, 600, "（没有记录到出牌）", size=13, color=DIM)

    # ── 右下：胜败印章
    seal = "胜" if won else "败"
    sx, sy = 880, 600
    c.rect(sx - 62, sy - 62, 124, 124, color=(0.55, 0.18, 0.14) if won else (0.25, 0.25, 0.28),
           alpha=0.30, radius=8)
    c.rect(sx - 62, sy - 62, 124, 124, color=BLOOD if won else (0.45, 0.45, 0.5),
           alpha=0.85, width=3, radius=8, outline=True)
    c.text(sx, sy - 4, seal, size=64, color=BLOOD if won else (0.55, 0.55, 0.58), anchor="cc")

    # ── 页脚
    ts = data.get("time") or time.strftime("%Y-%m-%d %H:%M")
    c.text(56, H - 46, f"灰烬信标 · 战报　{ts}", size=12, color=(0.45, 0.42, 0.38))
    c.text(W - 56, H - 46, "dse-core 叙事 × allin-engine 规则", size=12,
           color=(0.45, 0.42, 0.38), anchor="rb")

    return c.save(path)


def character_sheet(
    data: dict,
    path: str | Path,
    width: int = 1000,
    height: int = 720,
) -> Path:
    """生成角色卡。

    data: name, hp, max_hp, attributes{名:值}, skills{名:级}, techniques[..],
          items[(名, 数量)], cards, locales
    """
    W, H = width, height
    attrs: dict = data.get("attributes") or {}
    skills: dict = data.get("skills") or {}
    techs: list = data.get("techniques") or []
    items: list = data.get("items") or []

    c = Canvas(W, H)
    base = material.rust(W, H, seed=21)
    lit = dt.bump_light(base["height"], azimuth=130, elevation=42, strength=2.6)
    c.buf = np.clip(base["albedo"] * lit[..., None] * dt.vignette(W, H, 0.75)[..., None], 0, 1)

    # 中央铭牌
    _panel(c, 40, 36, W - 80, H - 72, seed=9, tilt=2.0)

    c.text(78, 64, str(data.get("name") or "无名"), size=38, color=INK)
    c.text(80, 118, "拾荒者 · 灰烬信标", size=14, color=DIM)
    c.line([(78, 148), (W - 78, 148)], color=(0.55, 0.45, 0.32), width=1, alpha=0.55)

    # 血条
    hp, hmax = float(data.get("hp") or 0), max(1.0, float(data.get("max_hp") or 1))
    c.text(78, 168, f"HP {int(hp)} / {int(hmax)}", size=15, color=DIM)
    charts.meter(c, 78, 192, 360, 14, hp / hmax, color=BLOOD)
    charts.meter(c, 78, 214, 360, 14, min(1.0, len(items) / 20.0), color=ACCENT)

    # 雷达图
    order = ["力量", "敏捷", "体质", "智力", "感知", "魅力"]
    vals = [float(attrs.get(k, 10)) for k in order]
    vmax = max(20.0, max(vals) + 2)
    charts.radar_chart(c, (78, 250, 340, 300), order, vals, vmax=vmax, color=MINT)

    # 技能条
    c.text(460, 168, "技能", size=17, color=INK)
    top = sorted(skills.items(), key=lambda kv: -kv[1])[:7]
    charts.bar_chart(c, (540, 200, 380, 200), top, color=(0.72, 0.55, 0.28), label_size=13, max_value=12)

    # 技术
    c.text(460, 424, "技术", size=17, color=INK)
    tx, ty = 460, 456
    for t in techs[:6]:
        wpx = 30 + len(t) * 14
        c.rect(tx, ty, wpx, 26, color=(0.30, 0.34, 0.30), alpha=0.8, radius=13)
        c.text(tx + wpx / 2, ty + 13, t, size=12, color=INK, anchor="cc")
        tx += wpx + 10
        if tx > W - 220:
            tx, ty = 460, ty + 34

    # 背包
    c.text(460, 530, "随身", size=17, color=INK)
    iy = 562
    for name, qty in items[:6]:
        tag = f" ×{qty}" if qty and qty > 1 else ""
        c.text(460, iy, f"· {name}{tag}", size=13, color=(0.80, 0.76, 0.68))
        iy += 24

    # 左下角统计
    c.text(78, H - 118, f"卡组 {data.get('cards', 0)} 张　·　走过 {data.get('locales', 0)} 处",
           size=13, color=DIM)
    c.text(78, H - 76, time.strftime("%Y-%m-%d %H:%M"), size=12, color=(0.45, 0.42, 0.38))

    return c.save(path)
