import logging
from fastapi import APIRouter, HTTPException
from api.models import (
    JobInput, JobScore, ResumeGap,
    BatchJobInput, BatchScoreOutput
)
from services.claude_client import (
    score_single_job, score_batch_jobs, analyze_resume_gap
)
from services.job_cleaner import clean_job_description

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scoring", tags=["Scoring"])


@router.post("/score", response_model=JobScore)
async def score_job(job: JobInput):
    """Score a single job against the candidate profile."""
    try:
        cleaned = clean_job_description(job.description)
        result = await score_single_job(
            job_id=job.job_id,
            title=job.title,
            company=job.company,
            location=job.location,
            cleaned_description=cleaned,
            source=job.source.value,   # pass the source through to the score
        )
        return JobScore(**result)
    except Exception as e:
        logger.error(f"Scoring failed for {job.job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/score-batch", response_model=BatchScoreOutput)
async def score_batch(batch: BatchJobInput):
    """Score up to 10 jobs. Splits into sub-batches of 5 internally.
    Partial failures are tolerated: good scores return, bad ones are counted."""
    all_scores: list[JobScore] = []
    error_count = 0

    cleaned_jobs = [{
        "job_id": job.job_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "cleaned_description": clean_job_description(job.description),
        "source": job.source.value,
    } for job in batch.jobs]

    for i in range(0, len(cleaned_jobs), 5):
        sub = cleaned_jobs[i : i + 5]
        try:
            results = await score_batch_jobs(sub)
            for r in results:
                try:
                    all_scores.append(JobScore(**r))
                except Exception as ve:
                    logger.error(f"Validation failed on result: {ve} — raw: {r}")
                    error_count += 1
        except Exception as e:
            logger.error(f"Sub-batch failed: {e}")
            error_count += len(sub)

    return BatchScoreOutput(
        scores=all_scores,
        total_jobs=len(batch.jobs),
        apply_count=sum(1 for s in all_scores if s.apply),
        error_count=error_count,
    )


@router.post("/resume-gap", response_model=ResumeGap)
async def resume_gap(job: JobInput):
    """Analyze how the candidate's skills line up against one job.
    Run after approval to decide whether to tailor the resume."""
    try:
        cleaned = clean_job_description(job.description)
        result = await analyze_resume_gap(
            job_id=job.job_id,
            title=job.title,
            company=job.company,
            cleaned_description=cleaned,
        )
        return ResumeGap(**result)
    except Exception as e:
        logger.error(f"Resume gap analysis failed for {job.job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))