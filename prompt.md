# 🚀 AI Auto Applier — Complete Build Prompt

> **What is this file?** This is a complete, self-contained execution specification. Follow it top-to-bottom to build the entire AI Auto Applier system — a multi-user SaaS that autonomously discovers, matches, tailors resumes, and applies to jobs.

---

## 🏗️ System Overview

**AI Auto Applier** is a multi-user SaaS platform where each user can:
1. Upload their resume and define their skills/preferences
2. The system scrapes jobs from multiple platforms (LinkedIn, Indeed, RemoteOK, Wellfound, Naukri, Glassdoor, etc.)
3. AI matches jobs to the user's profile and scores compatibility
4. AI tailors the resume per JD — rewrites bullets, reorders skills, picks relevant projects
5. AI generates a personalized cover letter
6. The system auto-fills and submits applications (with optional human approval)
7. A premium dashboard tracks everything in real-time

**Multi-User**: Each user gets their own profile, preferences, job feed, sessions, and application history. Users are isolated — no cross-user data leakage.

**Extensible**: New job platforms can be added via a plugin system without modifying core code.

**Deployable**: Frontend on Vercel, Backend + Workers on Railway, Database on Neon PostgreSQL.

---

## 📐 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        VERCEL (Frontend)                        │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Next.js 14 (App Router)                                 │  │
│  │  ├── Auth.js (Google/GitHub/Email login)                  │  │
│  │  ├── Dashboard Pages (Profile, Jobs, Queue, Tracker, etc) │  │
│  │  ├── Real-time WebSocket updates                         │  │
│  │  └── Vanilla CSS (dark theme, glassmorphism)             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │ REST API + WebSocket             │
└──────────────────────────────┼──────────────────────────────────┘
                               │
┌──────────────────────────────┼──────────────────────────────────┐
│                        RAILWAY (Backend)                        │
│  ┌───────────────────────────┼───────────────────────────────┐  │
│  │  FastAPI (Main API)       │                               │  │
│  │  ├── User Management      ├── Job Scraping Service        │  │
│  │  ├── Profile CRUD         ├── AI Agent Pipeline           │  │
│  │  ├── Application Tracking ├── Resume Generator            │  │
│  │  └── WebSocket Manager    └── Form Filler (Playwright)    │  │
│  └───────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Worker (Background Jobs via APScheduler)                 │  │
│  │  ├── Periodic job scraping (every 6 hours per user)       │  │
│  │  ├── Session health checks                                │  │
│  │  └── Application status polling                           │  │
│  └───────────────────────────────────────────────────────────┘  │
└──────────────────────────────┼──────────────────────────────────┘
                               │
┌──────────────────────────────┼──────────────────────────────────┐
│                     NEON (PostgreSQL Database)                   │
│  ├── users, profiles, skills, experiences, projects             │
│  ├── job_listings, applications, application_logs               │
│  ├── platform_sessions, platform_configs                        │
│  └── resume_templates, generated_resumes                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology | Version | Why |
|-------|-----------|---------|-----|
| **Frontend** | Next.js (App Router) | 14.x | SSR, premium UI, Vercel-native |
| **Styling** | Vanilla CSS + CSS Variables | — | Full design control, no deps, dark theme + glassmorphism |
| **Authentication** | Auth.js (NextAuth v5) | 5.x | Google/GitHub/Email login, session management |
| **Backend** | Python FastAPI | 0.110+ | Async, fast, OpenAPI auto-docs |
| **AI Orchestration** | LangGraph | 0.2+ | Stateful multi-agent workflows with HITL |
| **LLM** | Google Gemini API | gemini-2.0-flash | Free tier, fast, high quality |
| **Browser Automation** | Playwright (Python) | 1.45+ | Persistent sessions, stealth, reliable |
| **Database** | PostgreSQL (Neon) | 16 | Multi-user isolation, RLS, scalable |
| **ORM** | SQLAlchemy (async) | 2.0+ | Async support, mature |
| **Resume Parsing** | PyMuPDF + python-docx | — | Extract structured data from PDF/DOCX |
| **Resume Generation** | docxtpl + python-docx | — | Template-driven professional DOCX |
| **PDF Conversion** | WeasyPrint or LibreOffice CLI | — | DOCX/HTML → PDF |
| **Job Scraping** | httpx + BeautifulSoup + Playwright | — | Mix of API + browser scraping |
| **Task Queue** | APScheduler | 3.10+ | Background job scheduling |
| **WebSocket** | FastAPI WebSocket | — | Real-time dashboard updates |
| **File Storage** | Local filesystem (Railway volume) | — | Resume files, session data |

