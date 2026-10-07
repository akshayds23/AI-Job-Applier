import asyncio

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from api.router import api_router
from config import settings
from database.database import init_db
from database.seed import seed_data
from services.scheduler import start_scheduler, stop_scheduler

app = FastAPI(
    title=settings.APP_NAME,
    description="Autonomous Job Application System API",
    version="1.0.0"
)

# Configure CORS (not needed when the frontend is served from the same Vercel domain)
origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_origin_regex=settings.ALLOWED_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(api_router)

# One-time initialisation, run by whichever comes first: the startup event (normal
# server) or the first request (serverless runtimes may not send startup events).
_ready = False
_ready_lock = asyncio.Lock()


async def ensure_ready() -> None:
    global _ready
    if _ready:
        return
    async with _ready_lock:
        if _ready:
            return
        await init_db()
        await seed_data()
        _ready = True


@app.middleware("http")
async def _init_before_first_request(request: Request, call_next):
    if not _ready:
        await ensure_ready()
    return await call_next(request)


@app.on_event("startup")
async def on_startup():
    await ensure_ready()
    if not settings.serverless:
        start_scheduler()
        # Fill Tectonic's package cache in the background so the first LaTeX resume is fast.
        from services.latex_compiler import warm_up
        asyncio.get_running_loop().run_in_executor(None, warm_up)
    print(f"[INIT] {settings.APP_NAME} backend ready ({'serverless' if settings.serverless else 'server'} mode)")


@app.on_event("shutdown")
async def on_shutdown():
    stop_scheduler()


@app.get("/healthz")
@app.get("/api/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/config")
async def public_config():
    """Feature flags the frontend needs before login."""
    from services.latex_compiler import is_available as latex_available

    return {
        "assisted_apply": settings.ASSISTED_APPLY_ENABLED,
        "latex_pdf": latex_available(),
        "serverless": settings.serverless,
    }


@app.get("/api/internal/cron")
@app.post("/api/internal/cron")
async def cron(background_tasks: BackgroundTasks, authorization: str = Header(None)):
    """Serverless replacement for the in-process scheduler (Vercel Cron or any external pinger).

    Queues scheduled searches and inbox syncs, then advances due background jobs.
    """
    from services.jobs import kick
    from services.scheduler import enqueue_due_work

    if not settings.CRON_SECRET or authorization != f"Bearer {settings.CRON_SECRET}":
        raise HTTPException(status_code=401, detail="Invalid cron secret")
    queued = await enqueue_due_work()
    kick(background_tasks)
    return {"queued": queued}


@app.get("/")
async def root():
    return {"status": "online", "app": settings.APP_NAME, "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
