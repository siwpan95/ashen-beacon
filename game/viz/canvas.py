"""画布：numpy 做像素合成，Pillow 做文字与矢量形状。

关键能力是 paste_quad()：把一张纹理按单应变换贴到一个四边形上，
也就是真正意义上的「纹理贴图」（不是把图摆上去，而是 UV 映射 + 透视）。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import datatex as dt

__all__ = ["Canvas", "find_font"]

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",   # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",   # 黑体
    r"C:\Windows\Fonts\simsun.ttc",   # 宋体
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
_MONO_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | None] = {}


def find_font(size: int = 16, mono: bool = False) -> ImageFont.FreeTypeFont | None:
    """找一款能显示中文的字体；找不到就退回 Pillow 内置位图字体。"""
    key = ("mono" if mono else "sans", size)
    if key in _font_cache:
        return _font_cache[key]

    for path in _MONO_CANDIDATES if mono else _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _font_cache[key] = f
                return f
            except OSError:
                continue
    try:
        f = ImageFont.load_default(size)
    except TypeError:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> np.ndarray:
    """求 dst → src 的单应矩阵（PIL 的 PERSPECTIVE 需要反向映射）。"""
    A, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    try:
        coef = np.linalg.solve(np.array(A, np.float64), np.array(b, np.float64))
    except np.linalg.LinAlgError:
        coef = np.linalg.lstsq(np.array(A, np.float64), np.array(b, np.float64), rcond=None)[0]
    return coef


class Canvas:
    """一张 float32 的 RGB 画布，值域 [0,1]。"""

    def __init__(self, width: int, height: int, fill: tuple[float, float, float] = (0, 0, 0)):
        self.w, self.h = int(width), int(height)
        self.buf = np.zeros((self.h, self.w, 3), np.float32)
        self.buf[:] = np.array(fill, np.float32)

    # ---------------------------------------------------------------- 贴图

    def fill(self, rgb) -> None:
        self.buf[:] = np.asarray(rgb, np.float32)

    def paste_rect(self, rgb: np.ndarray, x: int, y: int, mode: str = "overlay", alpha: float = 1.0) -> None:
        """矩形贴图（按区域裁切，越界部分忽略）。"""
        src = np.asarray(rgb, np.float32)
        if src.ndim == 2:
            src = np.repeat(src[..., None], 3, axis=2)
        sh, sw = src.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + sw), min(self.h, y + sh)
        if x0 >= x1 or y0 >= y1:
            return
        sub = src[y0 - y:y1 - y, x0 - x:x1 - x]
        dst = self.buf[y0:y1, x0:x1]
        self.buf[y0:y1, x0:x1] = dt.blend(dst, sub, mode, alpha)

    def paste_quad(
        self,
        rgb: np.ndarray,
        quad: list[tuple[float, float]],
        mode: str = "overlay",
        alpha: float = 1.0,
    ) -> None:
        """把纹理按 UV 贴到任意四边形（仿射/透视均可）。

        quad 是目标四边形，顺序为 左上、右上、右下、左下。
        内部用单应变换做逐像素反向采样，所以能贴出倾斜的铁牌、俯视的地面。
        """
        src = np.asarray(rgb, np.float32)
        if src.ndim == 2:
            src = np.repeat(src[..., None], 3, axis=2)
        sh, sw = src.shape[:2]

        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, x1 = int(np.floor(min(xs))), int(np.ceil(max(xs)))
        y0, y1 = int(np.floor(min(ys))), int(np.ceil(max(ys)))
        x0c, y0c = max(0, x0), max(0, y0)
        x1c, y1c = min(self.w, x1), min(self.h, y1)
        if x0c >= x1c or y0c >= y1c:
            return

        # 局部坐标下求 dst→src 单应
        local = [(p[0] - x0, p[1] - y0) for p in quad]
        src_quad = [(0.0, 0.0), (float(sw), 0.0), (float(sw), float(sh)), (0.0, float(sh))]
        coef = _homography(src_quad, local)

        size = (x1 - x0, y1 - y0)
        pil = Image.fromarray(_to_u8(src), "RGB")
        warped = pil.transform(size, Image.PERSPECTIVE, tuple(coef), Image.BILINEAR)

        sub = np.asarray(warped, np.float32)[y0c - y0:y1c - y0, x0c - x0:x1c - x0] / 255.0
        dst = self.buf[y0c:y1c, x0c:x1c]
        self.buf[y0c:y1c, x0c:x1c] = dt.blend(dst, sub, mode, alpha)

    # ---------------------------------------------------------------- 形状

    def _fill_polygon(self, pts: list[tuple[float, float]], color=(1, 1, 1), alpha: float = 1.0) -> None:
        """填充多边形（折线图面积、雷达图都用它）。"""
        if len(pts) < 3:
            return
        col = tuple(int(c * 255) for c in color) + (int(255 * alpha),)
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon([(float(a), float(b)) for a, b in pts], fill=col)
        self._composite(layer)

    def rect(self, x: int, y: int, w: int, h: int, color=(1, 1, 1), alpha: float = 1.0,
             radius: int = 0, width: int = 1, outline=False) -> None:
        col = tuple(int(c * 255) for c in color)
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        box = [x, y, x + w - 1, y + h - 1]
        if radius > 0:
            d.rounded_rectangle(box, radius=radius, fill=None if outline else col + (int(255 * alpha),),
                                outline=col + (int(255 * alpha),) if outline else None, width=width)
        else:
            if outline:
                d.rectangle(box, outline=col + (int(255 * alpha),), width=width)
            else:
                d.rectangle(box, fill=col + (int(255 * alpha),))
        self._composite(layer)

    def line(self, pts: list[tuple[float, float]], color=(1, 1, 1), width: int = 2,
             alpha: float = 1.0) -> None:
        col = tuple(int(c * 255) for c in color)
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.line([(float(a), float(b)) for a, b in pts], fill=col + (int(255 * alpha),),
               width=width, joint="curve")
        self._composite(layer)

    def _composite(self, layer: Image.Image) -> None:
        arr = np.asarray(layer, np.float32) / 255.0
        a = arr[..., 3:4]
        self.buf[:] = np.clip(self.buf * (1 - a) + arr[..., :3] * a, 0.0, 1.0)

    # ---------------------------------------------------------------- 文字

    def text(self, x: float, y: float, s: str, size: int = 16, color=(1, 1, 1),
             alpha: float = 1.0, mono: bool = False, anchor: str = "lt", glow: float = 0.0) -> None:
        font = find_font(size, mono)
        if font is None:
            return
        bbox = font.getbbox(s)
        tw, th = max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])
        pad = 4 + int(glow * 6)

        layer = Image.new("L", (tw + pad * 2, th + pad * 2), 0)
        d = ImageDraw.Draw(layer)
        d.text((pad - bbox[0], pad - bbox[1]), s, font=font, fill=255)

        if glow > 0:
            from PIL import ImageFilter

            layer = layer.filter(ImageFilter.GaussianBlur(glow * 4))

        arr = np.asarray(layer, np.float32) / 255.0 * alpha
        if anchor[0] == "c":
            px = int(x - tw / 2) - pad
        elif anchor[0] == "r":
            px = int(x - tw) - pad
        else:
            px = int(x) - pad
        if anchor[1] == "c":
            py = int(y - th / 2) - pad
        elif anchor[1] == "b":
            py = int(y - th) - pad
        else:
            py = int(y) - pad

        self._alpha_paste(px, py, np.repeat(arr[..., None], 3, axis=2), color)

    def _alpha_paste(self, x: int, y: int, a: np.ndarray, color) -> None:
        ah, aw = a.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + aw), min(self.h, y + ah)
        if x0 >= x1 or y0 >= y1:
            return
        sub = a[y0 - y:y1 - y, x0 - x:x1 - x]
        col = np.asarray(color, np.float32)
        self.buf[y0:y1, x0:x1] = np.clip(
            self.buf[y0:y1, x0:x1] * (1 - sub) + col * sub, 0.0, 1.0
        )

    # ---------------------------------------------------------------- 输出

    def image(self) -> Image.Image:
        return Image.fromarray(_to_u8(self.buf), "RGB")

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.image().save(p)
        return p


def _to_u8(arr: np.ndarray) -> np.ndarray:
    return (np.clip(arr, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