---

## 📁 Complete Project Structure

```
d:/AI Auto applier/
│
├── prompt.md                          # THIS FILE — execution specification
├── implementation_plan.md             # Architecture overview
├── .gitignore                         # Ignore sessions, .env, node_modules, etc.
├── README.md                          # Project README
│
├── backend/                           # Python FastAPI Backend
│   ├── main.py                        # FastAPI app entry + CORS + WebSocket
│   ├── requirements.txt               # All Python dependencies
│   ├── Dockerfile                     # For Railway deployment
│   ├── .env.example                   # Template for environment variables
│   ├── config.py                      # Pydantic Settings (reads .env)
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   ├── database.py                # Async engine + session factory
│   │   ├── models.py                  # All SQLAlchemy models
│   │   └── seed.py                    # Seed resume templates
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py                  # Main API router (includes all sub-routers)
│   │   ├── auth.py                    # Auth endpoints (verify JWT from Next.js)
│   │   ├── profile.py                 # Profile CRUD
│   │   ├── skills.py                  # Skills management
│   │   ├── jobs.py                    # Job listing endpoints
│   │   ├── applications.py            # Application tracking
│   │   ├── sessions.py               # Platform session management
│   │   ├── templates.py              # Resume template endpoints
│   │   └── analytics.py              # Stats & chart data
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── orchestrator.py            # LangGraph main workflow
│   │   ├── jd_analyzer.py             # JD parsing & keyword extraction
│   │   ├── match_scorer.py            # Job-profile semantic matching
│   │   ├── resume_tailor.py           # Resume bullet rewriting
│   │   ├── cover_letter.py            # Cover letter generation
│   │   └── guardrails.py             # Prevent hallucination of skills/experience
│   │
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── registry.py               # Plugin registry — discovers & loads scrapers
│   │   ├── base_scraper.py            # Protocol (interface) all scrapers implement
│   │   ├── scraper_config.py          # Per-platform config (selectors, delays, etc.)
│   │   ├── linkedin_scraper.py
│   │   ├── indeed_scraper.py
│   │   ├── remoteok_scraper.py
│   │   ├── wellfound_scraper.py
│   │   ├── naukri_scraper.py
│   │   ├── glassdoor_scraper.py
│   │   └── generic_scraper.py         # Fallback for unknown sites
│   │
│   ├── automation/
│   │   ├── __init__.py
│   │   ├── login_manager.py           # Persistent session management per user
│   │   ├── browser_manager.py         # Playwright lifecycle (launch, close, pool)
│   │   ├── form_filler.py             # AI-powered form filling
│   │   └── platform_handlers/
│   │       ├── __init__.py
│   │       ├── handler_registry.py    # Plugin registry for handlers
│   │       ├── base_handler.py        # Protocol for platform handlers
│   │       ├── linkedin_handler.py    # LinkedIn Easy Apply flow
│   │       ├── indeed_handler.py      # Indeed Apply flow
│   │       ├── naukri_handler.py      # Naukri Apply flow
│   │       └── generic_handler.py     # AI fallback for unknown platforms
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── resume_parser.py           # PDF/DOCX → structured JSON
│   │   ├── resume_generator.py        # Tailored data → professional DOCX + PDF
│   │   ├── template_engine.py         # Manage resume templates
│   │   ├── scheduler.py              # APScheduler for background tasks
│   │   ├── websocket_manager.py       # Broadcast events to connected clients
│   │   └── notifications.py          # In-app + email notifications
│   │
│   ├── templates/
│   │   ├── classic.docx               # Classic ATS-friendly template
│   │   ├── modern.docx                # Modern template with subtle styling
│   │   └── minimal.docx              # Minimal clean template
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── llm_client.py             # LLM provider abstraction (Gemini/OpenAI/Claude)
│   │   ├── auth_middleware.py         # Verify JWT tokens from frontend
│   │   └── helpers.py                # Common utility functions
│   │
│   └── tests/
│       ├── test_agents/
│       ├── test_scrapers/
│       ├── test_automation/
│       └── test_api/
│
├── frontend/                          # Next.js 14 Dashboard
│   ├── package.json
│   ├── next.config.js                 # API proxy, env vars
│   ├── middleware.js                   # Auth middleware (protect routes)
│   ├── .env.local.example             # Template for frontend env vars
│   │
│   ├── app/
│   │   ├── layout.js                  # Root layout — sidebar nav, auth provider
│   │   ├── page.js                    # Dashboard home — stats overview
│   │   ├── globals.css                # Design system (ALL CSS)
│   │   │
│   │   ├── auth/
│   │   │   ├── signin/page.js         # Sign-in page (Google/GitHub/Email)
│   │   │   └── signup/page.js         # Sign-up page
│   │   │
│   │   ├── onboarding/
│   │   │   └── page.js               # First-time user setup wizard
│   │   │
│   │   ├── profile/
│   │   │   └── page.js               # Edit profile, skills, experience, projects
│   │   │
│   │   ├── jobs/
│   │   │   └── page.js               # Job discovery feed (matched jobs)
│   │   │
│   │   ├── queue/
│   │   │   └── page.js               # Application queue (review before submit)
│   │   │
│   │   ├── applications/
│   │   │   └── page.js               # Application tracker (all statuses)
│   │   │
│   │   ├── sessions/
│   │   │   └── page.js               # Manage platform login sessions
│   │   │
│   │   ├── templates/
│   │   │   └── page.js               # Resume template selector + preview
│   │   │
│   │   ├── analytics/
│   │   │   └── page.js               # Charts: apply rate, response rate, etc.
│   │   │
│   │   └── settings/
│   │       └── page.js               # API keys, preferences, notification prefs
│   │
│   ├── components/
│   │   ├── Sidebar.js                 # Collapsible sidebar navigation
│   │   ├── TopBar.js                  # Top bar with user avatar, notifications
│   │   ├── JobCard.js                 # Job listing card with match score
│   │   ├── MatchScore.js             # Animated circular match percentage
│   │   ├── ApplicationTimeline.js     # Timeline of application events
│   │   ├── ResumePreview.js          # Side-by-side original vs tailored
│   │   ├── CoverLetterPreview.js     # Generated cover letter display
│   │   ├── SessionStatus.js          # Platform session health badge
│   │   ├── StatsCard.js              # Glassmorphism stat cards
│   │   ├── ApprovalModal.js          # Review & approve application modal
│   │   ├── OnboardingWizard.js       # Multi-step setup wizard
│   │   ├── SkillTag.js               # Colored skill tag component
│   │   ├── PlatformIcon.js           # Platform logo/icon component
│   │   ├── Chart.js                  # Reusable chart wrapper
│   │   └── EmptyState.js            # Empty state illustrations
│   │
│   └── lib/
│       ├── api.js                     # Backend API client (fetch wrapper)
│       ├── auth.js                    # Auth.js configuration
│       ├── websocket.js              # WebSocket client for real-time updates
│       └── utils.js                  # Frontend utilities
│
├── sessions/                          # Persistent browser sessions (GITIGNORED)
│   └── {user_id}/
│       ├── linkedin/
│       ├── indeed/
│       └── .../
│
└── data/                              # Generated files (GITIGNORED)
    └── {user_id}/
        ├── resumes/                   # Generated tailored resumes
        └── screenshots/              # Application screenshots (audit trail)
```

