# 🚀 AI Auto Applier — Autonomous Job Application System

An AI-powered multi-user SaaS platform that **automatically discovers, matches, tailors 1-page resumes, and applies to jobs** on your behalf across multiple platforms (LinkedIn, Indeed, RemoteOK, Naukri, Wellfound).

![AI Auto Applier](https://img.shields.io/badge/Stack-Next.js%2014%20%7C%20FastAPI%20%7C%20Groq%20LLM%20%7C%20Playwright-6c5ce7)
![License](https://img.shields.io/badge/License-MIT-00cec9)

---

## 🌟 Key Features

- 💼 **Multi-Platform Job Scraping**: Automated periodic scraping from RemoteOK, LinkedIn Jobs, Indeed, Naukri, and Wellfound.
- 🧠 **Groq Llama 3.3 70B AI Matching**: Evaluates live job postings against your profile, extracts required skills, and computes an 0–100% compatibility match score.
- 📏 **Strict 1-Page Resume Tailoring**: Rewrites summary and bullet points per JD, weaving in relevant keywords while enforcing a strict **1-page ATS layout constraint**.
- ✉️ **Personalized Cover Letter Generator**: Generates customized 3-4 paragraph cover letters referencing specific job requirements and company details.
- 🔐 **Persistent Browser Session Login**: Uses Playwright persistent profile contexts—log into LinkedIn/Indeed **once** manually and your session remains authenticated forever.
- ⚡ **Application Review Queue**: Semi-auto approval workflow to review & edit AI-tailored resumes and cover letters before submission.
- 📌 **Kanban Application Tracker**: Visual board tracking application lifecycle (`Review Queue` → `Submitted` → `Employer Viewed` → `Interview Scheduled` → `Offer/Archived`).
- 🔌 **Extensible Plugin System**: Add new job boards by creating a single Python file conforming to the `JobScraperPlugin` protocol.

---

## 🏗️ Architecture & Tech Stack

```
┌────────────────────────────────────────────────────────┐
│                   VERCEL (Frontend)                    │
│  Next.js 14 (App Router) + Vanilla CSS Glassmorphism  │
│  All 10 Pages: Dashboard, Jobs, Queue, Tracker, etc.  │
└───────────────────────────┬────────────────────────────┘
                            │ REST API / WebSocket
┌───────────────────────────┴────────────────────────────┐
│                   RAILWAY (Backend)                    │
│  Python FastAPI + LangGraph Orchestrator               │
│  Groq Llama 3.3 70B LLM + Playwright Login Manager     │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────┴────────────────────────────┐
│                 PostgreSQL / SQLite DB                 │
└────────────────────────────────────────────────────────┘
```

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 14 (App Router), React, Vanilla CSS (Dark Theme + Glassmorphism) |
| **Backend** | Python FastAPI, Async SQLAlchemy |
| **AI LLM Engine** | Groq API (`llama-3.3-70b-versatile`), Google Gemini, OpenAI |
| **Automation** | Playwright Python (Persistent Contexts) |
| **Resume Engine** | `docxtpl` + `python-docx` (Word & PDF templates) |

---

## ⚙️ Quick Start Guide

### 1. Prerequisites
- Node.js 18+ and `npm`
- Python 3.10+ and `uv` (or `pip`)

### 2. Environment Configuration (`.env`)
Create a `.env` file in the root directory:

```env
# Database
DATABASE_URL=sqlite+aiosqlite:///./data/app.db
# Signs logins and encrypts users' stored API keys / mailbox passwords.
# Generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"
# Changing it later makes stored keys undecryptable (users must re-add them).
NEXTAUTH_SECRET=your-long-random-secret
```

**AI keys are not configured here.** Each user adds their own keys (Groq, Gemini, OpenAI or
Anthropic) in **Settings → AI keys**. Several keys are used in rotation; a rate-limited key rests
until the provider's reset time, and work waits for it (up to the user's limit) instead of failing.

---

## 🚀 Running the Application Live

### Step A: Launch Frontend Dashboard
```bash
cd frontend
npm install
npm run dev
```
👉 **Dashboard UI**: `http://localhost:3000`

### Step B: Launch FastAPI Backend Server
```bash
# Windows: use .venv312 - Application Control blocks the uv Python 3.14 in .venv
cd backend
..\.venv312\Scripts\python.exe main.py
# first time only: ..\.venv312\Scripts\python.exe -m playwright install chromium
```
Restart the backend after code changes (auto-reload is unreliable on this machine).

**LaTeX resumes:** PDFs are compiled from the LaTeX template with [Tectonic](https://tectonic-typesetting.github.io)
(`tools/tectonic/tectonic.exe` locally; set `TECTONIC_PATH` to use another copy). The first compile downloads
~45 MB of LaTeX packages (cached afterwards). If Tectonic is missing or fails, the built-in ATS-safe PDF is used.
Application-form uploads always use the ATS-safe PDF. Choose the style in **Settings → Resume details**.

**Docker (deployment):** `docker build -f backend/Dockerfile -t ai-applier-backend .` then
`docker run -p 8000:8000 --env-file .env -v applier-data:/app/data ai-applier-backend`.
The image installs Tectonic and pre-fills its package cache at build time. (Assisted apply opens a visible
browser, so it only works when the backend runs on your own computer.)

👉 **Backend API**: `http://localhost:8000`  

### How to use it
1. **Profile**: upload your resume. **Settings**: set target roles, locations and the minimum match score.
2. **Target Companies**: add companies you want to work for (Greenhouse / Lever / Ashby boards are detected automatically).
3. **Find New Jobs**: searches company career pages first, then 6 job boards. Every job gets a trust score
   (verified on company site / likely real / unconfirmed / stale / suspicious); suspicious and stale jobs never reach the AI.
4. **Application Queue**: approve, then **Auto-fill application** opens the real form in a browser window, fills it and
   attaches the tailored resume. You review and click Submit; the confirmation page is detected automatically.
5. **Settings → Email**: connect Gmail with an App Password to email hiring teams from the queue, track replies
   (interview / assessment / rejection / offer update the tracker automatically, hourly) and send follow-ups after 7 days.
👉 **Interactive API Docs**: `http://localhost:8000/docs`

---

## 🧪 Testing Live Job Discovery & Resume Tailoring

Run the automated live test script to verify scraping and Groq AI resume tailoring:

```bash
python backend/test_live.py
```

Output:
```text
=== Starting Live Job Discovery & Resume Tailoring Test ===
[SUCCESS] Found 6 live jobs!
--- Target Job: AI Engineer at RemoteOK ---
--- AI Agent Pipeline Execution Finished ---
  * Match Score: 80.0% (Groq Llama 3.3 70B)
  * Matched Skills: ['Python', 'FastAPI', 'React', 'Next.js']
[SUCCESS] 1-Page Resume Generated Successfully at: data/test_output/resume_live_test_1.txt
```

---

## 🔌 Adding a New Job Platform (Plugin Architecture)

To add a new job board (e.g. `dice`), create **ONE file** at `backend/scrapers/dice_scraper.py`:

```python
from scrapers.base_scraper import ScrapedJob

class DiceScraper:
    platform_name = "dice"
    requires_login = False

    async def initialize(self, config: dict, browser_context=None) -> None:
        pass

    async def search_jobs(self, query: str, location: str = "", filters: dict = None) -> list[ScrapedJob]:
        # Custom search logic here
        return [
            ScrapedJob(
                title=f"{query} Role",
                company="Company Name",
                location=location or "Remote",
                description="JD text here...",
                url="https://dice.com/job/123",
                platform="dice"
            )
        ]
```
The scraper plugin registry will **auto-discover** your scraper on restart! No core code changes needed.

---

## ☁️ Deploying

Everything deploys as **one Vercel project** (Next.js frontend + FastAPI backend as Vercel Services) with
**Neon** Postgres - see [DEPLOY.md](DEPLOY.md). The only setting the database needs is `DATABASE_URL`
(Vercel environment variable, or `.env` locally). Long work runs as resumable background jobs, so it fits
serverless time limits. An always-on Docker deployment (`backend/Dockerfile`, `render.yaml`) also works.

## 📄 License
MIT License. Built for autonomous job discovery, matching, and career acceleration.
