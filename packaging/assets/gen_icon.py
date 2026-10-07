"""Generate NetX Windows .ico from brand mark (matches web/public/favicon.svg)."""
from __future__ import annotations

import os
from PIL import Image, ImageDraw

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
BG = (15, 39, 68, 255)
LINE = (126, 182, 232, 255)
NODE = (232, 242, 252, 255)
ACCENT = (61, 130, 247, 255)


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = max(2, int(size * 0.22))
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)
    s = size / 32.0

    def xy(x: float, y: float) -> tuple[float, float]:
        return (x * s, y * s)

    stroke = max(2, int(round(2.2 * s)))
    for a, b in (
        (xy(9.5, 8.5), xy(9.5, 23.5)),
        (xy(9.5, 8.5), xy(22.5, 23.5)),
        (xy(22.5, 8.5), xy(22.5, 23.5)),
    ):
        d.line([a, b], fill=LINE, width=stroke)

    def circle(cx: float, cy: float, rad: float, fill: tuple[int, int, int, int]) -> None:
        x, y = xy(cx, cy)
        rr = rad * s
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=fill)

    circle(9.5, 8.5, 2.35, NODE)
    circle(9.5, 23.5, 2.35, NODE)
    circle(22.5, 8.5, 2.35, NODE)
    circle(22.5, 23.5, 2.35, NODE)
    circle(16, 16, 1.7, ACCENT)
    return img


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [make(s) for s in sizes]
    ico_path = os.path.join(OUT_DIR, "netx.ico")
    images[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])
    images[-1].save(os.path.join(OUT_DIR, "netx-256.png"))
    make(64).save(os.path.join(OUT_DIR, "netx-64.png"))
    print(f"wrote {ico_path} ({os.path.getsize(ico_path)} bytes)")


if __name__ == "__main__":
    main()
