"""
docx_to_pdf.py — the exporter.

Turns a tailored .docx into a pixel-faithful PDF by driving Microsoft Word itself
(via AppleScript on macOS). Word renders its own format perfectly, so the PDF looks
exactly like the doc — every font, tab-stopped date, hyperlink, and the one-page
layout, intact.

macOS + Word only. (Later, for other platforms/users, this is the one piece that
would swap out — e.g. a LibreOffice or cloud-convert path. The rest of the pipeline
doesn't care how the PDF gets made.)
"""

import subprocess
from pathlib import Path


def docx_to_pdf(docx_path: str, pdf_path: str | None = None) -> str:
    """Convert a .docx to PDF using Word via AppleScript. Returns the PDF path.

    If pdf_path is omitted, the PDF sits next to the .docx with a .pdf extension."""
    docx_abs = str(Path(docx_path).resolve())
    if pdf_path is None:
        pdf_path = str(Path(docx_path).with_suffix(".pdf"))
    pdf_abs = str(Path(pdf_path).resolve())

    # AppleScript: open the doc, save a copy as PDF, close without touching the doc.
    # We reference files by POSIX path converted to Mac 'file' objects.
    applescript = f'''
    tell application "Microsoft Word"
        set wasRunning to running
        activate
        open POSIX file "{docx_abs}"
        set theDoc to active document
        save as theDoc file name (POSIX file "{pdf_abs}" as string) file format format PDF
        close theDoc saving no
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", applescript],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Word PDF export failed:\n{result.stderr.strip()}\n"
            f"(Make sure Microsoft Word is installed and you've granted "
            f"automation permission when macOS prompts.)"
        )

    if not Path(pdf_abs).exists():
        raise RuntimeError(f"Word ran but no PDF appeared at {pdf_abs}")

    return pdf_abs


# ── Standalone test: convert the tailored resume we already made ──────────────
if __name__ == "__main__":
    src = "outputs/tailored_Anthropic.docx"
    if not Path(src).exists():
        print(f"No {src} found — run the tailoring first.")
        raise SystemExit
    print(f"🖨️  Converting {src} → PDF via Word...")
    out = docx_to_pdf(src)
    print(f"   ✅ Wrote {out}")