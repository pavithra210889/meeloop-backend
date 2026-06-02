import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import select

from ..dependencies import SessionDep
from ..users.routers import get_current_active_user
from ..users.models import User
from ..config import settings
from ..redis_client import redis_client
from .models import ArFilter, ArGameConfig
from .schemas import (
    ArFiltersResponse,
    ArGameConfigRead,
    ArFilterUpsertRequest,
    ArFilterUpdateRequest,
    ArGameConfigUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ar-filters"])

_REDIS_FILTERS_KEY = "ar:filters:v1"
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _require_admin(current_user: User) -> User:
    admins = {u.strip() for u in getattr(settings, "ADMIN_USERNAMES", "").split(",") if u.strip()}
    if current_user.username in admins:
        return current_user
    raise HTTPException(status_code=403, detail="Admin access required")


# ── Public endpoints ───────────────────────────────────────────────────────────

@router.get("/ar/filters", response_model=ArFiltersResponse)
async def get_ar_filters(request: Request, session: SessionDep):
    """Return all active AR filters, Redis-cached for 5 minutes with ETag support."""
    # Try Redis cache first
    cached = await redis_client.get(_REDIS_FILTERS_KEY)
    if cached:
        payload = cached
    else:
        rows = session.exec(
            select(ArFilter).where(ArFilter.is_active == True).order_by(ArFilter.sort_order)
        ).all()
        max_version = max((r.version for r in rows), default=1)
        filters_list = [r.filter_data for r in rows]
        response_data = {"version": max_version, "filters": filters_list}
        payload = json.dumps(response_data, ensure_ascii=False)
        try:
            await redis_client.set(_REDIS_FILTERS_KEY, payload, ex=_CACHE_TTL_SECONDS)
        except Exception:
            logger.warning("Redis set failed for ar:filters:v1")

    etag = '"' + hashlib.md5(payload.encode()).hexdigest() + '"'
    client_etag = request.headers.get("If-None-Match")
    if client_etag and client_etag == etag:
        return Response(status_code=304)

    data = json.loads(payload)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"ETag": etag},
    )


@router.get("/ar/games/{game_id}/config", response_model=ArGameConfigRead)
def get_game_config(game_id: str, session: SessionDep):
    """Return the game config for a given game_id."""
    row = session.exec(select(ArGameConfig).where(ArGameConfig.game_id == game_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Game config '{game_id}' not found")
    return ArGameConfigRead(game_id=row.game_id, config_data=row.config_data, version=row.version)


# ── Admin endpoints ────────────────────────────────────────────────────────────

@router.post("/ar/admin/filters")
async def upsert_ar_filter(
    payload: ArFilterUpsertRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Upsert an AR filter. Admin only."""
    _require_admin(current_user)

    existing = session.exec(
        select(ArFilter).where(ArFilter.filter_key == payload.filter_key)
    ).first()

    if existing:
        existing.filter_data = payload.filter_data
        existing.is_active = payload.is_active
        existing.sort_order = payload.sort_order
        existing.version += 1
        existing.updated_at = datetime.now(timezone.utc)
        session.add(existing)
    else:
        new_filter = ArFilter(
            filter_key=payload.filter_key,
            filter_data=payload.filter_data,
            is_active=payload.is_active,
            sort_order=payload.sort_order,
        )
        session.add(new_filter)

    session.commit()
    try:
        await redis_client.delete(_REDIS_FILTERS_KEY)
    except Exception:
        logger.warning("Redis delete failed for ar:filters:v1")

    return {"message": "ok"}


@router.put("/ar/admin/filters/{filter_key}")
async def update_ar_filter(
    filter_key: str,
    payload: ArFilterUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Partially update an AR filter. Admin only."""
    _require_admin(current_user)

    row = session.exec(select(ArFilter).where(ArFilter.filter_key == filter_key)).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Filter '{filter_key}' not found")

    if payload.filter_data is not None:
        row.filter_data = payload.filter_data
    if payload.is_active is not None:
        row.is_active = payload.is_active
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order

    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()

    try:
        await redis_client.delete(_REDIS_FILTERS_KEY)
    except Exception:
        logger.warning("Redis delete failed for ar:filters:v1")

    return {"message": "ok"}


@router.put("/ar/admin/games/{game_id}/config")
def update_game_config(
    game_id: str,
    payload: ArGameConfigUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Update game config. Admin only."""
    _require_admin(current_user)

    row = session.exec(select(ArGameConfig).where(ArGameConfig.game_id == game_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Game config '{game_id}' not found")

    row.config_data = payload.config_data
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()

    return {"message": "ok"}
