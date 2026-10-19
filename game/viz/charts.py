"""图表：直接画到 Canvas 上的轻量绘图函数（不依赖 matplotlib）。

坐标约定：box = (x, y, w, h)，左上角原点，y 向下。
"""

from __future__ import annotations

import math

import numpy as np

from .canvas import Canvas

__all__ = ["grid", "line_chart", "bar_chart", "radar_chart", "meter"]

INK = (0.90, 0.86, 0.78)
DIM = (0.55, 0.52, 0.47)


def grid(c: Canvas, box, rows: int = 4, cols: int = 6, color=DIM, alpha: float = 0.18) -> None:
    x, y, w, h = box
    for i in range(rows + 1):
        yy = y + h * i / rows
        c.line([(x, yy), (x + w, yy)], color=color, width=1, alpha=alpha)
    for j in range(cols + 1):
        xx = x + w * j / cols
        c.line([(xx, y), (xx, y + h)], color=color, width=1, alpha=alpha)


def line_chart(
    c: Canvas,
    box,
    series: list[tuple[str, list[float], tuple[float, float, float]]],
    vmin: float | None = None,
    vmax: float | None = None,
    fill: bool = True,
    label_size: int = 13,
    ticks: int = 4,
) -> None:
    """多序列折线。series = [(名字, 数值列表, RGB 颜色)]。

    所有序列共用一个 Y 轴范围，适合对比「玩家血量 vs 敌人血量」。
    """
    x, y, w, h = box
    seqs = [(n, np.asarray(v, np.float32), col) for n, v, col in series if len(v) > 0]
    if not seqs:
        return

    allv = np.concatenate([s[1] for s in seqs])
    lo = float(allv.min()) if vmin is None else vmin
    hi = float(allv.max()) if vmax is None else vmax
    if hi - lo < 1e-6:
        hi = lo + 1.0

    grid(c, box, rows=ticks, cols=max(2, len(seqs[0][1]) - 1), color=DIM, alpha=0.15)

    # Y 轴刻度
    for i in range(ticks + 1):
        val = lo + (hi - lo) * (1 - i / ticks)
        yy = y + h * i / ticks
        c.text(x - 8, yy, f"{val:.0f}", size=label_size - 2, color=DIM, anchor="rc")

    for name, vals, col in seqs:
        n = len(vals)
        pts = []
        for i, v in enumerate(vals):
            px = x + (w * i / max(1, n - 1))
            py = y + h * (1 - (v - lo) / (hi - lo))
            pts.append((px, py))
        if fill and len(pts) > 1:
            poly = pts + [(pts[-1][0], y + h), (pts[0][0], y + h)]
            c._fill_polygon(poly, col, alpha=0.16)
        c.line(pts, color=col, width=2, alpha=0.95)
        for px, py in pts:
            c.rect(int(px) - 2, int(py) - 2, 4, 4, color=col, alpha=1.0, radius=2)

    # 图例
    lx = x
    for name, _, col in seqs:
        c.rect(lx, y - 22, 10, 10, color=col, radius=2)
        c.text(lx + 16, y - 22, name, size=label_size, color=INK)
        lx += 26 + len(name) * (label_size * 0.95)


def bar_chart(
    c: Canvas,
    box,
    items: list[tuple[str, float]],
    color: tuple[float, float, float] = (0.85, 0.45, 0.18),
    label_size: int = 13,
    max_value: float | None = None,
    show_value: bool = True,
) -> None:
    """横向条形图：左边标签，右边条。"""
    x, y, w, h = box
    if not items:
        return
    mx = max(v for _, v in items) if max_value is None else max_value
    mx = mx or 1.0

    row_h = h / len(items)
    bar_h = max(6, int(row_h * 0.52))
    label_w = min(int(w * 0.34), 140)
    track_x = x + label_w + 8

    for i, (name, val) in enumerate(items):
        cy = y + row_h * i + row_h / 2
        c.text(x, cy, name, size=label_size, color=INK, anchor="lc")
        c.rect(int(track_x), int(cy - bar_h / 2), int(w - label_w - 8), bar_h,
               color=(0.25, 0.24, 0.23), alpha=0.55, radius=3)
        bw = int((w - label_w - 8) * min(1.0, val / mx))
        if bw > 0:
            c.rect(int(track_x), int(cy - bar_h / 2), bw, bar_h, color=color, alpha=0.95, radius=3)
        if show_value:
            c.text(x + w, cy, f"{val:g}", size=label_size - 1, color=DIM, anchor="rc")


def radar_chart(
    c: Canvas,
    box,
    labels: list[str],
    values: list[float],
    vmax: float = 20.0,
    color: tuple[float, float, float] = (0.35, 0.75, 0.68),
    rings: int = 3,
    label_size: int = 13,
) -> None:
    """六维雷达：TRPG 属性一览。"""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    r = min(w, h) / 2 - 26
    n = len(labels)
    if n < 3:
        return

    def at(i: int, frac: float) -> tuple[float, float]:
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + math.cos(ang) * r * frac, cy + math.sin(ang) * r * frac

    for g in range(1, rings + 1):
        frac = g / rings
        poly = [at(i, frac) for i in range(n)]
        c.line(poly + [poly[0]], color=DIM, width=1, alpha=0.25)
    for i in range(n):
        c.line([(cx, cy), at(i, 1.0)], color=DIM, width=1, alpha=0.2)

    pts = [at(i, max(0.06, min(1.0, values[i] / vmax))) for i in range(n)]
    c._fill_polygon(pts, color, alpha=0.28)
    c.line(pts + [pts[0]], color=color, width=2, alpha=1.0)
    for px, py in pts:
        c.rect(int(px) - 3, int(py) - 3, 6, 6, color=color, radius=3)

    for i, lab in enumerate(labels):
        lx, ly = at(i, 1.16)
        anch = "lc" if abs(lx - cx) < r * 0.4 else ("rc" if lx < cx else "lc")
        c.text(lx, ly, lab, size=label_size, color=INK, anchor=anch)
        vx, vy = at(i, 1.16)
        c.text(vx, vy + label_size + 4, f"{values[i]:g}", size=label_size - 2, color=DIM, anchor=anch)


def meter(c: Canvas, x: int, y: int, w: int, h: int, frac: float,
          color: tuple[float, float, float] = (0.80, 0.28, 0.22),
          track=(0.22, 0.21, 0.20), radius: int | None = None) -> None:
    """单条进度条（血条/神经条）。"""
    radius = h // 2 if radius is None else radius
    c.rect(x, y, w, h, color=track, alpha=0.7, radius=radius)
    fw = int(w * max(0.0, min(1.0, frac)))
    if fw > 0:
        c.rect(x, y, fw, h, color=color, alpha=0.95, radius=radius)
