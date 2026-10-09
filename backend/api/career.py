"""Career profile: import work from GitHub, a portfolio page or a document; review and approve it."""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from database.database import get_db
from database.models import CareerFact, Experience, User
from services import career
from utils.llm_client import NO_KEYS_MESSAGE, get_llm_client
from utils.llm_keys import JobWaitPolicy, current_keyring, reset_job_policy, set_job_policy

router = APIRouter(prefix="/career", tags=["career"])

IMPORT_TIMEOUT_SECONDS = 240
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class GithubIn(BaseModel):
    username: str


class UrlIn(BaseModel):
    url: str


class FactIn(BaseModel):
    kind: str
    title: str = ""
    text: str = ""
    skills: List[str] = []
    url: Optional[str] = None
    role_company: Optional[str] = None


class FactPatch(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    skills: Optional[List[str]] = None
    role_company: Optional[str] = None


def _view(fact: CareerFact) -> dict:
    return {
        "id": fact.id, "kind": fact.kind, "title": fact.title, "text": fact.text, "skills": fact.skills or [],
        "url": fact.url, "role_company": fact.role_company, "source": fact.source, "source_ref": fact.source_ref,
        "status": fact.status, "created_at": fact.created_at,
    }


async def _run_import(coro):
    """Imports call the AI a few times: short rate-limit waits only, and a hard time limit."""
    keyring = current_keyring()
    if keyring is None or not keyring.keys:
        raise HTTPException(status_code=400, detail=NO_KEYS_MESSAGE)
    token = set_job_policy(JobWaitPolicy(allow_fallback=False, inline_wait_seconds=20))
    try:
        return await asyncio.wait_for(coro, IMPORT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="The import took too long - try again, or import less at once")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # DeferJob / LLMUnavailable: rate-limited or no AI
        raise HTTPException(status_code=503, detail=f"AI is busy right now ({str(exc)[:120]}) - try again in a minute")
    finally:
        reset_job_policy(token)


@router.get("/facts")
async def list_facts(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    facts = (await db.execute(
        select(CareerFact).where(CareerFact.user_id == user.id, CareerFact.status != "rejected")
        .order_by(CareerFact.created_at.desc())
    )).scalars().all()
    roles = [
        {"company": e.company, "title": e.title}
        for e in (await db.execute(
            select(Experience).where(Experience.user_id == user.id).order_by(Experience.display_order)
        )).scalars()
    ]
    return {"facts": [_view(f) for f in facts], "roles": roles}


@router.post("/import/github")
async def import_github(data: GithubIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _run_import(career.import_github(db, user.id, data.username, get_llm_client()))


@router.post("/import/url")
async def import_url(data: UrlIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _run_import(career.import_url(db, user.id, data.url, get_llm_client()))


@router.post("/import/document")
async def import_document(
    file: UploadFile = File(...),
    role_company: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File is larger than 5 MB")
    try:
        text = career.document_text(file.filename or "document", raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _run_import(career.import_document(
        db, user.id, (file.filename or "document")[:200], text, get_llm_client(), role_company.strip() or None,
    ))


@router.post("/facts", status_code=201)
async def add_fact(data: FactIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Typed in by the user: approved straight away."""
    if data.kind not in {"project", "highlight", "skill", "achievement"}:
        raise HTTPException(status_code=400, detail="kind must be project, highlight, skill or achievement")
    title = (data.title or data.text).strip()[:255]
    if not title:
        raise HTTPException(status_code=400, detail="Write something first")
    role = (data.role_company or "").strip()[:255] or None
    if data.kind == "highlight" and not role:
        role = await career.current_role_company(db, user.id)  # work highlights belong to a job
    fact = CareerFact(
        user_id=user.id, kind=data.kind, title=title, text=(data.text or "").strip()[:2000] or None,
        skills=[s.strip()[:60] for s in data.skills if s.strip()][:15], url=(data.url or "").strip()[:500] or None,
        role_company=role, source="manual", status="approved",
    )
    db.add(fact)
    await db.commit()
    await db.refresh(fact)
    return _view(fact)


@router.patch("/facts/{fact_id}")
async def update_fact(fact_id: str, data: FactPatch, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    fact = await db.get(CareerFact, fact_id)
    if fact is None or fact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if data.status is not None:
        if data.status not in {"pending", "approved", "rejected"}:
            raise HTTPException(status_code=400, detail="Invalid status")
        fact.status = data.status
    if data.title is not None:
        fact.title = data.title.strip()[:255] or fact.title
    if data.text is not None:
        fact.text = data.text.strip()[:2000]
        if fact.kind == "highlight":
            fact.title = fact.text[:255]
    if data.skills is not None:
        fact.skills = [s.strip()[:60] for s in data.skills if s.strip()][:15]
    if data.role_company is not None:
        fact.role_company = data.role_company.strip()[:255] or None
    await db.commit()
    return _view(fact)


@router.post("/facts/approve-all")
async def approve_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    facts = (await db.execute(
        select(CareerFact).where(CareerFact.user_id == user.id, CareerFact.status == "pending")
    )).scalars().all()
    for fact in facts:
        fact.status = "approved"
    await db.commit()
    return {"approved": len(facts)}


@router.delete("/facts/{fact_id}", status_code=204)
async def delete_fact(fact_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    fact = await db.get(CareerFact, fact_id)
    if fact is None or fact.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(fact)
    await db.commit()
