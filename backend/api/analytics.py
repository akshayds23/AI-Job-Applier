from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database.database import get_db
from database.models import User, Application, UserJobMatch, JobListing, InboxMessage
from api.auth import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/overview")
async def get_analytics_overview(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Total matched jobs
    matched_count_res = await db.execute(
        select(func.count(UserJobMatch.id)).where(UserJobMatch.user_id == user.id)
    )
    total_matched = matched_count_res.scalar() or 0

    # Applications stats by status
    apps_res = await db.execute(
        select(Application.status, func.count(Application.id))
        .where(Application.user_id == user.id)
        .group_by(Application.status)
    )
    status_counts = dict(apps_res.all())

    total_applied = sum(status_counts.values())
    # Anything with a submission date was sent, whatever stage it has reached since.
    submitted = (
        await db.execute(
            select(func.count(Application.id)).where(
                Application.user_id == user.id, Application.submitted_at.isnot(None)
            )
        )
    ).scalar() or 0
    interviews = status_counts.get("interview", 0) + status_counts.get("offer", 0)
    offers = status_counts.get("offer", 0)
    replied = (
        await db.execute(
            select(func.count(func.distinct(InboxMessage.application_id))).where(
                InboxMessage.user_id == user.id,
                InboxMessage.application_id.isnot(None),
                InboxMessage.category != "received",
            )
        )
    ).scalar() or 0
    verified_jobs = (
        await db.execute(
            select(func.count(UserJobMatch.id))
            .join(JobListing, UserJobMatch.job_id == JobListing.id)
            .where(UserJobMatch.user_id == user.id, JobListing.trust_label == "verified")
        )
    ).scalar() or 0

    # Average match score
    avg_score_res = await db.execute(
        select(func.avg(UserJobMatch.match_score)).where(UserJobMatch.user_id == user.id)
    )
    avg_match_score = round(avg_score_res.scalar() or 0.0, 1)

    return {
        "total_matched_jobs": total_matched,
        "total_applications": total_applied,
        "submitted": submitted,
        "interviews": interviews,
        "offers": offers,
        "average_match_score": avg_match_score,
        "replies": replied,
        "verified_jobs": verified_jobs,
        "response_rate_percent": round((replied / submitted * 100), 1) if submitted > 0 else 0.0,
        "interview_rate_percent": round((interviews / submitted * 100), 1) if submitted > 0 else 0.0,
        "by_status": status_counts
    }