---

## 💾 Database Schema (PostgreSQL)

### Users & Authentication

```sql
-- Managed by Auth.js adapter, but here is the conceptual schema
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255),
    email VARCHAR(255) UNIQUE NOT NULL,
    email_verified TIMESTAMPTZ,
    image TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50),
    provider VARCHAR(50),
    provider_account_id VARCHAR(255),
    refresh_token TEXT,
    access_token TEXT,
    expires_at INTEGER,
    UNIQUE(provider, provider_account_id)
);

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_token VARCHAR(255) UNIQUE NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    expires TIMESTAMPTZ NOT NULL
);
```

### User Profile & Skills

```sql
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    phone VARCHAR(20),
    location VARCHAR(255),
    linkedin_url VARCHAR(500),
    github_url VARCHAR(500),
    portfolio_url VARCHAR(500),
    professional_summary TEXT,
    target_roles TEXT[],
    target_locations TEXT[],
    min_salary INTEGER,
    max_salary INTEGER,
    currency VARCHAR(10) DEFAULT 'USD',
    experience_years INTEGER,
    preferred_company_sizes TEXT[],
    preferred_template VARCHAR(50) DEFAULT 'classic',
    approval_mode VARCHAR(20) DEFAULT 'semi_auto',
    scrape_frequency_hours INTEGER DEFAULT 6,
    is_onboarded BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50),
    proficiency VARCHAR(20) DEFAULT 'proficient',
    years_experience INTEGER,
    UNIQUE(user_id, name)
);

CREATE TABLE experiences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    company VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    location VARCHAR(255),
    start_date DATE NOT NULL,
    end_date DATE,
    is_current BOOLEAN DEFAULT FALSE,
    description TEXT,
    bullets JSONB NOT NULL DEFAULT '[]',
    technologies TEXT[],
    display_order INTEGER DEFAULT 0
);

CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    detailed_description TEXT,
    technologies TEXT[],
    url VARCHAR(500),
    github_url VARCHAR(500),
    impact_metrics TEXT,
    start_date DATE,
    end_date DATE,
    display_order INTEGER DEFAULT 0
);

CREATE TABLE education (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    institution VARCHAR(255) NOT NULL,
    degree VARCHAR(100) NOT NULL,
    field VARCHAR(255),
    start_date DATE,
    end_date DATE,
    gpa VARCHAR(10),
    achievements TEXT[],
    display_order INTEGER DEFAULT 0
);

CREATE TABLE master_resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    file_name VARCHAR(255),
    file_path TEXT,
    file_type VARCHAR(10),
    parsed_text TEXT,
    structured_data JSONB,
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Job Listings & Applications

```sql
CREATE TABLE job_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(50) NOT NULL,
    external_id VARCHAR(255),
    title VARCHAR(500) NOT NULL,
    company VARCHAR(255),
    company_logo_url TEXT,
    location VARCHAR(255),
    is_remote BOOLEAN DEFAULT FALSE,
    job_type VARCHAR(50),
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency VARCHAR(10),
    description_text TEXT,
    description_html TEXT,
    url VARCHAR(1000) NOT NULL,
    apply_url VARCHAR(1000),
    posted_date TIMESTAMPTZ,
    scraped_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    tags TEXT[],
    seniority_level VARCHAR(50),
    dedup_hash VARCHAR(64),
    UNIQUE(dedup_hash)
);

