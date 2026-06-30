"""
run_pipeline.py — the ringmaster.

Cracks the whip and makes every act perform in order:
  discover → filter → ditch the ones we've already met → clean → score → save → rank

The "ditch the ones we've already met" step is the money-saver — run this twice
and the second run scores nothing, because the memory bank remembers everyone.
"""

import asyncio
from dotenv import load_dotenv

load_dotenv()  # grab the API key from .env before the Claude client wakes up

from services.discovery import discover_all
from services.job_filter import filter_jobs, filter_us_only
from services.job_cleaner import clean_job_description
from services.claude_client import score_batch_jobs
from services.visa_intel import get_visa_signal, classify_visa_disagreement
from services import storage

# How many fresh jobs to score per run. Small while we're poking at it.
SAMPLE_SIZE = 10


async def run(sample_size: int = SAMPLE_SIZE):
    # 1. Round up everyone (free)
    jobs = await discover_all()

    # 2. Toss the off-target and the off-continent (free)
    jobs = filter_jobs(jobs)
    jobs = filter_us_only(jobs)
    print(f"\n── {len(jobs)} jobs cleared role + US filters ──")

    # 3. Show the bouncer the guest list: only let in faces we haven't scored before
    jobs = storage.filter_unseen(jobs)
    print(f"── {len(jobs)} of those are strangers (not yet in the memory bank) ──")

    if not jobs:
        print("\nNothing new under the sun. Memory bank already knows them all. 🪙\n")
        return

    # 4. Grab a handful to actually score (keeps the bill tiny while testing)
    sample = jobs[:sample_size]
    print(f"── Scoring {len(sample)} of them this run ──\n")

    # Keep the JobInput objects around so we can save them with their scores later
    by_id = {j.job_id: j for j in sample}

    # 5. Tidy up the descriptions and shape them for the scorer
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

    # 6. Send them off to be judged, five at a time
    all_scores = []
    for i in range(0, len(to_score), 5):
        batch = to_score[i:i + 5]
        results = await score_batch_jobs(batch)
        all_scores.extend(results)

    # 7. Stash every verdict in the memory bank — now with the visa oracle's read
    #    AND the JD-vs-history reconciliation riding along. We never re-score these.
    from api.models import JobScore
    saved = 0
    for s in all_scores:
        job = by_id.get(s.get("job_id"))
        if not job:
            continue
        # Ask the oracle what the H-1B record says about THIS company.
        visa = get_visa_signal(job.company)
        # Reconcile what the POSTING said against what the company actually DID.
        verdict, verdict_note = classify_visa_disagreement(
            s.get("visa_signal"), visa.status, visa.new_hires
        )
        # Stash everything back onto the score dict so the ranking can show it.
        s["sponsor_status"] = visa.status
        s["sponsor_new"] = visa.new_hires
        s["sponsor_renewals"] = visa.renewals
        s["visa_verdict"] = verdict
        s["visa_verdict_note"] = verdict_note
        try:
            storage.save_scored_job(job, JobScore(**s), visa, verdict, verdict_note)
            saved += 1
        except Exception as e:
            print(f"  ⚠️  couldn't save {s.get('job_id')}: {e}")
    print(f"── Tucked {saved} scored jobs into the memory bank ──\n")

    # 8. Line them up, best first
    all_scores.sort(key=lambda s: s.get("score", 0), reverse=True)

    # 9. The reveal — the verdict is the headline now: the one-line reconciliation
    #    of what the posting claims vs what the company's H-1B record actually shows.
    print("══ RANKED RESULTS ══\n")
    for s in all_scores:
        print(f"  [{s.get('score')}]  {s.get('role_type')}  |  apply: {s.get('apply')}")
        print(f"        {s.get('job_id')}  ({s.get('source')})")
        print(f"        🛂 {s.get('visa_verdict')}: {s.get('visa_verdict_note')}")
        print(f"        {s.get('reasoning')}\n")


if __name__ == "__main__":
    asyncio.run(run())