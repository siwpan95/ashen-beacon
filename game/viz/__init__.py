"""viz —— 程序化纹理 + 数据可视化。

依赖 numpy / Pillow。游戏本体不依赖本包：装不上时 AVAILABLE=False，
bridge 会跳过所有出图逻辑，游戏照常跑。

管线：
    noise   → 材质高度/基色
    datatex → 数据烘焙成纹理、色带映射、凹凸光照、混合
    canvas  → 画布、UV 透视贴图、中文文字
    charts  → 折线/条形/雷达
    report  → 战报、角色卡
"""

from __future__ import annotations

__all__ = [
    "AVAILABLE", "Canvas", "charts", "datatex", "material", "noise", "report",
]

try:
    import numpy as _np  # noqa: F401
    from PIL import Image as _Image  # noqa: F401

    AVAILABLE = True
except (ImportError, OSError):  # pragma: no cover - 没装依赖 / 装坏了都走这里
    # OSError 也要接：numpy、Pillow 半损坏时（DLL 缺失等）抛的是 OSError 而非
    # ImportError，只接 ImportError 会让「出图坏了」升级成「游戏起不来」。
    AVAILABLE = False

if AVAILABLE:
    from . import charts, datatex, material, noise, report
    from .canvas import Canvas
else:  # 占位，避免 from viz import charts 直接炸
    charts = datatex = material = noise = report = None  # type: ignore
    Canvas = None  # type: ignore
