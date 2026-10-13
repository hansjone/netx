"""Generate NetX Windows .ico/.png from the brand mark.

Mirrors web/public/favicon.svg and web/src/layout/BrandMark.tsx (24x24 viewBox):
blue gradient tile, an "X" of links, four nodes around a hub, one node lit cyan.
"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
GRAD_A = (0x3B, 0x82, 0xF6)  # #3b82f6 (top-left)
GRAD_B = (0x1D, 0x4E, 0xD8)  # #1d4ed8 (bottom-right)
WHITE = (255, 255, 255, 255)
LINK = (255, 255, 255, int(255 * 0.55))
LIVE = (0x67, 0xE8, 0xF9, 255)  # #67e8f9
SS = 4  # supersample factor


def _gradient(px: int) -> Image.Image:
    # Diagonal gradient along (0,0)->(24,24), like the SVG userSpaceOnUse gradient.
    img = Image.new("RGBA", (px, px))
    pix = img.load()
    span = 2 * (px - 1) or 1
    for y in range(px):
        for x in range(px):
            t = (x + y) / span
            pix[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(GRAD_A, GRAD_B)) + (255,)
    return img


def make(size: int) -> Image.Image:
    px = size * SS
    u = px / 24.0

    tile = _gradient(px)
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1], radius=6.5 * u, fill=255)
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    img.paste(tile, (0, 0), mask)

    links = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ld = ImageDraw.Draw(links)
    w = max(1, round(1.6 * u))
    for (x1, y1), (x2, y2) in (((7, 7), (17, 17)), ((17, 7), (7, 17))):
        ld.line([(x1 * u, y1 * u), (x2 * u, y2 * u)], fill=LINK, width=w)
        for cx, cy in ((x1, y1), (x2, y2)):  # round caps
            r = w / 2
            ld.ellipse([cx * u - r, cy * u - r, cx * u + r, cy * u + r], fill=LINK)
    img = Image.alpha_composite(img, links)

    d = ImageDraw.Draw(img)

    def node(cx: float, cy: float, r: float, fill: tuple[int, int, int, int]) -> None:
        d.ellipse([(cx - r) * u, (cy - r) * u, (cx + r) * u, (cy + r) * u], fill=fill)

    node(12, 12, 2.6, WHITE)
    node(7, 7, 1.9, WHITE)
    node(7, 17, 1.9, WHITE)
    node(17, 17, 1.9, WHITE)
    node(17, 7, 2.1, LIVE)
    return img.resize((size, size), Image.LANCZOS)


WIZ_TOP = (0x0A, 0x12, 0x22)  # near-black navy
WIZ_BOTTOM = (0x10, 0x2A, 0x55)  # deep blue


def make_wizard(w: int, h: int) -> Image.Image:
    """Inno Setup side image: dark navy gradient, faint link mesh, brand mark."""
    img = Image.new("RGBA", (w, h))
    pix = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        row = tuple(round(a + (b - a) * t) for a, b in zip(WIZ_TOP, WIZ_BOTTOM)) + (255,)
        for x in range(w):
            pix[x, y] = row

    mesh = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    md = ImageDraw.Draw(mesh)
    pts = [(0.12, 0.62), (0.42, 0.70), (0.80, 0.60), (0.28, 0.86), (0.66, 0.90), (0.95, 0.80), (0.05, 0.95)]
    pts = [(x * w * SS, y * h * SS) for x, y in pts]
    line = (0x60, 0xA5, 0xFA, 46)
    for a, b in ((0, 1), (1, 2), (0, 3), (1, 3), (1, 4), (2, 4), (2, 5), (4, 5), (3, 6)):
        md.line([pts[a], pts[b]], fill=line, width=max(1, SS))
    for i, (x, y) in enumerate(pts):
        r = (2.4 if i != 2 else 3.0) * SS * w / 164
        fill = (0x67, 0xE8, 0xF9, 150) if i == 2 else (0xBF, 0xDB, 0xFE, 90)
        md.ellipse([x - r, y - r, x + r, y + r], fill=fill)
    img = Image.alpha_composite(img, mesh.resize((w, h), Image.LANCZOS))

    side = round(w * 0.42)
    mark = make(side)
    img.alpha_composite(mark, ((w - side) // 2, round(h * 0.26)))
    return img.convert("RGB")


def main() -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    images = {s: make(s) for s in sizes}
    ico_path = os.path.join(OUT_DIR, "netx.ico")
    images[256].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes], bitmap_format="bmp",
                     append_images=[images[s] for s in sizes[:-1]])
    images[256].save(os.path.join(OUT_DIR, "netx-256.png"))
    images[64].save(os.path.join(OUT_DIR, "netx-64.png"))
    # Inno Setup wizard images at 100/125/150/200% DPI (modern style base sizes).
    for pct in (100, 125, 150, 200):
        k = pct / 100
        make_wizard(round(164 * k), round(314 * k)).save(os.path.join(OUT_DIR, f"wizard-{pct}.png"))
        make(round(55 * k)).save(os.path.join(OUT_DIR, f"wizard-small-{pct}.png"))
    print(f"wrote {ico_path} ({os.path.getsize(ico_path)} bytes)")


if __name__ == "__main__":
    main()
