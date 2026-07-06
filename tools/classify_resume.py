"""
classify_resume.py — the structure reader.

Instead of guessing a resume's structure with hardcoded rules (which only work
for one specific resume), we ask Claude to read the paragraphs and label each one:
is it the summary? a bullet? a skills line? a header to leave alone? This works on
ANY resume regardless of section names or formatting — Claude understands what a
resume section IS, not just pattern-matches labels.

Cost is a non-issue: it's Haiku, and it runs ONCE per resume, then caches the
result to a JSON file. Future runs read the cache for free; we only re-classify
if the resume changes (detected by a hash of its text)."""

import json
import re
import hashlib
import logging
from pathlib import Path
import anthropic as anthropic_module
from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv
from docx import Document

load_dotenv()
client = AsyncAnthropic()

_MODEL = "claude-haiku-4-5-20251001"   # cheap; runs once per resume
_CACHE_PATH = "resume_structure.json"


_SYSTEM_PROMPT = """You classify the paragraphs of a resume. You are given a numbered \
list of every paragraph's text. For each, decide its role in the resume:

- "summary"  : the professional summary / profile / objective paragraph
- "bullet"   : a descriptive bullet point under a job or project (an accomplishment line)
- "skill"    : a technical-skills line, usually "Category: item, item, item"
- "skip"     : everything else — the name, contact line, section headers
               (EDUCATION, WORK EXPERIENCE, etc.), employer/date lines, job titles,
               degree lines, project title lines

Return ONLY valid JSON, no markdown, no preamble:
{
  "classifications": [
    {"index": 0, "kind": "skip|summary|bullet|skill"}
  ]
}
Include EVERY paragraph index you were given, in order."""


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    return json.loads(text.strip())


_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((
        anthropic_module.RateLimitError,
        anthropic_module.APIConnectionError,
        anthropic_module.APITimeoutError,
        json.JSONDecodeError,
    )),
    reraise=True,
)


def _read_paragraphs(docx_path: str) -> list[str]:
    """Every non-empty paragraph's text, in order. Deterministic docx reading —
    works on any docx (this part isn't resume-specific)."""
    doc = Document(docx_path)
    return [p.text for p in doc.paragraphs]  # keep all, including empties, to preserve indexing


def _resume_hash(paragraphs: list[str]) -> str:
    """A fingerprint of the resume's text, so we know when to re-classify."""
    joined = "\n".join(paragraphs)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@retry(**_RETRY)
async def _classify_with_claude(paragraphs: list[str]) -> list[dict]:
    numbered = "\n".join(
        f"[{i}] {t.strip()}" for i, t in enumerate(paragraphs) if t.strip()
    )
    user_message = f"""Classify every paragraph of this resume.

{numbered}

Return the JSON with a classification for every index shown above."""

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=1500,
        temperature=0,
        system=[{"type": "text", "text": _SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    )
    result = _parse_json_response(response.content[0].text)
    return result["classifications"]


async def get_swappable(docx_path: str, force: bool = False) -> list[dict]:
    """Return the swappable paragraphs of the resume:
       [{"original": <exact text>, "kind": "summary|bullet|skill", "index": N}]

    Uses a cached classification if the resume hasn't changed; otherwise calls
    Claude once and caches. Pass force=True to always re-classify."""
    paragraphs = _read_paragraphs(docx_path)
    current_hash = _resume_hash(paragraphs)

    # Try the cache first
    cache = Path(_CACHE_PATH)
    if cache.exists() and not force:
        saved = json.loads(cache.read_text())
        if saved.get("hash") == current_hash:
            return saved["swappable"]

    # Cache miss or resume changed → classify with Claude
    classifications = await _classify_with_claude(paragraphs)

    swappable = []
    for c in classifications:
        idx, kind = c["index"], c["kind"]
        if kind in ("summary", "bullet", "skill") and 0 <= idx < len(paragraphs):
            text = paragraphs[idx].strip()
            if text:
                swappable.append({"original": text, "kind": kind, "index": idx})

    # Save to cache for next time
    cache.write_text(json.dumps(
        {"hash": current_hash, "swappable": swappable}, indent=2
    ))
    return swappable


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    items = asyncio.run(get_swappable("Resume_Dharmil_Gandhi.docx"))
    print(f"Classified {len(items)} swappable paragraphs:\n")
    for it in items:
        print(f"  [{it['kind']:7}] ({it['index']:2}) {it['original'][:65]}")