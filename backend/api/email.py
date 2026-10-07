"""Mailbox connection, recruiter outreach, inbox replies and follow-ups."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from core.crypto import encrypt
from database.database import get_db
from database.models import Application, EmailAccount, InboxMessage, JobListing, OutreachEmail, User
from services.contact_finder import find_contacts
from services.documents import build_application_documents
from services.mailer import (
    DAILY_SEND_LIMIT,
    MailError,
    draft_outreach,
    follow_ups_due,
    get_account,
    send_outreach,
    sent_today,
    sync_inbox,
    test_connection,
)

router = APIRouter(prefix="/email", tags=["email"])


class AccountIn(BaseModel):
    email_address: str
    app_password: str
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993


class DraftIn(BaseModel):
    kind: str = "initial"


class SendIn(BaseModel):
    to_address: str
    subject: str
    body: str
    kind: str = "initial"
    attach_resume: bool = True
    attach_cover_letter: bool = False


def _account_dict(account: Optional[EmailAccount]) -> dict:
    if account is None:
        return {"connected": False}
    return {
        "connected": True,
        "email_address": account.email_address,
        "smtp_host": account.smtp_host,
        "imap_host": account.imap_host,
        "last_sync_at": account.last_sync_at,
        "last_error": account.last_error,
    }


async def _owned_application(db: AsyncSession, user: User, app_id: str) -> tuple[Application, JobListing]:
    row = (
        await db.execute(
            select(Application, JobListing)
            .join(JobListing, Application.job_id == JobListing.id)
            .where(Application.id == app_id, Application.user_id == user.id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row[0], row[1]


# -- account ---------------------------------------------------------------

@router.get("/account")
async def read_account(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    account = await get_account(db, user.id)
    return {**_account_dict(account), "sent_today": await sent_today(db, user.id), "daily_limit": DAILY_SEND_LIMIT}


@router.put("/account")
async def connect_account(data: AccountIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    password = data.app_password.replace(" ", "").strip()
    account = await get_account(db, user.id) or EmailAccount(user_id=user.id, password_encrypted="")
    account.email_address = data.email_address.strip().lower()
    account.smtp_host, account.smtp_port = data.smtp_host.strip(), data.smtp_port
    account.imap_host, account.imap_port = data.imap_host.strip(), data.imap_port
    try:
        await test_connection(account, password)
    except MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    account.password_encrypted = encrypt(password)
    account.last_error = None
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return _account_dict(account)


@router.delete("/account", status_code=204)
async def disconnect_account(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    account = await get_account(db, user.id)
    if account is not None:
        await db.delete(account)
        await db.commit()


# -- inbox -----------------------------------------------------------------

@router.post("/sync")
async def sync_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await sync_inbox(db, user.id)
    except MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/inbox")
async def inbox(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(InboxMessage, Application, JobListing)
            .outerjoin(Application, InboxMessage.application_id == Application.id)
            .outerjoin(JobListing, Application.job_id == JobListing.id)
            .where(InboxMessage.user_id == user.id)
            .order_by(desc(InboxMessage.received_at))
            .limit(200)
        )
    ).all()
    return [
        {
            "id": m.id,
            "from_name": m.from_name,
            "from_address": m.from_address,
            "subject": m.subject,
            "snippet": m.snippet,
            "received_at": m.received_at,
            "category": m.category,
            "application": {"id": app.id, "status": app.status, "title": job.title, "company": job.company}
            if app is not None and job is not None else None,
        }
        for m, app, job in rows
    ]


@router.get("/follow-ups")
async def follow_ups(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await follow_ups_due(db, user.id)


# -- per application ---------------------------------------------------------

@router.get("/applications/{app_id}/contacts")
async def contacts(app_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    _app, job = await _owned_application(db, user, app_id)
    return await find_contacts(job)


@router.post("/applications/{app_id}/draft")
async def draft(app_id: str, data: DraftIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    app, _job = await _owned_application(db, user, app_id)
    return await draft_outreach(db, app, "follow_up" if data.kind == "follow_up" else "initial")


@router.post("/applications/{app_id}/send")
async def send(app_id: str, data: SendIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    app, _job = await _owned_application(db, user, app_id)
    attachments: list[str] = []
    if data.attach_resume or data.attach_cover_letter:
        paths = await build_application_documents(db, app)
        if data.attach_resume:
            attachments.append(paths["pdf"])
        if data.attach_cover_letter and paths["cover_letter"]:
            attachments.append(paths["cover_letter"])
    try:
        record = await send_outreach(
            db, app, to_address=data.to_address.strip(), subject=data.subject.strip(), body=data.body,
            kind="follow_up" if data.kind == "follow_up" else "initial", attachments=attachments,
        )
    except MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": record.id, "status": record.status, "sent_at": record.sent_at, "to_address": record.to_address}


@router.get("/applications/{app_id}/history")
async def history(app_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    app, _job = await _owned_application(db, user, app_id)
    sent = (
        await db.execute(select(OutreachEmail).where(OutreachEmail.application_id == app.id).order_by(OutreachEmail.created_at))
    ).scalars().all()
    received = (
        await db.execute(select(InboxMessage).where(InboxMessage.application_id == app.id).order_by(InboxMessage.received_at))
    ).scalars().all()
    return {
        "sent": [{"kind": e.kind, "to": e.to_address, "subject": e.subject, "status": e.status,
                  "error": e.error, "sent_at": e.sent_at} for e in sent],
        "received": [{"from": m.from_address, "subject": m.subject, "category": m.category,
                      "snippet": m.snippet, "received_at": m.received_at} for m in received],
    }
