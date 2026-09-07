import logging
import os
from contextlib import asynccontextmanager

import redis
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from limiter import limiter, RateLimitExceeded
from model import LLMSingleton
from routers import users, conversation, document

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)

REQUIRED_ENV_VARS = ["GOOGLE_API_KEY", "POSTGRES_URI", "REDIS_URL", "PARALLEL_API_KEY"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    from database import engine, init_db

    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {missing}")

    app.state.db_engine = engine
    init_db()
    app.state.llm = LLMSingleton.get()
    app.state.redis = redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=10,
    )
    logger.info("BrandMaestro AI started successfully")
    yield
    app.state.redis.close()
    engine.dispose()
    logger.info("Shutting down BrandMaestro AI")


app = FastAPI(
    title="BrandMaestro AI",
    description="Brand voice content generation API",
    version="1.0.0",
    lifespan=lifespan
)

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

frontend_url = os.getenv("FRONTEND_URL")
if frontend_url:
    origins.append(frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in ("/docs", "/redoc", "/openapi.json"):
        return response
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."}
    )


app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(conversation.router, prefix="/conversation", tags=["Conversation"])
app.include_router(document.router, prefix="/documents", tags=["Documents"])


@app.get("/")
async def root():
    return {"message": "BrandMaestro AI API is running."}


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}