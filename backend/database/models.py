import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime, Date, 
    ForeignKey, Table, JSON, ARRAY, UniqueConstraint
)
from sqlalchemy.orm import relationship
from database.database import Base

def generate_uuid():
    return str(uuid.uuid4())


def utcnow():
    """Naive UTC timestamp, matching the existing DateTime columns."""
    return datetime.utcnow()

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=True)
    email = Column(String(255), unique=True, nullable=False)
    email_verified = Column(DateTime, nullable=True)
    image = Column(Text, nullable=True)
    password_hash = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    skills = relationship("Skill", back_populates="user", cascade="all, delete-orphan")
    experiences = relationship("Experience", back_populates="user", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")
    education = relationship("Education", back_populates="user", cascade="all, delete-orphan")
    resumes = relationship("MasterResume", back_populates="user", cascade="all, delete-orphan")
    matches = relationship("UserJobMatch", back_populates="user", cascade="all, delete-orphan")
    applications = relationship("Application", back_populates="user", cascade="all, delete-orphan")
    platform_sessions = relationship("PlatformSession", back_populates="user", cascade="all, delete-orphan")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    phone = Column(String(20), nullable=True)
    location = Column(String(255), nullable=True)
    linkedin_url = Column(String(500), nullable=True)
    github_url = Column(String(500), nullable=True)
    portfolio_url = Column(String(500), nullable=True)
    professional_summary = Column(Text, nullable=True)
    headline = Column(String(200), nullable=True)         # "AI & Robotics | Program Leadership"
    contact_email = Column(String(255), nullable=True)    # email shown on resumes/forms (may differ from login)
    achievements = Column(JSON, nullable=True)            # verbatim highlights from the resume
    certifications = Column(JSON, nullable=True)
    target_roles = Column(JSON, default=list)        # ['AI Engineer', 'ML Engineer']
    target_locations = Column(JSON, default=list)    # ['Remote', 'Bangalore']
    min_salary = Column(Integer, nullable=True)
    max_salary = Column(Integer, nullable=True)
    currency = Column(String(10), default="USD")
    experience_years = Column(Integer, nullable=True)
    preferred_company_sizes = Column(JSON, default=list)
    preferred_template = Column(String(50), default="classic")
    pdf_engine = Column(String(20), default="latex")      # 'latex' (Tectonic) | 'builtin' (ATS-safe)
    approval_mode = Column(String(20), default="semi_auto")  # 'semi_auto' | 'full_auto'
    scrape_frequency_hours = Column(Integer, default=24)  # each run spends the user's API tokens
    auto_apply_min_score = Column(Float, default=75.0)
    daily_application_limit = Column(Integer, default=10)
    enabled_platforms = Column(JSON, nullable=True)
    excluded_companies = Column(JSON, nullable=True)
    keywords_exclude = Column(JSON, nullable=True)
    llm_provider = Column(String(20), nullable=True)      # preferred provider for the user's key pool
    llm_max_wait_minutes = Column(Integer, default=15)
    remote_preference = Column(String(10), default="any")  # any | remote | onsite (on-site or hybrid)
    max_job_age_days = Column(Integer, default=30)          # ignore postings older than this
    title_suggestions = Column(JSON, nullable=True)         # cached {"suggestions": [...], "generated_at": ...}    # wait this long for rate limits before falling back
    is_onboarded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="profile")


class Skill(Base):
    __tablename__ = "skills"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=True)     # 'language', 'framework', 'tool'
    proficiency = Column(String(20), default="proficient")
    years_experience = Column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint('user_id', 'name', name='_user_skill_uc'),)
    user = relationship("User", back_populates="skills")


