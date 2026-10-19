"""数据纹理（data texture）管线的核心。

业界做法（GPU 热力图那一套）拆成三步：
    1. 把标量数据烘焙进一张单通道纹理       bake_*
    2. 用一条 1D 颜色梯度表（color ramp）把值映射成颜色   apply_lut
    3. 叠加材质与环境光，得到最终贴图       bump_light / blend

这里全部用 numpy 在 CPU 上做，输出 float32 的 [0,1] 数组，最后再转 uint8。
"""

from __future__ import annotations

import numpy as np

from .noise import normalize

__all__ = [
    "RAMPS", "lut", "apply_lut", "bake_series", "bake_bars",
    "bump_light", "blend", "vignette", "scanlines", "grain",
]


# 关键色标（值 0→1 对应的 RGB）。色带思路来自废土题材：灰烬、余烬、锈、冷光。
RAMPS: dict[str, list[tuple[float, str]]] = {
    "ember": [(0.0, "#0a0705"), (0.25, "#3d1408"), (0.52, "#a33b12"), (0.78, "#e88a2a"), (1.0, "#ffe3a3")],
    "ash":   [(0.0, "#0b0b0d"), (0.3, "#2e2c2b"), (0.6, "#6d6862"), (0.85, "#b8b0a4"), (1.0, "#f2ece0")],
    "rust":  [(0.0, "#160d08"), (0.3, "#4a2412"), (0.6, "#96501f"), (0.82, "#c9873f"), (1.0, "#e8c99a")],
    "ice":   [(0.0, "#050a12"), (0.3, "#0f2b45"), (0.6, "#2b6d94"), (0.85, "#7fc3d8"), (1.0, "#e6fbff")],
    "toxic": [(0.0, "#060a05"), (0.35, "#173310"), (0.65, "#4f7d20"), (0.88, "#a8d94a"), (1.0, "#f0ffc2")],
    "blood": [(0.0, "#0a0506"), (0.35, "#3a0d11"), (0.65, "#8d1d24"), (0.88, "#d4453f"), (1.0, "#ffc9b8")],
    "mono":  [(0.0, "#000000"), (1.0, "#ffffff")],
}


def _hex(c: str) -> np.ndarray:
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32) / 255.0


def lut(name: str = "ember", size: int = 256) -> np.ndarray:
    """把关键色标插值成 size×3 的查找表。"""
    stops = RAMPS.get(name) or RAMPS["ember"]
    pos = np.array([p for p, _ in stops], dtype=np.float32)
    cols = np.stack([_hex(c) for _, c in stops])

    t = np.linspace(0.0, 1.0, size, dtype=np.float32)
    out = np.empty((size, 3), dtype=np.float32)
    for ch in range(3):
        out[:, ch] = np.interp(t, pos, cols[:, ch])
    return out


def apply_lut(gray: np.ndarray, table: np.ndarray) -> np.ndarray:
    """用查找表把单通道值映射成 RGB。这就是「数据纹理 → 颜色」那一步。"""
    idx = np.clip(gray * (len(table) - 1), 0, len(table) - 1).astype(np.int32)
    return table[idx]


def _gauss_kernel1d(sigma: float, radius: int) -> np.ndarray:
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return k / k.sum()


def bake_series(
    series: list[float] | np.ndarray,
    width: int,
    height: int,
    sigma: float = 3.0,
    baseline: float = 0.06,
) -> np.ndarray:
    """把一维序列烘成二维场：X 轴=时间，Y 轴=数值，用高斯柱扩散出「热力」。

    返回 [H,W] 的 [0,1] 场，Y 向下增大（图像坐标），所以数值越高，亮点越靠上。
    """
    vals = np.asarray(series, dtype=np.float32)
    if vals.size == 0:
        return np.zeros((height, width), np.float32)
    if vals.size == 1:
        vals = np.repeat(vals, 2)

    vmax = float(np.max(np.abs(vals))) or 1.0
    vmin = float(np.min(vals))
    span = max(vmax - vmin, 1e-6)

    col_of = np.linspace(0, width - 1, vals.size)
    field = np.zeros((height, width), np.float32)

    rows = np.arange(height, dtype=np.float32)[:, None]
    for i, v in enumerate(vals):
        xn = float((v - vmin) / span)
        y_star = (1.0 - xn) * (height - 1)
        dist = np.abs(rows - y_star) / max(1.0, height * 0.06)
        field[:, int(round(col_of[i]))] += np.exp(-0.5 * dist[:, 0] ** 2).astype(np.float32)

    # 横向平滑，让柱体连成带
    k = _gauss_kernel1d(max(0.8, sigma), int(max(3, sigma * 2)))
    pad = len(k) // 2
    padded = np.pad(field, ((0, 0), (pad, pad)), mode="edge")
    smooth = np.zeros_like(field)
    for i, wgt in enumerate(k):
        smooth += wgt * padded[:, i:i + width]

    peak = float(smooth.max()) or 1.0
    smooth /= peak
    return np.clip(smooth + baseline, 0.0, 1.0).astype(np.float32)


