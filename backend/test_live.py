import asyncio
import json
from scrapers.remoteok_scraper import RemoteOKScraper
from agents.orchestrator import JobApplicationOrchestrator
from services.resume_generator import ResumeGenerator

async def run_live_test():
    print("=== Starting Live Job Discovery & Resume Tailoring Test ===")

    # Step 1: Discover real live jobs from RemoteOK
    scraper = RemoteOKScraper()
    jobs = await scraper.search_jobs(query="AI Engineer")
    print(f"[SUCCESS] Found {len(jobs)} live jobs!")

    if not jobs:
        print("[ERROR] No jobs found.")
        return

    top_job = jobs[0]
    print(f"\n--- Target Job: {top_job.title} at {top_job.company} ---")
    print(f"URL: {top_job.url}")

    # Step 2: User profile test data
    user_profile = {
        "id": "dev-user-1",
        "email": "engineer@example.com",
        "phone": "+1 555-0199",
        "location": "Remote",
        "professional_summary": "Experienced Full Stack AI Engineer skilled in building intelligent agent systems, FastAPI microservices, and React/Next.js interfaces.",
        "target_roles": ["AI Engineer", "Full Stack Engineer"],
        "linkedin_url": "https://linkedin.com/in/example",
        "github_url": "https://github.com/example"
    }

    skills = [
        {"name": "Python", "category": "language"},
        {"name": "FastAPI", "category": "framework"},
        {"name": "React", "category": "framework"},
        {"name": "Next.js", "category": "framework"},
        {"name": "PyTorch", "category": "ai"},
        {"name": "LangChain", "category": "ai"},
        {"name": "PostgreSQL", "category": "database"}
    ]

    experiences = [
        {
            "id": "exp1",
            "company": "TechCorp AI",
            "title": "Senior AI Developer",
            "start_date": "2022",
            "end_date": "Present",
            "bullets": [
                "Built production multi-agent LLM systems handling 10k+ requests daily.",
                "Designed async FastAPI endpoints and integrated PostgreSQL vector storage.",
                "Improved AI response latency by 45% using streaming and prompt optimization."
            ]
        }
    ]

    projects = [
        {
            "id": "proj1",
            "name": "AutoApplier AI",
            "description": "Autonomous job search & 1-page resume tailoring agent platform."
        }
    ]

    # Step 3: Run AI Orchestrator Pipeline
    orchestrator = JobApplicationOrchestrator()
    result = await orchestrator.process_job_application(
        user_name="John Developer",
        user_profile=user_profile,
        skills=skills,
        experiences=experiences,
        projects=projects,
        job_listing={
            "id": "live-job-1",
            "title": top_job.title,
            "company": top_job.company,
            "description_text": top_job.description
        }
    )

    print("\n--- AI Agent Pipeline Execution Finished ---")
    print(f"  * Match Score: {result['match_score']}%")
    print(f"  * Matched Skills: {result['matched_skills']}")
    print(f"  * Gap Skills: {result['gap_skills']}")

    # Step 4: Generate 1-Page Resume DOCX & PDF
    tailored = result.get("tailored_data") or {}
    generator = ResumeGenerator()
    docx_path, pdf_path = await generator.generate(
        output_dir="./data/test_output",
        job_id="live_test_1",
        user_profile=user_profile,
        user_name="John Developer",
        tailored_summary=tailored.get("tailored_summary", user_profile["professional_summary"]),
        tailored_bullets=tailored.get("tailored_bullets", {}),
        skills_order=tailored.get("skills_order", [s["name"] for s in skills]),
        experiences=experiences,
        projects=projects,
        education=[{"degree": "B.S.", "field": "Computer Science", "institution": "Tech University", "dates": "2018-2022"}]
    )

    print(f"\n[SUCCESS] 1-Page Resume Generated Successfully at:")
    print(f"  * Path: {docx_path}")
    print("\n=== Live Integration Test Complete ===")

if __name__ == "__main__":
    asyncio.run(run_live_test())
