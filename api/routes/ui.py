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


@router.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: str):
    from api.routes.onboarding import _is_ready
    if not _is_ready():
        return RedirectResponse(url="/welcome")
    return HTMLResponse(_JOB_PAGE)


@router.get("/")
async def dashboard():
    from api.routes.onboarding import _is_ready
    if not _is_ready():
        return RedirectResponse(url="/welcome")
    return HTMLResponse(_PAGE)


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
  <nav class="nav"><div class="wrap"><div class="navrow">
    <div class="brand">
      <div class="badge-avatar">__MASCOT__</div>
      <div><h1 class="display">Mr. Jober</h1><p>private investigator, job division</p></div>
    </div>
    <div class="status"><div class="dot"></div><span>on the case</span></div>
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
</style>
</head>
<body>
  <nav class="nav"><div class="wrap"><div class="navrow">
    <a class="back" href="/"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg> Back to the case files</a>
    <a class="joblink" id="joblink" href="#" target="_blank" rel="noopener" style="display:none;">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/></svg>
      Go to the posting
    </a>
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
</script>
</body>
</html>"""

_JOB_PAGE = _JOB_PAGE.replace("__MASCOT__", _MASCOT)
