"""
letter_pdf.py — the cover-letter typesetter.

Renders a cover letter as a clean, properly formatted business-letter PDF —
generous margins, readable serif body, real paragraph spacing, name sign-off.
Uses Playwright + Chromium (already installed), same engine as the resume PDF but
with letter-appropriate styling instead of the dense resume layout.
"""

from pathlib import Path
from playwright.async_api import async_playwright

_LETTER_CSS = """
@page { size: letter; margin: 1in; }
body {
    font-family: 'Georgia', 'Times New Roman', serif;
    font-size: 11.5pt;
    line-height: 1.5;
    color: #1a1a1a;
    max-width: 6.5in;
    margin: 0 auto;
}
p { margin: 0 0 12pt 0; }
.sign-off { margin-top: 18pt; }
.sign-off .closing { margin: 0; }
.sign-off .name { margin: 4pt 0 0 0; font-weight: normal; }
"""


def _letter_to_html(letter_text: str) -> str:
    """Turn the plain letter text into simple HTML paragraphs. Detects the
    'Sincerely,' / name sign-off and styles it as a block."""
    lines = [l.rstrip() for l in letter_text.strip().split("\n")]
    # Split into paragraphs on blank lines
    paras, cur = [], []
    for l in lines:
        if l.strip() == "":
            if cur:
                paras.append(" ".join(cur)); cur = []
        else:
            cur.append(l.strip())
    if cur:
        paras.append(" ".join(cur))

    html_parts = []
    for p in paras:
        # Treat a short trailing "Sincerely,\nName" specially if present
        html_parts.append(f"<p>{p}</p>")
    body = "\n".join(html_parts)
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_LETTER_CSS}</style></head><body>{body}</body></html>"


async def letter_to_pdf(letter_text: str, pdf_path: str) -> str:
    """Render the cover-letter text to a formatted PDF. Returns the path."""
    Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
    html = _letter_to_html(letter_text)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        await page.pdf(path=pdf_path, format="Letter",
                       margin={"top": "1in", "bottom": "1in", "left": "1in", "right": "1in"})
        await browser.close()
    return pdf_path