CREATE TABLE user_job_matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID REFERENCES job_listings(id) ON DELETE CASCADE,
    match_score FLOAT NOT NULL,
    matched_skills TEXT[],
    gap_skills TEXT[],
    relevant_projects TEXT[],
    jd_analysis JSONB,
    status VARCHAR(20) DEFAULT 'new',
    matched_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, job_id)
);

CREATE TABLE applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID REFERENCES job_listings(id) ON DELETE CASCADE,
    match_id UUID REFERENCES user_job_matches(id),
    tailored_summary TEXT,
    tailored_bullets JSONB,
    tailored_skills_order TEXT[],
    selected_project_ids UUID[],
    cover_letter TEXT,
    resume_docx_path TEXT,
    resume_pdf_path TEXT,
    status VARCHAR(30) DEFAULT 'pending',
    submitted_at TIMESTAMPTZ,
    last_status_check TIMESTAMPTZ,
    screenshot_paths TEXT[],
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, job_id)
);

CREATE TABLE application_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id UUID REFERENCES applications(id) ON DELETE CASCADE,
    action VARCHAR(100),
    details TEXT,
    screenshot_path TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Platform Sessions & Configuration

```sql
CREATE TABLE platform_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    session_dir TEXT,
    storage_state_path TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    last_verified TIMESTAMPTZ,
    last_used TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, platform)
);

CREATE TABLE platform_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100),
    icon_url TEXT,
    base_url VARCHAR(500),
    requires_login BOOLEAN DEFAULT FALSE,
    scraper_class VARCHAR(255),
    handler_class VARCHAR(255),
    default_delay_min FLOAT DEFAULT 2.0,
    default_delay_max FLOAT DEFAULT 8.0,
    max_scrapes_per_run INTEGER DEFAULT 50,
    is_enabled BOOLEAN DEFAULT TRUE,
    config_json JSONB DEFAULT '{}'
);

CREATE TABLE resume_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(255),
    description TEXT,
    template_path TEXT NOT NULL,
    preview_image_path TEXT,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 🔌 Plugin Architecture — Adding New Platforms

### The Protocol (Interface)

```python
# backend/scrapers/base_scraper.py
from typing import Protocol, runtime_checkable
from dataclasses import dataclass

