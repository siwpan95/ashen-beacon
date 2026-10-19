"""材质库：用噪声组合出废土质感的底图。

每个材质返回 dict：
    albedo  [H,W,3] 基色（0..1）
    height  [H,W]   高度图，交给 bump_light 做凹凸光照
"""

from __future__ import annotations

import numpy as np

from . import datatex as dt
from .noise import fbm, normalize, ridged, warp, worley

__all__ = ["sand", "rust", "concrete", "crt", "plate", "MATERIALS"]


def sand(width: int, height: int, seed: int = 0) -> dict:
    """风蚀沙地：大尺度起伏 + 细颗粒。"""
    base = warp(fbm(width, height, octaves=4, res=3, seed=seed), strength=width * 0.04, seed=seed)
    base = normalize(base)
    dunes = np.sin(base * 18.0 + fbm(width, height, octaves=2, res=2, seed=seed + 5) * 4.0)
    dunes = normalize(dunes, 0.0, 1.0)
    field = np.clip(base * 0.65 + dunes * 0.35, 0.0, 1.0)
    rgb = dt.apply_lut(field, dt.lut("rust"))
    # 暗角压低整体亮度，像黄昏
    rgb *= 0.82
    return {"albedo": rgb.astype(np.float32), "height": field.astype(np.float32)}


def rust(width: int, height: int, seed: int = 0) -> dict:
    """锈蚀铁皮：Worley 斑块 + 竖向流锈条纹。"""
    blotch = worley(width, height, cells=6, seed=seed)
    blotch = normalize(blotch, 0.0, 1.0)
    streak = fbm(width, height, octaves=3, res=3, seed=seed + 17)
    streak = normalize(streak)
    field = np.clip(blotch * 0.72 + streak * 0.28, 0.0, 1.0)
    rgb = dt.apply_lut(field, dt.lut("rust"))
    rgb *= 0.78
    return {"albedo": rgb.astype(np.float32), "height": field.astype(np.float32)}


def concrete(width: int, height: int, seed: int = 0) -> dict:
    """混凝土：低频斑驳 + 脊状裂纹。"""
    body = normalize(fbm(width, height, octaves=5, res=4, seed=seed))
    cracks = ridged(width, height, octaves=3, res=5, seed=seed + 31)
    cracks = np.clip((cracks - 0.55) * 3.0, 0.0, 1.0)
    field = np.clip(body * 0.85 - cracks * 0.5 + 0.1, 0.0, 1.0)
    rgb = dt.apply_lut(field, dt.lut("ash"))
    rgb *= 0.7
    return {"albedo": rgb.astype(np.float32), "height": (body - cracks * 0.6).astype(np.float32)}


def crt(width: int, height: int, seed: int = 0) -> dict:
    """老终端屏：暗绿磷光 + 横向扫描 + 轻微噪点。"""
    glow = normalize(fbm(width, height, octaves=3, res=2, seed=seed))
    rgb = dt.apply_lut(glow, dt.lut("toxic"))
    rgb *= 0.45
    lines = dt.scanlines(height, width, period=3, depth=0.35)[..., None]
    rgb = rgb * lines
    rgb *= dt.grain(width, height, 0.06, seed)[..., None]
    return {"albedo": np.clip(rgb, 0, 1).astype(np.float32), "height": glow.astype(np.float32)}


def plate(width: int, height: int, seed: int = 0) -> dict:
    """金属铭牌：横向拉丝 + 边缘压暗，适合当面板底。"""
    brush = fbm(width, height, octaves=2, res=2, seed=seed)
    # 横向拉伸：只在 Y 方向高频
    rows = np.linspace(0, 60, height, dtype=np.float32)[:, None]
    brush = normalize(brush + 0.35 * np.sin(rows + brush * 6.0))
    field = normalize(brush, 0.15, 0.85)

    edge_x = np.minimum(np.arange(width), width - 1 - np.arange(width)) / max(1, width * 0.08)
    edge_y = np.minimum(np.arange(height), height - 1 - np.arange(height)) / max(1, height * 0.12)
    edge = np.clip(np.minimum(edge_x[None, :], edge_y[:, None]), 0.0, 1.0)
    field = np.clip(field * (0.45 + 0.55 * edge), 0.0, 1.0)

    rgb = dt.apply_lut(field, dt.lut("ash"))
    rgb = rgb * 0.55 + np.array([0.06, 0.065, 0.07], dtype=np.float32)
    return {"albedo": np.clip(rgb, 0, 1).astype(np.float32), "height": field.astype(np.float32)}


MATERIALS = {
    "sand": sand,
    "rust": rust,
    "concrete": concrete,
    "crt": crt,
    "plate": plate,
}
