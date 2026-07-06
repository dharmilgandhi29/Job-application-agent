import hashlib
from services.job_cleaner import clean_job_description
from services.claude_client import score_single_job
from services.visa_intel import get_visa_signal, classify_visa_disagreement
from services import storage
from api.models import JobInput, JobScore, JobSource


"""
scrape_job_url.py — the catch-all intake.

Our ATS fetchers only see companies we've wired up (and some slugs go dead). This
lets you paste ANY job URL — a company page, a board posting — and pull it into the
same pipeline. Two stages:

  1. Fetch the page HTML with browser-like headers (dodges the easy bot blocks).
  2. Hand the messy HTML to Claude, which extracts the structured job — title,
     company, location, description. No brittle per-site parsing; Claude reads it.

Honest limits: big boards (LinkedIn, Indeed, Workday) fight scraping — they block
bots, hide content behind JavaScript, or require login. When that happens we say so
and you can paste the description text directly instead. Direct company career pages
(Greenhouse/Lever/Ashby) work best.
"""

import json
import re
import httpx
import anthropic as anthropic_module
from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()
client = AsyncAnthropic()

_MODEL = "claude-haiku-4-5-20251001"  # extraction is cheap, high-volume work

# Browser-like headers so servers don't instantly flag us as a bot.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


_SYSTEM_PROMPT = """You extract a job posting from raw webpage text. You're given the visible \
text of a job page (already stripped of HTML). Pull out the structured posting.

Return ONLY valid JSON, no markdown, no preamble:
{
  "title": "the job title, or empty string if you truly can't find one",
  "company": "the hiring company, or empty string",
  "location": "location if stated, else empty string",
  "description": "the full job description text — responsibilities, requirements, qualifications — cleaned up and readable",
  "looks_valid": true/false,   // false if the text is a login wall, error page, or clearly not a job posting
  "note": "if looks_valid is false, one line on what the page actually was (e.g. 'login required', 'page blocked', 'not a job posting')"
}

If the page is a bot-block, login wall, or empty JS shell, set looks_valid false and say so."""


def _parse_json_response(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        return {"title": "", "company": "", "location": "", "description": "",
                "looks_valid": False, "note": "Could not parse extraction result."}


def _fetch_html(url: str) -> tuple[str, str | None]:
    """Fetch the page. Returns (visible_text, error). On success error is None."""
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=20.0)
    except Exception as e:
        return "", f"Could not reach the URL ({e})."
    if resp.status_code != 200:
        return "", f"Server returned HTTP {resp.status_code} (often a bot block or expired posting)."
    # Strip HTML to visible text — that's what Claude needs, and it's far fewer tokens.
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r'\n\s*\n+', '\n\n', text).strip()
    if len(text) < 200:
        return text, "Page had almost no readable text (likely JavaScript-rendered or blocked)."
    return text, None


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type((anthropic_module.RateLimitError,
                                      anthropic_module.APIConnectionError,
                                      anthropic_module.APITimeoutError,
                                      json.JSONDecodeError)),
       reraise=True)
async def _extract_job(page_text: str, url: str) -> dict:
    user_message = f"""Extract the job posting from this page text.

URL: {url}

=== PAGE TEXT ===
{page_text[:12000]}

Return ONLY the JSON object."""
    response = await client.messages.create(
        model=_MODEL,
        max_tokens=2000,
        temperature=0,
        system=[{"type": "text", "text": _SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    )
    return _parse_json_response(response.content[0].text)


async def scrape_job_url(url: str) -> dict:
    """Paste a job URL, get back a structured job dict:
       {title, company, location, description, looks_valid, note, source_url}
    If the site blocked us, looks_valid is False and note explains why."""
    page_text, err = _fetch_html(url)
    if err and not page_text:
        return {"title": "", "company": "", "location": "", "description": "",
                "looks_valid": False, "note": err, "source_url": url}

    job = await _extract_job(page_text, url)
    job["source_url"] = url
    # If the fetch warned but we still got some text, surface both signals.
    if err and job.get("looks_valid"):
        job["note"] = (job.get("note") or "") + f" (fetch warning: {err})"
    return job


def _url_job_id(url: str) -> str:
    """A stable id from the URL so re-pasting the same link dedupes naturally
    (storage's INSERT OR IGNORE does the rest)."""
    return "url-" + hashlib.sha256(url.encode()).hexdigest()[:12]


async def scrape_and_score_url(url: str) -> dict:
    """Full intake for a pasted URL: scrape → clean → score → visa → save to DB,
    mirroring run_pipeline's per-job flow. Returns a summary dict for the agent.

    If the page couldn't be scraped, returns {saved: False, note: <why>} instead
    of crashing — the agent can relay that honestly."""
    scraped = await scrape_job_url(url)
    if not scraped.get("looks_valid") or not scraped.get("description"):
        return {"saved": False,
                "note": scraped.get("note") or "Couldn't extract a job from that URL.",
                "source_url": url}

    job_id = _url_job_id(url)

    # Build the JobInput (source UNKNOWN — a pasted URL isn't from a wired ATS).
    job = JobInput(
        job_id=job_id,
        title=scraped["title"] or "Unknown title",
        company=scraped["company"] or "Unknown company",
        location=scraped.get("location") or "",
        description=scraped["description"],
        job_url=url,
        source=JobSource.UNKNOWN,
    )

    # Score it with the real scorer (same as the pipeline), on cleaned text.
    cleaned = clean_job_description(job.description)
    s = await score_single_job(
        job_id=job_id, title=job.title, company=job.company,
        location=job.location, cleaned_description=cleaned,
        source="unknown",
    )

    # Visa lookup + JD-vs-history reconciliation — identical to run_pipeline.
    visa = get_visa_signal(job.company)
    verdict, verdict_note = classify_visa_disagreement(
        s.get("visa_signal"), visa.status, visa.new_hires
    )

    try:
        storage.save_scored_job(job, JobScore(**s), visa, verdict, verdict_note)
    except Exception as e:
        return {"saved": False, "note": f"Scored but couldn't save: {e}",
                "source_url": url}

    return {
        "saved": True,
        "job_id": job_id,
        "title": job.title,
        "company": job.company,
        "score": s.get("score"),
        "visa_verdict": verdict,
        "reasoning": (s.get("reasoning") or "")[:200],
        "source_url": url,
    }


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio, sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://job-boards.greenhouse.io/anthropic/jobs/5212119008"
    print(f"🔗 Scraping: {test_url}\n")
    result = asyncio.run(scrape_job_url(test_url))
    print(f"  valid:    {result['looks_valid']}")
    print(f"  title:    {result['title']}")
    print(f"  company:  {result['company']}")
    print(f"  location: {result['location']}")
    if result.get("note"):
        print(f"  note:     {result['note']}")
    print(f"\n  description ({len(result['description'])} chars):")
    print("  " + result['description'][:400].replace("\n", "\n  "))