@dataclass
class ScrapedJob:
    title: str
    company: str
    location: str
    description: str
    url: str
    apply_url: str | None
    salary_min: int | None
    salary_max: int | None
    posted_date: str | None
    is_remote: bool
    job_type: str | None
    tags: list[str]
    platform: str
    external_id: str | None

@runtime_checkable
class JobScraperPlugin(Protocol):
    platform_name: str
    requires_login: bool
    
    async def initialize(self, config: dict, browser_context=None) -> None: ...
    async def search_jobs(self, query: str, location: str, filters: dict | None = None) -> list[ScrapedJob]: ...
    async def get_job_details(self, job_url: str) -> ScrapedJob | None: ...
    async def is_job_active(self, job_url: str) -> bool: ...
```

### The Registry (Auto-Discovery)

```python
# backend/scrapers/registry.py
import importlib, pkgutil
from pathlib import Path

class ScraperRegistry:
    _scrapers: dict[str, type] = {}
    
    @classmethod
    def discover(cls):
        scrapers_dir = Path(__file__).parent
        for importer, module_name, is_pkg in pkgutil.iter_modules([str(scrapers_dir)]):
            if module_name in ('base_scraper', 'registry', 'scraper_config', '__init__'):
                continue
            try:
                module = importlib.import_module(f'.{module_name}', package='scrapers')
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and hasattr(attr, 'platform_name'):
                        cls._scrapers[attr.platform_name] = attr
            except Exception as e:
                print(f"Warning: Failed to load scraper {module_name}: {e}")
    
    @classmethod
    def get_scraper(cls, platform: str) -> type | None:
        return cls._scrapers.get(platform)
    
    @classmethod
    def list_platforms(cls) -> list[str]:
        return list(cls._scrapers.keys())
```

### Adding a New Platform (Example: Dice.com)

Create ONE file — `backend/scrapers/dice_scraper.py`:

```python
class DiceScraper:
    platform_name = "dice"
    requires_login = False
    
    async def initialize(self, config, browser_context=None):
        self.base_url = "https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search"
    
    async def search_jobs(self, query, location, filters=None):
        # Dice API scraping logic
        ...
    
    async def get_job_details(self, job_url):
        ...
    
    async def is_job_active(self, job_url):
        ...
```

Then add to `platform_configs` table. The registry auto-discovers it. No core code changes needed.

---

## 🤖 AI Agent Pipeline (LangGraph)

### Orchestrator Flow

```python
# backend/agents/orchestrator.py
from langgraph.graph import StateGraph, END
from typing import TypedDict

class ApplicationState(TypedDict):
    user_profile: dict
    job_listing: dict
    jd_requirements: dict
    match_score: float
    matched_skills: list[str]
    gap_skills: list[str]
    relevant_project_ids: list[str]
    should_apply: bool
    tailored_summary: str
    tailored_experience_bullets: dict
    tailored_skills_order: list[str]
    selected_projects: list[dict]
    cover_letter: str
    resume_docx_path: str
    resume_pdf_path: str
    status: str
    log: list[str]

