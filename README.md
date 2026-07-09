# Mr. Jober 🕵️

**Your private eye for the job hunt.** A local-first, agentic job-search tool that
finds roles, scores them against your fit, runs a visa-sponsorship background check,
tailors your resume, drafts cover letters, and helps you reach the right people — with
you approving every move.

Mr. Jober runs entirely on your machine. Your resume, your API key, and your data never
leave your laptop.

---

## What it does

- **Discovers jobs** straight from company career pages (Greenhouse, Lever, Ashby) — no
  stale aggregator reposts.
- **Scores each role** against your profile with Claude, and explains its reasoning.
- **Visa intelligence** — cross-references the USCIS H-1B Employer Data Hub so you know
  which companies actually sponsor (a signal, never a hard filter).
- **Tailors your resume** to a specific role (Mr. Fixer) and **drafts a cover letter**
  (Mr. Wordsmith) — honest edits only, never fabricated experience.
- **Tracks applications** on a board with follow-up nudges and LinkedIn outreach help.
- **You approve everything.** Nothing is auto-submitted, ever.

---

## Prerequisites

Before you start, you'll need:

1. **Python 3.11 or newer** (developed on 3.13). Check with `python --version`.
2. **An Anthropic API key** — get one at https://console.anthropic.com. You pay Anthropic
   directly for usage; browsing is free, and AI actions cost a few cents each.
3. **LibreOffice** — used to export tailored resumes to PDF. Install for your OS:
   - **macOS:** `brew install --cask libreoffice`
   - **Ubuntu/Debian:** `sudo apt install libreoffice`
   - **Windows:** download from https://www.libreoffice.org/download/
4. **The USCIS H-1B Employer Data Hub file** (for the visa layer):
   - Download the "Employer Information" export from
     https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
   - Save it as `data/h1b/Employer Information.xlsx` (note the space in the filename).
   - This file is large and not included in the repo — you supply your own.

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/dharmilgandhi29/Job-application-agent.git
cd Job-application-agent

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install the Playwright browser (needed for scraping some job pages)
playwright install chromium

# 5. Set up your API key
cp .env.example .env
# then open .env and paste in your ANTHROPIC_API_KEY

# 6. Set up your profile
cp config/user.example.json config/user.json
# then edit config/user.json with your details (name, target roles, work auth)

# 7. Load the H-1B visa data (after placing the xlsx — see Prerequisites step 4)
python load_h1b.py

# 8. Run it
python run.py
```

Then open **http://localhost:8000** in your browser. On first run, Mr. Jober will walk
you through onboarding — your name, work authorization, and resume.

---

## How to use it

1. **Onboard** — drop in your resume (.docx), and Mr. Jober parses it.
2. **Browse leads** on the dashboard — each scored, ranked, and visa-checked. Browsing is free.
3. **Open a case file** — see the full reasoning, then send in **Mr. Fixer** (resume),
   **Mr. Wordsmith** (cover letter), or "Do the Whole Thing" (both). These run the AI
   and cost a few cents each.
4. **Mark as applied** — it lands on **The Board**, where you get follow-up nudges and
   LinkedIn outreach help.

---

## Cost

Mr. Jober is free to run and browse. You only spend on explicit AI actions, billed by
Anthropic to your own key:

- Resume auto-parse (once per onboard): ~$0.003
- Full application (research + resume + cover letter): ~$0.17
- Outreach message draft: ~$0.001

You approve every AI action, so costs never surprise you.

---

## Tech

FastAPI · SQLite · Claude (Haiku for high-volume, Sonnet for judgment writing) ·
Playwright · LibreOffice (PDF export) · pandas (H-1B data).

---

## Honest caveats

- **Visa intelligence is a signal, not a guarantee.** It reflects a company's recent
  H-1B filing history, which is a prior, not a prediction. A company that sponsored last
  year might not this year, and vice versa.
- **The resume tailoring never fabricates.** It reshapes and reframes your real
  experience to match a role, and flags gaps honestly rather than inventing skills.
- **This is a personal tool, run locally.** It's not a hosted service. Each person runs
  their own copy with their own key and data.

---

*Mr. Jober is a personal project. Use it, fork it, make it yours.*