class Experience(Base):
    __tablename__ = "experiences"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    company = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    location = Column(String(255), nullable=True)
    start_date = Column(String(50), nullable=True)
    end_date = Column(String(50), nullable=True)
    is_current = Column(Boolean, default=False)
    description = Column(Text, nullable=True)
    bullets = Column(JSON, default=list)              # Array of bullet points
    technologies = Column(JSON, default=list)
    display_order = Column(Integer, default=0)

    user = relationship("User", back_populates="experiences")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    detailed_description = Column(Text, nullable=True)
    technologies = Column(JSON, default=list)
    url = Column(String(500), nullable=True)
    github_url = Column(String(500), nullable=True)
    impact_metrics = Column(Text, nullable=True)
    start_date = Column(String(50), nullable=True)
    end_date = Column(String(50), nullable=True)
    display_order = Column(Integer, default=0)

    user = relationship("User", back_populates="projects")


class Education(Base):
    __tablename__ = "education"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    institution = Column(String(255), nullable=False)
    degree = Column(String(100), nullable=False)
    field = Column(String(255), nullable=True)
    start_date = Column(String(50), nullable=True)
    end_date = Column(String(50), nullable=True)
    gpa = Column(String(10), nullable=True)
    achievements = Column(JSON, default=list)
    display_order = Column(Integer, default=0)

    user = relationship("User", back_populates="education")


class MasterResume(Base):
    __tablename__ = "master_resumes"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), nullable=True)
    file_path = Column(Text, nullable=True)
    file_type = Column(String(10), nullable=True)
    parsed_text = Column(Text, nullable=True)
    structured_data = Column(JSON, default=dict)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="resumes")


class JobListing(Base):
    __tablename__ = "job_listings"

    id = Column(String, primary_key=True, default=generate_uuid)
    platform = Column(String(50), nullable=False)
    external_id = Column(String(255), nullable=True)
    title = Column(String(500), nullable=False)
    company = Column(String(255), nullable=True)
    company_logo_url = Column(Text, nullable=True)
    location = Column(String(255), nullable=True)
    is_remote = Column(Boolean, default=False)
    job_type = Column(String(50), nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    salary_currency = Column(String(10), nullable=True)
    description_text = Column(Text, nullable=True)
    description_html = Column(Text, nullable=True)
    url = Column(String(1000), nullable=False)
    apply_url = Column(String(1000), nullable=True)
    posted_date = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    tags = Column(JSON, default=list)
    seniority_level = Column(String(50), nullable=True)
    raw_payload = Column(JSON, nullable=True)
    trust_score = Column(Float, nullable=True)        # 0-100, see services/job_verifier.py
    trust_label = Column(String(20), nullable=True)   # 'verified' | 'likely_real' | 'unconfirmed' | 'stale' | 'suspicious'
    trust_flags = Column(JSON, nullable=True)         # human-readable reasons behind the score
    verified_url = Column(String(1000), nullable=True)  # same role on the company's own careers page
    dedup_hash = Column(String(64), unique=True, nullable=False)

    matches = relationship("UserJobMatch", back_populates="job", cascade="all, delete-orphan")
    applications = relationship("Application", back_populates="job", cascade="all, delete-orphan")


class UserJobMatch(Base):
    __tablename__ = "user_job_matches"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(String, ForeignKey("job_listings.id", ondelete="CASCADE"), nullable=False)
    match_score = Column(Float, nullable=False)
    matched_skills = Column(JSON, default=list)
    gap_skills = Column(JSON, default=list)
    relevant_projects = Column(JSON, default=list)
    jd_analysis = Column(JSON, default=dict)
    reasoning = Column(Text, nullable=True)
    scoring_method = Column(String(20), nullable=True)
    status = Column(String(20), default="new")       # 'new', 'reviewed', 'queued', 'skipped'
    matched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('user_id', 'job_id', name='_user_job_match_uc'),)
    user = relationship("User", back_populates="matches")
    job = relationship("JobListing", back_populates="matches")


