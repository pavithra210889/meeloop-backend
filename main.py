import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import socketio
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_req_logger = logging.getLogger("meeloop.requests")

from app.users.routers import router
from app.posts.routers import router as post_router
from app.messages.routers import router as message_router
from app.contacts.routers import router as contact_router
from app.stories.routers import router as story_router
from app.calls.routers import router as call_router
from app.notifications.routers import router as notification_router
from app.loops.routers import router as loop_router
from app.sockets.socketio_server import sio
import app.sockets.socketio_events
import app.loops.socketio_events
from app.storage.routers import router as media_router
from app.reports.routers import router as reports_router
from app.meme_templates.routers import router as meme_template_router
from app.link_preview.routers import router as link_preview_router
from app.gifs.routers import router as gifs_router
from app.maps.routers import router as maps_router
from app.turn.routers import router as turn_router
from app.groups.routers import router as groups_router
from app.scheduled_calls.routers import router as scheduled_calls_router
from app.ar_filters.routers import router as ar_filters_router
from app.recommendations.routers import router as recommendations_router
from sqlmodel import SQLModel
from app.database import engine

# Disable docs in production
docs_url = None if settings.ENVIRONMENT == "production" else "/docs"
redoc_url = None if settings.ENVIRONMENT == "production" else "/redoc"
openapi_url = None if settings.ENVIRONMENT == "production" else "/openapi.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.scheduled_calls.scheduler import run_scheduled_call_checker
    from app.recommendations.scheduler import run_recommendations_engine
    task1 = asyncio.create_task(run_scheduled_call_checker())
    task2 = asyncio.create_task(run_recommendations_engine())
    yield
    task1.cancel()
    task2.cancel()
    for t in (task1, task2):
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="meeloop", docs_url=docs_url, redoc_url=redoc_url, openapi_url=openapi_url,
    lifespan=lifespan, redirect_slashes=False,
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    if not settings.LOG_PAYLOADS:
        return await call_next(request)

    start = time.perf_counter()
    body_bytes = await request.body()

    body_log = None
    content_type = request.headers.get("content-type", "")
    if body_bytes and "application/json" in content_type:
        try:
            body_log = json.loads(body_bytes)
        except Exception:
            body_log = body_bytes.decode("utf-8", errors="replace")[:500]

    # Restore body so downstream handlers can read it
    async def _receive():
        return {"type": "http.request", "body": body_bytes}
    request._receive = _receive

    response = await call_next(request)
    ms = round((time.perf_counter() - start) * 1000, 1)

    entry: dict = {
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "ms": ms,
    }
    if request.url.query:
        entry["query"] = request.url.query
    if body_log is not None:
        entry["body"] = body_log

    _req_logger.info(json.dumps(entry))
    return response


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

socket_app = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="/ws/socket.io")
app.mount("/ws", socket_app)

import os
if os.path.exists(settings.MEDIA_ROOT):
    app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")
app.include_router(router)
app.include_router(post_router)
app.include_router(story_router)
app.include_router(message_router)
app.include_router(contact_router)
app.include_router(call_router)
app.include_router(notification_router)
app.include_router(loop_router)
app.include_router(media_router)
app.include_router(reports_router)
app.include_router(meme_template_router)
app.include_router(link_preview_router)
app.include_router(gifs_router)
app.include_router(maps_router)
app.include_router(turn_router)
app.include_router(groups_router)
app.include_router(scheduled_calls_router)
app.include_router(ar_filters_router)
app.include_router(recommendations_router)

# Ensure all tables exist (for environments without migrations)
try:
    SQLModel.metadata.create_all(engine)
except Exception:
    pass

# Mount SQLAdmin panel at /sqladmin
from app.admin.sqladmin_setup import create_admin
create_admin(app)
