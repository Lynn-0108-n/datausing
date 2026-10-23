"""生成 FlowMask 桌面图标（exe、系统托盘、网页 favicon 共用）。

用法（在 ``server/`` 目录下）::

    python scripts/make_icon.py

产物：
- ``server/assets/flowmask.ico``：16/24/32/48/64/128/256 多尺寸（PyInstaller 的 icon=、
  Inno Setup 的 SetupIconFile 都用它）；
- ``server/assets/flowmask-128.png``：托盘/网页用的 128px PNG（PIL 读 ICO 也能读，
  但尺寸由 pystray 自己缩放，直接给 PNG 更稳）。

设计说明：深蓝渐变圆角底 + 白色盾牌 + 「FM」字标。盾牌在 16px 下仍是清晰剪影，
字标只在 ≥32px 时用得上，所以没有做复杂图形——小图标靠形状辨识，大图标靠字标。
脚本只依赖 Pillow（打包/构建环境本来就有），不引入新的设计工具。
"""

from __future__ import annotations

import struct
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SERVER_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = SERVER_DIR / "assets"

CANVAS = 1024
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
# 16/24px 下字标会糊成一团，改用「只有盾牌」的简化图，小尺寸靠剪影识别
NO_TEXT_SIZES = {16, 24}

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\segoeuib.ttf"),
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]

BG_TOP = (10, 20, 38)      # #0A1426
BG_BOTTOM = (26, 74, 143)  # #1A4A8F
SHIELD_FILL = (247, 251, 255)
SHIELD_EDGE = (176, 203, 240)
MONOGRAM = (13, 32, 58)    # #0D203A


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """两色线性插值（t=0 → a，t=1 → b）。"""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def _background() -> Image.Image:
    """垂直渐变 + 左上角柔光，再裁成圆角方块。"""
    gradient = Image.new("RGB", (CANVAS, CANVAS))
    draw = ImageDraw.Draw(gradient)
    for y in range(CANVAS):
        draw.line([(0, y), (CANVAS, y)], fill=_lerp(BG_TOP, BG_BOTTOM, y / CANVAS))
    # 左上角柔光，避免整块死板
    glow = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(glow).ellipse(
        [-CANVAS // 3, -CANVAS // 2, CANVAS, CANVAS // 2], fill=34
    )
    glow = glow.filter(ImageFilter.GaussianBlur(CANVAS // 5))
    gradient.paste(Image.new("RGB", (CANVAS, CANVAS), (120, 190, 255)), (0, 0), glow)

    mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, CANVAS - 1, CANVAS - 1], radius=int(CANVAS * 0.22), fill=255)
    rounded = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    rounded.paste(gradient, (0, 0), mask)
    return rounded


def _shield_points() -> list[tuple[float, float]]:
    """盾牌轮廓：上宽下尖，底边收成弧。坐标按 1024 画布比例。"""
    w, h = CANVAS, CANVAS
    return [
        (0.500 * w, 0.115 * h),
        (0.835 * w, 0.240 * h),
        (0.818 * w, 0.520 * h),
        (0.745 * w, 0.700 * h),
        (0.500 * w, 0.875 * h),
        (0.255 * w, 0.700 * h),
        (0.182 * w, 0.520 * h),
        (0.165 * w, 0.240 * h),
    ]


def _shield() -> Image.Image:
    """白色盾牌（带一点内阴影/描边，缩小后仍有立体感）。"""
    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon(
        [(x + 10, y + 18) for x, y in _shield_points()], fill=(0, 0, 0, 110)
    )
    layer = Image.alpha_composite(layer, shadow.filter(ImageFilter.GaussianBlur(18)))
    draw = ImageDraw.Draw(layer)
    draw.polygon(_shield_points(), fill=SHIELD_FILL + (255,), outline=SHIELD_EDGE + (255,), width=6)
    return layer


def _monogram() -> Image.Image:
    """「FM」字标：优先 Segoe UI Bold，退 Arial Bold，再退 DejaVu；取不到字体就不画。"""
    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    for font_path in FONT_CANDIDATES:
        if not font_path.is_file():
            continue
        font = ImageFont.truetype(str(font_path), int(CANVAS * 0.40))
        draw = ImageDraw.Draw(layer)
        text = "FM"
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=2)
        draw.text(
            ((CANVAS - (right - left)) / 2 - left, (CANVAS - (bottom - top)) / 2 - top - CANVAS * 0.01),
            text,
            font=font,
            fill=MONOGRAM + (255,),
            stroke_width=4,
            stroke_fill=(255, 255, 255, 255),
        )
        return layer
    print("[warn] 没找到可用字体，字标跳过（图标仍可用）", file=sys.stderr)
    return layer


def build(with_text: bool = True) -> Image.Image:
    icon = _background()
    icon = Image.alpha_composite(icon, _shield())
    if with_text:
        icon = Image.alpha_composite(icon, _monogram().filter(ImageFilter.GaussianBlur(0.4)))
    return icon


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_ico(path: Path, frames: list[tuple[int, bytes]]) -> None:
    """手工拼 ICO 容器：每个尺寸单独渲染，避免 Pillow 只能「一张图整体缩放」把字标压糊。"""
    header = struct.pack("<HHH", 0, 1, len(frames))
    entries = b""
    offset = 6 + 16 * len(frames)
    for size, data in frames:
        dim = 0 if size >= 256 else size  # ICO 目录里 256 记作 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(data for _, data in frames))


def main() -> int:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    with_text = build(with_text=True)
    no_text = build(with_text=False)
    png_path = ASSETS_DIR / "flowmask-128.png"
    with_text.resize((128, 128), Image.LANCZOS).save(png_path)

    frames: list[tuple[int, bytes]] = []
    for width, height in ICO_SIZES:
        source = no_text if width in NO_TEXT_SIZES else with_text
        frames.append((width, _png_bytes(source.resize((width, height), Image.LANCZOS))))
    ico_path = ASSETS_DIR / "flowmask.ico"
    _write_ico(ico_path, frames)
    print(f"写出 {ico_path}（{ico_path.stat().st_size} B，{len(frames)} 个尺寸）")
    print(f"写出 {png_path}（{png_path.stat().st_size} B）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
