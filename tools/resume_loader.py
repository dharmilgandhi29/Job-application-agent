"""
resume_loader.py — the "whose resume is this?" desk.

The ONE place that answers "fetch the current user's anchor resume." Every tool
and the agent get the resume THROUGH here, never by hardcoding a filename. That's
the whole multi-user seam: today this reads resume.md off disk; tomorrow it reads
whichever logged-in user's resume from wherever we store it. The agent and tools
never change — only this desk does.
"""

from pathlib import Path

# Today: one user, one file. Later: this takes a user_id and looks up their resume.
DEFAULT_RESUME_PATH = "resume.md"


def load_resume(user_id: str = "default") -> str:
    """Hand back the anchor resume as markdown text. The user_id is here from day
    one even though we ignore it for now — so when multi-user lands, the agent and
    tools already pass it and nothing downstream has to change."""
    path = Path(DEFAULT_RESUME_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"No resume found at '{DEFAULT_RESUME_PATH}'. Create it in the project root."
        )
    return path.read_text(encoding="utf-8")
