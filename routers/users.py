from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated
from sqlalchemy.orm import Session
from database import get_db, User
from auth import verify_password, create_access_token, hash_password
from schema import UserCreate, UserResponse
from limiter import limiter
from utils import logger
from starlette.requests import Request
from auth import verify_token, oauth2_scheme

router = APIRouter()

@router.get("/me", response_model=UserResponse)
async def get_me(db: Annotated[Session, Depends(get_db)], token: Annotated[str, Depends(oauth2_scheme)]):
    user_id = verify_token(token)
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
    except Exception as e:
        logger.error("Database error during user look up: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/create", status_code=201)
@limiter.limit("2/minute")
async def register_user(request: Request, user: UserCreate, db: Annotated[Session, Depends(get_db)]):
    try:
        existing = db.query(User).filter(
            (User.username == user.username) | (User.email == user.email)
        ).first()
    except Exception as e:
        logger.error("Database error during user lookup: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    if existing:
        raise HTTPException(status_code=400, detail="Username or email already exists")

    new_user = User(
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        email=user.email,
        password=hash_password(user.password)
    )

    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        token = create_access_token({"sub": str(new_user.id)})
        return {"access_token": token, "token_type": "bearer", "user": new_user}
    except Exception as e:
        db.rollback()
        logger.error("Database error during user creation: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)]
):
    try:
        user = db.query(User).filter(User.username == form_data.username).first()
    except Exception as e:
        logger.error("Database error during login: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(form_data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token, "token_type": "bearer"}
