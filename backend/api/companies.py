"""Company watchlist: track employers' own careers pages (Greenhouse/Lever/Ashby)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database.database import get_db
from database.models import Company, User, utcnow
from services.ats import BoardNotFound, BoardRef, detect_board, fetch_board

router = APIRouter(prefix="/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str = ""
    careers_url: str = ""


def _company_to_dict(company: Company) -> dict:
    return {
        "id": company.id,
        "name": company.name,
        "careers_url": company.careers_url,
        "ats": company.ats,
        "ats_slug": company.ats_slug,
        "is_active": company.is_active,
        "last_checked_at": company.last_checked_at,
        "last_job_count": company.last_job_count,
        "last_error": company.last_error,
        "created_at": company.created_at,
    }


async def _get_owned(db: AsyncSession, user: User, company_id: str) -> Company:
    company = await db.get(Company, company_id)
    if company is None or company.user_id != user.id:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/")
async def list_companies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    companies = (
        await db.execute(
            select(Company).where(Company.user_id == user.id).order_by(desc(Company.created_at))
        )
    ).scalars().all()
    return [_company_to_dict(c) for c in companies]


@router.post("/", status_code=201)
async def add_company(
    data: CompanyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    name = data.name.strip()
    url = data.careers_url.strip()
    if not name and not url:
        raise HTTPException(status_code=400, detail="Enter a company name or careers-page URL")

    try:
        ref = await detect_board(name, url)
        jobs = await fetch_board(ref, name or None)
    except BoardNotFound as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    existing = (
        await db.execute(
            select(Company).where(
                Company.user_id == user.id, Company.ats == ref.ats, Company.ats_slug == ref.slug
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Already watching {existing.name}")

    display_name = name or (jobs[0].company if jobs and jobs[0].company != ref.slug else ref.slug.replace("-", " ").title())
    company = Company(
        user_id=user.id,
        name=display_name,
        careers_url=url or ref.careers_url,
        ats=ref.ats,
        ats_slug=ref.slug,
        last_checked_at=utcnow(),
        last_job_count=len(jobs),
    )
    db.add(company)
    await db.commit()
    await db.refresh(company)
    return _company_to_dict(company)


@router.post("/{company_id}/check")
async def check_company(
    company_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Fetch the board now and preview its open roles (no AI scoring)."""
    company = await _get_owned(db, user, company_id)
    try:
        jobs = await fetch_board(BoardRef(company.ats, company.ats_slug), company.name)
        company.last_error = None
        company.last_job_count = len(jobs)
    except Exception as exc:
        jobs = []
        company.last_error = str(exc)[:300]
    company.last_checked_at = utcnow()
    await db.commit()
    await db.refresh(company)

    jobs.sort(key=lambda job: job.posted_date.isoformat() if job.posted_date else "", reverse=True)
    return {
        "company": _company_to_dict(company),
        "jobs": [
            {
                "title": job.title,
                "location": job.location,
                "url": job.url,
                "posted_date": job.posted_date,
                "is_remote": job.is_remote,
                "tags": job.tags,
            }
            for job in jobs[:200]
        ],
    }


@router.patch("/{company_id}")
async def toggle_company(
    company_id: str,
    is_active: bool,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    company = await _get_owned(db, user, company_id)
    company.is_active = is_active
    await db.commit()
    await db.refresh(company)
    return _company_to_dict(company)


@router.delete("/{company_id}", status_code=204)
async def remove_company(
    company_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    company = await _get_owned(db, user, company_id)
    await db.delete(company)
    await db.commit()
