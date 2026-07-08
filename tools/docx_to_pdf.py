"""
docx_to_pdf.py — the exporter.

Turns a tailored .docx into a faithful PDF using LibreOffice in headless mode.
LibreOffice renders Word documents very closely and runs on macOS, Windows, and
Linux — so this works everywhere, unlike the old Word/AppleScript path. It's also
exactly what runs inside a container when the app is deployed later; the code
doesn't change, only where LibreOffice lives.

The rest of the pipeline calls docx_to_pdf() and doesn't care how the PDF is made.
"""

import os
import shutil
import subprocess
from pathlib import Path


def _find_soffice() -> str:
    """Locate the LibreOffice binary across platforms. Returns the path/command.

    Checks PATH first (Linux, and Mac/Windows if installed there), then the known
    default install locations for Mac and Windows."""
    # On PATH? (covers most Linux installs and any platform where it's linked)
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found

    # Known default locations
    candidates = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",          # macOS
        r"C:\Program Files\LibreOffice\program\soffice.exe",             # Windows
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",       # Windows 32-bit
    ]
    for path in candidates:
        if Path(path).exists():
            return path

    raise RuntimeError(
        "LibreOffice not found. Install it to enable PDF export:\n"
        "  macOS:   brew install --cask libreoffice\n"
        "  Ubuntu:  sudo apt install libreoffice\n"
        "  Windows: https://www.libreoffice.org/download/\n"
        "(The .docx is still produced; only the PDF step needs LibreOffice.)"
    )


def docx_to_pdf(docx_path: str, pdf_path: str | None = None) -> str:
    """Convert a .docx to PDF using LibreOffice headless. Returns the PDF path.

    If pdf_path is omitted, the PDF sits next to the .docx with a .pdf extension.
    LibreOffice always writes <same-name>.pdf into the output directory we give it,
    so if a specific pdf_path is requested we rename to match afterward."""
    soffice = _find_soffice()
    docx_abs = Path(docx_path).resolve()
    if not docx_abs.exists():
        raise FileNotFoundError(f"No .docx to convert at {docx_abs}")

    out_dir = docx_abs.parent if pdf_path is None else Path(pdf_path).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # LibreOffice headless: convert to pdf, writing into out_dir. It names the file
    # <docx-stem>.pdf automatically. --headless runs with no GUI; the env var keeps
    # it from touching a real user profile (important for servers/containers).
    result = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir",
         str(out_dir), str(docx_abs)],
        capture_output=True, text=True,
        env={**os.environ, "HOME": os.environ.get("HOME", str(out_dir))},
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice PDF export failed:\n{result.stderr.strip() or result.stdout.strip()}"
        )

    # LibreOffice wrote <stem>.pdf into out_dir. Figure out that path.
    produced = out_dir / (docx_abs.stem + ".pdf")
    if not produced.exists():
        raise RuntimeError(f"LibreOffice ran but no PDF appeared at {produced}")

    # If the caller asked for a specific name/path, move it there.
    if pdf_path is not None:
        target = Path(pdf_path).resolve()
        if target != produced:
            produced.replace(target)
        return str(target)

    return str(produced)


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "outputs/tailored_Anthropic.docx"
    if not Path(src).exists():
        print(f"No {src} found — run the tailoring first.")
        raise SystemExit
    print(f"🖨️  Converting {src} → PDF via LibreOffice...")
    out = docx_to_pdf(src)
    print(f"   ✅ Wrote {out}")