def build_application_graph():
    graph = StateGraph(ApplicationState)
    graph.add_node("analyze_jd", analyze_jd_node)
    graph.add_node("score_match", score_match_node)
    graph.add_node("tailor_resume", tailor_resume_node)
    graph.add_node("generate_cover_letter", cover_letter_node)
    graph.add_node("generate_documents", generate_docs_node)
    graph.add_node("skip_job", skip_job_node)
    
    graph.set_entry_point("analyze_jd")
    graph.add_edge("analyze_jd", "score_match")
    graph.add_conditional_edges(
        "score_match",
        lambda state: "tailor" if state["should_apply"] else "skip",
        {"tailor": "tailor_resume", "skip": "skip_job"}
    )
    graph.add_edge("tailor_resume", "generate_cover_letter")
    graph.add_edge("generate_cover_letter", "generate_documents")
    graph.add_edge("generate_documents", END)
    graph.add_edge("skip_job", END)
    
    return graph.compile()
```

### Agent Prompts

**JD Analyzer:**
```
Analyze this job description and extract structured information as JSON:
- required_skills, preferred_skills, responsibilities, tech_stack
- seniority_level, years_experience_required, education_required, job_type
Be precise. Only include skills explicitly mentioned in the JD.
```

**Match Scorer:**
```
Score how well the candidate matches (0-100). Return:
- score, matched_skills, gap_skills, relevant_projects, reasoning
Scoring: 90-100 perfect, 70-89 strong, 60-69 decent, <60 skip.
```

**Resume Tailor (with Guardrails):**
```
Rewrite the resume for this specific JD. Rules:
❌ DO NOT invent skills the candidate does not have
❌ DO NOT fabricate work experience or companies
❌ DO NOT change job titles, company names, or dates
✅ DO rephrase bullets to highlight relevant aspects
✅ DO reorder sections to prioritize matching content
✅ DO use keywords from the JD naturally
✅ DO select the most relevant projects
```

**Cover Letter:**
```
Write a 3-4 paragraph cover letter (250-350 words).
Professional but warm. Reference specific JD requirements.
Mention the company by name. No generic phrases or cliches.
```

---

## 🔐 Login Manager

```python
# backend/automation/login_manager.py
class LoginManager:
    async def get_authenticated_context(self, user_id, platform):
        """
        1. Check session dir exists for user + platform
        2. Launch Playwright persistent context
        3. Verify if still logged in
        4. If expired → open visible browser → user logs in manually
        5. Return authenticated context
        """
    
    async def _verify_login(self, page, platform) -> bool:
        """Navigate to known page, check for logged-in indicator."""
    
    async def _wait_for_login(self, page, platform, timeout=300):
        """Poll every 3s for login success, up to 5 minutes."""
