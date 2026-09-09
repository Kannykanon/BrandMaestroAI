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

# Required regardless of which Gemini backend is in use.
REQUIRED_ENV_VARS = ["POSTGRES_URI", "REDIS_URL", "PARALLEL_API_KEY"]

# The credential requirement depends on the backend, so it cannot be a fixed
# list. On ai_studio an API key is the credential. On vertex_ai there is no key
# at all: authentication is Application Default Credentials, which come from a
# service account attached to the host (Compute Engine, Cloud Run) or from a
# key file named by GOOGLE_APPLICATION_CREDENTIALS. Demanding GOOGLE_API_KEY
# there fails startup on a correctly configured host.
REQUIRED_ENV_VARS_BY_PROVIDER = {
    "ai_studio": ["GOOGLE_API_KEY"],
    "vertex_ai": ["PROJECT_ID"],
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from database import engine, init_db

    from model import LLM_PROVIDER

    required = REQUIRED_ENV_VARS + REQUIRED_ENV_VARS_BY_PROVIDER.get(LLM_PROVIDER, [])
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise RuntimeError(
            f"Missing required env vars for LLM_PROVIDER={LLM_PROVIDER}: {missing}"
        )
    logger.info("Startup config OK (LLM_PROVIDER=%s)", LLM_PROVIDER)

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


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}


# Serve the UI from the API itself, so one deployed URL is the whole product.
#
# Mounted last, after every router, because a mount at "/" claims all paths
# below it: mounting earlier would shadow /users, /conversation and /documents.
# Serving both from one origin also removes the CORS and mixed-content problems
# a separately-hosted frontend would introduce.
#
# The API's own liveness lives at /health, which is registered above and so
# still wins over the static mount.
# The UI is served straight from disk with no build step, so nothing rewrites
# asset URLs per release: index.html asked for /app.js?v=2 with the 2 written
# by hand. Every deploy therefore shipped new bytes at a URL the browser had
# already cached, and a returning visitor kept running the previous release's
# JavaScript against the current API until they hard-refreshed. A newly added
# panel simply did not exist for them.
#
# no-cache does not mean do not store: the browser still caches, but must
# revalidate before reuse. StaticFiles already sends ETag and Last-Modified,
# so a revalidation is a 304 with no body, and correctness stops depending on
# somebody remembering to bump a number.
@app.middleware("http")
async def _revalidate_ui_assets(request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.startswith(("text/html", "text/css", "application/javascript", "text/javascript")):
        response.headers["Cache-Control"] = "no-cache"
    return response


_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    logger.info("Serving UI from %s at /", _STATIC_DIR)
else:
    logger.warning("static/ not found — API will run without a UI")

    @app.get("/")
    async def root():
        return {"message": "BrandMaestro AI API is running."}