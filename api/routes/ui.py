"""
ui.py — Mr. Jober's office.

Serves the web interface: a detective-themed dashboard where Mr. Jober (your
job-hunting private investigator) presents the leads he's dug up. The scored jobs
from jobs.db become "case files," ranked by lead strength, each with a visa
background-check stamp. Light, warm, colorful where it counts.

Endpoints:
  GET /            -> the dashboard page
  GET /api/jobs    -> scored jobs as JSON (the data the page renders)
"""

import sqlite3
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["UI"])

DB_PATH = "jobs.db"


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# Stored visa_verdict -> (badge label, tone). Tone drives the stamp color.
_VERDICT_LABELS = {
    "silent_but_sponsors":      ("visa verified", "good"),
    "says_closed_but_sponsors": ("visa verified", "good"),
    "renewals_only":            ("renewals only", "warn"),
    "claims_but_no_history":    ("unconfirmed", "warn"),
    "no_history":               ("no record", "neutral"),
    "unknown":                  ("visa unknown", "neutral"),
}


@router.get("/api/jobs")
async def api_jobs():
    """Scored jobs as JSON, best lead first."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT job_id, title, company, location, score, role_type, "
            "visa_verdict, sponsor_new, job_url "
            "FROM jobs WHERE score IS NOT NULL ORDER BY score DESC"
        ).fetchall()

    jobs = []
    for r in rows:
        j = dict(r)
        label, tone = _VERDICT_LABELS.get(j.get("visa_verdict") or "unknown",
                                          ("visa unknown", "neutral"))
        j["visa_label"] = label
        j["visa_tone"] = tone
        jobs.append(j)

    # Stats for Mr. Jober's briefing.
    stats = {
        "total": len(jobs),
        "sponsors": sum(1 for j in jobs if j["visa_tone"] == "good"),
        "strong": sum(1 for j in jobs if (j["score"] or 0) >= 65),
    }
    return JSONResponse({"jobs": jobs, "stats": stats})


@router.get("/")
async def dashboard():
    from api.routes.onboarding import _is_ready
    from fastapi.responses import RedirectResponse, HTMLResponse
    if not _is_ready():
        return RedirectResponse(url="/welcome")
    return HTMLResponse(_PAGE)


# The detective mascot, as inline SVG so it's crisp and reusable.
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
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700;9..144,800&family=Inter:wght@400;500;600;700&display=swap"rel="stylesheet">
<style>
  :root{
    --paper:#FBF6EC; --card:#FFFDF9; --ink:#2D2A3E; --muted:#9A8F7C;
    --line:#EBE1CE; --coral:#FF6B4A; --purple:#8659C4; --green:#22A45C;
    --amber:#B45309;
  }
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
  .status{display:flex; gap:7px; align-items:center; background:#EAF9EF;
    padding:7px 13px; border-radius:20px;}
  .status .dot{width:7px; height:7px; border-radius:50%; background:var(--green);
    animation:pulse 2s infinite;}
  .status span{font-size:12px; font-weight:700; color:#15803D;}
  @keyframes pulse{0%,100%{opacity:1;}50%{opacity:.35;}}

  .greeting{display:flex; gap:14px; align-items:flex-start; padding:26px 0 18px;}
  .greeting .av{width:38px; height:38px; border-radius:10px; background:var(--ink);
    flex-shrink:0; display:flex; align-items:center; justify-content:center; padding:6px;}
  .bubble{background:var(--card); border:2px solid var(--line);
    border-radius:4px 16px 16px 16px; padding:14px 18px; font-size:14.5px;
    line-height:1.6; color:#413B4D;}
  .bubble b{color:var(--coral);} .bubble .g{color:#15803D;}

  .stats{display:flex; gap:12px; padding-bottom:20px;}
  .stat{flex:1; border-radius:15px; padding:16px 18px; color:#fff;}
  .stat .n{font-size:27px; font-weight:800; line-height:1; font-family:Fraunces,serif;}
  .stat .l{font-size:11.5px; opacity:.92; margin-top:4px; font-weight:600;}

  .sectionhead{display:flex; align-items:center; gap:8px; padding:4px 0 14px;}
  .sectionhead svg{stroke:var(--muted);}
  .sectionhead span{font-size:12.5px; font-weight:700; color:var(--muted);
    text-transform:uppercase; letter-spacing:.09em;}

  .grid{display:grid; gap:11px; padding-bottom:24px;}
  .lead{background:var(--card); border:2px solid var(--line); border-radius:15px;
    padding:15px 18px; display:flex; align-items:center; gap:16px; cursor:pointer;
    transition:transform .2s cubic-bezier(.2,.7,.2,1), box-shadow .2s, border-color .2s;
    opacity:0; transform:translateY(14px);}
  .lead.in{opacity:1; transform:none;}
  .lead:hover{transform:translateY(-2px); box-shadow:0 10px 26px rgba(45,42,62,.09);
    border-color:#D9CDB5;}
  .score{width:54px; height:54px; border-radius:13px; display:flex;
    align-items:center; justify-content:center; flex-shrink:0;
    font-size:23px; font-weight:800; color:#fff; font-family:Fraunces,serif;}
  .lead .meta{flex:1; min-width:0;}
  .lead .role{font-size:14.5px; font-weight:700;}
  .lead .sub{font-size:12.5px; color:var(--muted); margin-top:1px;}
  .stamp{font-size:10.5px; font-weight:800; padding:5px 10px; border-radius:7px;
    letter-spacing:.03em; white-space:nowrap;}
  .stamp.good{background:#EAF9EF; color:#15803D;}
  .stamp.warn{background:#FFF1E0; color:var(--amber);}
  .stamp.neutral{background:#F1EEE7; color:#8B8B96;}

  .cta{background:var(--ink); border-radius:16px; padding:17px 22px;
    display:flex; align-items:center; justify-content:space-between; margin-bottom:40px;}
  .cta .left{display:flex; align-items:center; gap:13px;}
  .cta svg{stroke:var(--coral);}
  .cta .t{font-size:14.5px; font-weight:700; color:#fff;}
  .cta .s{font-size:12px; color:#A5A0B5; margin-top:2px;}
  .cta button{background:var(--coral); color:#fff; font-size:12.5px; font-weight:700;
    padding:10px 17px; border-radius:10px; border:none; cursor:pointer;
    font-family:inherit; transition:transform .15s;}
  .cta button:hover{transform:scale(1.04);}

  @media (prefers-reduced-motion:reduce){
    .lead{opacity:1; transform:none;} .status .dot{animation:none;}
  }
  @media (max-width:640px){ .stats{flex-wrap:wrap;} .stat{min-width:44%;} }
</style>
</head>
<body>
  <nav class="nav"><div class="wrap"><div class="navrow">
    <div class="brand">
      <div class="badge-avatar">""" + _MASCOT + """</div>
      <div><h1 class="display">Mr. Jober</h1><p>private investigator, job division</p></div>
    </div>
    <div class="status"><div class="dot"></div><span>on the case</span></div>
  </div></div></nav>

  <main class="wrap">
    <div class="greeting">
      <div class="av">""" + _MASCOT + """</div>
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

function leadRow(j){
  const el = document.createElement('div');
  el.className = 'lead';
  const loc = (j.location || '').split(/[|;]/)[0].trim();
  el.innerHTML = `
    <div class="score" style="background:${SCORE_COLORS(j.score)}">${j.score}</div>
    <div class="meta">
      <div class="role">${j.title}</div>
      <div class="sub">${j.company}${loc ? ' \\u00b7 ' + loc : ''}</div>
    </div>
    <span class="stamp ${j.visa_tone}">${j.visa_label.toUpperCase()}</span>`;
  return el;
}

fetch('/api/jobs').then(r => r.json()).then(data => {
  const {jobs, stats} = data;
  document.getElementById('greeting').innerHTML =
    `Case cracked open, boss. I dug up <b>${stats.total} leads</b> and ran background checks on every one. The <span class="g">green stamps</span> mean the company's got a real visa sponsorship record. Point me at a lead and I'll build the case file.`;

  document.getElementById('stats').innerHTML = `
    <div class="stat" style="background:var(--coral)"><div class="n">${stats.total}</div><div class="l">leads dug up</div></div>
    <div class="stat" style="background:var(--green)"><div class="n">${stats.sponsors}</div><div class="l">visa sponsors</div></div>
    <div class="stat" style="background:var(--purple)"><div class="n">${stats.strong}</div><div class="l">strong leads</div></div>`;

  const grid = document.getElementById('grid');
  jobs.forEach(j => grid.appendChild(leadRow(j)));

  const io = new IntersectionObserver(entries => {
    entries.forEach((e, i) => { if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } });
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