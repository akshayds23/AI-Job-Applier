"""Email the user's own mailbox: recruiter outreach, follow-ups, and reply tracking.

Uses plain SMTP/IMAP with an app password (Gmail: Google Account -> Security ->
2-Step Verification -> App passwords), so no OAuth app has to be registered.
Everything is sent from, and read from, the user's own mailbox.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt
from core.events import event_bus
from core.logging_config import get_logger
from database.models import (
    Application,
    ApplicationLog,
    EmailAccount,
    InboxMessage,
    JobListing,
    OutreachEmail,
    User,
    UserJobMatch,
    utcnow,
)
from scrapers.base_scraper import html_to_text, normalise_key
from utils.llm_client import get_llm_client

logger = get_logger("mailer")

DAILY_SEND_LIMIT = 15          # keeps a personal mailbox well clear of spam heuristics
FOLLOW_UP_AFTER_DAYS = 7
INBOX_LOOKBACK_DAYS = 45
_MAX_MESSAGES_PER_SYNC = 300

_TIMEOUT = 30


class MailError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def _smtp_login(account: EmailAccount, password: str) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if account.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=_TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=_TIMEOUT)
        server.starttls(context=context)
    server.login(account.email_address, password)
    return server


def _imap_login(account: EmailAccount, password: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(account.imap_host, account.imap_port, timeout=_TIMEOUT)
    client.login(account.email_address, password)
    return client


def _friendly(exc: Exception) -> str:
    text = str(exc)
    if "Application-specific password required" in text or "BadCredentials" in text or "AUTHENTICATIONFAILED" in text \
            or "Username and Password not accepted" in text or "Invalid credentials" in text             or isinstance(exc, smtplib.SMTPServerDisconnected):
        return ("Login rejected. For Gmail, use a 16-character App Password (Google Account -> Security -> "
                "2-Step Verification -> App passwords), not your normal password.")
    return text[:300]


async def test_connection(account: EmailAccount, password: str) -> None:
    """Log in to both SMTP and IMAP; raise MailError with a helpful message on failure."""
    def check() -> None:
        # IMAP first: it reports bad credentials clearly, whereas Gmail's SMTP often just hangs up.
        client = _imap_login(account, password)
        client.logout()
        _smtp_login(account, password).quit()

    try:
        await asyncio.to_thread(check)
    except Exception as exc:
        raise MailError(_friendly(exc)) from exc


async def get_account(db: AsyncSession, user_id: str) -> EmailAccount | None:
    return (await db.execute(select(EmailAccount).where(EmailAccount.user_id == user_id))).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


async def draft_outreach(db: AsyncSession, application: Application, kind: str = "initial") -> dict[str, str]:
    user = await db.get(User, application.user_id)
    job = await db.get(JobListing, application.job_id)
    match = await db.get(UserJobMatch, application.match_id) if application.match_id else None
    skills = ", ".join((match.matched_skills or [])[:5]) if match else ""
    name = user.name or "Applicant"

    if kind == "follow_up":
        prompt = f"""Write a brief, polite follow-up email (60-90 words) from {name} about their application
for the {job.title} role at {job.company}, sent about a week ago with no reply yet.
Restate interest in one sentence, mention one relevant strength ({skills or 'their relevant experience'}),
and ask if there is any update on next steps. No flattery, no pressure, no invented facts.
Return JSON: {{"subject": "...", "body": "..."}} where body is plain text with a sign-off from {name}."""
        fallback = {
            "subject": f"Following up: {job.title} application",
            "body": (f"Hi,\n\nI applied for the {job.title} role at {job.company} last week and wanted to follow up. "
                     f"I'm still very interested and believe my experience{(' with ' + skills) if skills else ''} "
                     f"would be a good fit.\n\nIs there any update on next steps? Happy to share anything else "
                     f"that would help.\n\nBest regards,\n{name}"),
        }
    else:
        prompt = f"""Write a concise email (110-160 words) from {name} to the hiring team at {job.company}