```

**Session Storage:** `sessions/{user_id}/{platform}/` — each user gets isolated browser profiles per platform.

---

## 🖥️ Frontend Design System

```css
:root {
    --bg-primary: #0a0a0f;
    --bg-secondary: #12121a;
    --bg-card: rgba(255, 255, 255, 0.03);
    --bg-glass: rgba(255, 255, 255, 0.05);
    --text-primary: #e8e8ed;
    --text-secondary: #8b8b9e;
    --accent-primary: #6c5ce7;
    --accent-secondary: #00cec9;
    --accent-gradient: linear-gradient(135deg, #6c5ce7, #00cec9);
    --success: #00b894;
    --warning: #fdcb6e;
    --error: #e17055;
    --glass-border: rgba(255, 255, 255, 0.08);
    --glass-blur: blur(20px);
    --font-primary: 'Inter', -apple-system, sans-serif;
    --radius-md: 12px;
    --shadow-glow: 0 0 20px rgba(108, 92, 231, 0.3);
}
```

### Dashboard Pages

| Page | Purpose |
|------|---------|
| `/` | Stats overview — jobs found, applied, interviews, response rate |
| `/onboarding` | Multi-step wizard: upload resume → edit skills → set preferences → connect platforms |
| `/profile` | Edit profile, skills, experience, projects with live resume preview |
| `/jobs` | Job discovery feed with match scores, filters, approve/skip |
| `/queue` | Review tailored resume + cover letter before submission |
| `/applications` | Kanban board — Pending → Submitted → Viewed → Interview → Offer |
| `/sessions` | Manage platform login sessions (connect, verify, disconnect) |
| `/templates` | Resume template selector with live preview |
| `/analytics` | Charts — applications/day, response rates, skill demand, funnel |
| `/settings` | API keys, preferences, notifications, danger zone |

---

## 🚀 Deployment

### Frontend → Vercel
```env
NEXTAUTH_URL=https://your-app.vercel.app
NEXTAUTH_SECRET=<random-secret>
GOOGLE_CLIENT_ID=<from-google-cloud-console>
GOOGLE_CLIENT_SECRET=<from-google-cloud-console>
NEXT_PUBLIC_API_URL=https://your-backend.railway.app
NEXT_PUBLIC_WS_URL=wss://your-backend.railway.app/ws
```

### Backend → Railway (Dockerfile)
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y libreoffice-writer && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium && playwright install-deps
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$PORT"]
```

### Database → Neon PostgreSQL (free tier)

---

## 🔨 Build Phases

| Phase | What Gets Built | Test |
|-------|----------------|------|
| **1** | Project setup, DB models, FastAPI skeleton, Auth.js | User can sign in |
| **2** | Profile CRUD, Resume parser, Onboarding wizard | Upload resume → see parsed data |
| **3** | AI Agents (JD analyzer, scorer, tailor, cover letter, guardrails) | Feed JD → get tailored resume |
| **4** | Resume templates + DOCX/PDF generation | Generate professional resume |
| **5** | Job scrapers (RemoteOK, LinkedIn, Indeed, Naukri) + Job Feed UI | See matched jobs flowing in |
| **6** | Login Manager + Sessions UI | Log in once → persistent sessions |
| **7** | Form filler + Auto-apply + Application Queue UI | Review → approve → auto-submit |
| **8** | Analytics, WebSocket, Notifications, Polish, Deploy | Full e2e working + deployed |

---

## 📋 Python Dependencies

```txt
fastapi==0.115.0
uvicorn[standard]==0.30.0
python-multipart==0.0.9
websockets==12.0
sqlalchemy[asyncio]==2.0.31
asyncpg==0.29.0
alembic==1.13.2
langchain==0.2.14
langchain-google-genai==1.0.10
langgraph==0.2.16
playwright==1.45.0
pymupdf==1.24.9
python-docx==1.1.2
docxtpl==0.17.0
httpx==0.27.0
beautifulsoup4==4.12.3
pyjwt==2.9.0
cryptography==43.0.0
apscheduler==3.10.4
pydantic==2.8.2
pydantic-settings==2.4.0
python-dotenv==1.0.1
pytest==8.3.2
pytest-asyncio==0.23.8
```

---

## ✅ Success Criteria

1. ✅ **Multi-user**: Multiple users sign up, each with isolated data
2. ✅ **Onboarding**: Upload resume → auto-parsed → profile populated
3. ✅ **Job Discovery**: Jobs scraped from 5+ platforms periodically
4. ✅ **AI Matching**: Each job scored 0-100 against user profile
5. ✅ **Resume Tailoring**: Per-JD rewriting (bullets, skills, projects)
6. ✅ **Professional Output**: ATS-friendly resumes that look hand-crafted
7. ✅ **Cover Letters**: Unique per application
8. ✅ **Semi-Auto Apply**: Review queue → approve → auto-submit
9. ✅ **Session Persistence**: Log in once, stay logged in
10. ✅ **Extensible**: Add new platforms by creating one file
11. ✅ **Real-time**: WebSocket updates as events happen
12. ✅ **Analytics**: Application funnel, response rates, trends
13. ✅ **Deployable**: Vercel + Railway + Neon
14. ✅ **Premium UI**: Dark glassmorphism, micro-animations, stunning design
