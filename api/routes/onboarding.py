"""
onboarding.py — Mr. Jober's front door.

First-run setup: Mr. Jober asks the person's name, visa status, and takes a resume
(.docx) upload. Saves the resume as the anchor, auto-parses it into a full profile
(via tools/parse_resume), writes resume.md, and writes config/user.json so everything
downstream works unchanged.

  GET  /welcome       -> the onboarding page
  POST /api/onboard   -> save + parse resume, write user.json + resume.md
  GET  /api/setup     -> {"ready": bool} so the dashboard knows whether to redirect
"""

import json
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["Onboarding"])

_CONFIG = Path("config/user.json")
_PROJECT_ROOT = Path(".")

# Visa dropdown value -> the profile string the scorer/visa layer reads.
_VISA_MAP = {
    "citizen":     "US Citizen — no sponsorship required",
    "green_card":  "Permanent Resident (Green Card) — no sponsorship required",
    "need_now":    "Requires visa sponsorship now",
    "opt_then_h1b":"F1 OPT (STEM) — authorized to work without sponsorship for up to 3 years, H1B sponsorship needed after",
    "other":       "Other / prefer not to say",
}


def _is_ready() -> bool:
    if not _CONFIG.exists():
        return False
    try:
        data = json.loads(_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return False
    name = (data.get("name") or "").strip()
    anchor = data.get("anchor_docx")
    if not name or not anchor:
        return False
    return (_PROJECT_ROOT / anchor).exists()


@router.get("/api/setup")
async def setup_status():
    return JSONResponse({"ready": _is_ready()})


@router.post("/api/onboard")
async def onboard(
    name: str = Form(...),
    visa: str = Form("other"),
    resume: UploadFile = File(...),
):
    """Save the resume, auto-parse it into a profile, write user.json + resume.md."""
    name = name.strip()
    if not name:
        return JSONResponse({"ok": False, "error": "Please tell me your name."}, status_code=400)

    filename = resume.filename or ""
    if not filename.lower().endswith(".docx"):
        return JSONResponse(
            {"ok": False, "error": "Mr. Jober needs a Word .docx resume to work his magic."},
            status_code=400,
        )

    # 1. Save the resume as the anchor.
    safe = "".join(c if c.isalnum() or c in " -_" else "" for c in name).strip()
    anchor_name = f"Resume_{safe.replace(' ', '_')}.docx"
    anchor_path = _PROJECT_ROOT / anchor_name
    with anchor_path.open("wb") as f:
        shutil.copyfileobj(resume.file, f)

    # 2. Auto-parse it into a profile + markdown. (Import here so a parse-time
    #    import error can't stop the whole app from loading.)
    try:
        from tools.parse_resume import parse_resume
        parsed = await parse_resume(str(anchor_path))
        profile = parsed["profile"]
        markdown = parsed["markdown"]
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"I saved your resume but couldn't read it cleanly: {e}"},
            status_code=500,
        )

    # 3. Stamp in the fields the resume can't provide.
    profile["name"] = name
    profile["visa_status"] = _VISA_MAP.get(visa, _VISA_MAP["other"])

    # 4. Write resume.md (cover-letter content reads this).
    Path("resume.md").write_text(markdown, encoding="utf-8")

    # 5. Write user.json.
    data = {
        "name": name,
        "anchor_docx": anchor_name,
        "resume_md": "resume.md",
        "profile": profile,
    }
    _CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return JSONResponse({"ok": True, "name": name, "anchor": anchor_name})


@router.get("/welcome", response_class=HTMLResponse)
async def welcome():
    return _WELCOME


_MASCOT = """<svg viewBox="0 0 72 72" fill="none" style="width:100%;height:100%;">
  <path d="M12 30 Q36 8 60 30 L55 22 Q36 4 17 22 Z" fill="#FF6B4A"/>
  <rect x="12" y="28" width="48" height="6" rx="3" fill="#FF6B4A"/>
  <circle cx="36" cy="42" r="16" fill="#FFD9A0"/>
  <circle cx="30" cy="40" r="5.5" fill="#fff" stroke="#2D2A3E" stroke-width="2"/>
  <circle cx="42" cy="40" r="5.5" fill="#fff" stroke="#2D2A3E" stroke-width="2"/>
  <circle cx="30.5" cy="40.5" r="2.2" fill="#2D2A3E"/>
  <circle cx="42.5" cy="40.5" r="2.2" fill="#2D2A3E"/>
  <path d="M31 50 Q36 53 41 50" stroke="#B45309" stroke-width="2.4" fill="none" stroke-linecap="round"/>
</svg>"""


