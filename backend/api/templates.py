from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.database import get_db
from database.models import ResumeTemplate

router = APIRouter(prefix="/templates", tags=["templates"])

@router.get("/")
async def get_templates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ResumeTemplate))
    return result.scalars().all()
