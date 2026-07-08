"""
resume_loader.py — the "whose resume is this?" desk.

The ONE place that answers "fetch the current user's anchor resume." Reads the
paths from config/user.json (via config.user), so multi-user is just a matter of
swapping that file. The user_id arg is here from day one so nothing downstream
changes when real multi-user lands.
"""

from pathlib import Path
from config.user import RESUME_MD, ANCHOR_DOCX


def load_resume(user_id: str = "default") -> str:
    """The anchor resume as markdown text (cover-letter content)."""
    path = Path(RESUME_MD)
    if not path.exists():
        raise FileNotFoundError(
            f"No resume found at '{RESUME_MD}'. Create it in the project root "
            f"(or fix resume_md in config/user.json)."
        )
    return path.read_text(encoding="utf-8")


def anchor_docx_path(user_id: str = "default") -> str:
    """The path to the user's real Word resume (tailored in place)."""
    if not Path(ANCHOR_DOCX).exists():
        raise FileNotFoundError(
            f"No anchor resume at '{ANCHOR_DOCX}'. Place it in the project root "
            f"(or fix anchor_docx in config/user.json)."
        )
    return ANCHOR_DOCX