def bake_bars(values: list[float], width: int, height: int, gap: float = 0.18) -> np.ndarray:
    """横向条形数据纹理：每根柱子一个值，用于技能/卡牌使用分布。"""
    vals = np.asarray(values, dtype=np.float32)
    n = max(1, vals.size)
    vmax = float(vals.max()) if vals.size else 1.0
    vmax = vmax or 1.0

    field = np.zeros((height, width), np.float32)
    slot = width / n
    inner = max(1, int(slot * (1.0 - gap)))
    for i, v in enumerate(vals):
        x0 = int(i * slot) + (int(slot) - inner) // 2
        h = int((v / vmax) * (height - 1))
        field[height - h:, max(0, x0):min(width, x0 + inner)] = 1.0
    return field


def bump_light(
    height: np.ndarray,
    azimuth: float = 135.0,
    elevation: float = 45.0,
    strength: float = 1.0,
) -> np.ndarray:
    """把高度图当凹凸贴图，用梯度算法线做朗伯光照。

    这一步是「贴图有立体感」和「只是像素」的分界线。
    """
    gy, gx = np.gradient(height.astype(np.float32))
    nx = -gx * strength
    ny = -gy * strength
    nz = np.ones_like(nx)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / norm, ny / norm, nz / norm

    az = np.deg2rad(azimuth)
    el = np.deg2rad(elevation)
    lx = np.cos(el) * np.cos(az)
    ly = np.cos(el) * np.sin(az)
    lz = np.sin(el)

    lam = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)
    return (0.35 + 0.65 * lam).astype(np.float32)


def blend(base: np.ndarray, over: np.ndarray, mode: str = "overlay", alpha: float = 1.0) -> np.ndarray:
    """图层混合。base/over 都是 [H,W,3] 的 [0,1]。"""
    a, b = base.astype(np.float32), over.astype(np.float32)
    if mode == "overlay":
        out = np.where(a < 0.5, 2 * a * b, 1 - 2 * (1 - a) * (1 - b))
    elif mode == "screen":
        out = 1 - (1 - a) * (1 - b)
    elif mode == "multiply":
        out = a * b
    elif mode == "add":
        out = a + b
    elif mode == "lighten":
        out = np.maximum(a, b)
    else:
        out = b
    out = a * (1 - alpha) + out * alpha
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def vignette(width: int, height: int, strength: float = 0.55, power: float = 2.2) -> np.ndarray:
    """暗角遮罩，值 0=全暗 1=不变。"""
    gy, gx = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    r = np.sqrt(((gx - cx) / cx) ** 2 + ((gy - cy) / cy) ** 2) / np.sqrt(2.0)
    return np.clip(1.0 - strength * (r / max(1e-6, np.max(r))) ** power, 0.0, 1.0).astype(np.float32)


def scanlines(height: int, width: int, period: int = 3, depth: float = 0.12) -> np.ndarray:
    """CRT 扫描线遮罩。"""
    rows = np.arange(height, dtype=np.float32)[:, None]
    m = 1.0 - depth * (np.sin(rows * (2 * np.pi / max(1, period))) * 0.5 + 0.5)
    return np.repeat(m, width, axis=1).astype(np.float32)


def grain(width: int, height: int, amount: float = 0.05, seed: int = 0) -> np.ndarray:
    """胶片颗粒遮罩（1 附近抖动）。"""
    rng = np.random.default_rng(seed)
    return (1.0 + rng.normal(0.0, amount, (height, width)).astype(np.float32))
