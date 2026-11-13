"""Draw the GB Grid app icon and write packaging/icon.icns (+ web/public/favicon.svg).

Motif = the map's own language: a grid supply point (diamond) feeding two bulk supply points (squares)
and three primaries (circles, coloured like headroom). Navy macOS-style rounded tile.

    uv run --with pillow python packaging/make_icon.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
S = 4096  # draw big, downsample for smooth edges
NAVY_TOP, NAVY_BOT = (22, 58, 99), (12, 30, 54)
LINE = (88, 166, 255)
WHITE = (255, 255, 255)
GREEN, AMBER = (26, 152, 80), (242, 158, 46)

# node positions on a 1000-unit grid
GSP = (500, 255)
BSP = [(300, 520), (700, 520)]
PRI = [(195, 775), (405, 775), (700, 775)]
EDGES = [(GSP, BSP[0]), (GSP, BSP[1]), (BSP[0], PRI[0]), (BSP[0], PRI[1]), (BSP[1], PRI[2])]


def u(v: float) -> int:
    return int(v * S / 1000)


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # vertical gradient tile, macOS-ish margins and corner radius
    tile = Image.new("RGBA", (S, S))
    td = ImageDraw.Draw(tile)
    for y in range(S):
        t = y / S
        td.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(NAVY_TOP, NAVY_BOT, strict=True)))
    mask = Image.new("L", (S, S), 0)
    m = u(80)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], radius=u(185), fill=255)
    img.paste(tile, (0, 0), mask)

    d = ImageDraw.Draw(img)
    for a, b in EDGES:
        d.line([(u(a[0]), u(a[1])), (u(b[0]), u(b[1]))], fill=LINE, width=u(26))
    # GSP diamond
    r = 118
    x, y = GSP
    d.polygon([(u(x), u(y - r)), (u(x + r), u(y)), (u(x), u(y + r)), (u(x - r), u(y))], fill=WHITE)
    # BSP squares
    for x, y in BSP:
        r = 72
        d.rounded_rectangle([u(x - r), u(y - r), u(x + r), u(y + r)], radius=u(12), fill=WHITE)
    # primaries: headroom colours with a white rim
    for (x, y), c in zip(PRI, [GREEN, AMBER, GREEN], strict=True):
        r = 64
        d.ellipse([u(x - r - 12), u(y - r - 12), u(x + r + 12), u(y + r + 12)], fill=WHITE)
        d.ellipse([u(x - r), u(y - r), u(x + r), u(y + r)], fill=c)
    return img.resize((1024, 1024), Image.LANCZOS)


def write_icns(img: Image.Image, out: Path) -> None:
    tmp = Path(tempfile.mkdtemp()) / "icon.iconset"
    tmp.mkdir()
    for base in (16, 32, 128, 256, 512):
        img.resize((base, base), Image.LANCZOS).save(tmp / f"icon_{base}x{base}.png")
        img.resize((base * 2, base * 2), Image.LANCZOS).save(tmp / f"icon_{base}x{base}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(tmp), "-o", str(out)], check=True)
    shutil.rmtree(tmp.parent, ignore_errors=True)


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect x="2" y="2" width="60" height="60" rx="13" fill="#163a63"/>
<g stroke="#58a6ff" stroke-width="2.4"><path d="M32 16L19 33M32 16L45 33M19 33L12 50M19 33L26 50M45 33L45 50"/></g>
<path d="M32 9l7 7-7 7-7-7z" fill="#fff"/>
<rect x="15" y="29" width="8" height="8" rx="1" fill="#fff"/><rect x="41" y="29" width="8" height="8" rx="1" fill="#fff"/>
<circle cx="12" cy="50" r="4.6" fill="#1a9850" stroke="#fff" stroke-width="1.4"/>
<circle cx="26" cy="50" r="4.6" fill="#f29e2e" stroke="#fff" stroke-width="1.4"/>
<circle cx="45" cy="50" r="4.6" fill="#1a9850" stroke="#fff" stroke-width="1.4"/>
</svg>
"""

if __name__ == "__main__":
    icon = draw()
    icon.save(ROOT / "packaging" / "icon.png")
    write_icns(icon, ROOT / "packaging" / "icon.icns")
    (ROOT / "web" / "public" / "favicon.svg").write_text(FAVICON)
    print("wrote packaging/icon.icns, packaging/icon.png, web/public/favicon.svg")
