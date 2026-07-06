from pathlib import Path
from tools.resume_loader import load_resume
from tools.tailor_resume import tailor_resume


"""
orchestrator.py — the brain.

This is the part that makes the whole thing an AGENT instead of a pipeline. We
hand Claude a box of tools and a goal, and CLAUDE decides what to do — which tool
to call, in what order, reacting to what it sees. Our code just runs whatever tool
Claude asks for and hands the result back. The loop repeats until Claude says it's
done (or we hit a safety cap so it can't run away with your token budget).

First version, deliberately small and safe:
  • works on jobs already in your DB (no big discovery bill)
  • prints every decision so you can watch it think
  • stops BEFORE the expensive tailoring step and asks you to approve
"""

import json
import asyncio
import sqlite3
from dotenv import load_dotenv
from anthropic import AsyncAnthropic

load_dotenv()
client = AsyncAnthropic()

_MODEL = "claude-sonnet-4-6"   # the agent's "thinking" brain
_MAX_STEPS = 12                # hard safety cap: agent can't loop forever
DB_PATH = "jobs.db"


# ── The tools the agent is allowed to use ────────────────────────────────────
TOOLS = [
    {
        "name": "list_scored_jobs",
        "description": "List jobs already scored and stored in the database, best-fit first. "
                       "Use this first to see what jobs are available to work with. "
                       "Returns job_id, title, company, score, visa verdict for each.",
        "input_schema": {
            "type": "object",
            "properties": {
                "min_score": {"type": "integer", "description": "Only return jobs scoring at or above this (0-100)."}
            },
            "required": ["min_score"],
        },
    },
    {
        "name": "get_job_details",
        "description": "Get the full details of one job by its job_id, including the full "
                       "description and visa info. Use before deciding whether it's worth pursuing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "propose_for_application",
        "description": "Propose a job as worth applying to. Call this for each job you judge "
                       "a strong fit and worth tailoring a resume for. This does NOT tailor "
                       "anything yet — it adds the job to a list the human will approve first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "why": {"type": "string", "description": "One sentence: why this job is worth pursuing."},
            },
            "required": ["job_id", "why"],
        },
    },
]


# ── The actual code that runs when Claude picks a tool ────────────────────────
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def handle_list_scored_jobs(min_score: int) -> str:
    with _db() as conn:
        rows = conn.execute(
            "SELECT job_id, title, company, score, visa_verdict "
            "FROM jobs WHERE score >= ? ORDER BY score DESC LIMIT 25",
            (min_score,),
        ).fetchall()
    jobs = [dict(r) for r in rows]
    return json.dumps(jobs) if jobs else "No jobs found at or above that score."


def handle_get_job_details(job_id: str) -> str:
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return f"No job found with id {job_id}."
    j = dict(row)
    if j.get("description"):
        j["description"] = j["description"][:1500]
    return json.dumps(j)


# The agent's proposals land here — the list the HUMAN approves before any
# tailoring happens. This is the gate: the agent fills the basket, you check out.
PROPOSED = []


def handle_propose_for_application(job_id: str, why: str) -> str:
    PROPOSED.append({"job_id": job_id, "why": why})
    return f"Noted '{job_id}' as a proposal. ({len(PROPOSED)} so far.)"


TOOL_HANDLERS = {
    "list_scored_jobs": handle_list_scored_jobs,
    "get_job_details": handle_get_job_details,
    "propose_for_application": handle_propose_for_application,
}


