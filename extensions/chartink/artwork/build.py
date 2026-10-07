"""Rebuild original vector artwork and PNGs. Development dependency: Pillow.

No network, fonts, browser data or generated price information is used. The
geometric logo belongs to this project and uses the repository's AGPL v3 license.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT.parents[1] / "docs/research/extension-store"
INK, MINT, WHITE = "#143E35", "#A5F5C8", "#F1FFF6"
SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-labelledby="title">
  <title id="title">OpenAlgo Research connector</title>
  <rect x="16" y="16" width="96" height="96" rx="25" fill="#143E35"/>
  <g fill="#F1FFF6">
    <rect x="34" y="66" width="12" height="25" rx="6"/>
    <rect x="53" y="57" width="12" height="34" rx="6"/>
    <rect x="72" y="70" width="12" height="21" rx="6"/>
  </g>
  <path d="M37 43H88M77 32L88 43L77 54" fill="none" stroke="#A5F5C8" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def icon(size, padding=None):
    padding = size // 8 if padding is None else padding
    scale = (size - 2 * padding) / 96 * 8
    canvas = Image.new("RGBA", (size * 8, size * 8))
    draw = ImageDraw.Draw(canvas)

    def p(x, y):
        return ((x - 16) * scale + padding * 8, (y - 16) * scale + padding * 8)

    def rounded(box, radius, fill):
        draw.rounded_rectangle((*p(*box[:2]), *p(*box[2:])), radius=radius * scale, fill=fill)

    rounded((16, 16, 112, 112), 25, INK)
    for box in ((34, 66, 46, 91), (53, 57, 65, 91), (72, 70, 84, 91)):
        rounded(box, 6, WHITE)
    for points in (((37, 43), (88, 43)), ((77, 32), (88, 43), (77, 54))):
        draw.line([p(*point) for point in points], fill=MINT, width=round(7 * scale), joint="curve")
        for x, y in points:
            cx, cy = p(x, y)
            radius = 3.5 * scale
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=MINT)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def main():
    icons = ROOT / "icons"
    icons.mkdir(exist_ok=True)
    STORE.mkdir(parents=True, exist_ok=True)
    (icons / "logo.svg").write_text(SVG, encoding="utf-8")
    for size in (16, 32, 48, 128):
        icon(size, size // 16 if size < 128 else 16).save(icons / f"icon{size}.png")
    icon(512).save(STORE / "logo-preview.png")
    # A brand-only promotional tile, not a fabricated product screenshot.
    promo = Image.new("RGB", (880, 560), "#D7F7E5")
    draw = ImageDraw.Draw(promo)
    for x, y, radius in ((-20, 50, 220), (875, 530, 260)):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline="#B9E8CF", width=2)
    for x in (70, 140, 690, 760):
        draw.line((x, 280, x + 42, 280), fill="#62AD8C", width=6)
        draw.ellipse((x - 5, 275, x + 5, 285), fill="#62AD8C")
    mark = icon(464)
    promo.paste(mark, (208, 48), mark)
    promo.resize((440, 280), Image.Resampling.LANCZOS).save(STORE / "promo-440x280.png")
    print("Built the original SVG, four Chrome icons, logo preview and 440x280 promo tile.")


if __name__ == "__main__":
    main()