class Application(Base):
    __tablename__ = "applications"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(String, ForeignKey("job_listings.id", ondelete="CASCADE"), nullable=False)
    match_id = Column(String, ForeignKey("user_job_matches.id", ondelete="SET NULL"), nullable=True)
    tailored_summary = Column(Text, nullable=True)
    tailored_headline = Column(String(200), nullable=True)
    tailored_skill_groups = Column(JSON, nullable=True)   # {"Program Leadership": ["...", ...]}
    tailored_bullets = Column(JSON, default=dict)
    tailored_skills_order = Column(JSON, default=list)
    selected_project_ids = Column(JSON, default=list)
    achievements = Column(JSON, default=list)
    cover_letter = Column(Text, nullable=True)
    resume_docx_path = Column(Text, nullable=True)
    resume_pdf_path = Column(Text, nullable=True)
    cover_letter_path = Column(Text, nullable=True)
    prepare_state = Column(JSON, nullable=True)           # {"state": preparing|done|failed, "message": ...}
    submission_method = Column(String(30), nullable=True)
    external_reference = Column(String(255), nullable=True)
    status = Column(String(30), default="pending")    # 'pending', 'approved', 'submitting', 'submitted', 'failed', 'interview', 'rejected'
    submitted_at = Column(DateTime, nullable=True)
    last_status_check = Column(DateTime, nullable=True)
    screenshot_paths = Column(JSON, default=list)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint('user_id', 'job_id', name='_user_app_uc'),)
    user = relationship("User", back_populates="applications")
    job = relationship("JobListing", back_populates="applications")
    logs = relationship("ApplicationLog", back_populates="application", cascade="all, delete-orphan")


class ApplicationLog(Base):
    __tablename__ = "application_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    application_id = Column(String, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(100), nullable=False)
    details = Column(Text, nullable=True)
    screenshot_path = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", back_populates="logs")


class PlatformSession(Base):
    __tablename__ = "platform_sessions"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)
    session_dir = Column(Text, nullable=True)
    storage_state_path = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    last_verified = Column(DateTime, nullable=True)
    last_used = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('user_id', 'platform', name='_user_platform_uc'),)
    user = relationship("User", back_populates="platform_sessions")


class PlatformConfig(Base):
    __tablename__ = "platform_configs"

    id = Column(String, primary_key=True, default=generate_uuid)
    platform = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100), nullable=True)
    icon_url = Column(Text, nullable=True)
    base_url = Column(String(500), nullable=True)
    requires_login = Column(Boolean, default=False)
    scraper_class = Column(String(255), nullable=True)
    handler_class = Column(String(255), nullable=True)
    default_delay_min = Column(Float, default=2.0)
    default_delay_max = Column(Float, default=8.0)
    max_scrapes_per_run = Column(Integer, default=50)
    is_enabled = Column(Boolean, default=True)
    config_json = Column(JSON, default=dict)


class ResumeTemplate(Base):
    __tablename__ = "resume_templates"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False)
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    template_path = Column(Text, nullable=False)
    preview_image_path = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ScrapeRun(Base):
    """One discovery run (manual or scheduled) and what it produced."""
    __tablename__ = "scrape_runs"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger = Column(String(20), default="manual")      # 'manual' | 'scheduled'
    platforms = Column(JSON, default=list)
    queries = Column(JSON, default=list)
    status = Column(String(20), default="running")      # 'running' | 'completed' | 'failed'
    jobs_found = Column(Integer, default=0)
    jobs_new = Column(Integer, default=0)
    matches_created = Column(Integer, default=0)
    applications_drafted = Column(Integer, default=0)
    jobs_verified = Column(Integer, default=0)
    jobs_suspicious = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class Company(Base):
    """A company whose own careers page (ATS job board) the user watches."""
    __tablename__ = "companies"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    careers_url = Column(String(1000), nullable=True)
    ats = Column(String(30), nullable=True)              # 'greenhouse' | 'lever' | 'ashby' | None
    ats_slug = Column(String(255), nullable=True)        # board token used in the ATS API
    is_active = Column(Boolean, default=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_job_count = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('user_id', 'ats', 'ats_slug', name='_user_company_uc'),)


class AtsLookup(Base):
    """Cache of 'which careers board does this company use' (shared by all users)."""
    __tablename__ = "ats_lookups"

    id = Column(String, primary_key=True, default=generate_uuid)
    company_key = Column(String(255), unique=True, nullable=False)  # normalised company name
    ats = Column(String(30), nullable=True)
    ats_slug = Column(String(255), nullable=True)
    found = Column(Boolean, default=False)
    checked_at = Column(DateTime, default=datetime.utcnow)