# ── The agent loop itself ─────────────────────────────────────────────────────
async def run_agent(goal: str):
    """Hand Claude the goal + tools, then loop: Claude decides -> we run the tool
    -> feed the result back -> repeat. Stops when Claude is done or we hit the cap."""
    messages = [{"role": "user", "content": goal}]

    print(f"\n🤖 Agent goal: {goal}\n")

    for step in range(1, _MAX_STEPS + 1):
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=1500,
            tools=TOOLS,
            messages=messages,
        )

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"💭 [step {step}] {block.text.strip()}\n")

        tool_calls = [b for b in response.content if b.type == "tool_use"]

        if not tool_calls:
            print("✅ Agent finished.\n")
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for call in tool_calls:
            name, args = call.name, call.input
            print(f"🔧 [step {step}] Claude calls: {name}({json.dumps(args)})")
            handler = TOOL_HANDLERS.get(name)
            if handler:
                try:
                    result = handler(**args)
                except Exception as e:
                    result = f"Tool error: {e}"
            else:
                result = f"Unknown tool: {name}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"⚠️  Hit the {_MAX_STEPS}-step safety cap — stopping so it can't run away.\n")

    print("══ AGENT PROPOSALS (awaiting your approval) ══\n")
    if not PROPOSED:
        print("  (the agent didn't propose any jobs)")
    for p in PROPOSED:
        print(f"  • {p['job_id']} — {p['why']}")
    print()

# ── The human gate + the produce step ─────────────────────────────────────────
def get_human_approval(proposals: list[dict]) -> list[dict]:
    """Show the agent's proposals and let the human pick which to actually pursue.
    THIS is the gate — nothing expensive happens until you say so."""
    if not proposals:
        return []
    print("══ APPROVE PROPOSALS ══")
    print("The agent proposed these. Which should I tailor your resume for?\n")
    for i, p in enumerate(proposals, 1):
        print(f"  [{i}] {p['job_id']}")
        print(f"      {p['why']}\n")
    raw = input("Enter the numbers to approve (e.g. '1 3'), or press Enter for none: ").strip()
    if not raw:
        return []
    chosen = []
    for token in raw.replace(",", " ").split():
        if token.isdigit() and 1 <= int(token) <= len(proposals):
            chosen.append(proposals[int(token) - 1])
    return chosen


async def produce_for_approved(approved: list[dict]):
    """For each job the human approved: pull the anchor resume + the job's details
    + the scorer's keyword hints, tailor the resume, save it, and show the change
    report. Runs Sonnet once per approved job — bounded by YOUR approval."""
    if not approved:
        print("\nNothing approved — no resumes tailored. (No tokens spent.)\n")
        return

    anchor = load_resume()  # the multi-user seam: today, your resume.md

    for p in approved:
        job_id = p["job_id"]
        with _db() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            print(f"  ⚠️  {job_id} vanished from the DB, skipping.")
            continue
        job = dict(row)

        print(f"\n✍️  Tailoring your resume for: {job['title']} @ {job['company']}...")
        result = await tailor_resume(
            anchor_markdown=anchor,
            job_title=job["title"],
            company=job["company"],
            job_description=job.get("description") or "",
            # scorer hints aren't stored as columns, so we skip them here for now;
            # the tool works without them (it'll read the JD directly).
        )

        # Save the tailored resume to outputs/, named by company + job_id
        safe_company = "".join(c for c in job["company"] if c.isalnum() or c in " -_").strip().replace(" ", "_")
        out_path = Path("outputs") / f"resume_{safe_company}_{job_id}.md"
        out_path.write_text(result.get("tailored_resume_markdown", ""), encoding="utf-8")

        # Show the change report — what changed, what gaps were left honest
        print(f"   📄 Saved → {out_path}")
        print("   ── what changed ──")
        for c in result.get("change_report", []):
            print(f"     • {c.get('change')}  ↳ {c.get('reason')}")
        gaps = result.get("gaps_left_honest", [])
        if gaps:
            print("   ── gaps left honest (NOT faked) ──")
            for g in gaps:
                print(f"     • {g}")
        print()

if __name__ == "__main__":
    GOAL = (
        "I'm job hunting. Look at the jobs already scored in my database "
        "(use a min_score of 60). For the most promising ones, get their details, "
        "and propose the ones genuinely worth applying to — explaining why for each. "
        "Don't propose more than 3. Then stop."
    )

    async def main():
        await run_agent(GOAL)            # agent finds & proposes
        approved = get_human_approval(PROPOSED)   # YOU approve
        await produce_for_approved(approved)      # tailor resumes for approved only

    asyncio.run(main())