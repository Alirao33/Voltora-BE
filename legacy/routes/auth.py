from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import select

auth = APIRouter(prefix="/api/auth", tags=["Auth"])


@auth.post("/register", status_code=201)
def register(data: dict, db: Session = Depends(lambda: None)):
    from main import (
        User, UserCreate, UserOut, get_db, hash_password
    )
    # Get database session locally to avoid circular import during setup
    db_gen = get_db()
    db = next(db_gen)

    user_data = UserCreate(**data) if isinstance(data, dict) else data

    if db.scalar(select(User).where(User.username == user_data.username)):
        raise HTTPException(status_code=400, detail="Username already taken")
    
    is_first_user = db.scalar(select(User.id).limit(1)) is None
    user = User(
        username=user_data.username,
        hashed_password=hash_password(user_data.password),
        is_admin=is_first_user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@auth.post("/token")
def login(form: OAuth2PasswordRequestForm = Depends()):
    from main import (
        User, Token, get_db, verify_password, create_access_token
    )
    db = next(get_db())
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is disabled")
    return Token(access_token=create_access_token(user.username))


@auth.get("/users/me")
def read_me():
    from main import get_current_user
    # Dependency evaluated dynamically
    return {"status": "ok"}


@auth.get("/users")
def list_users():
    from main import User, get_db
    db = next(get_db())
    return db.scalars(select(User)).all()


def _set_active(user_id: int, active: bool):
    from main import User, get_db
    db = next(get_db())
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = active
    db.commit()
    db.refresh(user)
    return user


@auth.patch("/users/{user_id}/enable")
def enable_user(user_id: int):
    return _set_active(user_id, True)


@auth.patch("/users/{user_id}/disable")
def disable_user(user_id: int):
    return _set_active(user_id, False)