about the {job.title} role. The tailored resume is attached.
Candidate summary: {(application.tailored_summary or '')[:700]}
Skills that match the job: {skills or 'n/a'}
Rules: specific to this role and company, one concrete strength, a clear ask (a short call or consideration
for the role), plain text, no flattery, no invented facts, numbers or employers.
Return JSON: {{"subject": "...", "body": "..."}} where body ends with a sign-off from {name}."""
        fallback = {
            "subject": f"Application: {job.title} - {name}",
            "body": (f"Hi {job.company} hiring team,\n\nI'm writing about the {job.title} role. "
                     f"{(application.tailored_summary or '').strip()[:400]}\n\n"
                     f"I've attached my resume tailored to this role and would welcome the chance to discuss "
                     f"how I could contribute.\n\nBest regards,\n{name}"),
        }

    try:
        draft = await get_llm_client(interactive=True).generate_json(prompt, temperature=0.4, max_tokens=900)
        subject, body = str(draft.get("subject") or "").strip(), str(draft.get("body") or "").strip()
        if subject and len(body) > 40:
            return {"subject": subject[:200], "body": body}
    except Exception as exc:
        logger.info("Outreach draft fell back to template: %s", exc)
    return fallback


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


async def sent_today(db: AsyncSession, user_id: str) -> int:
    since = utcnow() - timedelta(hours=24)
    return (
        await db.execute(
            select(func.count(OutreachEmail.id)).where(
                OutreachEmail.user_id == user_id, OutreachEmail.status == "sent", OutreachEmail.sent_at >= since
            )
        )
    ).scalar() or 0


async def send_outreach(
    db: AsyncSession,
    application: Application,
    *,
    to_address: str,
    subject: str,
    body: str,
    kind: str = "initial",
    attachments: list[str] | None = None,
) -> OutreachEmail:
    account = await get_account(db, application.user_id)
    if account is None:
        raise MailError("Connect your email account in Settings first.")
    if await sent_today(db, application.user_id) >= DAILY_SEND_LIMIT:
        raise MailError(f"Daily limit of {DAILY_SEND_LIMIT} emails reached - this protects your mailbox from spam flags.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", to_address or ""):
        raise MailError("Enter a valid recipient email address.")

    user = await db.get(User, application.user_id)
    thread_parent = None
    if kind == "follow_up":
        thread_parent = (
            await db.execute(
                select(OutreachEmail)
                .where(OutreachEmail.application_id == application.id, OutreachEmail.status == "sent",
                       OutreachEmail.to_address == to_address)
                .order_by(OutreachEmail.sent_at)
            )
        ).scalars().first()

    message = EmailMessage()
    message["From"] = f"{user.name} <{account.email_address}>" if user.name else account.email_address
    message["To"] = to_address
    message["Subject"] = subject if not thread_parent else (
        subject if subject.lower().startswith("re:") else f"Re: {thread_parent.subject}"
    )
    message["Message-ID"] = make_msgid(domain=account.email_address.split("@")[-1])
    if thread_parent and thread_parent.message_id:
        message["In-Reply-To"] = thread_parent.message_id
        message["References"] = thread_parent.message_id
    message.set_content(body)

    for path in attachments or []:
        if path and Path(path).exists():
            data = Path(path).read_bytes()
            subtype = "pdf" if path.lower().endswith(".pdf") else "octet-stream"
            message.add_attachment(data, maintype="application", subtype=subtype, filename=Path(path).name)

    record = OutreachEmail(
        user_id=application.user_id, application_id=application.id, kind=kind,
        to_address=to_address, subject=str(message["Subject"]), body=body, message_id=str(message["Message-ID"]),
    )
    password = decrypt(account.password_encrypted)

    def deliver() -> None:
        server = _smtp_login(account, password)
        try:
            server.send_message(message)
        finally:
            server.quit()

    try:
        await asyncio.to_thread(deliver)
        record.status, record.sent_at = "sent", utcnow()
        db.add(ApplicationLog(application_id=application.id, action=f"email_{kind}", details=f"Emailed {to_address}: {record.subject}"))
    except Exception as exc:
        record.status, record.error = "failed", _friendly(exc)
    db.add(record)
    await db.commit()
    if record.status != "sent":
        raise MailError(record.error or "Sending failed")
    return record


# ---------------------------------------------------------------------------
# Inbox sync + classification
# ---------------------------------------------------------------------------

_CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("offer", re.compile(r"\b(offer letter|pleased to (extend|offer)|job offer|offer of employment)\b", re.I)),
    ("rejection", re.compile(
        r"\b(unfortunately|regret to inform|not (be )?(moving|move) forward|decided (not )?to (pursue|proceed) with other|"
        r"other candidates|not selected|position has been filled|will not be proceeding|no longer under consideration)\b", re.I)),
    ("assessment", re.compile(
        r"\b(assessment|coding (challenge|test|exercise)|take[- ]home|hackerrank|codility|codesignal|testgorilla|online test)\b", re.I)),
    ("interview", re.compile(
        r"\b(interview|schedule (a|some) (time|call)|your availability|calendly\.com|phone screen|"
        r"next (step|round|stage)s?|move forward with your application|would (love|like) to (chat|speak|talk|connect))\b", re.I)),
    ("received", re.compile(
        r"\b(thank(s| you) for (applying|your application)|we('ve| have) received your application|application (was |has been )?received)\b", re.I)),
]

_JOB_WORDS = re.compile(r"\b(application|applied|interview|position|role|candidate|candidacy|opportunit|recruit|hiring|offer|assessment)\b", re.I)

_ATS_SENDERS = ("greenhouse", "lever.co", "ashbyhq", "workday", "smartrecruiters", "workable", "icims", "jobvite",
                "bamboohr", "recruitee", "teamtailor", "breezy", "personio", "darwinbox", "keka")

# A reply can only move an application forward through the funnel.
_STATUS_RANK = {"pending": 0, "approved": 0, "applying": 1, "awaiting_confirmation": 1, "submitted": 2,
                "viewed": 3, "interview": 4, "offer": 5}
_CATEGORY_STATUS = {"received": "submitted", "recruiter": "viewed", "assessment": "interview",
                    "interview": "interview", "offer": "offer"}


def classify(subject: str, body: str) -> str:
    text = f"{subject}\n{body[:4000]}"
    for category, pattern in _CATEGORY_RULES:
        if pattern.search(text):
            return category
    return "recruiter" if _JOB_WORDS.search(text) else "other"


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_text(msg: email.message.Message) -> str:
    plain, html = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and not plain:
            plain = text
        elif part.get_content_type() == "text/html" and not html:
            html = text
    return (plain or html_to_text(html)).strip()


def _fetch_messages(account: EmailAccount, password: str, since: datetime) -> list[dict[str, Any]]:
    """Headers for recent mail, and full bodies only for job-looking candidates."""
    client = _imap_login(account, password)
    try:
        client.select("INBOX", readonly=True)
        _typ, data = client.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        ids = (data[0] or b"").split()[-_MAX_MESSAGES_PER_SYNC:]
        messages: list[dict[str, Any]] = []
        for msg_id in ids:
            _typ, parts = client.fetch(
                msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])"
            )
            if not parts or not isinstance(parts[0], tuple):
                continue
            header = email.message_from_bytes(parts[0][1])
            messages.append({
                "seq": msg_id,
                "from": _decode(header.get("From")),
                "subject": _decode(header.get("Subject")),
                "date": header.get("Date"),
                "message_id": (header.get("Message-ID") or "").strip(),
                "in_reply_to": f"{header.get('In-Reply-To') or ''} {header.get('References') or ''}",
            })
        return messages
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _fetch_bodies(account: EmailAccount, password: str, seqs: list[bytes]) -> dict[bytes, str]:
    if not seqs:
        return {}
    client = _imap_login(account, password)
    try:
        client.select("INBOX", readonly=True)
        bodies = {}
        for seq in seqs:
            _typ, parts = client.fetch(seq, "(BODY.PEEK[])")
            if parts and isinstance(parts[0], tuple):
                bodies[seq] = _body_text(email.message_from_bytes(parts[0][1]))
        return bodies
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def sync_inbox(db: AsyncSession, user_id: str) -> dict[str, int]:
    account = await get_account(db, user_id)
    if account is None:
        raise MailError("No email account connected.")

    apps = (
        await db.execute(
            select(Application, JobListing)
            .join(JobListing, Application.job_id == JobListing.id)
            .where(Application.user_id == user_id, Application.status.notin_(["pending", "skipped"]))
        )
    ).all()
    outreach = (
        await db.execute(select(OutreachEmail).where(OutreachEmail.user_id == user_id, OutreachEmail.status == "sent"))
    ).scalars().all()
    known_ids = {
        row.message_id for row in (
            await db.execute(select(InboxMessage.message_id).where(InboxMessage.user_id == user_id))
        )
    }

    by_message_id = {o.message_id: o.application_id for o in outreach if o.message_id}
    by_domain = {o.to_address.split("@")[-1].lower(): o.application_id for o in outreach}
    company_keys = [(normalise_key(job.company), app.id) for app, job in apps if job.company and len(normalise_key(job.company)) >= 4]

    def match_application(sender: str, subject: str, body: str, references: str) -> str | None:
        for message_id, app_id in by_message_id.items():
            if message_id in references:
                return app_id
        domain = sender.split("@")[-1].lower()
        if domain in by_domain:
            return by_domain[domain]
        haystack = normalise_key(f"{sender} {subject} {body[:1500]}")
        for key, app_id in company_keys:
            if key in haystack:
                return app_id
        return None

    since = (account.last_sync_at - timedelta(days=1)) if account.last_sync_at else utcnow() - timedelta(days=INBOX_LOOKBACK_DAYS)
    password = decrypt(account.password_encrypted)
    try:
        headers = await asyncio.to_thread(_fetch_messages, account, password, since)
    except Exception as exc:
        account.last_error = _friendly(exc)
        await db.commit()
        raise MailError(account.last_error) from exc

    candidates = []
    for message in headers:
        if not message["message_id"] or message["message_id"] in known_ids:
            continue
        sender = parseaddr(message["from"])[1].lower()
        if sender == account.email_address.lower():
            continue
        looks_relevant = (
            any(mid in message["in_reply_to"] for mid in by_message_id)
            or sender.split("@")[-1] in by_domain
            or any(ats in sender for ats in _ATS_SENDERS)
            or _JOB_WORDS.search(message["subject"] or "")
            or match_application(sender, message["subject"], "", message["in_reply_to"])
        )
        if looks_relevant:
            candidates.append(message)

    bodies = await asyncio.to_thread(_fetch_bodies, account, password, [m["seq"] for m in candidates])

    stats = {"scanned": len(headers), "stored": 0, "status_updates": 0}
    app_rows = {app.id: app for app, _job in apps}
    for message in candidates:
        body = bodies.get(message["seq"], "")
        sender_name, sender = parseaddr(message["from"])
        app_id = match_application(sender.lower(), message["subject"], body, message["in_reply_to"])
        category = classify(message["subject"], body)
        # Keep unrelated mail out of the database entirely.
        if app_id is None and category in ("other", "recruiter") and not any(ats in sender for ats in _ATS_SENDERS):
            continue
        try:
            received = parsedate_to_datetime(message["date"]) if message["date"] else None
            if received is not None and received.tzinfo is not None:
                received = received.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            received = None

        db.add(InboxMessage(
            user_id=user_id, application_id=app_id, message_id=message["message_id"][:500],
            from_address=sender[:255], from_name=(sender_name or "")[:255], subject=(message["subject"] or "")[:500],
            snippet=re.sub(r"\s+", " ", body)[:600], received_at=received, category=category,
        ))
        stats["stored"] += 1

        app = app_rows.get(app_id) if app_id else None
        if app is not None:
            if await _advance_status(db, app, category, message["subject"]):
                stats["status_updates"] += 1

    account.last_sync_at = utcnow()
    account.last_error = None
    await db.commit()
    if stats["stored"]:
        await event_bus.publish(user_id, "inbox_synced", stats)
    logger.info("Inbox sync for %s: %s", user_id, stats)
    return stats


async def _advance_status(db: AsyncSession, app: Application, category: str, subject: str) -> bool:
    current = app.status or "pending"
    if category == "rejection":
        new = "rejected" if current not in ("offer", "rejected") else None
    else:
        target = _CATEGORY_STATUS.get(category)
        new = target if target and _STATUS_RANK.get(target, 0) > _STATUS_RANK.get(current, 0) else None
    if not new:
        return False
    app.status = new
    # A reply about an application proves it was sent, even if the submit step was never confirmed.
    if new != "rejected" and app.submitted_at is None:
        app.submitted_at = utcnow()
    db.add(ApplicationLog(application_id=app.id, action="reply_received",
                          details=f"{category.title()} email: {subject[:200]} -> status {new}"))
    return True


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------


async def follow_ups_due(db: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(days=FOLLOW_UP_AFTER_DAYS)
    rows = (
        await db.execute(
            select(Application, JobListing)
            .join(JobListing, Application.job_id == JobListing.id)
            .where(Application.user_id == user_id, Application.status.in_(["submitted", "awaiting_confirmation", "viewed"]))
        )
    ).all()

    due = []
    for app, job in rows:
        emails = (
            await db.execute(select(OutreachEmail).where(OutreachEmail.application_id == app.id, OutreachEmail.status == "sent"))
        ).scalars().all()
        if any(e.kind == "follow_up" for e in emails):
            continue
        initial = min((e for e in emails if e.kind == "initial"), key=lambda e: e.sent_at, default=None)
        last_touch = max(filter(None, [app.submitted_at, initial.sent_at if initial else None]), default=None)
        if last_touch is None or last_touch > cutoff:
            continue
        replied = (
            await db.execute(
                select(func.count(InboxMessage.id)).where(
                    InboxMessage.application_id == app.id, InboxMessage.category != "received"
                )
            )
        ).scalar()
        if replied:
            continue
        due.append({
            "application_id": app.id,
            "title": job.title,
            "company": job.company,
            "days_since": (utcnow() - last_touch).days,
            "contact": initial.to_address if initial else None,
        })
    return sorted(due, key=lambda d: -d["days_since"])
