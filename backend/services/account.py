"""Delete a user's account and everything stored for it.

Rows are deleted explicitly, children first, so nothing relies on the database
enforcing ON DELETE CASCADE (SQLite does not by default). Files the user uploaded or
that were generated for them are removed too, but only inside the app's own upload and
output folders. Shared job postings (not personal data) are kept.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.logging_config import get_logger
from database.models import (
    AIKey, Application, ApplicationLog, BackgroundJob, CareerFact, Company, Education, EmailAccount,
    Experience, InboxMessage, MasterResume, OutreachEmail, PlatformSession, Project, ScrapeRun, Skill,
    User, UserJobMatch, UserProfile,
)

logger = get_logger("account")

# Children before parents.
_USER_TABLES = [
    OutreachEmail, InboxMessage, Application, UserJobMatch, ScrapeRun, BackgroundJob, Company,
    CareerFact, AIKey, EmailAccount, PlatformSession, MasterResume, Skill, Experience, Project,
    Education, UserProfile,
]


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(root) for root in roots)


async def _files_for(db: AsyncSession, user_id: str) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    folders: list[Path] = []

    for (path,) in (await db.execute(select(MasterResume.file_path).where(MasterResume.user_id == user_id))).all():
        if path:
            files.append(Path(path))
    for app_id, *paths in (await db.execute(select(
        Application.id, Application.resume_docx_path, Application.resume_pdf_path, Application.cover_letter_path,
    ).where(Application.user_id == user_id))).all():
        files.extend(Path(p) for p in paths if p)
        folders.append(settings.generated_path / app_id)
    for (path,) in (await db.execute(select(ApplicationLog.screenshot_path).join(
        Application, Application.id == ApplicationLog.application_id
    ).where(Application.user_id == user_id))).all():
        if path:
            files.append(Path(path))
    for (path,) in (await db.execute(select(PlatformSession.storage_state_path).where(PlatformSession.user_id == user_id))).all():
        if path:
            files.append(Path(path))

    uploads = settings.uploads_path
    if uploads.is_dir():
        files.extend(uploads.glob(f"{user_id}_*"))
    return files, folders


def _remove(files: list[Path], folders: list[Path]) -> int:
    roots = [settings.uploads_path.resolve(), settings.generated_path.resolve()]
    removed = 0
    for path in files:
        if _inside(path, roots) and path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    for folder in folders:
        if _inside(folder, roots) and folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


async def delete_account(db: AsyncSession, user: User) -> dict[str, int]:
    user_id = user.id
    files, folders = await _files_for(db, user_id)

    app_ids = select(Application.id).where(Application.user_id == user_id)
    rows = (await db.execute(delete(ApplicationLog).where(ApplicationLog.application_id.in_(app_ids)))).rowcount or 0
    for model in _USER_TABLES:
        rows += (await db.execute(delete(model).where(model.user_id == user_id))).rowcount or 0
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()

    removed = _remove(files, folders)  # after the commit, so a failed delete never loses files
    logger.info("Deleted account %s: %d rows, %d files or folders", user_id, rows + 1, removed)
    return {"rows": rows + 1, "files": removed}
