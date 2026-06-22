import httpx
import logging
from api.models import JobInput, JobSource
import asyncio
from config.companies import COMPANIES

logger = logging.getLogger(__name__)

# Greenhouse's free public board API. {slug} is the company identifier.
# ?content=true tells it to include the full job description, not just titles.
GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


async def fetch_greenhouse(company_name: str, slug: str) -> list[JobInput]:
    """
    Ask one company's Greenhouse board for its open jobs.
    Returns a list of JobInput (possibly empty). Never raises — on any failure
    it logs a clear warning and returns [], so one bad company can't crash the run.
    """
    url = GREENHOUSE_URL.format(slug=slug)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Greenhouse fetch failed for {company_name} ({slug}): {e}")
        return []

    jobs = []
    for j in data.get("jobs", []):
        jobs.append(JobInput(
            job_id=f"gh-{j['id']}",                          # prefix keeps IDs unique across sources
            title=j.get("title", ""),
            company=company_name,
            location=j.get("location", {}).get("name", "Unknown"),
            description=j.get("content", ""),                 # HTML — we'll strip tags later
            job_url=j.get("absolute_url", ""),
            source=JobSource.GREENHOUSE,
        ))
    return jobs






# Lever's free public postings API. {slug} is the company identifier.
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


async def fetch_lever(company_name: str, slug: str) -> list[JobInput]:
    """
    Ask one company's Lever board for its open jobs.
    Same safety contract as Greenhouse: never raises, logs and returns [] on failure.
    Note Lever's data shape differs from Greenhouse — the translation below absorbs
    that difference so the OUTPUT is still identical JobInput objects.
    """
    url = LEVER_URL.format(slug=slug)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Lever fetch failed for {company_name} ({slug}): {e}")
        return []

    jobs = []
    # Lever returns a FLAT list of postings — not wrapped in a "jobs" key like Greenhouse.
    for j in data:
        jobs.append(JobInput(
            job_id=f"lv-{j.get('id', '')}",                       # 'lv-' prefix, distinct from 'gh-'
            title=j.get("text", ""),                              # Lever calls the title 'text'
            company=company_name,
            location=j.get("categories", {}).get("location", "Unknown"),
            description=j.get("descriptionPlain", ""),            # Lever gives plain text — no HTML to strip
            job_url=j.get("hostedUrl", ""),                       # Lever calls the link 'hostedUrl'
            source=JobSource.LEVER,
        ))
    return jobs


# Ashby's free public job-board API. Unlike the others, this is a POST:
# the company identifier goes in the request body, not the URL.
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


async def fetch_ashby(company_name: str, slug: str) -> list[JobInput]:
    """
    Ask one company's Ashby board for its open jobs.
    Same safety contract as the others: never raises, logs and returns [] on failure.
    Ashby differs in that the slug goes in the URL but it returns jobs under a
    'jobs' key with Ashby's own field names — absorbed in the loop below.
    """
    url = ASHBY_URL.format(slug=slug)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Ashby fetch failed for {company_name} ({slug}): {e}")
        return []

    jobs = []
    for j in data.get("jobs", []):
        jobs.append(JobInput(
            job_id=f"ash-{j.get('id', '')}",                     # 'ash-' prefix, distinct from gh-/lv-
            title=j.get("title", ""),
            company=company_name,
            location=j.get("location", "Unknown"),               # Ashby gives location as a plain string
            description=j.get("descriptionPlain", ""),           # plain text available, like Lever
            job_url=j.get("jobUrl", ""),                         # Ashby calls the link 'jobUrl'
            source=JobSource.ASHBY,
        ))
    return jobs




async def discover_company(company_name: str, slug: str) -> tuple[str, list[JobInput]]:
    """
    Try all three ATSes for one company. Use the first that returns jobs.
    Returns (status_message, jobs) so the caller can report exactly what happened
    for every company — found, empty, or not found anywhere. Nothing is silent.
    """
    # Try each ATS in turn; stop at the first that returns jobs.
    for ats_name, fetcher in [
        ("greenhouse", fetch_greenhouse),
        ("lever", fetch_lever),
        ("ashby", fetch_ashby),
    ]:
        jobs = await fetcher(company_name, slug)
        if jobs:
            return (f"{company_name}: {len(jobs)} jobs ({ats_name})", jobs)

    # Tried everything, found nothing — a visible signal, not a silent gap.
    return (f"{company_name}: 0 jobs anywhere — check slug '{slug}'", [])


async def discover_all() -> list[JobInput]:
    """
    Run discovery across every company in COMPANIES, concurrently.
    Prints a clear status line per company, returns the combined list of all jobs found.
    """
    # Fire off all companies at once instead of one-at-a-time. asyncio.gather
    # runs them concurrently and waits for all to finish.
    results = await asyncio.gather(*[
        discover_company(name, slug) for name, slug in COMPANIES
    ])

    all_jobs: list[JobInput] = []
    print("\n── Discovery results ──")
    for status, jobs in results:
        print(f"  {status}")
        all_jobs.extend(jobs)

    print(f"\nTotal jobs found: {len(all_jobs)}\n")
    return all_jobs