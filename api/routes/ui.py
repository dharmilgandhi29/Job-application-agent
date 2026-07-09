"""
ui.py — Mr. Jober's office.

Detective-themed dashboard: scored jobs from jobs.db become "case files," ranked
by lead strength, each with a visa background-check stamp. Clicking a card expands
it in place to reveal the detail; a button opens the full case file page.

  GET /                  -> the dashboard page (redirects to /welcome if not set up)
  GET /api/jobs          -> scored jobs as JSON (list)
  GET /api/job/{job_id}  -> one job's full detail
"""

import sqlite3
from fastapi import APIRouter
import json
import os
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse

router = APIRouter(tags=["UI"])

DB_PATH = "jobs.db"


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_VERDICT_LABELS = {
    "silent_but_sponsors":      ("visa verified", "good"),
    "says_closed_but_sponsors": ("visa verified", "good"),
    "renewals_only":            ("renewals only", "warn"),
    "claims_but_no_history":    ("unconfirmed", "warn"),
    "no_history":               ("no record", "neutral"),
    "unknown":                  ("visa unknown", "neutral"),
}


def _label(verdict):
    return _VERDICT_LABELS.get(verdict or "unknown", ("visa unknown", "neutral"))


@router.get("/api/jobs")
async def api_jobs():
    with _db() as conn:
        rows = conn.execute(
            "SELECT job_id, title, company, location, score, role_type, "
            "visa_verdict, sponsor_new, job_url "
            "FROM jobs WHERE score IS NOT NULL ORDER BY score DESC"
        ).fetchall()
    jobs = []
    for r in rows:
        j = dict(r)
        label, tone = _label(j.get("visa_verdict"))
        j["visa_label"] = label
        j["visa_tone"] = tone
        jobs.append(j)
    stats = {
        "total": len(jobs),
        "sponsors": sum(1 for j in jobs if j["visa_tone"] == "good"),
        "strong": sum(1 for j in jobs if (j["score"] or 0) >= 65),
    }
    return JSONResponse({"jobs": jobs, "stats": stats})


@router.get("/api/job/{job_id}")
async def api_job(job_id: str):
    """One job's full detail for the expanded view."""
    with _db() as conn:
        r = conn.execute(
            "SELECT job_id, title, company, location, job_url, source, description, "
            "score, role_type, reasoning, seniority_fit, status, first_seen, "
            "sponsor_status, sponsor_new, sponsor_renewals, visa_verdict, visa_verdict_note "
            "FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    j = dict(r)
    label, tone = _label(j.get("visa_verdict"))
    j["visa_label"] = label
    j["visa_tone"] = tone
    return JSONResponse(j)


def _sse(agen_factory):
    async def event_gen():
        try:
            async for ev in agen_factory():
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'step':'error','status':'warn','msg':str(e)})}\n\n"
    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/job/{job_id}/tailor")
async def tailor_resume(job_id: str):
    """Mr. Fixer: tailor the resume only (no research spend)."""
    from agent.producer_stream import tailor_resume_stream
    return _sse(lambda: tailor_resume_stream(job_id))


@router.get("/api/job/{job_id}/letter")
async def write_letter(job_id: str):
    """Mr. Wordsmith: write the cover letter (runs research)."""
    from agent.producer_stream import write_letter_stream
    return _sse(lambda: write_letter_stream(job_id))


@router.get("/api/job/{job_id}/full")
async def full_case(job_id: str):
    """Do the Whole Thing: resume + shared research + cover letter, one pass."""
    from agent.producer_stream import full_case_stream
    return _sse(lambda: full_case_stream(job_id))


@router.get("/api/download")
async def download(path: str):
    """Download a produced file. We only ever serve from outputs/ by basename,
    so whatever path comes in (absolute or relative), we reduce it to its filename
    and look for it in outputs/ — no traversal possible."""
    name = os.path.basename(path)          # strip any directory part
    if not name or "/" in name or "\\" in name:
        return JSONResponse({"error": "bad path"}, status_code=400)
    full = os.path.join("outputs", name)
    if not os.path.exists(full):
        return JSONResponse({"error": "not found", "looked_for": full}, status_code=404)
    return FileResponse(full, filename=name)


@router.post("/api/job/{job_id}/status")
async def set_job_status(job_id: str, payload: dict):
    """Mark a job's application status (applied / following_up / interview / etc.)
    and stamp the date when it becomes 'applied'."""
    import datetime as _dt
    from services.storage import set_status
    status = (payload.get("status") or "").strip()
    if not status:
        return JSONResponse({"error": "no status"}, status_code=400)
    applied_date = None
    if status == "applied":
        applied_date = payload.get("applied_date") or _dt.date.today().isoformat()
    set_status(job_id, status, applied_date=applied_date, notes=payload.get("notes"))
    return JSONResponse({"ok": True, "status": status, "applied_date": applied_date})


@router.get("/api/tracked")
async def api_tracked():
    """Jobs the user has acted on, enriched for the value board: two-stage nudges,
    LinkedIn outreach search links, funnel counts, and a simple timeline."""
    import datetime as _dt
    import urllib.parse as _url
    from collections import Counter, OrderedDict
    from services.storage import get_tracked

    jobs = get_tracked()
    today = _dt.date.today()

    def li_people(company, extra):
        q = f'{company} {extra}'
        return "https://www.linkedin.com/search/results/people/?keywords=" + _url.quote(q)

    for j in jobs:
        days = None
        if j.get("applied_date"):
            try:
                days = (today - _dt.date.fromisoformat(j["applied_date"])).days
            except Exception:
                days = None
        j["days_since"] = days
        # Two-stage: immediate outreach flag (always for a fresh applied job),
        # and a 2-day follow-up flag if still just 'applied' and quiet.
        j["do_outreach"] = (j.get("status") == "applied")
        j["do_followup"] = (j.get("status") == "applied" and days is not None and days >= 2)
        co = j.get("company") or ""
        # Location: use the job's location (first city) to bias the people search.
        loc = (j.get("location") or "").split("|")[0].split(";")[0].split(",")[0].strip()
        loc_suffix = f" {loc}" if loc else ""
        j["li"] = {
            "recruiter": li_people(co, "recruiter talent acquisition" + loc_suffix),
            "hiring_manager": li_people(co, f'hiring manager {j.get("role_type","")}' + loc_suffix),
            "team": li_people(co, j.get("title","") + loc_suffix),
        }

    # Funnel counts
    order = ["applied", "following_up", "interview", "offer"]
    by_status = Counter(j.get("status") for j in jobs)
    funnel = [{"stage": s, "count": by_status.get(s, 0)} for s in order]
    # closed/rejected shown separately
    closed = by_status.get("closed", 0) + by_status.get("rejected", 0)

    # Timeline: applications per day (by applied_date)
    tl = Counter()
    for j in jobs:
        if j.get("applied_date"):
            tl[j["applied_date"]] += 1
    timeline = [{"date": d, "count": c} for d, c in sorted(tl.items())]

    # Today's actions
    actions = []
    for j in jobs:
        if j["do_followup"]:
            actions.append({"job_id": j["job_id"], "kind": "followup",
                            "text": f"You applied to {j['company']} {j['days_since']} days ago and it's quiet. Follow up, or ping someone inside."})
        elif j["do_outreach"] and (j["days_since"] == 0 or j["days_since"] is None):
            actions.append({"job_id": j["job_id"], "kind": "outreach",
                            "text": f"Fresh application at {j['company']}. Get ahead of the pile: reach out to a recruiter or someone on the team now."})

    stats = {
        "total": len(jobs),
        "interview": by_status.get("interview", 0),
        "offer": by_status.get("offer", 0),
        "nudges": len(actions),
        "closed": closed,
    }
    return JSONResponse({"jobs": jobs, "stats": stats, "funnel": funnel,
                         "timeline": timeline, "actions": actions})


@router.get("/tracker", response_class=HTMLResponse)
async def tracker_page():
    from api.routes.onboarding import _is_ready
    if not _is_ready():
        return RedirectResponse(url="/welcome")
    return HTMLResponse(_TRACKER_PAGE)


@router.get("/api/job/{job_id}/outreach")
async def outreach_message(job_id: str, target: str = "recruiter", variant: int = 0):
    """On demand only: draft a short, personalized LinkedIn outreach note. variant
    nudges the tone so 'draft another' gives genuinely different options."""
    from config.user import NAME, PROFILE
    from anthropic import AsyncAnthropic
    from dotenv import load_dotenv
    load_dotenv()
    _client = AsyncAnthropic()

    with _db() as conn:
        row = conn.execute("SELECT title, company, role_type FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    j = dict(row)

    who = {"recruiter": "a recruiter", "hiring_manager": "the hiring manager",
           "team": "someone on the team"}.get(target, "someone at the company")
    bg = PROFILE.get("experience_years", "")
    skills = ", ".join(PROFILE.get("technical_skills", [])[:6])

    tones = [
        "warm and curious, leading with genuine interest in their work",
        "confident and direct, leading with your strongest relevant skill",
        "humble and specific, mentioning one concrete thing about the role",
    ]
    tone = tones[variant % len(tones)]

    prompt = (
        f"Write a short LinkedIn connection note (under 280 characters, no em-dashes) "
        f"from {NAME} to {who} at {j['company']}, about the {j['title']} role. "
        f"Tone: {tone}. Background: {bg}; key skills: {skills}. "
        f"Be specific and human, not salesy. No greeting like 'Dear', just the note body, "
        f"one short paragraph. Return only the message text."
    )
    try:
        resp = await _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            temperature=1.0,
            messages=[{"role": "user", "content": prompt}],
        )
        msg = resp.content[0].text.strip()
        return JSONResponse({"ok": True, "message": msg})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: str):
    from api.routes.onboarding import _is_ready
    if not _is_ready():
        return RedirectResponse(url="/welcome")
    return HTMLResponse(_JOB_PAGE)


