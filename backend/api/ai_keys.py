"""Bring-your-own-key management: each user stores and rotates their own LLM API keys."""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from core.crypto import encrypt
from database.database import get_db
from database.models import AIKey, User, UserProfile
from utils.llm_client import probe_key
from utils.llm_keys import DEFAULT_MODELS, PROVIDERS, PooledKey, current_keyring, invalidate_keyring, load_keyring

router = APIRouter(prefix="/ai-keys", tags=["ai-keys"])

MAX_KEYS_PER_USER = 12


class KeyIn(BaseModel):
    provider: str
    api_key: str
    label: str = ""
    model: str = ""


class KeyPatch(BaseModel):
    label: Optional[str] = None
    model: Optional[str] = None
    is_enabled: Optional[bool] = None


class PoolSettings(BaseModel):
    preferred_provider: Optional[str] = None
    max_wait_minutes: Optional[int] = None


def _key_dict(row: AIKey, live: dict | None) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "label": row.label,
        "model": row.model or DEFAULT_MODELS.get(row.provider, ""),
        "model_is_default": not row.model,
        "masked": f"••••{row.key_last4}" if row.key_last4 else "••••",
        "is_enabled": row.is_enabled,
        "status": row.status,
        "last_error": row.last_error,
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
        "live": live,
    }


async def _owned(db: AsyncSession, user: User, key_id: str) -> AIKey:
    row = await db.get(AIKey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Key not found")
    return row


@router.get("/")
async def list_keys(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AIKey).where(AIKey.user_id == user.id).order_by(AIKey.created_at))).scalars().all()
    keyring = current_keyring()
    live = {k["id"]: k for k in (keyring.status()["keys"] if keyring else [])}
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    return {
        "keys": [_key_dict(r, live.get(r.id)) for r in rows],
        "providers": [{"id": p, "default_model": DEFAULT_MODELS[p]} for p in PROVIDERS],
        "settings": {
            "preferred_provider": profile.llm_provider if profile else None,
            "max_wait_minutes": (profile.llm_max_wait_minutes if profile and profile.llm_max_wait_minutes is not None else 15),
        },
        "pool": keyring.status() if keyring else None,
    }


@router.post("/", status_code=201)
async def add_key(data: KeyIn, background_tasks: BackgroundTasks, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    provider = data.provider.strip().lower()
    secret = data.api_key.strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Provider must be one of: {', '.join(PROVIDERS)}")
    if len(secret) < 12:
        raise HTTPException(status_code=400, detail="That doesn't look like a complete API key")
    count = len((await db.execute(select(AIKey.id).where(AIKey.user_id == user.id))).all())
    if count >= MAX_KEYS_PER_USER:
        raise HTTPException(status_code=400, detail=f"At most {MAX_KEYS_PER_USER} keys per account")

    model = data.model.strip() or DEFAULT_MODELS[provider]
    test = await probe_key(PooledKey(id="new", provider=provider, model=model, api_key=secret, label="new key"))
    if not test["ok"]:
        raise HTTPException(status_code=400, detail=f"Key test failed: {test['error']}")

    row = AIKey(
        user_id=user.id,
        provider=provider,
        label=data.label.strip()[:100] or f"{provider.title()} key {count + 1}",
        model=data.model.strip() or None,
        key_encrypted=encrypt(secret),
        key_last4=secret[-4:],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    invalidate_keyring(user.id)

    # A resume uploaded before the first key was parsed without AI (no work history) - read it again now.
    from services.jobs import kick, start_resume_import

    reimport = await start_resume_import(user.id)
    if reimport is not None:
        kick(background_tasks, user.id)
    return {**_key_dict(row, None), "test": test, "resume_reimport": reimport is not None}


@router.patch("/{key_id}")
async def update_key(key_id: str, data: KeyPatch, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    row = await _owned(db, user, key_id)
    if data.label is not None:
        row.label = data.label.strip()[:100] or row.label
    if data.model is not None:
        row.model = data.model.strip() or None
    if data.is_enabled is not None:
        row.is_enabled = data.is_enabled
    await db.commit()
    await db.refresh(row)
    invalidate_keyring(user.id)
    return _key_dict(row, None)


@router.post("/{key_id}/test")
async def test_key(key_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Re-test a key; a passing test also clears an earlier 'invalid' mark."""
    from core.crypto import decrypt

    row = await _owned(db, user, key_id)
    key = PooledKey(id=row.id, provider=row.provider, model=row.model or DEFAULT_MODELS[row.provider],
                    api_key=decrypt(row.key_encrypted), label=row.label or row.provider)
    result = await probe_key(key)
    row.status = "active" if result["ok"] else ("invalid" if key.invalid_reason else row.status)
    row.last_error = None if result["ok"] else result["error"]
    await db.commit()
    invalidate_keyring(user.id)
    return result


@router.delete("/{key_id}", status_code=204)
async def delete_key(key_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    row = await _owned(db, user, key_id)
    await db.delete(row)
    await db.commit()
    invalidate_keyring(user.id)


@router.put("/settings")
async def update_pool_settings(data: PoolSettings, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
    if data.preferred_provider is not None:
        if data.preferred_provider and data.preferred_provider not in PROVIDERS:
            raise HTTPException(status_code=400, detail="Unknown provider")
        profile.llm_provider = data.preferred_provider or None
    if data.max_wait_minutes is not None:
        profile.llm_max_wait_minutes = max(0, min(120, data.max_wait_minutes))
    await db.commit()
    invalidate_keyring(user.id)
    await load_keyring(db, user.id)
    return {"preferred_provider": profile.llm_provider, "max_wait_minutes": profile.llm_max_wait_minutes}


@router.get("/status")
async def pool_status(user: User = Depends(get_current_user)):
    keyring = current_keyring()
    return keyring.status() if keyring else {"keys": [], "waiting_seconds": 0, "parallelism": 0}
