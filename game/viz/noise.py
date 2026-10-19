"""程序化噪声：Perlin / fBm / 域扭曲（纯 numpy，无第三方 noise 依赖）。

这是「纹理」的来源。所有函数返回 float32 数组，值域大致 [-1,1] 或已归一化到 [0,1]，
方便后面直接当高度图/遮罩/数据纹理用。
"""

from __future__ import annotations

import numpy as np

__all__ = ["perlin", "fbm", "warp", "ridged", "worley", "sample_bilinear", "normalize"]


def _fade(t: np.ndarray) -> np.ndarray:
    """Perlin 的 6t^5-15t^4+10t^3 平滑曲线，比 linear 更自然。"""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _grad_field(res: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """在 res×res 的格点上放随机单位梯度向量。"""
    rng = np.random.default_rng(seed)
    ang = rng.uniform(0.0, 2.0 * np.pi, (res, res)).astype(np.float32)
    return np.cos(ang), np.sin(ang)


def perlin(width: int, height: int, res: int = 4, seed: int = 0) -> np.ndarray:
    """二维 Perlin 梯度噪声。res 越大特征越密。

    实现遵循经典流程：格点随机梯度 → 四角点积 → fade 插值。
    索引对 res 取模，所以天然可平铺。
    """
    res = max(1, int(res))
    gx, gy = _grad_field(res, seed)
    xs = np.linspace(0.0, res, width, endpoint=False, dtype=np.float32)
    ys = np.linspace(0.0, res, height, endpoint=False, dtype=np.float32)
    X, Y = np.meshgrid(xs, ys)

    x0 = np.floor(X).astype(np.int32)
    y0 = np.floor(Y).astype(np.int32)
    fx = (X - x0).astype(np.float32)
    fy = (Y - y0).astype(np.float32)

    def dot(ix: np.ndarray, iy: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
        return gx[iy % res, ix % res] * dx + gy[iy % res, ix % res] * dy

    n00 = dot(x0, y0, fx, fy)
    n10 = dot(x0 + 1, y0, fx - 1.0, fy)
    n01 = dot(x0, y0 + 1, fx, fy - 1.0)
    n11 = dot(x0 + 1, y0 + 1, fx - 1.0, fy - 1.0)

    u = _fade(fx)
    v = _fade(fy)
    nx0 = n00 * (1.0 - u) + n10 * u
    nx1 = n01 * (1.0 - u) + n11 * u
    return (nx0 * (1.0 - v) + nx1 * v).astype(np.float32)


def fbm(
    width: int,
    height: int,
    octaves: int = 5,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
    res: int = 4,
    seed: int = 0,
) -> np.ndarray:
    """分形布朗运动：多层不同频率的 Perlin 叠加，做出自然的多尺度细节。

    persistence 控制每层振幅衰减，lacunarity 控制频率增长。
    输出按振幅总和归一化，值域约 [-1,1]。
    """
    total = np.zeros((height, width), np.float32)
    amp = 1.0
    norm = 0.0
    r = res
    for o in range(max(1, octaves)):
        total += amp * perlin(width, height, max(1, r), seed + o * 7919)
        norm += amp
        amp *= persistence
        r = max(1, int(round(r * lacunarity)))
    return (total / max(norm, 1e-6)).astype(np.float32)


def sample_bilinear(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """双线性采样，坐标按边界钳制（域扭曲用）。"""
    h, w = img.shape[:2]
    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    fx = (xs - x0).astype(np.float32)
    fy = (ys - y0).astype(np.float32)
    x0 = np.clip(x0, 0, w - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y0 = np.clip(y0, 0, h - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)

    if img.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]

    v00 = img[y0, x0]
    v10 = img[y0, x1]
    v01 = img[y1, x0]
    v11 = img[y1, x1]
    return ((v00 * (1 - fx) + v10 * fx) * (1 - fy) + (v01 * (1 - fx) + v11 * fx) * fy).astype(
        np.float32
    )


def warp(
    base: np.ndarray,
    strength: float = 16.0,
    scale: int = 3,
    seed: int = 0,
) -> np.ndarray:
    """域扭曲：用另一层噪声扰动采样坐标，把规整的图案揉成有机形状。

    这是让「云/烟/锈斑」不像程序图的关键一步。
    """
    h, w = base.shape
    wx = fbm(w, h, octaves=3, res=scale, seed=seed + 11) * strength
    wy = fbm(w, h, octaves=3, res=scale, seed=seed + 23) * strength
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
    return sample_bilinear(base, gx + wx, gy + wy)


def ridged(width: int, height: int, octaves: int = 4, res: int = 4, seed: int = 0) -> np.ndarray:
    """脊状噪声：1-|noise| 的叠加，产生裂纹/山脊一样的锐利结构。"""
    total = np.zeros((height, width), np.float32)
    amp = 1.0
    norm = 0.0
    r = res
    for o in range(max(1, octaves)):
        n = 1.0 - np.abs(perlin(width, height, max(1, r), seed + o * 131))
        total += amp * n * n
        norm += amp
        amp *= 0.5
        r *= 2
    out = total / max(norm, 1e-6)
    return normalize(out)


def worley(width: int, height: int, cells: int = 8, seed: int = 0) -> np.ndarray:
    """Worley/Voronoi 噪声：到最近特征点的距离。用于碎石、锈斑、细胞状纹理。"""
    rng = np.random.default_rng(seed)
    pts = rng.uniform(0.0, 1.0, (cells * cells, 2)).astype(np.float32)
    gy, gx = np.mgrid[0:height, 0:width].astype(np.float32)
    px = (gx / max(1, width - 1))[:, :, None]
    py = (gy / max(1, height - 1))[:, :, None]

    d = np.sqrt((px - pts[:, 0]) ** 2 + (py - pts[:, 1]) ** 2)
    near = np.partition(d, 1, axis=2)[:, :, :2]
    f1, f2 = near[:, :, 0], near[:, :, 1]
    # 用 f2-f1 强调边界，比单纯 f1 更像龟裂
    return normalize(f2 - f1)


def normalize(a: np.ndarray, lo: float = 0.0, hi: float = 1.0) -> np.ndarray:
    """线性拉伸到 [lo,hi]。全平输入退回中值，避免除零。"""
    amin, amax = float(a.min()), float(a.max())
    if amax - amin < 1e-8:
        return np.full_like(a, (lo + hi) * 0.5, dtype=np.float32)
    return (lo + (a - amin) / (amax - amin) * (hi - lo)).astype(np.float32)