@router.get("/", response_class=HTMLResponse)
async def root():
    """New visitors meet Mr. Jober on the landing page; set-up users go straight
    to their case files."""
    from api.routes.onboarding import _is_ready
    if not _is_ready():
        return HTMLResponse(_LANDING_PAGE)
    return HTMLResponse(_PAGE)


@router.get("/landing", response_class=HTMLResponse)
async def landing():
    """The landing page on demand (so set-up users can revisit it)."""
    return HTMLResponse(_LANDING_PAGE)


_MASCOT = """<svg viewBox="0 0 72 72" fill="none" class="mascot">
  <path d="M12 30 Q36 8 60 30 L55 22 Q36 4 17 22 Z" fill="#FF6B4A"/>
  <rect x="12" y="28" width="48" height="6" rx="3" fill="#FF6B4A"/>
  <circle cx="36" cy="42" r="16" fill="#FFD9A0"/>
  <circle cx="30" cy="40" r="5.5" fill="#fff" stroke="#2D2A3E" stroke-width="2"/>
  <circle cx="42" cy="40" r="5.5" fill="#fff" stroke="#2D2A3E" stroke-width="2"/>
  <circle cx="30.5" cy="40.5" r="2.2" fill="#2D2A3E"/>
  <circle cx="42.5" cy="40.5" r="2.2" fill="#2D2A3E"/>
  <path d="M31 50 Q36 53 41 50" stroke="#B45309" stroke-width="2.4" fill="none" stroke-linecap="round"/>
</svg>"""


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mr. Jober</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700;9..144,800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/motion.css">
<style>
  :root{ --paper:#FBF6EC; --card:#FFFDF9; --ink:#2D2A3E; --muted:#9A8F7C;
    --line:#EBE1CE; --coral:#FF6B4A; --purple:#8659C4; --green:#22A45C; --amber:#B45309; }
  *{box-sizing:border-box;}
  html{-webkit-font-smoothing:antialiased;}
  body{margin:0; background:var(--paper); color:var(--ink);
    font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;}
  .display{font-family:Fraunces,Georgia,serif;}
  .mascot{width:100%; height:100%;}
  .nav{position:sticky; top:0; z-index:20; background:rgba(255,253,249,.85);
    backdrop-filter:saturate(180%) blur(16px); border-bottom:2px solid var(--line);}
  .wrap{max-width:920px; margin:0 auto; padding:0 24px;}
  .navrow{display:flex; align-items:center; justify-content:space-between; padding:16px 0;}
  .brand{display:flex; align-items:center; gap:13px;}
  .badge-avatar{width:46px; height:46px; border-radius:13px; background:var(--ink);
    display:flex; align-items:center; justify-content:center; padding:6px;}
  .brand h1{font-size:20px; font-weight:800; margin:0; letter-spacing:-.02em;}
  .brand p{font-size:11.5px; color:var(--muted); margin:1px 0 0; font-weight:500;}
  .status{display:flex; gap:7px; align-items:center; background:#EAF9EF; padding:7px 13px; border-radius:20px;}
  .status .dot{width:7px; height:7px; border-radius:50%; background:var(--green); animation:pulse 2s infinite;}
  .status span{font-size:12px; font-weight:700; color:#15803D;}
  @keyframes pulse{0%,100%{opacity:1;}50%{opacity:.35;}}
  .greeting{display:flex; gap:14px; align-items:flex-start; padding:26px 0 18px;}
  .greeting .av{width:38px; height:38px; border-radius:10px; background:var(--ink); flex-shrink:0; display:flex; align-items:center; justify-content:center; padding:6px;}
  .bubble{background:var(--card); border:2px solid var(--line); border-radius:4px 16px 16px 16px; padding:14px 18px; font-size:14.5px; line-height:1.6; color:#413B4D;}
  .bubble b{color:var(--coral);} .bubble .g{color:#15803D;}
  .stats{display:flex; gap:12px; padding-bottom:20px;}
  .stat{flex:1; border-radius:15px; padding:16px 18px; color:#fff;}
  .stat .n{font-size:27px; font-weight:800; line-height:1; font-family:Fraunces,serif;}
  .stat .l{font-size:11.5px; opacity:.92; margin-top:4px; font-weight:600;}
  .sectionhead{display:flex; align-items:center; gap:8px; padding:4px 0 14px;}
  .sectionhead svg{stroke:var(--muted);}
  .sectionhead span{font-size:12.5px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.09em;}
  .grid{display:grid; gap:11px; padding-bottom:24px;}
  .lead{background:var(--card); border:2px solid var(--line); border-radius:15px; overflow:hidden;
    transition:box-shadow .2s, border-color .2s; opacity:0; transform:translateY(14px);}
  .lead.in{opacity:1; transform:none;}
  .lead .head{padding:15px 18px; display:flex; align-items:center; gap:16px; cursor:pointer;}
  .lead:hover{box-shadow:0 10px 26px rgba(45,42,62,.09); border-color:#D9CDB5;}
  .lead.open{border-color:var(--coral);}
  .score{width:54px; height:54px; border-radius:13px; display:flex; align-items:center; justify-content:center; flex-shrink:0; font-size:23px; font-weight:800; color:#fff; font-family:Fraunces,serif;}
  .lead .meta{flex:1; min-width:0;}
  .lead .role{font-size:14.5px; font-weight:700;}
  .lead .sub{font-size:12.5px; color:var(--muted); margin-top:1px;}
  .stamp{font-size:10.5px; font-weight:800; padding:5px 10px; border-radius:7px; letter-spacing:.03em; white-space:nowrap;}
  .stamp.good{background:#EAF9EF; color:#15803D;}
  .stamp.warn{background:#FFF1E0; color:var(--amber);}
  .stamp.neutral{background:#F1EEE7; color:#8B8B96;}
  .chev{color:var(--muted); transition:transform .25s; flex-shrink:0;}
  .lead.open .chev{transform:rotate(180deg);}
  .detail{max-height:0; overflow:hidden; transition:max-height .3s ease;}
  .detail-in{padding:0 18px 18px;}
  .dsec{border-top:1px solid var(--line); padding-top:14px; margin-top:4px;}
  .dsec h4{font-size:11px; font-weight:800; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; margin:0 0 6px;}
  .dsec p{font-size:13.5px; line-height:1.6; color:#413B4D; margin:0;}
  .jd{font-size:13px; line-height:1.6; color:#5A5468; max-height:150px; overflow:auto; white-space:pre-wrap;}
  .pills{display:flex; gap:8px; flex-wrap:wrap; margin-top:4px;}
  .pill{font-size:11.5px; font-weight:600; padding:4px 10px; border-radius:8px; background:#F1EEE7; color:#6E6A74;}
  .dactions{display:flex; gap:10px; margin-top:16px; align-items:center;}
  .openbtn{background:var(--coral); color:#fff; font-size:13px; font-weight:700; padding:10px 18px; border-radius:10px; border:none; cursor:pointer; font-family:inherit; transition:transform .15s;}
  .openbtn:hover{transform:translateY(-1px);}
  .origlink{font-size:13px; font-weight:600; color:var(--muted); text-decoration:none;}
  .origlink:hover{color:var(--ink);}
  .cta{background:var(--ink); border-radius:16px; padding:17px 22px; display:flex; align-items:center; justify-content:space-between; margin-bottom:40px;}
  .cta .left{display:flex; align-items:center; gap:13px;}
  .cta svg{stroke:var(--coral);}
  .cta .t{font-size:14.5px; font-weight:700; color:#fff;}
  .cta .s{font-size:12px; color:#A5A0B5; margin-top:2px;}
  .cta button{background:var(--coral); color:#fff; font-size:12.5px; font-weight:700; padding:10px 17px; border-radius:10px; border:none; cursor:pointer; font-family:inherit; transition:transform .15s;}
  .cta button:hover{transform:scale(1.04);}
  .hunch-verdict{font-weight:700; color:var(--coral); margin-bottom:6px !important;}
  .jd-toggle{display:flex; align-items:center; justify-content:space-between; cursor:pointer;}
  .jd-chev{transition:transform .25s; flex-shrink:0;}
  .jd-wrap{max-height:0; overflow:hidden; transition:max-height .3s ease;}
  .jd-wrap.open{max-height:400px;}
  .jd{font-size:13px; line-height:1.6; color:#5A5468; white-space:pre-wrap; overflow:auto; max-height:360px; padding-top:10px;}
  @media (prefers-reduced-motion:reduce){ .lead{opacity:1; transform:none;} .status .dot{animation:none;} }
  @media (max-width:640px){ .stats{flex-wrap:wrap;} .stat{min-width:44%;} }
</style>
</head>
<body>
  <nav class="nav js-nav"><div class="wrap"><div class="navrow">
    <div class="brand">
      <div class="badge-avatar">__MASCOT__</div>
      <div><h1 class="display">Mr. Jober</h1><p>private investigator, job division</p></div>
    </div>
    <div style="display:flex; align-items:center; gap:14px;">
      <a href="/tracker" style="display:flex; align-items:center; gap:6px; color:var(--ink); text-decoration:none; font-size:13px; font-weight:700; border:2px solid var(--line); padding:8px 14px; border-radius:10px; background:var(--card);">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/></svg>
        The board
      </a>
      <div class="status"><div class="dot"></div><span>on the case</span></div>
    </div>
  </div></div></nav>
  <main class="wrap">
    <div class="greeting">
      <div class="av">__MASCOT__</div>
      <div class="bubble" id="greeting">Cracking open the case files...</div>
    </div>
    <div class="stats" id="stats"></div>
    <div class="sectionhead">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke-width="2.4" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      <span>Case files, ranked by lead strength</span>
    </div>
    <div class="grid" id="grid"></div>
    <div class="cta">
      <div class="left">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.5-1.5"/></svg>
        <div><div class="t">Got a lead from somewhere else?</div><div class="s">Hand me the link, I'll run it down.</div></div>
      </div>
      <button onclick="alert('Coming soon: paste a job link and Mr. Jober investigates it.')">Investigate a link</button>
    </div>
  </main>
<script>
const SCORE_COLORS = c => c >= 73 ? 'var(--coral)' : c >= 63 ? 'var(--purple)' : 'var(--green)';
const esc = s => (s||'').replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
let openId = null;

function leadRow(j){
  const el = document.createElement('div');
  el.className = 'lead';
  el.dataset.id = j.job_id;
  const loc = (j.location || '').split(/[|;]/)[0].trim();
  el.innerHTML = `
    <div class="head">
      <div class="score" style="background:${SCORE_COLORS(j.score)}">${j.score}</div>
      <div class="meta">
        <div class="role">${esc(j.title)}</div>
        <div class="sub">${esc(j.company)}${loc ? ' \\u00b7 ' + esc(loc) : ''}</div>
      </div>
      <span class="stamp ${j.visa_tone}">${j.visa_label.toUpperCase()}</span>
      <svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
    </div>
    <div class="detail"><div class="detail-in" data-detail></div></div>`;
  el.querySelector('.head').addEventListener('click', () => toggle(el, j.job_id));
  return el;
}

async function toggle(el, id){
  const detail = el.querySelector('.detail');
  const inner = el.querySelector('[data-detail]');
  // close if already open
  if(openId === id){ detail.style.maxHeight = '0'; el.classList.remove('open'); openId = null; return; }
  // close any other
  document.querySelectorAll('.lead.open').forEach(o => { o.classList.remove('open'); o.querySelector('.detail').style.maxHeight='0'; });
  openId = id;
  el.classList.add('open');
  if(!inner.dataset.loaded){
    inner.innerHTML = '<div class="dsec"><p style="color:#9A8F7C">Pulling the file...</p></div>';
    detail.style.maxHeight = '80px';
    try {
      const j = await fetch('/api/job/' + encodeURIComponent(id)).then(r => r.json());
      inner.innerHTML = detailHTML(j);
      inner.dataset.loaded = '1';
    } catch(e){ inner.innerHTML = '<div class="dsec"><p>Could not pull this file.</p></div>'; }
  }
  requestAnimationFrame(() => { detail.style.maxHeight = inner.scrollHeight + 40 + 'px'; });
}

function detailHTML(j){
  const stripHtml = s => (s||'').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&').replace(/<[^>]*>/g,' ').replace(/&nbsp;/g,' ').replace(/\s+/g,' ').trim();
  const jd = esc(stripHtml(j.description));
  const seen = j.first_seen ? new Date(j.first_seen).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}) : 'unknown';

  // Jober's Hunch: assembled from what we already computed. No LLM call.
  const bits = [];
  if(j.reasoning) bits.push(esc(j.reasoning));
  const tags = [];
  if(j.role_type) tags.push(`<span class="pill">${esc(j.role_type)}</span>`);
  if(j.seniority_fit) tags.push(`<span class="pill">seniority: ${esc(j.seniority_fit)}</span>`);
  if(j.sponsor_new != null && j.sponsor_new > 0) tags.push(`<span class="pill">${j.sponsor_new} new H-1B on record</span>`);

  // A quirky one-liner verdict based on the score, in Jober's voice.
  let verdict;
  const s = j.score || 0;
  if(s >= 73) verdict = "This one's got legs, boss. Worth chasing.";
  else if(s >= 63) verdict = "Solid lead. Some gaps, but nothing that kills it.";
  else verdict = "Bit of a long shot, but I've seen weirder pan out.";

  return `
    <div class="dsec">
      <h4>Jober's Hunch</h4>
      <p class="hunch-verdict">${verdict}</p>
      <p>${bits.join(' ') || 'No notes on this one.'}</p>
      <div class="pills">${tags.join('')}</div>
    </div>
    <div class="dsec">
      <h4>Background check</h4>
      <p>${esc(j.visa_verdict_note) || j.visa_label}</p>
    </div>
    <div class="dsec">
      <div class="jd-toggle" onclick="toggleJd(this)">
        <h4 style="margin:0;">The full file <span style="font-weight:600;text-transform:none;letter-spacing:0;color:#B8AE9A">· first spotted ${seen}</span></h4>
        <svg class="jd-chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#9A8F7C" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
      </div>
      <div class="jd-wrap"><div class="jd">${jd}</div></div>
    </div>
    <div class="dactions">
      <button class="openbtn" onclick="location.href='/job/${encodeURIComponent(j.job_id)}'">Open full case file</button>
      ${j.job_url ? `<a class="origlink" href="${esc(j.job_url)}" target="_blank" rel="noopener">View original posting</a>` : ''}
    </div>`;
}

function toggleJd(el){
  const wrap = el.parentElement.querySelector('.jd-wrap');
  const chev = el.querySelector('.jd-chev');
  const open = wrap.classList.toggle('open');
  chev.style.transform = open ? 'rotate(180deg)' : '';
  // resize the parent detail panel to fit
  const detail = el.closest('.detail');
  if(detail){ requestAnimationFrame(() => { detail.style.maxHeight = detail.querySelector('.detail-in').scrollHeight + 60 + 'px'; }); }
}

fetch('/api/jobs').then(r => r.json()).then(data => {
  const {jobs, stats} = data;
  document.getElementById('greeting').innerHTML =
    `Case cracked open, boss. I dug up <b>${stats.total} leads</b> and ran background checks on every one. The <span class="g">green stamps</span> mean the company's got a real visa sponsorship record. Click a file to see what I found.`;
  document.getElementById('stats').innerHTML = `
    <div class="stat" style="background:var(--coral)"><div class="n">${stats.total}</div><div class="l">leads dug up</div></div>
    <div class="stat" style="background:var(--green)"><div class="n">${stats.sponsors}</div><div class="l">visa sponsors</div></div>
    <div class="stat" style="background:var(--purple)"><div class="n">${stats.strong}</div><div class="l">strong leads</div></div>`;
  const grid = document.getElementById('grid');
  jobs.forEach(j => grid.appendChild(leadRow(j)));
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => { if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } });
  }, {threshold:.1});
  document.querySelectorAll('.lead').forEach((el, i) => {
    el.style.transitionDelay = (Math.min(i,8)*45) + 'ms';
    io.observe(el);
  });
}).catch(e => {
  document.getElementById('greeting').textContent = "Hit a snag reading the case files. Is the server running?";
  console.error(e);
});
</script>
<script src="/static/motion.js"></script>
</body>
</html>"""

_PAGE = _PAGE.replace("__MASCOT__", _MASCOT)


_JOB_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Case File - Mr. Jober</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700;9..144,800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/motion.css">
<style>
  :root{ --paper:#FBF6EC; --card:#FFFDF9; --ink:#2D2A3E; --muted:#9A8F7C;
    --line:#EBE1CE; --coral:#FF6B4A; --purple:#8659C4; --green:#22A45C; --amber:#B45309; --blue:#3B7DD8; }
  *{box-sizing:border-box;}
  html{-webkit-font-smoothing:antialiased;}
  body{margin:0; background:var(--paper); color:var(--ink); font-family:Inter,-apple-system,sans-serif;}
  .display{font-family:Fraunces,Georgia,serif;}
  .wrap{max-width:860px; margin:0 auto; padding:0 24px;}
  .nav{position:sticky; top:0; z-index:20; background:rgba(255,253,249,.85); backdrop-filter:blur(16px); border-bottom:2px solid var(--line);}
  .navrow{display:flex; align-items:center; justify-content:space-between; padding:14px 0;}
  .back{display:flex; align-items:center; gap:7px; color:var(--muted); text-decoration:none; font-size:13.5px; font-weight:600;}
  .back:hover{color:var(--ink);}
  .joblink{display:inline-flex; align-items:center; gap:7px; color:var(--ink); text-decoration:none; font-size:13px; font-weight:700; border:2px solid var(--line); padding:8px 14px; border-radius:10px; background:var(--card);}
  .joblink:hover{border-color:var(--coral); color:var(--coral);}

  .head{padding:36px 0 6px; display:flex; align-items:flex-start; gap:18px;}
  .score{width:66px; height:66px; border-radius:16px; display:flex; align-items:center; justify-content:center; flex-shrink:0; font-size:29px; font-weight:800; color:#fff; font-family:Fraunces,serif;}
  .head h1{font-size:27px; font-weight:800; margin:0 0 3px; letter-spacing:-.02em;}
  .head .sub{font-size:15px; color:var(--muted);}
  .stamp{display:inline-block; margin-top:9px; font-size:11px; font-weight:800; padding:5px 11px; border-radius:7px; letter-spacing:.03em;}
  .stamp.good{background:#EAF9EF; color:#15803D;} .stamp.warn{background:#FFF1E0; color:var(--amber);} .stamp.neutral{background:#F1EEE7; color:#8B8B96;}

  .hunch{background:var(--card); border:2px solid var(--line); border-radius:16px; padding:20px 22px; margin:20px 0;}
  .hunch h3{font-size:12px; font-weight:800; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; margin:0 0 10px;}
  .hunch .verdict{font-family:Fraunces,serif; font-size:18px; font-weight:700; color:var(--coral); margin:0 0 8px;}
  .hunch p{font-size:14px; line-height:1.6; color:#413B4D; margin:0;}

  .specialists{display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:22px 0;}
  @media(max-width:640px){ .specialists{grid-template-columns:1fr;} }
  .spec{background:var(--ink); border-radius:18px; padding:22px; color:#fff; display:flex; flex-direction:column;}
  .spec .who{display:flex; align-items:center; gap:11px; margin-bottom:12px;}
  .spec .avatar{width:42px; height:42px; border-radius:11px; flex-shrink:0; display:flex; align-items:center; justify-content:center;}
  .spec .name{font-family:Fraunces,serif; font-size:17px; font-weight:700;}
  .spec .job{font-size:11.5px; color:#A5A0B5;}
  .spec .desc{font-size:12.5px; color:#C3BFD0; line-height:1.5; margin:0 0 16px; flex:1;}
  .spec button{width:100%; border:none; font-family:inherit; font-size:14px; font-weight:700; padding:12px; border-radius:11px; cursor:pointer; transition:transform .15s; color:#fff;}
  .spec button:hover{transform:translateY(-1px);} .spec button:disabled{opacity:.5; cursor:not-allowed; transform:none;}
  .spec.fixer button{background:var(--coral);}
  .spec.wordsmith button{background:var(--purple);}
  .cost{font-size:11px; color:#858095; text-align:center; margin-top:8px;}

  .steps{margin:10px 0 0; padding:0;}
  .steprow{display:flex; align-items:center; gap:11px; padding:7px 0; font-size:13.5px; opacity:.4; transition:opacity .3s;}
  .steprow.active, .steprow.done, .steprow.warn{opacity:1;}
  .stepdot{width:18px; height:18px; border-radius:50%; flex-shrink:0; border:2px solid #4A4658;}
  .steprow.active .stepdot{border-color:#fff; border-top-color:transparent; animation:spin .8s linear infinite;}
  .steprow.done .stepdot{border-color:var(--green); background:var(--green);}
  .steprow.warn .stepdot{border-color:var(--amber); background:var(--amber);}
  @keyframes spin{to{transform:rotate(360deg);}}
  .stepmsg{color:#EAE7E1;} .stepmsg small{color:#A5A0B5; display:block; font-size:11.5px; margin-top:1px;}

  .bothbar{text-align:center; margin:4px 0 8px;}
  .bothbar button{background:var(--ink); color:#fff; border:none; font-family:Fraunces,serif; font-size:16px; font-weight:700; padding:15px 34px; border-radius:13px; cursor:pointer; transition:transform .15s;}
  .bothbar button:hover{transform:translateY(-2px);} .bothbar button:disabled{opacity:.5; cursor:not-allowed; transform:none;}
  .bothsub{font-size:12.5px; color:var(--muted); margin-top:9px;}
  .bothbar .steps{max-width:420px; margin:14px auto 0; text-align:left; background:var(--ink); border-radius:14px; padding:6px 20px;}
  .bothbar .steps:empty{display:none;}
  .bothbar .stepmsg{color:#EAE7E1;} .bothbar .stepmsg small{color:#A5A0B5;}
  /* results break OUT into the open page - spacious, highlighted */
  .results{margin:8px 0 50px;}
  .rhero{text-align:center; padding:26px 0 20px;}
  .rhero .msg{font-family:Fraunces,serif; font-size:22px; font-weight:700; margin:14px 0 0;}
  .dlgrid{display:flex; gap:14px; flex-wrap:wrap; justify-content:center; margin:22px 0 8px;}
  .dl{display:inline-flex; align-items:center; gap:9px; color:#fff; text-decoration:none; font-size:14px; font-weight:700; padding:14px 22px; border-radius:12px; transition:transform .15s;}
  .dl:hover{transform:translateY(-2px);}
  .dl.resume{background:var(--coral);} .dl.letter{background:var(--purple);} .dl.ghost{background:var(--card); color:var(--ink); border:2px solid var(--line);}

  .rsec{margin-top:30px;}
  .rsec h3{font-family:Fraunces,serif; font-size:19px; font-weight:700; margin:0 0 4px; display:flex; align-items:center; gap:10px;}
  .rsec .lead{font-size:13.5px; color:var(--muted); margin:0 0 16px;}
  .changegrid{display:grid; gap:10px;}
  .change{background:var(--card); border:2px solid var(--line); border-left:4px solid var(--coral); border-radius:12px; padding:13px 16px;}
  .change .k{font-size:10px; font-weight:800; color:var(--coral); text-transform:uppercase; letter-spacing:.06em;}
  .change .r{font-size:13.5px; color:#413B4D; line-height:1.5; margin-top:3px;}
  .gapgrid{display:grid; gap:9px;}
  .gap{display:flex; gap:10px; align-items:flex-start; background:#FFF6E8; border-radius:11px; padding:12px 15px; font-size:13.5px; color:#8A5A00; line-height:1.5;}
  .gap svg{flex-shrink:0; margin-top:2px; stroke:var(--amber);}
  .noteflag{background:var(--ink); color:#EAE7E1; border-radius:14px; padding:18px 20px; margin-top:24px; font-size:14px; line-height:1.6;}
  .noteflag b{color:var(--coral);}

  @keyframes jbFadeUp{from{opacity:0; transform:translateY(22px);}to{opacity:1; transform:none;}}
  .head{animation:jbFadeUp .7s cubic-bezier(.16,1,.3,1) both;}
  .hunch{animation:jbFadeUp .7s cubic-bezier(.16,1,.3,1) .08s both;}
  .specialists{animation:jbFadeUp .7s cubic-bezier(.16,1,.3,1) .16s both;}
  .bothbar{animation:jbFadeUp .7s cubic-bezier(.16,1,.3,1) .24s both;}
  .spec{transition:transform .3s, box-shadow .3s;}
  .spec:hover{transform:translateY(-4px); box-shadow:0 16px 34px rgba(45,42,62,.14);}
  @media (prefers-reduced-motion:reduce){ .head,.hunch,.specialists,.bothbar{animation:none;} }
</style>
</head>
<body>
  <nav class="nav js-nav"><div class="wrap"><div class="navrow">
    <a class="back" href="/"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg> Back to the case files</a>
    <div style="display:flex; gap:10px; align-items:center;">
      <button id="applyBtn" class="joblink" style="cursor:pointer;" onclick="markApplied()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
        <span id="applyLabel">Mark as applied</span>
      </button>
      <a class="joblink" id="joblink" href="#" target="_blank" rel="noopener" style="display:none;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/></svg>
        Go to the posting
      </a>
    </div>
  </div></div></nav>

  <main class="wrap">
    <div class="head"><div class="score" id="scoretile">-</div>
      <div><h1 class="display" id="jtitle">Loading...</h1><div class="sub" id="jsub"></div><div id="jstamp"></div></div>
    </div>

    <div class="hunch"><h3>Jober's Hunch</h3><p class="verdict" id="verdict"></p><p id="hunch"></p></div>

    <div class="specialists">
      <div class="spec fixer">
        <div class="who">
          <div class="avatar" style="background:var(--coral);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l3 6 6 .5-4.5 4 1.5 6-6-3.5L6 18.5 7.5 12.5 3 8.5 9 8z"/></svg>
          </div>
          <div><div class="name">Mr. Fixer</div><div class="job">resume tailor</div></div>
        </div>
        <p class="desc">I'll reshape your resume to fit this exact role. Keep it honest, keep your voice, match their language. No cover letter, no wasted spend.</p>
        <button id="fixerBtn" onclick="run('fixer')">Send in Mr. Fixer</button>
        <div class="cost">tailors resume only</div>
        <div class="steps" id="fixerSteps"></div>
      </div>
      <div class="spec wordsmith">
        <div class="who">
          <div class="avatar" style="background:var(--purple);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></svg>
          </div>
          <div><div class="name">Mr. Wordsmith</div><div class="job">cover letter writer</div></div>
        </div>
        <p class="desc">I'll research the company and write you a sharp, story-driven cover letter. This one digs into who they are, so it costs a touch more.</p>
        <button id="wordsmithBtn" onclick="run('wordsmith')">Send in Mr. Wordsmith</button>
        <div class="cost">researches + writes letter</div>
        <div class="steps" id="wordsmithSteps"></div>
      </div>
    </div>

    <div class="bothbar">
      <button id="bothBtn" onclick="run('both')">Do the Whole Thing</button>
      <div class="bothsub">Resume and cover letter, the full works. I'll research once and put both specialists on it.</div>
      <div class="steps" id="bothSteps"></div>
    </div>

    <div class="results" id="results"></div>
  </main>

<script>
const jobId = location.pathname.split('/').pop();
const SCORE_COLORS = c => c >= 73 ? '#FF6B4A' : c >= 63 ? '#8659C4' : '#22A45C';
const esc = s => (s||'').replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
const clean = s => (s||'').replace(/<\\/?cite[^>]*>/g,'').replace(/<[^>]*>/g,' ').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&').replace(/&nbsp;/g,' ').replace(/\\s+/g,' ').trim();

const STEP_LABELS = {prep:'Reading your resume', research:'Background check', tailor:'Tailoring resume', apply:'Rewriting the doc', pdf:'Making the PDF', cover:'Writing the letter'};
const ENDPOINT = {fixer:'tailor', wordsmith:'letter', both:'full'};

fetch('/api/job/' + encodeURIComponent(jobId)).then(r => r.json()).then(j => {
  document.getElementById('scoretile').textContent = j.score;
  document.getElementById('scoretile').style.background = SCORE_COLORS(j.score);
  document.getElementById('jtitle').textContent = j.title;
  document.getElementById('jsub').textContent = j.company + (j.location ? ' - ' + j.location.split(/[|;]/)[0].trim() : '');
  document.getElementById('jstamp').innerHTML = `<span class="stamp ${j.visa_tone}">${(j.visa_label||'').toUpperCase()}</span>`;
  const v = j.score >= 73 ? "This one's got legs, boss. Worth chasing." : j.score >= 63 ? "Solid lead. Some gaps, but nothing that kills it." : "Bit of a long shot, but I've seen weirder pan out.";
  document.getElementById('verdict').textContent = v;
  document.getElementById('hunch').textContent = clean(j.reasoning) || 'No notes on this one.';
  if(j.job_url){ const l = document.getElementById('joblink'); l.href = j.job_url; l.style.display = 'inline-flex'; }
  reflectStatus(j.status);
});

const rowsByAgent = {fixer:{}, wordsmith:{}, both:{}};
function ensureRow(agent, step){
  const box = document.getElementById(agent+'Steps');
  if(rowsByAgent[agent][step]) return rowsByAgent[agent][step];
  const row = document.createElement('div');
  row.className = 'steprow';
  row.innerHTML = `<div class="stepdot"></div><div class="stepmsg"><span>${STEP_LABELS[step]||step}</span><small></small></div>`;
  box.appendChild(row);
  rowsByAgent[agent][step] = row;
  return row;
}

function run(agent){
  const btn = document.getElementById(agent+'Btn');
  btn.disabled = true;
  btn.textContent = 'On the case...';
  const es = new EventSource('/api/job/' + encodeURIComponent(jobId) + '/' + ENDPOINT[agent]);
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if(ev.step === 'done'){ es.close(); btn.textContent = 'Done'; showResult(agent, ev); return; }
    if(ev.step === 'error'){ es.close(); btn.disabled=false; btn.textContent='Try again'; return; }
    const row = ensureRow(agent, ev.step);
    const small = row.querySelector('small');
    row.className = 'steprow ' + (ev.status === 'running' ? 'active' : ev.status === 'done' ? 'done' : 'warn');
    small.textContent = clean(ev.msg);
  };
  es.onerror = () => { es.close(); if(btn.textContent === 'On the case...'){ btn.disabled=false; btn.textContent='Try again'; } };
}

function dl(path, label, cls){
  if(!path) return '';
  const icon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';
  return `<a class="dl ${cls}" href="/api/download?path=${encodeURIComponent(path)}">${icon}${label}</a>`;
}

function showResult(agent, ev){
  const box = document.getElementById('results');
  let html = `<div class="rhero"><div class="msg">${esc(clean(ev.msg))}</div><div class="dlgrid">`;
  if(ev.files.resume_pdf) html += dl(ev.files.resume_pdf, 'Download resume (PDF)', 'resume');
  if(ev.files.cover_pdf) html += dl(ev.files.cover_pdf, 'Download cover letter (PDF)', 'letter');
  if(ev.files.cover_txt) html += dl(ev.files.cover_txt, 'Editable letter', 'ghost');
  html += '</div></div>';

  if(ev.changes && ev.changes.length){
    html += '<div class="rsec"><h3>What Mr. Fixer changed</h3><p class="lead">Same truth, sharper fit. Here is every edit and why.</p><div class="changegrid">';
    ev.changes.forEach(c => { html += `<div class="change"><div class="k">${esc(c.kind||'')}</div><div class="r">${esc(clean(c.reason))}</div></div>`; });
    html += '</div></div>';
  }
  if(ev.gaps && ev.gaps.length){
    html += '<div class="rsec"><h3>Gaps I left honest</h3><p class="lead">I did not fake these. Worth knowing before you apply.</p><div class="gapgrid">';
    ev.gaps.forEach(g => { html += `<div class="gap"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg><div>${esc(clean(g))}</div></div>`; });
    html += '</div></div>';
  }
  if(ev.notes){ html += `<div class="noteflag"><b>Mr. Wordsmith's note:</b> ${esc(clean(ev.notes))}</div>`; }

  box.insertAdjacentHTML('beforeend', html);
  box.scrollIntoView({behavior:'smooth', block:'start'});
}

let currentStatus = null;
function reflectStatus(s){
  currentStatus = s;
  const label = document.getElementById('applyLabel');
  const btn = document.getElementById('applyBtn');
  if(s === 'applied'){ label.textContent = 'Applied'; btn.style.background = '#EAF9EF'; btn.style.borderColor = '#22A45C'; btn.style.color = '#15803D'; }
  else { label.textContent = 'Mark as applied'; }
}

async function markApplied(){
  const next = currentStatus === 'applied' ? 'lead' : 'applied';
  const r = await fetch('/api/job/' + encodeURIComponent(jobId) + '/status', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({status: next})
  }).then(r => r.json());
  if(r.ok) reflectStatus(r.status);
}
</script>
<script src="/static/motion.js"></script>
</body>
</html>"""

_JOB_PAGE = _JOB_PAGE.replace("__MASCOT__", _MASCOT)


_TRACKER_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Board - Mr. Jober</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700;9..144,800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/motion.css">
<style>
  :root{ --paper:#FBF6EC; --card:#FFFDF9; --ink:#2D2A3E; --muted:#9A8F7C;
    --line:#EBE1CE; --coral:#FF6B4A; --purple:#8659C4; --green:#22A45C; --amber:#B45309; --blue:#3B7DD8; }
  *{box-sizing:border-box;}
  html{-webkit-font-smoothing:antialiased;}
  body{margin:0; background:var(--paper); color:var(--ink); font-family:Inter,-apple-system,sans-serif;}
  .display{font-family:Fraunces,Georgia,serif;}
  .wrap{max-width:960px; margin:0 auto; padding:0 24px;}
  .nav{position:sticky; top:0; z-index:20; background:rgba(255,253,249,.85); backdrop-filter:blur(16px); border-bottom:2px solid var(--line);}
  .navrow{display:flex; align-items:center; justify-content:space-between; padding:14px 0;}
  .back{display:flex; align-items:center; gap:7px; color:var(--muted); text-decoration:none; font-size:13.5px; font-weight:600;}
  .back:hover{color:var(--ink);}
  .hdr{padding:30px 0 4px;}
  .hdr .eyebrow{font-size:13px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:var(--muted);}
  .hdr h1{font-size:32px; font-weight:800; letter-spacing:-.03em; margin:6px 0 4px;}
  .hdr p{font-size:15px; color:var(--muted); margin:0;}

  .actions{margin:20px 0;}
  .actioncard{display:flex; align-items:center; gap:14px; background:#2D2A3E; color:#FFFFFF; border-radius:14px; padding:18px 20px; margin-bottom:10px;}
  .actioncard .t{color:#FFFFFF;}
  .actioncard .ic{width:34px; height:34px; border-radius:9px; flex-shrink:0; display:flex; align-items:center; justify-content:center;}
  .actioncard.outreach .ic{background:var(--green);}
  .actioncard.followup .ic{background:var(--amber);}
  .actioncard .body{flex:1;}
  .actioncard .body .t{font-size:14px; font-weight:600; line-height:1.5;}
  .actioncard .go{margin-top:9px; display:inline-flex; align-items:center; gap:6px; background:var(--coral); color:#fff; border:none; font-family:inherit; font-size:12.5px; font-weight:700; padding:8px 14px; border-radius:9px; cursor:pointer;}
  .allclear{background:#EAF9EF; color:#15803D; border-radius:14px; padding:16px 18px; font-size:14px; font-weight:600;}

  .panels{display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:8px 0 24px;}
  @media(max-width:720px){ .panels{grid-template-columns:1fr;} }
  .panel{background:var(--card); border:2px solid var(--line); border-radius:16px; padding:20px;}
  .panel h3{font-size:12px; font-weight:800; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; margin:0 0 16px;}
  .funnel-row{display:flex; align-items:center; gap:12px; margin-bottom:11px;}
  .funnel-label{font-size:12.5px; font-weight:600; width:92px; flex-shrink:0; color:#413B4D;}
  .funnel-bar-wrap{flex:1; background:#F1EEE7; border-radius:8px; height:26px; overflow:hidden;}
  .funnel-bar{height:100%; border-radius:8px; display:flex; align-items:center; padding:0 9px; color:#fff; font-size:12px; font-weight:800; min-width:26px; transition:width .5s cubic-bezier(.2,.7,.2,1);}
  .spark{display:flex; align-items:flex-end; gap:5px; height:90px; padding-top:8px;}
  .spark .bar{flex:1; background:var(--coral); border-radius:5px 5px 0 0; min-height:4px; position:relative;}
  .spark .bar span{position:absolute; top:-18px; left:0; right:0; text-align:center; font-size:10px; color:var(--muted); font-weight:700;}
  .spark-x{display:flex; gap:5px; margin-top:6px;} .spark-x div{flex:1; text-align:center; font-size:9.5px; color:var(--muted);}
  .chartempty{font-size:13px; color:var(--muted); text-align:center; padding:24px 0;}

  .sectitle{font-family:Fraunces,serif; font-size:20px; font-weight:700; margin:8px 0 14px;}
  .board{display:grid; gap:13px; padding-bottom:60px;}
  .caseblock{background:var(--card); border:2px solid var(--line); border-radius:16px; overflow:hidden;}
  .caseblock.nudge{border-color:var(--amber);}
  .casehead{padding:16px 18px; display:flex; align-items:center; gap:15px;}
  .casehead .role{font-size:14.5px; font-weight:700;} .casehead .co{font-size:12.5px; color:var(--muted); margin-top:1px;}
  .casehead .grow{flex:1; min-width:0;}
  .badge{font-size:10.5px; font-weight:800; padding:5px 10px; border-radius:7px; letter-spacing:.03em; white-space:nowrap; text-transform:uppercase;}
  .b-applied{background:#EAF2FB; color:#2C5FA3;} .b-following_up{background:#FFF1E0; color:var(--amber);}
  .b-interview{background:#EAF9EF; color:#15803D;} .b-offer{background:#EAF9EF; color:#15803D;}
  .b-rejected{background:#F1EEE7; color:#8B8B96;} .b-closed{background:#F1EEE7; color:#8B8B96;}
  .statuspick{font-family:inherit; font-size:12px; font-weight:600; border:2px solid var(--line); border-radius:9px; padding:6px 8px; background:var(--paper); color:var(--ink); cursor:pointer;}
  .outreach{border-top:2px solid var(--line); padding:16px 18px; background:#FCFAF4;}
  .outreach .lbl{font-size:11px; font-weight:800; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; margin-bottom:10px;}
  .lilinks{display:flex; gap:9px; flex-wrap:wrap; margin-bottom:12px;}
  .lilink{display:inline-flex; align-items:center; gap:6px; font-size:12.5px; font-weight:600; text-decoration:none; color:#2C5FA3; background:#EAF2FB; padding:8px 13px; border-radius:9px;}
  .lilink:hover{background:#DCE9F9;}
  .draftbtn{background:var(--purple); color:#fff; border:none; font-family:inherit; font-size:12.5px; font-weight:700; padding:9px 15px; border-radius:9px; cursor:pointer;}
  .draftbtn:disabled{opacity:.55; cursor:not-allowed;}
  .draftout{margin-top:12px; background:var(--card); border:2px solid var(--line); border-radius:11px; padding:13px 15px; font-size:13.5px; line-height:1.6; color:#413B4D; white-space:pre-wrap; display:none;}
  .draftout.on{display:block;}
  .copybtn{margin-top:8px; font-size:11.5px; font-weight:700; color:var(--muted); background:none; border:none; cursor:pointer;}
  .copybtn2{margin-top:10px; font-size:12.5px; font-weight:700; color:var(--purple); background:#F3EEFC; border:none; padding:8px 15px; border-radius:9px; cursor:pointer; transition:all .15s;}
  .copybtn2:hover{background:#E9E0F9;}
  .copybtn2.copied{background:#EAF9EF; color:#15803D;}
    .empty{text-align:center; padding:70px 0; color:var(--muted);}
  .empty .display{font-size:22px; color:var(--ink); margin-bottom:8px;}
</style>
</head>
<body>
  <nav class="nav js-nav"><div class="wrap"><div class="navrow">
    <a class="back" href="/"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg> Back to the case files</a>
    <div style="font-size:13px; font-weight:700; color:var(--muted);">The Board</div>
  </div></div></nav>
  <main class="wrap">
    <div class="hdr">
      <div class="eyebrow">Case tracker</div>
      <h1 class="display">The Board</h1>
      <p id="sub">Loading your open cases...</p>
    </div>
    <div class="actions" id="actions"></div>
    <div class="panels" id="panels" style="display:none;">
      <div class="panel"><h3>The funnel</h3><div id="funnel"></div></div>
      <div class="panel"><h3>Applications over time</h3><div id="timeline"></div></div>
    </div>
    <div class="sectitle" id="sectitle" style="display:none;">Open cases</div>
    <div class="board" id="board"></div>
  </main>
<script>
const STATUSES = ['applied','following_up','interview','offer','rejected','closed'];
const STATUS_LABEL = {applied:'Applied', following_up:'Following up', interview:'Interview', offer:'Offer', rejected:'Rejected', closed:'Closed'};
const FUNNEL_COLORS = {applied:'#3B7DD8', following_up:'#B45309', interview:'#22A45C', offer:'#22A45C'};
const esc = s => (s||'').replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));

function actionCard(a){
  const icon = a.kind === 'outreach'
    ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11h-6"/></svg>'
    : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>';
  return `<div class="actioncard ${a.kind}"><div class="ic">${icon}</div><div class="body"><div class="t">${esc(a.text)}</div><button class="go" onclick="document.getElementById('case-${a.job_id}').scrollIntoView({behavior:'smooth',block:'center'})">Handle it</button></div></div>`;
}

function funnelRow(f, max){
  const pct = max > 0 ? Math.max(6, Math.round(f.count / max * 100)) : 6;
  const c = FUNNEL_COLORS[f.stage] || '#9A8F7C';
  return `<div class="funnel-row"><div class="funnel-label">${STATUS_LABEL[f.stage]}</div><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:${pct}%;background:${c}">${f.count}</div></div></div>`;
}

function renderTimeline(tl){
  const box = document.getElementById('timeline');
  if(!tl.length){ box.innerHTML = '<div class="chartempty">Apply to a few roles and your pace shows up here.</div>'; return; }
  const max = Math.max(...tl.map(t => t.count));
  const bars = tl.slice(-10);
  box.innerHTML = '<div class="spark">' + bars.map(t =>
    `<div class="bar" style="height:${Math.max(8, t.count/max*80)}px"><span>${t.count}</span></div>`).join('') +
    '</div><div class="spark-x">' + bars.map(t => `<div>${t.date.slice(5)}</div>`).join('') + '</div>';
}

function caseBlock(j){
  const opts = STATUSES.map(s => `<option value="${s}"${s===j.status?' selected':''}>${STATUS_LABEL[s]}</option>`).join('');
  const showOutreach = (j.status === 'applied');
  let outreach = '';
  if(showOutreach){
    outreach = `<div class="outreach">
      <div class="lbl">Get someone on the inside</div>
      <div class="lilinks">
        <a class="lilink" href="${j.li.recruiter}" target="_blank" rel="noopener">Find a recruiter</a>
        <a class="lilink" href="${j.li.hiring_manager}" target="_blank" rel="noopener">Find the hiring manager</a>
        <a class="lilink" href="${j.li.team}" target="_blank" rel="noopener">Find the team</a>
      </div>
      <button class="draftbtn" onclick="draft('${j.job_id}', this)">Draft me a message</button>
      <div class="draftout" id="draft-${j.job_id}"></div>
      <button class="copybtn2" id="copy-${j.job_id}" style="display:none;" onclick="copyMsg('${j.job_id}')">Copy message</button>
    </div>`;
  }
  return `<div class="caseblock ${j.do_followup?'nudge':''}" id="case-${j.job_id}">
    <div class="casehead">
      <div class="grow"><div class="role">${esc(j.title)}</div><div class="co">${esc(j.company)}${j.days_since!=null?' &middot; applied '+(j.days_since===0?'today':j.days_since+'d ago'):''}</div></div>
      <span class="badge b-${j.status}">${STATUS_LABEL[j.status]||j.status}</span>
      <select class="statuspick" onchange="setStatus('${j.job_id}', this.value)">${opts}</select>
    </div>${outreach}</div>`;
}

const draftVariant = {};
async function draft(jobId, btn){
  btn.disabled = true; btn.textContent = 'Mr. Wordsmith is thinking...';
  const out = document.getElementById('draft-' + jobId);
  const copyBtn = document.getElementById('copy-' + jobId);
  const v = (draftVariant[jobId] = (draftVariant[jobId] || 0) + 1) - 1;  // 0,1,2...
  try {
    const r = await fetch('/api/job/' + encodeURIComponent(jobId) + '/outreach?target=recruiter&variant=' + v).then(r => r.json());
    if(r.ok){
      out.textContent = r.message; out.classList.add('on');
      copyBtn.style.display = 'inline-flex';   // ONE copy button, reused (no pileup)
      copyBtn.textContent = 'Copy message';
      copyBtn.dataset.copied = '';
      btn.textContent = 'Draft another';
    } else { out.textContent = 'Could not draft that: ' + (r.error||''); out.classList.add('on'); btn.textContent='Try again'; }
  } catch(e){ out.textContent = 'Something went wrong.'; out.classList.add('on'); btn.textContent='Try again'; }
  btn.disabled = false;
}

function copyMsg(jobId){
  const out = document.getElementById('draft-' + jobId);
  const btn = document.getElementById('copy-' + jobId);
  navigator.clipboard.writeText(out.textContent).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy message'; btn.classList.remove('copied'); }, 1800);
  });
}

function copyMsg(jobId){
  const out = document.getElementById('draft-' + jobId);
  const btn = document.getElementById('copy-' + jobId);
  navigator.clipboard.writeText(out.textContent).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy message'; btn.classList.remove('copied'); }, 1800);
  });
}

async function setStatus(jobId, status){
  await fetch('/api/job/' + encodeURIComponent(jobId) + '/status', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status})
  });
  load();
}

function load(){
  fetch('/api/tracked').then(r => r.json()).then(data => {
    const {jobs, stats, funnel, timeline, actions} = data;
    const sub = document.getElementById('sub');
    const board = document.getElementById('board');
    const actionsBox = document.getElementById('actions');
    if(!jobs.length){
      document.getElementById('panels').style.display = 'none';
      document.getElementById('sectitle').style.display = 'none';
      actionsBox.innerHTML = '';
      board.innerHTML = '<div class="empty"><div class="display">No open cases yet, boss.</div><div>Mark a lead as applied and it lands here. Then I\\'ll help you work it.</div></div>';
      sub.textContent = 'Nothing in play yet.';
      return;
    }
    sub.textContent = stats.total + ' case' + (stats.total===1?'':'s') + ' in play' + (stats.nudges ? ', ' + stats.nudges + ' need action' : ', all quiet');
    // actions
    if(actions.length){ actionsBox.innerHTML = actions.map(actionCard).join(''); }
    else { actionsBox.innerHTML = '<div class="allclear">All caught up, boss. Nothing needs chasing right now.</div>'; }
    // panels
    document.getElementById('panels').style.display = 'grid';
    const maxF = Math.max(1, ...funnel.map(f => f.count));
    document.getElementById('funnel').innerHTML = funnel.map(f => funnelRow(f, maxF)).join('');
    renderTimeline(timeline);
    // board
    document.getElementById('sectitle').style.display = 'block';
    board.innerHTML = '';
    jobs.forEach(j => board.insertAdjacentHTML('beforeend', caseBlock(j)));
  });
}
load();
</script>
<script src="/static/motion.js"></script>
</body>
</html>"""


_LANDING_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mr. Jober - Your private eye for the job hunt</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700;9..144,900&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/motion.css">
<style>
  :root{ --paper:#FBF6EC; --card:#FFFDF9; --ink:#2D2A3E; --muted:#9A8F7C;
    --line:#EBE1CE; --coral:#FF6B4A; --purple:#8659C4; --green:#22A45C; }
  *{box-sizing:border-box; margin:0; padding:0;}
  html{scroll-behavior:smooth;}
  body{background:var(--paper); color:var(--ink); font-family:Inter,-apple-system,sans-serif; overflow-x:hidden;}
  .display{font-family:Fraunces,Georgia,serif;}
  @media (prefers-reduced-motion: reduce){ *{animation:none !important; transition:none !important;} .reveal{opacity:1 !important; transform:none !important;} }

  /* ambient floating shapes behind everything */
  .bg-shapes{position:fixed; inset:0; z-index:0; pointer-events:none; overflow:hidden;}
  .blob{position:absolute; border-radius:50%; opacity:.06;}
  .blob.a{width:520px; height:520px; background:var(--coral); top:-120px; right:-100px;}
  .blob.b{width:420px; height:420px; background:var(--purple); bottom:10%; left:-140px;}
  .blob.c{width:300px; height:300px; background:var(--green); top:45%; right:8%;}

  .wrap{max-width:1080px; margin:0 auto; padding:0 32px; position:relative; z-index:1;}

  /* nav */
  nav{position:fixed; top:0; left:0; right:0; z-index:50; padding:18px 0; background:rgba(251,246,236,0); transition:background .4s, box-shadow .4s;}
  nav.scrolled{background:rgba(251,246,236,.9); backdrop-filter:blur(14px); box-shadow:0 1px 0 var(--line);}
  .navrow{display:flex; align-items:center; justify-content:space-between;}
  .brand{display:flex; align-items:center; gap:11px; font-family:Fraunces,serif; font-weight:800; font-size:19px;}
  .brand .badge{width:34px; height:34px;}
  .navcta{background:var(--ink); color:#fff; text-decoration:none; font-size:14px; font-weight:700; padding:11px 20px; border-radius:11px; transition:transform .2s;}
  .navcta:hover{transform:translateY(-2px);}

  /* hero */
  .hero{min-height:100vh; display:flex; align-items:center; position:relative; padding:120px 0 80px;}
  .hero-grid{display:grid; grid-template-columns:1.15fr .85fr; gap:40px; align-items:center; width:100%;}
  @media(max-width:820px){ .hero-grid{grid-template-columns:1fr; text-align:center;} }
  .eyebrow{display:inline-flex; align-items:center; gap:8px; font-size:13px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--coral); margin-bottom:22px;}
  .eyebrow .dot{width:8px; height:8px; border-radius:50%; background:var(--coral); animation:blink 1.4s infinite;}
  @keyframes blink{0%,100%{opacity:1;}50%{opacity:.3;}}
  h1.hero-title{font-family:Fraunces,serif; font-weight:900; font-size:clamp(44px,7vw,86px); line-height:.98; letter-spacing:-.03em; margin-bottom:24px;}
  h1.hero-title .line{display:block; overflow:hidden;}
  h1.hero-title .line span{display:block; transform:translateY(110%); animation:riseIn .9s cubic-bezier(.16,1,.3,1) forwards;}
  h1.hero-title .line:nth-child(2) span{animation-delay:.12s;}
  h1.hero-title .line:nth-child(3) span{animation-delay:.24s;}
  .accent{color:var(--coral);}
  @keyframes riseIn{to{transform:translateY(0);}}
  .hero-sub{font-size:19px; line-height:1.6; color:#5A5468; max-width:520px; margin-bottom:32px; opacity:0; animation:fadeUp .8s ease .5s forwards;}
  @media(max-width:820px){ .hero-sub{margin-left:auto; margin-right:auto;} }
  .hero-ctas{display:flex; gap:14px; opacity:0; animation:fadeUp .8s ease .65s forwards;}
  @media(max-width:820px){ .hero-ctas{justify-content:center;} }
  @keyframes fadeUp{from{opacity:0; transform:translateY(20px);}to{opacity:1; transform:translateY(0);}}
  .btn-primary{background:var(--coral); color:#fff; text-decoration:none; font-size:15px; font-weight:700; padding:15px 28px; border-radius:13px; transition:transform .2s, box-shadow .2s;}
  .btn-primary:hover{transform:translateY(-3px); box-shadow:0 12px 28px rgba(255,107,74,.35);}
  .btn-ghost{background:transparent; color:var(--ink); text-decoration:none; font-size:15px; font-weight:700; padding:15px 24px; border-radius:13px; border:2px solid var(--line); transition:border-color .2s;}
  .btn-ghost:hover{border-color:var(--ink);}

  /* hero mascot with float + parallax */
  .hero-visual{display:flex; justify-content:center; position:relative;}
  .mascot-stage{position:relative; width:260px; height:260px;}
  .mascot-stage svg.mascot{width:150px; height:150px;}
  .mascot-ring{position:absolute; inset:0; border:2px dashed var(--line); border-radius:50%; animation:spin 40s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .mascot-fig{position:absolute; inset:0; display:flex; align-items:center; justify-content:center; animation:float 5s ease-in-out infinite;}
  @keyframes float{0%,100%{transform:translateY(0);}50%{transform:translateY(-18px);}}
  .mascot-fig svg{width:150px; height:150px; filter:drop-shadow(0 18px 24px rgba(45,42,62,.18));}
  .chip{position:absolute; background:var(--card); border:2px solid var(--line); border-radius:12px; padding:9px 14px; font-size:13px; font-weight:700; box-shadow:0 8px 20px rgba(45,42,62,.08);}
  .chip .k{color:var(--coral); font-size:10px; text-transform:uppercase; letter-spacing:.05em; display:block;}
  .chip.one{top:6%; left:-8%; animation:float 4s ease-in-out infinite;}
  .chip.two{bottom:12%; right:-10%; animation:float 5.5s ease-in-out .5s infinite;}
  .chip.three{bottom:-4%; left:12%; animation:float 4.6s ease-in-out .2s infinite;}

  /* sections */
  section{padding:110px 0; position:relative;}
  .sec-eyebrow{font-size:13px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin-bottom:14px;}
  .sec-title{font-family:Fraunces,serif; font-weight:800; font-size:clamp(32px,4.5vw,52px); line-height:1.05; letter-spacing:-.02em; margin-bottom:20px;}
  .sec-lead{font-size:18px; line-height:1.65; color:#5A5468; max-width:560px;}

  /* reveal on scroll */
  .reveal{opacity:0; transition:opacity .9s cubic-bezier(.16,1,.3,1), transform .9s cubic-bezier(.16,1,.3,1);}
  .reveal.from-left{transform:translateX(-70px);}
  .reveal.from-right{transform:translateX(70px);}
  .reveal.from-bottom{transform:translateY(60px);}
  .reveal.in{opacity:1; transform:none;}
  .reveal.d1{transition-delay:.1s;} .reveal.d2{transition-delay:.2s;} .reveal.d3{transition-delay:.3s;}

  /* steps: the investigation */
  .steps{display:grid; grid-template-columns:repeat(3,1fr); gap:24px; margin-top:56px;}
  @media(max-width:820px){ .steps{grid-template-columns:1fr;} }
  .step{background:var(--card); border:2px solid var(--line); border-radius:20px; padding:30px; position:relative; transition:transform .3s, box-shadow .3s, border-color .3s;}
  .step:hover{transform:translateY(-8px); box-shadow:0 20px 40px rgba(45,42,62,.1); border-color:var(--coral);}
  .step-num{font-family:Fraunces,serif; font-weight:900; font-size:15px; color:var(--coral); border:2px solid var(--coral); width:38px; height:38px; border-radius:50%; display:flex; align-items:center; justify-content:center; margin-bottom:20px;}
  .step h3{font-family:Fraunces,serif; font-size:22px; font-weight:700; margin-bottom:10px;}
  .step p{font-size:14.5px; line-height:1.6; color:#5A5468;}

  /* specialists band */
  .band{background:var(--ink); border-radius:32px; padding:60px; color:#fff; display:grid; grid-template-columns:1fr 1fr; gap:40px; align-items:center;}
  @media(max-width:820px){ .band{grid-template-columns:1fr; padding:40px;} }
  .band h2{font-family:Fraunces,serif; font-size:clamp(30px,4vw,46px); font-weight:800; line-height:1.05; margin-bottom:18px;}
  .band p{font-size:16px; line-height:1.65; color:#C3BFD0; margin-bottom:14px;}
  .crew{display:flex; flex-direction:column; gap:16px;}
  .crewcard{background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.12); border-radius:16px; padding:20px; display:flex; align-items:center; gap:16px; transition:transform .3s, background .3s;}
  .crewcard:hover{transform:translateX(8px); background:rgba(255,255,255,.09);}
  .crewcard .av{width:46px; height:46px; border-radius:12px; flex-shrink:0; display:flex; align-items:center; justify-content:center;}
  .crewcard .nm{font-family:Fraunces,serif; font-weight:700; font-size:17px;}
  .crewcard .rl{font-size:12.5px; color:#A5A0B5;}

  /* closing CTA */
  .closer{text-align:center; padding:130px 0;}
  .closer h2{font-family:Fraunces,serif; font-weight:900; font-size:clamp(38px,6vw,72px); line-height:1; letter-spacing:-.02em; margin-bottom:24px;}
  .closer p{font-size:19px; color:#5A5468; margin-bottom:34px;}
  footer{padding:40px 0; border-top:2px solid var(--line); text-align:center; color:var(--muted); font-size:13px;}
</style>
</head>
<body>
  <div class="bg-shapes">
    <div class="blob a" data-parallax="0.3"></div>
    <div class="blob b" data-parallax="0.5"></div>
    <div class="blob c" data-parallax="0.2"></div>
  </div>

  <nav id="nav" class="js-nav"><div class="wrap"><div class="navrow">
    <div class="brand"><span class="badge">__MASCOT__</span> Mr. Jober</div>
    <a class="navcta" href="#start">Start the hunt</a>
  </div></div></nav>

  <header class="hero"><div class="wrap"><div class="hero-grid">
    <div class="hero-copy">
      <div class="eyebrow"><span class="dot"></span> Case status: open</div>
      <h1 class="hero-title display">
        <span class="line"><span>Your private eye</span></span>
        <span class="line"><span>for the <span class="accent">job hunt</span>.</span></span>
      </h1>
      <p class="hero-sub">Mr. Jober works your leads like a detective works a case. He finds the roles, reads the fine print, tailors your resume, and gets you in front of the right people. You approve every move.</p>
      <div class="hero-ctas">
        <a class="btn-primary" href="#start">Put him on the case</a>
        <a class="btn-ghost" href="#how">See how it works</a>
      </div>
    </div>
    <div class="hero-visual">
      <div class="mascot-stage" data-parallax="0.15">
        <div class="mascot-ring"></div>
        <div class="mascot-fig">__MASCOT__</div>
        <div class="chip one"><span class="k">Lead</span>Data Analyst, 78</div>
        <div class="chip two"><span class="k">Visa</span>Verified sponsor</div>
        <div class="chip three"><span class="k">Status</span>Resume tailored</div>
      </div>
    </div>
  </div></div></header>

  <section id="how"><div class="wrap">
    <div class="sec-eyebrow reveal from-bottom">How the agency works</div>
    <h2 class="sec-title reveal from-bottom d1">Four moves, one clean case file.</h2>
    <p class="sec-lead reveal from-bottom d2">No black boxes. Every step is something you can see, question, and approve before a single application goes out.</p>
    <div class="steps">
      <div class="step reveal from-left"><div class="step-num">1</div><h3>Works the leads</h3><p>Pulls fresh roles straight from company career pages. No stale aggregator junk, no reposts from six weeks ago.</p></div>
      <div class="step reveal from-bottom d1"><div class="step-num">2</div><h3>Reads the room</h3><p>Scores each role against your fit and runs a background check on visa sponsorship, so you chase the ones that can actually hire you.</p></div>
      <div class="step reveal from-right d2"><div class="step-num">3</div><h3>Tailors the file</h3><p>Mr. Fixer reshapes your resume to the role. Mr. Wordsmith writes the cover letter. Honest edits only, never faked.</p></div>
    </div>
  </div></section>

  <section id="crew"><div class="wrap">
    <div class="band reveal from-bottom">
      <div>
        <h2 class="display">Two specialists<br>on the payroll.</h2>
        <p>Every good detective has a crew. Mr. Jober's got two hands he trusts with the delicate work, and neither one spends your budget on things you didn't ask for.</p>
        <p style="color:#A5A0B5; font-size:14px;">Click, and only then do they get to work.</p>
      </div>
      <div class="crew">
        <div class="crewcard"><div class="av" style="background:var(--coral)"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l3 6 6 .5-4.5 4 1.5 6-6-3.5L6 18.5 7.5 12.5 3 8.5 9 8z"/></svg></div><div><div class="nm">Mr. Fixer</div><div class="rl">Reshapes your resume to fit the role, keeps your voice</div></div></div>
        <div class="crewcard"><div class="av" style="background:var(--purple)"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/></svg></div><div><div class="nm">Mr. Wordsmith</div><div class="rl">Researches the company, writes a sharp cover letter</div></div></div>
      </div>
    </div>
  </div></section>

  <section class="closer" id="start"><div class="wrap">
    <h2 class="display reveal from-bottom">Ready to<br>crack the case?</h2>
    <p class="reveal from-bottom d1">Drop in your resume. Mr. Jober takes it from there.</p>
    <div class="reveal from-bottom d2"><a class="btn-primary" href="/welcome">Put him on the case</a></div>
  </div></section>

  <footer><div class="wrap">Mr. Jober - a private detective for your job hunt. Runs local, works quiet.</div></footer>

<script>
  const nav = document.getElementById('nav');
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => { if(e.isIntersecting) e.target.classList.add('in'); });
  }, {threshold:0.15});
  document.querySelectorAll('.reveal').forEach(el => io.observe(el));

  let ticking = false;
  window.addEventListener('scroll', () => {
    if(window.scrollY > 30) nav.classList.add('scrolled'); else nav.classList.remove('scrolled');
    if(!ticking){
      requestAnimationFrame(() => {
        const y = window.scrollY;
        document.querySelectorAll('[data-parallax]').forEach(el => {
          const speed = parseFloat(el.dataset.parallax);
          el.style.transform = 'translateY(' + (y * speed) + 'px)';
        });
        ticking = false;
      });
      ticking = true;
    }
  }, {passive:true});
</script>
<script src="/static/motion.js"></script>
</body>
</html>
"""
_LANDING_PAGE = _LANDING_PAGE.replace("__MASCOT__", _MASCOT)
