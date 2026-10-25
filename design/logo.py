"""Draw the logo for the GitHub and Hugging Face accounts from Strata's disc, into design/logo.svg
and logo.png: the live disc's soft rim with twelve lines, heavy enough to read at avatar sizes. A
1024 px square whose disc stays inside a centred circle, since Hugging Face crops avatars round
and GitHub rounds their corners.

    python3 design/logo.py     # needs Inkscape on PATH for the PNGs
"""
import math
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
GROUND, INK = "#100f0d", "#efe8da"
SIZE = 1024
C = SIZE / 2


def bell(x, width):
    return math.exp(-(x / width) ** 2)


def ridges(R, lines, stroke, height, width=0.42, fade=0.0, fill=GROUND, gap=0.0):
    """Lines through a circle of radius R, each lifted by one broad mound at the centre, nearer
    lines filled underneath so they hide the ones behind (as field.ts draws them). `fade` eases
    each line out towards the rim over that fraction of R."""
    out, defs = [], []
    for k in range(lines):
        y = C - R + (k + 0.5 + gap) / (lines + 2 * gap) * 2 * R
        yr = (y - C) / R
        half = math.sqrt(max(0.0, R * R - (y - C) ** 2))
        if half < stroke:
            continue
        lift = height * R * bell(yr, 0.75)
        points = [(C - half + i / 200 * 2 * half, 0.0) for i in range(201)]
        points = [(x, y - lift * bell((x - C) / R, width)) for x, _ in points]
        d = "M" + "L".join(f"{x:.1f} {py:.1f}" for x, py in points)
        out.append(f'<path d="{d}L{C + half:.1f} {y + R * 0.2:.1f}L{C - half:.1f} {y + R * 0.2:.1f}Z" fill="{fill}"/>')
        hue = INK
        if fade:
            span = min(0.5, fade * R / (2 * half))
            strength = min(1.0, half / (fade * R))
            stops = "".join(f'<stop offset="{p:.3f}" stop-color="{hue}" '
                            f'stop-opacity="{strength * smooth(min(p, 1 - p) / span):.3f}"/>'
                            for p in [i / 24 for i in range(25)])
            defs.append(f'<linearGradient id="l{k}" gradientUnits="userSpaceOnUse" x1="{C - half:.1f}" '
                        f'x2="{C + half:.1f}" y1="0" y2="0">{stops}</linearGradient>')
            hue = f"url(#l{k})"
        out.append(f'<path d="{d}" fill="none" stroke="{hue}" stroke-width="{stroke}" stroke-linecap="round"/>')
    clip = f'<clipPath id="disc"><circle cx="{C}" cy="{C}" r="{R}"/></clipPath>'
    return f'<defs>{clip}{"".join(defs)}</defs><g clip-path="url(#disc)">{"".join(out)}</g>'


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def svg(body, ground=GROUND):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">'
            f'<rect width="{SIZE}" height="{SIZE}" fill="{ground}"/>{body}</svg>\n')


def main():
    R = SIZE * 0.36
    source = HERE / "logo.svg"
    source.write_text(svg(ridges(R * 1.08, 12, R * 0.05, 0.3, fade=0.45)))
    subprocess.run(["inkscape", str(source), "-w", str(SIZE), "-h", str(SIZE), "-o", str(HERE / "logo.png")],
                   check=True, capture_output=True)


if __name__ == "__main__":
    main()
