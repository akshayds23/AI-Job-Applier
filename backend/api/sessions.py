from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.database import get_db
from database.models import User, PlatformSession, PlatformConfig
from api.auth import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])

@router.get("/")
async def get_platform_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    configs_res = await db.execute(select(PlatformConfig).where(PlatformConfig.is_enabled == True))
    configs = configs_res.scalars().all()

    sessions_res = await db.execute(
        select(PlatformSession).where(PlatformSession.user_id == user.id)
    )
    user_sessions = {s.platform: s for s in sessions_res.scalars().all()}

    res = []
    for cfg in configs:
        user_sess = user_sessions.get(cfg.platform)
        res.append({
            "platform": cfg.platform,
            "display_name": cfg.display_name,
            "requires_login": cfg.requires_login,
            "is_connected": bool(user_sess and user_sess.is_active),
            "last_verified": user_sess.last_verified if user_sess else None,
            "last_used": user_sess.last_used if user_sess else None
        })

    return res
