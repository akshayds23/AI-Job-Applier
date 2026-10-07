from fastapi import APIRouter
from api.auth import router as auth_router
from api.profile import router as profile_router
from api.jobs import router as jobs_router
from api.applications import router as applications_router
from api.sessions import router as sessions_router
from api.templates import router as templates_router
from api.analytics import router as analytics_router
from api.companies import router as companies_router
from api.email import router as email_router
from api.ai_keys import router as ai_keys_router

api_router = APIRouter(prefix="/api")

api_router.include_router(auth_router)
api_router.include_router(profile_router)
api_router.include_router(jobs_router)
api_router.include_router(applications_router)
api_router.include_router(sessions_router)
api_router.include_router(templates_router)
api_router.include_router(analytics_router)
api_router.include_router(companies_router)
api_router.include_router(email_router)
api_router.include_router(ai_keys_router)
