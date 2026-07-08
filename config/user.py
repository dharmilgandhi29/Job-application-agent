"""
user.py — who is this? The single source of truth for the current user.

Reads config/user.json (data, not code) and exposes it as clean names the rest of
the app imports. A new user changes ONE thing: config/user.json (their name, resume
filenames, profile) — plus their own ANTHROPIC_API_KEY in .env. Nothing else in the
codebase hardcodes identity. Later, the web UI's "upload your resume" onboarding
will simply WRITE this json, and everything downstream keeps working unchanged.
"""

import json
from pathlib import Path

_JSON_PATH = Path(__file__).parent / "user.json"

if not _JSON_PATH.exists():
    raise FileNotFoundError(
        f"No user config at {_JSON_PATH}. Create config/user.json with your "
        f"name, resume filenames, and profile (see the README)."
    )

_data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))

NAME        = _data["name"]
ANCHOR_DOCX = _data["anchor_docx"]
RESUME_MD   = _data["resume_md"]
PROFILE     = _data["profile"]