"""
generate_pdf.py — the printer.

Takes markdown (a tailored resume, the anchor, or a cover letter) and renders a
clean, ATS-friendly PDF: markdown → styled HTML → headless Chromium → PDF. Same
render pipeline the big tools use, so the output looks professional, but we keep
the layout deliberately simple (single column, real selectable text, no graphics)
because that's exactly what résumé parsers read happily.

One renderer, two jobs: resumes and cover letters both flow through here — the
CSS just adapts. Swappable later (if you ever want a different engine, only the
render step changes, not the callers)."""

import markdown as md_lib
from playwright.async_api import async_playwright

# ── The stylesheet: clean, single-column, ATS-safe, but not ugly ──────────────
# Tuned for resumes — tight margins, readable serif headings, plain body. No
# multi-column trickery or background graphics that confuse ATS parsers.
_CSS = """
* { box-sizing: border-box; }
body {
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.4;
    color: #1a1a1a;
    max-width: 8.5in;
    margin: 0;
    padding: 0.5in 0.6in;
}
h1 {
    font-size: 20pt;
    margin: 0 0 2pt 0;
    letter-spacing: 0.5px;
}
/* the contact line: the paragraph right after the H1 */
h1 + p {
    font-size: 9.5pt;
    color: #444;
    margin: 0 0 10pt 0;
}
h2 {
    font-size: 11pt;
    text-transform: uppercase;
    letter-spacing: 1px;
    border-bottom: 1px solid #999;
    padding-bottom: 2pt;
    margin: 12pt 0 6pt 0;
}
h3 {
    font-size: 10.5pt;
    margin: 8pt 0 1pt 0;
}
/* the italic date/location line under each role */
h3 + p em, p em:first-child { color: #555; }
p { margin: 1pt 0 4pt 0; }
ul { margin: 2pt 0 6pt 0; padding-left: 16pt; }
li { margin: 0 0 2pt 0; }
strong { color: #000; }
a { color: #1a1a1a; text-decoration: none; }
"""


def _markdown_to_html(markdown_text: str) -> str:
    """Wrap rendered markdown in a full HTML doc with our resume stylesheet."""
    body = md_lib.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>{body}</body></html>"""

async def generate_pdf(markdown_text: str, out_path: str) -> str:
    """Render markdown to a styled, SINGLE-PAGE PDF at out_path. Returns the path.

    One-page guarantee: render once, measure the content height, and if it spills
    past a letter page, scale down just enough to fit — but only down to a readable
    floor. The content should already be ~one page (anchor-length), so this scaling
    is a safety net, not the main mechanism. page_ranges='1' is the hard backstop:
    the PDF physically cannot exceed one page."""
    html = _markdown_to_html(markdown_text)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="load")

        # Measure rendered content height vs one letter page (11in × 96px/in).
        content_px = await page.evaluate("document.body.scrollHeight")
        page_px = 11 * 96

        # Scale down to fit, but never below 0.8 — past that, text gets too small
        # and the real fix is tighter CONTENT, not tinier type.
        scale = 1.0
        if content_px > page_px:
            scale = max(0.8, page_px / content_px)

        await page.pdf(
            path=out_path,
            format="Letter",
            print_background=True,
            scale=scale,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            page_ranges="1",   # hard cap: only ever emit page 1
        )
        await browser.close()

    return out_path

# ── Standalone test: render your actual tailored resume to PDF ────────────────
# Run from project root:  python -m tools.generate_pdf
if __name__ == "__main__":
    import asyncio
    from pathlib import Path

    # Use the tailored Anthropic resume we made earlier if it exists, else the anchor.
    candidates = list(Path("outputs").glob("resume_*.md"))
    src = candidates[0] if candidates else Path("resume.md")
    text = src.read_text(encoding="utf-8")

    out = str(Path("outputs") / (src.stem + ".pdf"))
    print(f"\n🖨️  Rendering {src.name} → {out} ...")
    result = asyncio.run(generate_pdf(text, out))
    print(f"   ✅ Wrote {result}")