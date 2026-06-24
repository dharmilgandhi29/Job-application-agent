"""
run_pipeline.py — the orchestrator.

Ties the whole pipeline together:
  discover → title filter → US filter → clean → score → ranked output

For now it scores a small SAMPLE (default 10) so we can verify wiring and
sanity-check scores on real jobs cheaply before scaling to the full set.
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()  # load ANTHROPIC_API_KEY from .env before the Claude client is used

from services.discovery import discover_all
from services.job_filter import filter_jobs, filter_us_only
from services.job_cleaner import clean_job_description
from services.claude_client import score_batch_jobs

# How many jobs to score in this run. Small while testing; raise later.
SAMPLE_SIZE = 10


async def run(sample_size: int = SAMPLE_SIZE):
    # 1. Discover everything (free)
    jobs = await discover_all()

    # 2. Pre-filters (free): role titles, then US-only
    jobs = filter_jobs(jobs)
    jobs = filter_us_only(jobs)
    print(f"\n── After filters: {len(jobs)} jobs match role + US ──")

    # 3. Take a sample to score (keeps this test cheap)
    sample = jobs[:sample_size]
    print(f"── Scoring first {len(sample)} (sample) ──\n")

    # 4. Clean descriptions and shape for the scorer
    to_score = []
    for job in sample:
        to_score.append({
            "job_id": job.job_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "cleaned_description": clean_job_description(job.description),
            "source": job.source.value,
        })

    # 5. Score in sub-batches of 5 (matches the scorer's batch limit)
    all_scores = []
    for i in range(0, len(to_score), 5):
        batch = to_score[i:i + 5]
        results = await score_batch_jobs(batch)
        all_scores.extend(results)

    # 6. Rank by score, highest first
    all_scores.sort(key=lambda s: s.get("score", 0), reverse=True)

    # 7. Show results
    print("══ RANKED RESULTS ══\n")
    for s in all_scores:
        print(f"  [{s.get('score')}]  {s.get('role_type')}  |  visa: {s.get('visa_signal')}  |  apply: {s.get('apply')}")
        print(f"        {s.get('job_id')}  ({s.get('source')})")
        print(f"        {s.get('reasoning')}\n")


if __name__ == "__main__":
    asyncio.run(run())