"""Draw the favicon and the home-screen icons from Strata's disc, into frontend/public.

    python3 design/icons.py     # needs Inkscape on PATH for the PNGs
"""
import math
import subprocess
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "frontend" / "public"
GROUND, INK, DISC = "#100f0d", "#efe8da", "#1f1d19"
LINES = [(10, 3), (15, 5), (20, 5), (25, 3)]   # (baseline y, lift) in a 32-unit box


def ridge(y, lift, s=1.0, o=0.0):
    """One line, flat at the rim and lifted by a mound at the centre."""
    points = []
    for i in range(65):
        x = i * 0.5
        lifted = y - lift * math.exp(-((x - 16) / 6) ** 2)
        points.append(f"{x * s + o:.2f} {lifted * s + o:.2f}")
    return "M" + "L".join(points)


def disc(s=1.0, o=0.0):
    paths = "".join(f'<path d="{ridge(y, lift, s, o)}"/>' for y, lift in LINES)
    return (f'<clipPath id="disc"><circle cx="{16 * s + o:g}" cy="{16 * s + o:g}" r="{15 * s:g}"/></clipPath>'
            f'<g clip-path="url(#disc)" fill="none" stroke="{INK}" stroke-width="{2.2 * s:g}" '
            f'stroke-linecap="round">{paths}</g>')


def main():
    (PUBLIC / "favicon.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                                        f'<circle cx="16" cy="16" r="15" fill="{GROUND}"/>{disc()}</svg>\n')
    # Full bleed, since iOS and Android round the corners themselves; the disc stays inside the
    # maskable safe zone (a circle 80% across), so Android can crop it to any shape.
    size = 512
    s = size * 0.70 / 30
    o = size / 2 - 16 * s
    icon = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
            f'<rect width="{size}" height="{size}" fill="{GROUND}"/>'
            f'<circle cx="{size / 2:g}" cy="{size / 2:g}" r="{15 * s:g}" fill="{DISC}"/>{disc(s, o)}</svg>\n')
    source = Path(__file__).resolve().parent / "app-icon.svg"
    source.write_text(icon)
    for name, px in [("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)]:
        subprocess.run(["inkscape", str(source), "-w", str(px), "-h", str(px), "-o", str(PUBLIC / name)],
                       check=True, capture_output=True)


if __name__ == "__main__":
    main()
