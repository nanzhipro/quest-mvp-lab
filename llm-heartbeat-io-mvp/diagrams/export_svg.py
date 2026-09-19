#!/usr/bin/env python3
"""Extract the diagram SVG from a generated HTML file into a standalone .svg.

Implements the SVG export procedure from references/export.md, so the steps are
repeatable instead of retyped:

  1. extract the first <svg ...>...</svg> block
  2. ensure xmlns + viewBox, keep role/aria-labelledby/<title>/<desc> as authored
  3. inject the Google Fonts @import into the existing <defs> (XML-escaped &)
  4. normalise rgba(...) / transparent into SVG 1.1-safe
     fill="#rrggbb" fill-opacity="..."  (PowerPoint paints rgba() opaque black)
  5. prepend the XML declaration
  6. write next to the source

Usage
-----
    python3 export_svg.py <diagram.html> [out.svg]
"""

import pathlib
import re
import sys

FONT_IMPORT = (
    "<style>@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1"
    "&amp;family=Geist:wght@400;500;600&amp;family=Geist+Mono:wght@400;500;600"
    "&amp;family=Noto+Sans+SC:wght@400;500;600&amp;family=Noto+Sans+KR:wght@400;500;600"
    "&amp;family=Noto+Serif+KR:wght@400&amp;family=Noto+Sans+TC:wght@400;500;600"
    "&amp;family=Noto+Serif+TC:wght@400&amp;display=swap');</style>"
)

ATTR_RGBA = re.compile(
    r'(fill|stroke)="rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d*\.?\d+)\s*\)"'
)


def normalise_colours(svg: str) -> str:
    svg = ATTR_RGBA.sub(
        lambda m: '{0}="#{1:02x}{2:02x}{3:02x}" {0}-opacity="{4}"'.format(
            m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
        ),
        svg,
    )
    return re.sub(r'(fill|stroke)="transparent"', r'\1="none"', svg)


def main() -> int:
    src = pathlib.Path(sys.argv[1]).resolve()
    html = src.read_text(encoding="utf-8")
    m = re.search(r"<svg\b.*?</svg>", html, re.S)
    if not m:
        print("no <svg> block found", file=sys.stderr)
        return 2
    svg = m.group(0)

    # standalone requirements
    if 'xmlns=' not in svg.split(">", 1)[0]:
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    if "viewBox=" not in svg.split(">", 1)[0]:
        print("warning: no viewBox on the extracted <svg>", file=sys.stderr)

    if "<defs>" in svg:
        svg = svg.replace("<defs>", "<defs>" + FONT_IMPORT, 1)
    else:
        svg = svg.replace(">", "><defs>" + FONT_IMPORT + "</defs>", 1)

    svg = normalise_colours(svg)
    out = pathlib.Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else src.with_suffix(".svg")
    out.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + svg, encoding="utf-8")
    print(f"written: {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
