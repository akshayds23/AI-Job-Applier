import asyncio
from sqlalchemy import select
from database.database import AsyncSessionLocal, init_db
from database.models import PlatformConfig, ResumeTemplate

DEFAULT_PLATFORMS = [
    {
        "platform": "remoteok",
        "display_name": "RemoteOK",
        "base_url": "https://remoteok.com",
        "requires_login": False,
        "scraper_class": "scrapers.remoteok_scraper.RemoteOKScraper",
        "handler_class": "automation.platform_handlers.generic_handler.GenericHandler",
        "is_enabled": True
    },
    {
        "platform": "linkedin",
        "display_name": "LinkedIn Jobs",
        "base_url": "https://www.linkedin.com/jobs",
        "requires_login": True,
        "scraper_class": "scrapers.linkedin_scraper.LinkedInScraper",
        "handler_class": "automation.platform_handlers.linkedin_handler.LinkedInHandler",
        "is_enabled": True
    },
    {
        "platform": "indeed",
        "display_name": "Indeed",
        "base_url": "https://www.indeed.com",
        "requires_login": True,
        "scraper_class": "scrapers.indeed_scraper.IndeedScraper",
        "handler_class": "automation.platform_handlers.indeed_handler.IndeedHandler",
        "is_enabled": True
    },
    {
        "platform": "naukri",
        "display_name": "Naukri",
        "base_url": "https://www.naukri.com",
        "requires_login": True,
        "scraper_class": "scrapers.naukri_scraper.NaukriScraper",
        "handler_class": "automation.platform_handlers.naukri_handler.NaukriHandler",
        "is_enabled": True
    },
    {
        "platform": "wellfound",
        "display_name": "Wellfound (AngelList)",
        "base_url": "https://wellfound.com/jobs",
        "requires_login": True,
        "scraper_class": "scrapers.wellfound_scraper.WellfoundScraper",
        "handler_class": "automation.platform_handlers.generic_handler.GenericHandler",
        "is_enabled": True
    }
]

DEFAULT_TEMPLATES = [
    {
        "name": "classic",
        "display_name": "Classic Professional",
        "description": "Traditional single-column layout, highly readable and ATS-friendly.",
        "template_path": "templates/classic.docx",
        "is_default": True
    },
    {
        "name": "modern",
        "display_name": "Modern Tech",
        "description": "Sleek and clean layout with subtle accents for tech roles.",
        "template_path": "templates/modern.docx",
        "is_default": False
    },
    {
        "name": "minimal",
        "display_name": "Ultra Clean ATS",
        "description": "Maximum whitespace, minimalist typography focused purely on content.",
        "template_path": "templates/minimal.docx",
        "is_default": False
    }
]

async def seed_data():
    await init_db()
    async with AsyncSessionLocal() as session:
        # Seed Platform Configs
        for item in DEFAULT_PLATFORMS:
            res = await session.execute(
                select(PlatformConfig).where(PlatformConfig.platform == item["platform"])
            )
            if not res.scalar_one_or_none():
                config = PlatformConfig(**item)
                session.add(config)

        # Seed Resume Templates
        for tmpl in DEFAULT_TEMPLATES:
            res = await session.execute(
                select(ResumeTemplate).where(ResumeTemplate.name == tmpl["name"])
            )
            if not res.scalar_one_or_none():
                template = ResumeTemplate(**tmpl)
                session.add(template)

        await session.commit()
        print("Database system initialized with platforms and templates.")

if __name__ == "__main__":
    asyncio.run(seed_data())
