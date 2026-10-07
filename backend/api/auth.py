from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.database import get_db
from database.models import User, UserProfile, utcnow
from pydantic import BaseModel
from core.security import create_access_token, decode_access_token, hash_password, verify_password
from utils.llm_keys import bind_user_keys

router = APIRouter(prefix="/auth", tags=["auth"])

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


def _normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _token_response(user: User) -> dict:
    token, expires_in = create_access_token(user.id, user.email, user.name)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name
        }
    }


async def get_current_user(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Resolve the user from a valid bearer token, or reject the request."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(authorization.split(" ", 1)[1])
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = await db.get(User, payload["sub"])
    if not user or user.is_active is False:
        raise HTTPException(status_code=401, detail="User not found")
    # Every AI call made for this request (and tasks it starts) uses this user's own keys.
    await bind_user_keys(db, user.id)
    return user


@router.post("/login")
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    email = _normalise_email(data.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.password_hash:
        if not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
    else:
        # Accounts created before passwords were enforced: the first login sets it.
        try:
            user.password_hash = hash_password(data.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    user.last_login_at = utcnow()
    await db.commit()
    return _token_response(user)


@router.post("/register")
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    email = _normalise_email(data.email)
    if "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account with this email already exists. Please sign in.")

    try:
        password_hash = hash_password(data.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    user = User(email=email, name=data.name.strip() or email.split("@")[0].title(), password_hash=password_hash)
    db.add(user)
    await db.flush()
    db.add(UserProfile(user_id=user.id))
    await db.commit()
    await db.refresh(user)
    return _token_response(user)


@router.get("/me")
async def read_current_user(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name
    }
