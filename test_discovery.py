import asyncio
from services.discovery import discover_all
from services.job_filter import filter_jobs
from services.job_cleaner import clean_job_description


async def main():
    jobs = await discover_all()
    filtered = filter_jobs(jobs)

    print(f"\n── Title filter ──")
    print(f"  Before: {len(jobs)} jobs")
    print(f"  After:  {len(filtered)} jobs match target roles\n")

    print("── Sample: cleaned descriptions (first 3) ──")
    for job in filtered[:3]:
        cleaned = clean_job_description(job.description)
        print(f"\n  {job.title} @ {job.company}")
        print(f"  CLEANED: {cleaned[:300]}...")


if __name__ == "__main__":
    asyncio.run(main())