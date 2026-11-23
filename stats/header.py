#!/usr/bin/env python3
"""Render the profile header: a pixel wordmark plus a short intro.

The age is computed from $BIRTH_DATE (YYYY-MM-DD, kept in a repository secret), so the
birth date itself never appears in the repo or in the image.
"""

import argparse
import os
from datetime import date
from html import escape
from pathlib import Path

# 5x7 pixel glyphs, lowercase sits on rows 2-6
GLYPHS = {
    "a": [".....", ".....", ".###.", "....#", ".####", "#...#", ".####"],
    "m": [".....", ".....", "####.", "#.#.#", "#.#.#", "#.#.#", "#.#.#"],
    "o": [".....", ".....", ".###.", "#...#", "#...#", "#...#", ".###."],
}
REDSTONE = ["#a3130b", "#b8180e", "#c92012", "#d92b18"]

LIGHT = {"t": "#1f2328", "x": "#59636e"}
DARK = {"t": "#f0f6fc", "x": "#9198a1"}


def age_from(birth: str):
    try:
        b = date.fromisoformat(birth.strip())
    except (ValueError, AttributeError):
        return None
    t = date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def wordmark(text: str, x0: int, y0: int, px: int) -> tuple:
    rects, col = [], 0
    for ch in text:
        for r, row in enumerate(GLYPHS[ch]):
            for c, cell in enumerate(row):
                if cell == "#":
                    tone = REDSTONE[(r * 3 + (col + c) * 5 + r * c) % len(REDSTONE)]
                    rects.append(f'<rect x="{x0 + (col + c) * px}" y="{y0 + r * px}" '
                                 f'width="{px - 2}" height="{px - 2}" fill="{tone}"/>')
        col += 6
    return "".join(rects), (col - 1) * px


def render(name: str, age, since: int, lines: list) -> str:
    px = 14
    marks, mark_w = wordmark("ammo", 4, 26, px)
    tx = mark_w + 56
    title = f"{escape(name)}, {age}" if age is not None else escape(name)
    body = "".join(
        f'<text x="{tx}" y="{98 + i * 24}" class="x">{escape(line)}</text>' for i, line in enumerate(lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="180" viewBox="0 0 1000 180" fill="none" role="img" aria-labelledby="t">
<title id="t">{title}. {escape(" ".join(lines))}</title>
<style>
  .t {{ font: 600 30px 'Segoe UI', Ubuntu, 'Helvetica Neue', Arial, sans-serif; fill: {LIGHT["t"]}; }}
  .x {{ font: 400 17px 'Segoe UI', Ubuntu, 'Helvetica Neue', Arial, sans-serif; fill: {LIGHT["x"]}; }}
  @media (prefers-color-scheme: dark) {{ .t {{ fill: {DARK["t"]}; }} .x {{ fill: {DARK["x"]}; }} }}
</style>
{marks}
<text x="{tx}" y="62" class="t">{title}</text>
{body}
</svg>
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", default="Simon")
    p.add_argument("--birth-date", default=os.environ.get("BIRTH_DATE", ""), help="YYYY-MM-DD (default: $BIRTH_DATE)")
    p.add_argument("--since", type=int, default=2009)
    p.add_argument("--out", default="assets/header.svg")
    args = p.parse_args()
    lines = [
        "Full-time in emergency medical services in Germany.",
        "In between shifts I build Minecraft servers and web apps.",
        f"Writing code since {args.since}.",
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(args.name, age_from(args.birth_date), args.since, lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
