#!/usr/bin/env python3
"""Rasterise a diagram-design HTML file to PNG without Playwright.

The diagram-design skill's export path uses Playwright; when Playwright is not
installed this script does the same job with the locally installed Chrome:

  1. extract the first <svg> block from the source HTML
  2. embed it in a zero-margin wrapper page at its exact viewBox size
  3. Chrome --headless=new --screenshot with a transparent default background
     and --force-device-scale-factor=<scale>

    python3 render_png.py message-assembly.html [scale] [chrome-binary]

Output: <basename>.png next to the source.
"""

import pathlib
import re
import subprocess
import sys
import tempfile

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

FONT_LINK = (
    '<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1'
    '&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600'
    '&family=Noto+Sans+SC:wght@400;500;600&family=Noto+Sans+KR:wght@400;500;600'
    '&family=Noto+Serif+KR:wght@400&family=Noto+Sans+TC:wght@400;500;600'
    '&family=Noto+Serif+TC:wght@400&display=swap" rel="stylesheet">'
)


def main() -> int:
    src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "message-assembly.html").resolve()
    scale = sys.argv[2] if len(sys.argv) > 2 else "2"
    chrome = sys.argv[3] if len(sys.argv) > 3 else next(
        (c for c in CHROME_CANDIDATES if pathlib.Path(c).exists()), None
    )
    if not chrome:
        print("no Chrome/Chromium/Edge binary found; pass one as argv[3]", file=sys.stderr)
        return 2

    html = src.read_text(encoding="utf-8")
    m = re.search(r"<svg\b.*?</svg>", html, re.S)
    if not m:
        print("no <svg> block found in source", file=sys.stderr)
        return 2
    svg = m.group(0)
    vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    if not vb:
        print("svg has no '0 0 W H' viewBox", file=sys.stderr)
        return 2
    w, h = vb.group(1), vb.group(2)

    wrapper = f"""<!DOCTYPE html><html><head><meta charset="utf-8">{FONT_LINK}
<style>html,body{{margin:0;padding:0;background:transparent}}
svg{{display:block;width:{w}px;height:{h}px}}</style></head><body>{svg}</body></html>"""

    out = src.with_suffix(".png")
    with tempfile.TemporaryDirectory() as td:
        wrap_path = pathlib.Path(td) / "wrap.html"
        wrap_path.write_text(wrapper, encoding="utf-8")
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",
            f"--window-size={w},{h}",
            "--default-background-color=00000000",
            "--virtual-time-budget=10000",
            f"--screenshot={out}",
            wrap_path.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not out.exists():
        print("chrome produced no file\n" + proc.stderr[-2000:], file=sys.stderr)
        return 1
    print(f"written: {out}  ({out.stat().st_size:,} bytes, viewBox {w}x{h} @{scale}x)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