_WELCOME = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mr. Jober</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700;9..144,800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{ --paper:#FBF6EC; --card:#FFFDF9; --ink:#2D2A3E; --muted:#9A8F7C;
    --line:#EBE1CE; --coral:#FF6B4A; --green:#22A45C; }
  *{box-sizing:border-box;}
  body{margin:0; background:var(--paper); color:var(--ink); min-height:100vh;
    font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;
    display:flex; align-items:center; justify-content:center; padding:24px;}
  .display{font-family:Fraunces,Georgia,serif;}
  .box{max-width:480px; width:100%; text-align:center;}
  .av{width:88px; height:88px; border-radius:22px; background:var(--ink);
    display:flex; align-items:center; justify-content:center; padding:14px;
    margin:0 auto 22px; animation:drop .6s cubic-bezier(.2,.8,.2,1);}
  @keyframes drop{from{opacity:0; transform:translateY(-16px);}to{opacity:1;transform:none;}}
  h1{font-size:32px; font-weight:800; margin:0 0 8px; letter-spacing:-.02em;}
  .lede{font-size:16px; color:#5A5468; line-height:1.55; margin:0 0 30px;}
  .lede b{color:var(--coral);}
  .field{text-align:left; margin-bottom:16px;}
  label{display:block; font-size:12.5px; font-weight:700; color:var(--muted);
    text-transform:uppercase; letter-spacing:.06em; margin-bottom:7px;}
  input[type=text], select{width:100%; padding:13px 15px; font-size:15px; font-family:inherit;
    border:2px solid var(--line); border-radius:12px; background:var(--card);
    color:var(--ink); outline:none; transition:border-color .15s;}
  input[type=text]:focus, select:focus{border-color:var(--coral);}
  .drop{border:2px dashed var(--line); border-radius:14px; padding:26px 20px;
    background:var(--card); cursor:pointer; transition:border-color .15s, background .15s;}
  .drop:hover{border-color:var(--coral);}
  .drop.has{border-color:var(--green); border-style:solid;}
  .drop .icon{margin-bottom:10px;}
  .drop .main{font-size:14.5px; font-weight:600;}
  .drop .sub{font-size:12.5px; color:var(--muted); margin-top:3px;}
  .go{width:100%; margin-top:24px; padding:15px; font-size:15px; font-weight:700;
    font-family:inherit; color:#fff; background:var(--coral); border:none;
    border-radius:12px; cursor:pointer; transition:transform .15s, opacity .15s;}
  .go:hover{transform:translateY(-1px);}
  .go:disabled{opacity:.5; cursor:not-allowed; transform:none;}
  .err{color:#C0392B; font-size:13px; margin-top:14px; min-height:18px;}
</style>
</head>
<body>
  <div class="box">
    <div class="av">__MASCOT__</div>
    <h1 class="display">Mr. Jober here.</h1>
    <p class="lede">I'm your job-hunting private eye. Give me your <b>name</b> and your <b>resume</b>, and I'll start digging up leads worth your time.</p>

    <div class="field">
      <label>What should I call you?</label>
      <input type="text" id="name" placeholder="Your name" autocomplete="name">
    </div>

    <div class="field">
      <label>Work authorization</label>
      <select id="visa">
        <option value="opt_then_h1b">F1 OPT / STEM (need H1B sponsorship later)</option>
        <option value="need_now">Need visa sponsorship now</option>
        <option value="citizen">US Citizen</option>
        <option value="green_card">Green Card holder</option>
        <option value="other">Other / prefer not to say</option>
      </select>
    </div>

    <div class="field">
      <label>Your resume (Word .docx)</label>
      <div class="drop" id="drop" onclick="document.getElementById('file').click()">
        <div class="icon">
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#9A8F7C" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
        </div>
        <div class="main" id="dropmain">Drop your resume here, or click to pick</div>
        <div class="sub">Word document, .docx only</div>
      </div>
      <input type="file" id="file" accept=".docx" style="display:none">
    </div>

    <button class="go" id="go" disabled>Start the investigation</button>
    <div class="err" id="err"></div>
  </div>

<script>
const nameEl = document.getElementById('name');
const visaEl = document.getElementById('visa');
const fileEl = document.getElementById('file');
const drop = document.getElementById('drop');
const dropmain = document.getElementById('dropmain');
const go = document.getElementById('go');
const err = document.getElementById('err');
let chosen = null;

function refresh(){ go.disabled = !(nameEl.value.trim() && chosen); }
nameEl.addEventListener('input', refresh);

fileEl.addEventListener('change', e => {
  const f = e.target.files[0];
  if(!f) return;
  if(!f.name.toLowerCase().endsWith('.docx')){ err.textContent = "I need a .docx file, boss."; return; }
  chosen = f; err.textContent = "";
  drop.classList.add('has'); dropmain.textContent = f.name; refresh();
});

['dragover','dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.style.borderColor = '#FF6B4A'; }));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.style.borderColor = ''; }));
drop.addEventListener('drop', e => {
  const f = e.dataTransfer.files[0];
  if(f && f.name.toLowerCase().endsWith('.docx')){ fileEl.files = e.dataTransfer.files; chosen = f; drop.classList.add('has'); dropmain.textContent = f.name; err.textContent=""; refresh(); }
  else { err.textContent = "That's not a .docx, boss."; }
});

go.addEventListener('click', async () => {
  go.disabled = true; go.textContent = "Reading your file, sit tight...";
  const fd = new FormData();
  fd.append('name', nameEl.value.trim());
  fd.append('visa', visaEl.value);
  fd.append('resume', chosen);
  try {
    const r = await fetch('/api/onboard', { method:'POST', body: fd });
    const data = await r.json();
    if(data.ok){ window.location.href = '/'; }
    else { err.textContent = data.error || "Something went sideways."; go.disabled=false; go.textContent="Start the investigation"; }
  } catch(e){
    err.textContent = "Couldn't reach the office. Is the server running?";
    go.disabled=false; go.textContent="Start the investigation";
  }
});
</script>
</body>
</html>"""

_WELCOME = _WELCOME.replace("__MASCOT__", _MASCOT)