class EmailAccount(Base):
    """The user's own mailbox, used to email recruiters and read their replies."""
    __tablename__ = "email_accounts"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    email_address = Column(String(255), nullable=False)
    smtp_host = Column(String(255), default="smtp.gmail.com")
    smtp_port = Column(Integer, default=465)
    imap_host = Column(String(255), default="imap.gmail.com")
    imap_port = Column(Integer, default=993)
    password_encrypted = Column(Text, nullable=False)  # Fernet, keyed from SECRET_KEY
    last_sync_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OutreachEmail(Base):
    """An email to a recruiter/HR about one application (initial note or follow-up)."""
    __tablename__ = "outreach_emails"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id = Column(String, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(20), default="initial")        # 'initial' | 'follow_up'
    to_address = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(20), default="sent")         # 'sent' | 'failed'
    message_id = Column(String(255), nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class InboxMessage(Base):
    """A job-related email received by the user, classified and linked to an application."""
    __tablename__ = "inbox_messages"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id = Column(String, ForeignKey("applications.id", ondelete="SET NULL"), nullable=True)
    message_id = Column(String(500), nullable=False)
    from_address = Column(String(255), nullable=True)
    from_name = Column(String(255), nullable=True)
    subject = Column(String(500), nullable=True)
    snippet = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=True)
    category = Column(String(20), nullable=True)        # interview | assessment | offer | rejection | recruiter | other
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('user_id', 'message_id', name='_user_inbox_msg_uc'),)


class AIKey(Base):
    """An LLM API key the user brought (BYOK). Stored encrypted; never returned in full."""
    __tablename__ = "ai_keys"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(20), nullable=False)          # groq | gemini | openai | anthropic
    label = Column(String(100), nullable=True)
    model = Column(String(120), nullable=True)             # None = provider default
    key_encrypted = Column(Text, nullable=False)
    key_last4 = Column(String(8), nullable=True)
    is_enabled = Column(Boolean, default=True)
    status = Column(String(20), default="active")          # active | invalid
    last_error = Column(Text, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    # Rate-limit state lives in the database so every server instance agrees on it.
    cooldown_until = Column(DateTime, nullable=True)       # UTC
    cooldown_reason = Column(String(60), nullable=True)
    calls = Column(Integer, default=0)
    tokens = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class BackgroundJob(Base):
    """Durable unit of background work (discovery run, document preparation, inbox sync).

    Jobs are processed in bounded steps by whichever process calls the job
    runner - a local loop, a request that just enqueued work, or a cron call on
    serverless hosts - so no work depends on a long-lived server.
    """
    __tablename__ = "background_jobs"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(30), nullable=False)               # discovery | prepare | inbox_sync
    status = Column(String(20), default="queued", index=True)  # queued | running | waiting | done | failed
    payload = Column(JSON, default=dict)                   # step state, carried between runs
    message = Column(Text, nullable=True)                  # human-readable progress
    run_after = Column(DateTime, default=datetime.utcnow, index=True)
    locked_until = Column(DateTime, nullable=True)         # lease held by the process running a step
    attempts = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CareerFact(Base):
    """Something the user has done that their resume may not mention, from GitHub,
    their portfolio, an uploaded document or typed in. Used for scoring and tailoring
    only once the user approves it (company documents describe the whole team's work)."""
    __tablename__ = "career_facts"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)            # 'project' | 'highlight' | 'skill'
    title = Column(String(255), nullable=False)
    text = Column(Text, nullable=True)                   # project description or the highlight statement
    skills = Column(JSON, default=list)
    url = Column(String(500), nullable=True)
    role_company = Column(String(255), nullable=True)   # highlights: the job it belongs to
    source = Column(String(20), nullable=False)          # 'github' | 'portfolio' | 'document' | 'manual'
    source_ref = Column(String(500), nullable=True)      # repo URL, page URL or file name
    status = Column(String(20), default="pending")       # 'pending' | 'approved' | 'rejected'
    created_at = Column(DateTime, default=datetime.utcnow)
